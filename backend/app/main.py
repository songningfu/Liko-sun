from collections.abc import AsyncIterable
from datetime import UTC, datetime, timedelta, timezone
import asyncio
import json
import math
import re
import secrets
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent

from .attachments import (
    AttachmentNotFoundError,
    AttachmentProcessor,
    AttachmentServiceError,
    AttachmentTooLargeError,
)
from .auth import (
    GUEST_COOKIE,
    CurrentUser,
    create_guest_token,
    get_current_user,
    private_hash,
    valid_guest_token,
)
from .auth_routes import auth_payload, router as auth_router
from .config import Settings, get_settings
from .database import Database, get_database, utc_now
from .fastgpt import FastGPTClient, FastGPTUpstreamError
from .memory import (
    LongTermMemoryStore,
    MemoryNotFoundError,
    MemoryRecord,
    MemoryServiceError,
    get_memory_store,
)
from .quota import QuotaStatus, consume_chat_quota
from .schemas import (
    ChatRequest,
    AttachmentSummary,
    AttachmentUploadResponse,
    ConversationMessageResponse,
    ConversationSummaryResponse,
    FeedbackRequest,
    MemoryItemResponse,
    MemoryUpdateRequest,
    RecommendationItemResponse,
    SourceItem,
)


app = FastAPI(
    title="Liko API",
    version="0.1.0",
    description="Liko 校园 AI 伴生助手的最小业务后端。",
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-ID"],
)
app.include_router(auth_router)


def get_fastgpt_client(
    config: Annotated[Settings, Depends(get_settings)],
) -> FastGPTClient:
    return FastGPTClient(config)


def get_attachment_processor(
    config: Annotated[Settings, Depends(get_settings)],
) -> AttachmentProcessor:
    return AttachmentProcessor(config)


def _attachment_owner_hash(
    request: Request,
    response: Response | None,
    current_user: CurrentUser,
    config: Settings,
    *,
    create_guest: bool,
) -> str:
    if not config.session_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="附件服务尚未配置。",
        )
    if current_user.authenticated and current_user.user_id:
        identity = f"user:{current_user.user_id}"
    else:
        signed_token = request.cookies.get(GUEST_COOKIE)
        guest_id = valid_guest_token(signed_token, config.session_secret)
        if not guest_id and create_guest and response is not None:
            signed_token = create_guest_token(config.session_secret)
            guest_id = valid_guest_token(signed_token, config.session_secret)
            response.set_cookie(
                GUEST_COOKIE,
                signed_token,
                max_age=365 * 24 * 60 * 60,
                httponly=True,
                secure=config.secure_cookies,
                samesite="lax",
                path="/",
            )
        if not guest_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="附件会话已经失效，请重新上传。")
        identity = f"guest:{guest_id}"
    return private_hash(f"attachment:{identity}", config.session_secret)


DEFAULT_RECOMMENDATIONS = [
    ("丽湖校区图书馆几点关门？", "学习资源", "学习"),
    ("校园卡丢了怎么补办？", "办事指南", "办事"),
    ("这学期什么时候放假？", "校历安排", "校历"),
    ("新生宿舍入住要准备什么？", "校园生活", "生活"),
]


def _question_key(value: str) -> str:
    return re.sub(r"[\s？?！!，,。；;：:、]+", "", value).lower()


def _question_category(value: str) -> str:
    rules = (
        ("校历", ("放假", "开学", "考试", "校历")),
        ("办事", ("校园卡", "补办", "申请", "证明", "缴费")),
        ("生活", ("宿舍", "食堂", "校车", "快递", "医院")),
        ("学习", ("图书馆", "选课", "教室", "培养方案", "课程")),
    )
    return next((category for category, words in rules if any(word in value for word in words)), "校园")


def _rank_recommendations(rows: list[dict], now: datetime) -> list[dict]:
    grouped: dict[str, dict] = {}
    for row in rows:
        question = str(row.get("question") or "").strip()
        key = _question_key(question)
        if not key or len(question) > 120:
            continue
        created_at = row.get("createdAt")
        age_days = max(0.0, (now - created_at).total_seconds() / 86400) if created_at else 7.0
        weight = math.exp(-age_days / 3.5) * (1.0 if row.get("source") == "input" else 0.15)
        item = grouped.setdefault(
            key,
            {"question": question, "score": 0.0, "users": set(), "category": _question_category(question)},
        )
        item["score"] += weight
        item["users"].add(row.get("userId"))
        if created_at and created_at >= item.get("latest", datetime.min):
            item["latest"] = created_at
            item["question"] = question
    ranked = sorted(
        (
            item
            for item in grouped.values()
            if len(item["users"]) >= 2 and item["category"] != "校园"
        ),
        key=lambda item: (item["score"] + len(item["users"]) * 0.65, item.get("latest", datetime.min)),
        reverse=True,
    )
    selected: list[dict] = []
    categories: set[str] = set()
    for item in ranked:
        if len(selected) >= 4:
            break
        if item["category"] in categories and len(ranked) > 4:
            continue
        categories.add(item["category"])
        selected.append(
            {
                "question": item["question"],
                "label": "近 7 天高频",
                "category": item["category"],
                "score": round(item["score"] + len(item["users"]) * 0.65, 3),
            }
        )
    used = {_question_key(item["question"]) for item in selected}
    for question, label, category in DEFAULT_RECOMMENDATIONS:
        if len(selected) >= 4:
            break
        if _question_key(question) in used:
            continue
        selected.append({"question": question, "label": label, "category": category, "score": 0.0})
    return selected


