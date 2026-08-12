from collections.abc import AsyncIterable
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent

from .auth import CurrentUser, get_current_user
from .config import Settings, get_settings
from .fastgpt import FastGPTClient, FastGPTUpstreamError
from .memory import (
    LongTermMemoryStore,
    MemoryNotFoundError,
    MemoryRecord,
    MemoryServiceError,
    get_memory_store,
)
from .schemas import ChatRequest, FeedbackRequest, MemoryItemResponse, MemoryUpdateRequest


app = FastAPI(
    title="Liko API",
    version="0.1.0",
    description="Liko 校园 AI 伴生助手的最小业务后端。",
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-ID"],
)


def get_fastgpt_client(
    config: Annotated[Settings, Depends(get_settings)],
) -> FastGPTClient:
    return FastGPTClient(config)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
async def health(config: Annotated[Settings, Depends(get_settings)]) -> dict:
    return {
        "status": "ok",
        "service": config.app_name,
        "version": config.app_version,
        "environment": config.environment,
        "integrations": {
            "fastgptConfigured": config.fastgpt_configured,
            "databaseConfigured": config.database_configured,
            "memoryConfigured": config.memory_configured,
        },
    }


@app.post("/api/chat/stream", response_class=EventSourceResponse)
async def chat_stream(
    payload: ChatRequest,
    config: Annotated[Settings, Depends(get_settings)],
    fastgpt: Annotated[FastGPTClient, Depends(get_fastgpt_client)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    memory_store: Annotated[LongTermMemoryStore, Depends(get_memory_store)],
) -> AsyncIterable[ServerSentEvent]:
    """Expose the frontend streaming contract without fabricating a campus answer."""

    answer_id = f"ans_{uuid4().hex}"
    conversation_id = payload.conversation_id or f"guest_{uuid4().hex}"
    memory_active = current_user.can_use_long_term_memory and memory_store.configured
    memory_context: list[str] = []
    if memory_active and current_user.user_id:
        try:
            relevant = await memory_store.search(
                current_user.user_id,
                payload.message,
                config.mem0_top_k,
            )
            memory_context = [item.text for item in relevant]
        except MemoryServiceError:
            memory_context = []

    yield ServerSentEvent(
        event="meta",
        data={
            "requestId": payload.request_id,
            "conversationId": conversation_id,
            "answerId": answer_id,
            "mode": "fastgpt" if config.fastgpt_configured else "integration_stub",
            "memoryMode": "long_term" if memory_active else "session_only",
        },
    )

    if not config.fastgpt_configured:
        message = "校园知识库服务尚未连接。我暂时不能可靠回答这个问题，也不会根据示例内容猜测。"
        yield ServerSentEvent(event="delta", data={"text": message})
        yield ServerSentEvent(event="sources", data={"items": []})
        yield ServerSentEvent(
            event="done",
            data={
                "answerId": answer_id,
                "finishReason": "integration_stub",
                "canRate": False,
            },
        )
        return

    source_count = 0
    answer_started = False
    answer_parts: list[str] = []
    try:
        async for upstream_event in fastgpt.stream_chat(
            message=payload.message,
            conversation_id=conversation_id,
            answer_id=answer_id,
            campus=payload.campus,
            memories=memory_context,
        ):
            if upstream_event.event == "delta":
                answer_started = True
                text = upstream_event.data.get("text")
                if isinstance(text, str):
                    answer_parts.append(text)
            elif upstream_event.event == "sources":
                source_count = len(upstream_event.data.get("items", []))
            yield ServerSentEvent(event=upstream_event.event, data=upstream_event.data)
    except FastGPTUpstreamError as error:
        yield ServerSentEvent(event="error", data={"message": str(error), "retryable": True})
        return

    if source_count == 0:
        yield ServerSentEvent(event="sources", data={"items": []})
    yield ServerSentEvent(
        event="done",
        data={
            "answerId": answer_id,
            "finishReason": "stop" if answer_started else "empty",
            "canRate": answer_started,
            "sourceCount": source_count,
        },
    )
    if memory_active and current_user.user_id and answer_started:
        try:
            await memory_store.remember_interaction(
                current_user.user_id,
                payload.message,
                "".join(answer_parts),
                conversation_id,
            )
        except MemoryServiceError:
            pass


@app.post(
    "/api/answers/{answer_id}/feedback",
    status_code=status.HTTP_202_ACCEPTED,
)
async def record_feedback(answer_id: str, payload: FeedbackRequest) -> JSONResponse:
    """Validate the feedback contract while persistence is not yet available."""

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "answerId": answer_id,
            "rating": payload.rating,
            "status": "not_persisted",
            "message": "反馈格式已通过校验，但数据库尚未接入，本次不会保存。",
        },
    )


@app.get("/api/users/me/liko")
async def current_liko() -> dict:
    return {
        "authenticated": False,
        "companion": None,
        "message": "游客模式尚未创建正式 Liko；登录和注册顺序编号将在用户系统接入后提供。",
    }


def _require_memory_user(current_user: CurrentUser, memory_store: LongTermMemoryStore) -> str:
    if not current_user.authenticated or not current_user.user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录。")
    if not current_user.memory_consent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="请先开启长期记忆。",
        )
    if not memory_store.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="长期记忆服务尚未配置。",
        )
    return current_user.user_id


def _memory_response(memory: MemoryRecord) -> MemoryItemResponse:
    return MemoryItemResponse(
        id=memory.id,
        text=memory.text,
        metadata=memory.metadata,
        createdAt=memory.created_at,
        updatedAt=memory.updated_at,
    )


@app.get("/api/users/me/memories", response_model=list[MemoryItemResponse])
async def list_memories(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    memory_store: Annotated[LongTermMemoryStore, Depends(get_memory_store)],
    query: Annotated[str | None, Query(max_length=1000)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[MemoryItemResponse]:
    user_id = _require_memory_user(current_user, memory_store)
    try:
        if query and query.strip():
            memories = await memory_store.search(user_id, query.strip(), limit)
        else:
            memories = await memory_store.list_memories(user_id, limit)
    except MemoryServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    return [_memory_response(memory) for memory in memories]


@app.patch("/api/users/me/memories/{memory_id}", response_model=MemoryItemResponse)
async def update_memory(
    memory_id: str,
    payload: MemoryUpdateRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    memory_store: Annotated[LongTermMemoryStore, Depends(get_memory_store)],
) -> MemoryItemResponse:
    user_id = _require_memory_user(current_user, memory_store)
    try:
        memory = await memory_store.update_memory(user_id, memory_id, payload.text)
    except MemoryNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except MemoryServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    return _memory_response(memory)


@app.delete("/api/users/me/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: str,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    memory_store: Annotated[LongTermMemoryStore, Depends(get_memory_store)],
) -> Response:
    user_id = _require_memory_user(current_user, memory_store)
    try:
        await memory_store.delete_memory(user_id, memory_id)
    except MemoryNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except MemoryServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.delete("/api/users/me/memories", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_memories(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    memory_store: Annotated[LongTermMemoryStore, Depends(get_memory_store)],
) -> Response:
    user_id = _require_memory_user(current_user, memory_store)
    try:
        await memory_store.delete_all(user_id)
    except MemoryServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
