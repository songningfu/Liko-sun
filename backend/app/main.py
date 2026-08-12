from collections.abc import AsyncIterable
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent

from .config import Settings, get_settings
from .fastgpt import FastGPTClient, FastGPTUpstreamError
from .schemas import ChatRequest, FeedbackRequest


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
        },
    }


@app.post("/api/chat/stream", response_class=EventSourceResponse)
async def chat_stream(
    payload: ChatRequest,
    config: Annotated[Settings, Depends(get_settings)],
    fastgpt: Annotated[FastGPTClient, Depends(get_fastgpt_client)],
) -> AsyncIterable[ServerSentEvent]:
    """Expose the frontend streaming contract without fabricating a campus answer."""

    answer_id = f"ans_{uuid4().hex}"
    conversation_id = payload.conversation_id or f"guest_{uuid4().hex}"

    yield ServerSentEvent(
        event="meta",
        data={
            "requestId": payload.request_id,
            "conversationId": conversation_id,
            "answerId": answer_id,
            "mode": "fastgpt" if config.fastgpt_configured else "integration_stub",
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
    try:
        async for upstream_event in fastgpt.stream_chat(
            message=payload.message,
            conversation_id=conversation_id,
            answer_id=answer_id,
            campus=payload.campus,
        ):
            if upstream_event.event == "delta":
                answer_started = True
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