def _explicit_profile_fact(message: str) -> str | None:
    match = re.search(
        r"(?:我叫|我的名字是|请叫我)([\u4e00-\u9fffA-Za-z0-9·_-]{1,20})(?:[，。！？!?、]|$)",
        message.strip(),
    )
    if not match:
        return None
    name = match.group(1).strip()
    if any(word in name for word in ("什么", "啥", "谁", "哪个", "哪一个")):
        return None
    return f"用户的名字是{name}。"


def _asks_user_name(message: str) -> bool:
    normalized = re.sub(r"[\s？?！!，,。]+", "", message)
    return normalized in {"我叫什么", "我叫什么名字", "你记得我叫什么", "你记得我的名字吗"}


def _name_from_memories(memories: list[str]) -> str | None:
    for memory in memories:
        match = re.search(r"用户的名字是([^。；;，,\n]{1,20})", memory)
        if match:
            return match.group(1).strip()
    return None


_background_tasks: set[asyncio.Task] = set()


async def _remember_interaction_safely(
    memory_store: LongTermMemoryStore,
    user_id: str,
    user_message: str,
    assistant_message: str,
    conversation_id: str,
) -> None:
    try:
        await memory_store.remember_interaction(
            user_id,
            user_message,
            assistant_message,
            conversation_id,
        )
    except MemoryServiceError:
        return


def _schedule_memory_write(coroutine) -> None:
    task = asyncio.create_task(coroutine)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


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
            "authConfigured": config.auth_configured,
            "memoryConfigured": config.memory_configured,
            "visionConfigured": config.vision_configured,
        },
    }


@app.post("/api/chat/attachments", response_model=AttachmentUploadResponse)
async def upload_chat_attachment(
    request: Request,
    response: Response,
    file: Annotated[UploadFile, File(...)],
    config: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    processor: Annotated[AttachmentProcessor, Depends(get_attachment_processor)],
) -> AttachmentUploadResponse:
    owner_hash = _attachment_owner_hash(
        request,
        response,
        current_user,
        config,
        create_guest=True,
    )
    try:
        data = await file.read(config.attachment_max_bytes + 1)
        record = await processor.save(
            owner_hash=owner_hash,
            filename=file.filename or "附件",
            content_type=file.content_type,
            data=data,
        )
    except AttachmentTooLargeError as error:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(error)) from error
    except AttachmentServiceError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    finally:
        await file.close()
    return AttachmentUploadResponse(
        id=record.id,
        name=record.name,
        kind=record.kind,
        size=record.size,
        preview=record.preview,
        expiresAt=record.expires_at,
    )


@app.delete("/api/chat/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_attachment(
    attachment_id: str,
    request: Request,
    config: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    processor: Annotated[AttachmentProcessor, Depends(get_attachment_processor)],
) -> Response:
    owner_hash = _attachment_owner_hash(
        request,
        None,
        current_user,
        config,
        create_guest=False,
    )
    try:
        processor.delete(attachment_id, owner_hash)
    except AttachmentNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/recommendations", response_model=list[RecommendationItemResponse])
async def recommendations(
    database: Annotated[Database | None, Depends(get_database)],
) -> list[RecommendationItemResponse]:
    now = utc_now()
    rows = database.recent_questions(since=now - timedelta(days=7)) if database else []
    return [RecommendationItemResponse(**item) for item in _rank_recommendations(rows, now)]


@app.get("/api/admin/summary")
async def admin_summary(
    request: Request,
    config: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database | None, Depends(get_database)],
) -> dict:
    """Expose aggregate Liko operations metrics to the local admin gateway only."""

    expected = config.admin_api_token or ""
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无权访问管理统计。")

    china_tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
    now_china = datetime.now(china_tz)
    midnight_china = now_china.replace(hour=0, minute=0, second=0, microsecond=0)
    registered_since = midnight_china.astimezone(UTC).replace(tzinfo=None)
    counts = (
        database.admin_summary(period_key=now_china.date().isoformat(), registered_since=registered_since)
        if database
        else {
            "registeredUsers": 0,
            "registeredToday": 0,
            "memoryConsentUsers": 0,
            "activeSessions": 0,
            "chatRequestsToday": 0,
        }
    )
    return {
        "status": "ok",
        "service": config.app_name,
        "version": config.app_version,
        "environment": config.environment,
        "integrations": {
            "fastgptConfigured": config.fastgpt_configured,
            "databaseConfigured": config.database_configured,
            "authConfigured": config.auth_configured,
            "memoryConfigured": config.memory_configured,
        },
        "counts": counts,
    }


@app.post("/api/chat/stream", response_class=EventSourceResponse)
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    config: Annotated[Settings, Depends(get_settings)],
    fastgpt: Annotated[FastGPTClient, Depends(get_fastgpt_client)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    memory_store: Annotated[LongTermMemoryStore, Depends(get_memory_store)],
    quota: Annotated[QuotaStatus, Depends(consume_chat_quota)],
    database: Annotated[Database | None, Depends(get_database)],
    attachment_processor: Annotated[AttachmentProcessor, Depends(get_attachment_processor)],
) -> AsyncIterable[ServerSentEvent]:
    """Expose the frontend streaming contract without fabricating a campus answer."""

    answer_id = f"ans_{uuid4().hex}"
    conversation_id = payload.conversation_id or (
        f"chat_{uuid4().hex}" if current_user.authenticated else f"guest_{uuid4().hex}"
    )
    attachments = []
    if payload.attachment_ids:
        owner_hash = _attachment_owner_hash(
            request,
            None,
            current_user,
            config,
            create_guest=False,
        )
        try:
            attachments = attachment_processor.resolve(payload.attachment_ids, owner_hash)
        except AttachmentNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except AttachmentServiceError as error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    attachment_context = attachment_processor.build_context(attachments)
    model_message = payload.message
    if attachment_context:
        model_message = f"{payload.message}\n\n{attachment_context}"
    attachment_summaries = [{"name": item.name, "kind": item.kind} for item in attachments]
    if current_user.authenticated and current_user.user_id and database:
        try:
            database.ensure_conversation(
                user_id=current_user.user_id,
                conversation_id=conversation_id,
                title=payload.message,
            )
        except PermissionError:
            conversation_id = f"chat_{uuid4().hex}"
            database.ensure_conversation(
                user_id=current_user.user_id,
                conversation_id=conversation_id,
                title=payload.message,
            )
        database.add_chat_message(
            message_id=str(uuid4()),
            conversation_id=conversation_id,
            user_id=current_user.user_id,
            role="user",
            content=payload.message,
            attachments_json=json.dumps(attachment_summaries, ensure_ascii=False),
            source_type=payload.source,
        )
    memory_active = current_user.can_use_long_term_memory and memory_store.configured
    memory_context: list[str] = []
    explicit_memory_saved: bool | None = None
    if memory_active and current_user.user_id:
        explicit_fact = _explicit_profile_fact(payload.message)
        if explicit_fact:
            try:
                await memory_store.remember_fact(current_user.user_id, explicit_fact, conversation_id)
                memory_context.append(explicit_fact)
                explicit_memory_saved = True
            except MemoryServiceError:
                explicit_memory_saved = False
        try:
            relevant = await memory_store.search(
                current_user.user_id,
                payload.message,
                config.mem0_top_k,
            )
            memory_context.extend(item.text for item in relevant if item.text not in memory_context)
        except MemoryServiceError:
            pass

        if _asks_user_name(payload.message) and not _name_from_memories(memory_context):
            try:
                all_memories = await memory_store.list_memories(current_user.user_id, 100)
                memory_context.extend(
                    item.text for item in all_memories if item.text not in memory_context
                )
            except MemoryServiceError:
                pass

    yield ServerSentEvent(
        event="meta",
        data={
            "requestId": payload.request_id,
            "conversationId": conversation_id,
            "answerId": answer_id,
            "mode": "fastgpt" if config.fastgpt_configured else "integration_stub",
            "memoryMode": "long_term" if memory_active else "session_only",
            "quota": {
                "limit": quota.limit,
                "used": quota.used,
                "remaining": quota.remaining,
            },
        },
    )

    remembered_name = _name_from_memories(memory_context) if _asks_user_name(payload.message) else None
    if remembered_name:
        answer_text = f"你叫{remembered_name}。"
        yield ServerSentEvent(event="delta", data={"text": answer_text})
        yield ServerSentEvent(event="sources", data={"items": []})
        if current_user.authenticated and current_user.user_id and database:
            try:
                database.add_chat_message(
                    message_id=str(uuid4()),
                    conversation_id=conversation_id,
                    user_id=current_user.user_id,
                    role="assistant",
                    content=answer_text,
                    answer_id=answer_id,
                    sources_json="[]",
                )
            except (PermissionError, TypeError, ValueError):
                pass
        yield ServerSentEvent(
            event="done",
            data={
                "answerId": answer_id,
                "finishReason": "stop",
                "canRate": True,
                "sourceCount": 0,
                "memorySaved": True,
            },
        )
        return

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
    source_items: list[dict] = []
    answer_started = False
    answer_parts: list[str] = []
    try:
        async for upstream_event in fastgpt.stream_chat(
            message=model_message,
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
                items = upstream_event.data.get("items", [])
                source_items = items if isinstance(items, list) else []
                source_count = len(source_items)
            yield ServerSentEvent(event=upstream_event.event, data=upstream_event.data)
    except FastGPTUpstreamError as error:
        yield ServerSentEvent(event="error", data={"message": str(error), "retryable": True})
        return

    if source_count == 0:
        yield ServerSentEvent(event="sources", data={"items": []})
    answer_text = "".join(answer_parts)
    if current_user.authenticated and current_user.user_id and database and answer_started:
        try:
            database.add_chat_message(
                message_id=str(uuid4()),
                conversation_id=conversation_id,
                user_id=current_user.user_id,
                role="assistant",
                content=answer_text,
                answer_id=answer_id,
                sources_json=json.dumps(source_items, ensure_ascii=False),
            )
        except (PermissionError, TypeError, ValueError):
            pass
    if memory_active and current_user.user_id and answer_started:
        _schedule_memory_write(
            _remember_interaction_safely(
                memory_store,
                current_user.user_id,
                payload.message,
                answer_text,
                conversation_id,
            )
        )
    yield ServerSentEvent(
        event="done",
        data={
            "answerId": answer_id,
            "finishReason": "stop" if answer_started else "empty",
            "canRate": answer_started,
            "sourceCount": source_count,
            "memorySaved": explicit_memory_saved,
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


def _require_history_user(current_user: CurrentUser, database: Database | None) -> tuple[str, Database]:
    if not current_user.authenticated or not current_user.user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录。")
    if database is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="对话记录服务尚未配置。")
    return current_user.user_id, database


@app.get("/api/users/me/conversations", response_model=list[ConversationSummaryResponse])
async def list_conversations(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    database: Annotated[Database | None, Depends(get_database)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ConversationSummaryResponse]:
    user_id, database = _require_history_user(current_user, database)
    return [ConversationSummaryResponse(**item) for item in database.list_conversations(user_id=user_id, limit=limit)]


@app.get(
    "/api/users/me/conversations/{conversation_id}/messages",
    response_model=list[ConversationMessageResponse],
)
async def conversation_messages(
    conversation_id: str,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    database: Annotated[Database | None, Depends(get_database)],
) -> list[ConversationMessageResponse]:
    user_id, database = _require_history_user(current_user, database)
    try:
        messages = database.conversation_messages(user_id=user_id, conversation_id=conversation_id)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    result = []
    for message in messages:
        try:
            raw_sources = json.loads(message.sources_json or "[]")
            sources = [SourceItem.model_validate(item) for item in raw_sources if isinstance(item, dict)]
        except (json.JSONDecodeError, ValueError, TypeError):
            sources = []
        try:
            raw_attachments = json.loads(message.attachments_json or "[]")
            attachments = [
                AttachmentSummary.model_validate(item)
                for item in raw_attachments
                if isinstance(item, dict)
            ]
        except (json.JSONDecodeError, ValueError, TypeError):
            attachments = []
        result.append(
            ConversationMessageResponse(
                id=message.id,
                role=message.role,
                content=message.content,
                answerId=message.answer_id,
                sources=sources,
                attachments=attachments,
                createdAt=message.created_at.isoformat(),
            )
        )
    return result


@app.delete(
    "/api/users/me/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation(
    conversation_id: str,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    database: Annotated[Database | None, Depends(get_database)],
) -> Response:
    user_id, database = _require_history_user(current_user, database)
    if not database.delete_conversation(user_id=user_id, conversation_id=conversation_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="没有找到这段对话。")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/users/me/liko")
async def current_liko(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> dict:
    payload = auth_payload(current_user)
    if not current_user.authenticated:
        payload["message"] = "游客可以正常使用问答；注册后才会生成并永久绑定专属 Liko。"
    return payload


def _require_memory_user(current_user: CurrentUser, memory_store: LongTermMemoryStore) -> str:
    if not current_user.authenticated or not current_user.user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录。")
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
