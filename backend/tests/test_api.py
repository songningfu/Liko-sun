import json

from fastapi.testclient import TestClient

from app.auth import CurrentUser, get_current_user
from app.attachments import AttachmentRecord
from app.config import Settings, get_settings
from app.fastgpt import FastGPTEvent
from app.main import (
    _activity_from_memories,
    _asks_current_activity,
    _asks_user_name,
    _explicit_profile_fact,
    _explicit_profile_facts,
    _is_campus_fact_query,
    _name_from_memories,
    app,
    get_attachment_processor,
    get_fastgpt_client,
)
from app.memory import MemoryRecord, get_memory_store


client = TestClient(app)


def parse_sse_data(response_text: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response_text.splitlines()
        if line.startswith("data: ")
    ]


def test_health_reports_unconfigured_integrations() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["integrations"] == {
        "fastgptConfigured": False,
        "databaseConfigured": False,
        "authConfigured": False,
        "memoryConfigured": False,
        "visionConfigured": False,
    }
    assert response.headers["cache-control"] == "no-store"


def test_chat_stream_exposes_all_contract_events() -> None:
    response = client.post(
        "/api/chat/stream",
        json={
            "requestId": "request-0001",
            "conversationId": None,
            "message": "校园卡在哪里补办？",
            "campus": "粤海",
            "source": "input",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: meta" in response.text
    assert "event: delta" in response.text
    assert "event: sources" in response.text
    assert "event: done" in response.text
    event_data = parse_sse_data(response.text)
    assert any("不会根据示例内容猜测" in item.get("text", "") for item in event_data)


def test_chat_stream_proxies_configured_fastgpt_events() -> None:
    class FakeFastGPTClient:
        async def stream_chat(self, **kwargs):
            assert kwargs["message"] == "校园卡在哪里补办？"
            yield FastGPTEvent(event="delta", data={"text": "请前往校园卡服务点办理。"})
            yield FastGPTEvent(
                event="sources",
                data={
                    "items": [
                        {
                            "title": "校园卡服务",
                            "url": "https://its.szu.edu.cn/card",
                            "department": "信息中心",
                        }
                    ]
                },
            )

    configured = Settings(
        _env_file=None,
        fastgpt_base_url="https://rag.example.com/api",
        fastgpt_api_key="test-only-key",
        fastgpt_app_id="app_test",
    )
    app.dependency_overrides[get_settings] = lambda: configured
    app.dependency_overrides[get_fastgpt_client] = lambda: FakeFastGPTClient()
    try:
        response = client.post(
            "/api/chat/stream",
            json={"requestId": "request-real-1", "message": "校园卡在哪里补办？"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert '"mode": "fastgpt"' in response.text
    event_data = parse_sse_data(response.text)
    assert any(item.get("text") == "请前往校园卡服务点办理。" for item in event_data)
    assert any(
        source.get("url") == "https://its.szu.edu.cn/card"
        for item in event_data
        for source in item.get("items", [])
    )
    assert any(
        item.get("finishReason") == "stop" and item.get("canRate") is True
        for item in event_data
    )


def test_chat_attachment_content_is_added_only_to_current_model_message() -> None:
    record = AttachmentRecord(
        id="a" * 32,
        owner_hash="opaque-owner",
        name="课程安排.pdf",
        kind="document",
        mime_type="application/pdf",
        size=120,
        extracted_text="课程考试安排在周五下午。",
        created_at="2026-08-13T00:00:00+00:00",
        expires_at="2026-08-14T00:00:00+00:00",
    )

    class FakeAttachmentProcessor:
        def resolve(self, attachment_ids, owner_hash):
            assert attachment_ids == [record.id]
            assert len(owner_hash) == 64
            return [record]

        def build_context(self, records):
            assert records == [record]
            return "附件参考：课程考试安排在周五下午。"

    class FakeFastGPTClient:
        async def stream_chat(self, **kwargs):
            assert kwargs["message"].startswith("帮我确认考试时间")
            assert "附件参考：课程考试安排在周五下午。" in kwargs["message"]
            yield FastGPTEvent(event="delta", data={"text": "考试安排在周五下午。"})

    configured = Settings(
        _env_file=None,
        fastgpt_base_url="https://rag.example.com/api",
        fastgpt_api_key="test-only-key",
        fastgpt_app_id="app_test",
        session_secret="test-session-secret-at-least-thirty-two-characters",
    )
    app.dependency_overrides[get_settings] = lambda: configured
    app.dependency_overrides[get_fastgpt_client] = lambda: FakeFastGPTClient()
    app.dependency_overrides[get_attachment_processor] = lambda: FakeAttachmentProcessor()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="user_123",
        authenticated=True,
        memory_consent=False,
    )
    try:
        response = client.post(
            "/api/chat/stream",
            json={
                "requestId": "request-file-01",
                "message": "帮我确认考试时间",
                "attachmentIds": [record.id],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert any(item.get("text") == "考试安排在周五下午。" for item in parse_sse_data(response.text))


def test_guest_chat_never_reads_or_writes_long_term_memory() -> None:
    class FakeMemoryStore:
        configured = True

        async def search(self, *args, **kwargs):
            raise AssertionError("guest must not search long-term memory")

        async def remember_interaction(self, *args, **kwargs):
            raise AssertionError("guest must not write long-term memory")

    class FakeFastGPTClient:
        async def stream_chat(self, **kwargs):
            assert kwargs["memories"] == []
            yield FastGPTEvent(event="delta", data={"text": "游客回答"})

    configured = Settings(
        _env_file=None,
        fastgpt_base_url="https://rag.example.com/api",
        fastgpt_api_key="test-only-key",
        fastgpt_app_id="app_test",
    )
    app.dependency_overrides[get_settings] = lambda: configured
    app.dependency_overrides[get_fastgpt_client] = lambda: FakeFastGPTClient()
    app.dependency_overrides[get_memory_store] = lambda: FakeMemoryStore()
    try:
        response = client.post(
            "/api/chat/stream",
            json={"requestId": "request-guest-1", "message": "我最近喜欢跑步"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert '"memoryMode": "session_only"' in response.text


def test_authenticated_user_with_consent_uses_long_term_memory() -> None:
    calls: list[tuple] = []

    class FakeMemoryStore:
        configured = True

        async def search(self, user_id, query, limit):
            calls.append(("search", user_id, query, limit))
            return [MemoryRecord(id="mem_1", text="用户喜欢简短直接的回答")]

        async def remember_interaction(
            self, user_id, user_message, assistant_message, conversation_id
        ):
            calls.append(
                (
                    "remember",
                    user_id,
                    user_message,
                    assistant_message,
                    conversation_id,
                )
            )

    class FakeFastGPTClient:
        async def stream_chat(self, **kwargs):
            assert kwargs["memories"] == ["用户喜欢简短直接的回答"]
            yield FastGPTEvent(event="delta", data={"text": "我会简短回答。"})

    configured = Settings(
        _env_file=None,
        fastgpt_base_url="https://rag.example.com/api",
        fastgpt_api_key="test-only-key",
        fastgpt_app_id="app_test",
    )
    app.dependency_overrides[get_settings] = lambda: configured
    app.dependency_overrides[get_fastgpt_client] = lambda: FakeFastGPTClient()
    app.dependency_overrides[get_memory_store] = lambda: FakeMemoryStore()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="user_123",
        authenticated=True,
        memory_consent=True,
    )
    try:
        response = client.post(
            "/api/chat/stream",
            json={"requestId": "request-user-01", "message": "给我一点学习建议"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert '"memoryMode": "long_term"' in response.text
    assert calls[0] == ("search", "user_123", "给我一点学习建议", 5)
    assert calls[1][0:4] == (
        "remember",
        "user_123",
        "给我一点学习建议",
        "我会简短回答。",
    )


def test_explicit_name_profile_is_parsed_and_recalled_without_campus_rag() -> None:
    assert _explicit_profile_fact("我的名字是小荔。") == "用户的名字是小荔。"
    assert _explicit_profile_fact("我叫什么名字？") is None
    assert _asks_user_name("我叫什么名字？") is True
    assert _name_from_memories(["用户的名字是小荔。"]) == "小荔"

    class FakeMemoryStore:
        configured = True

        async def search(self, user_id, query, limit):
            assert (user_id, query, limit) == ("user_123", "我叫什么名字？", 5)
            return []

        async def list_memories(self, user_id, limit):
            assert (user_id, limit) == ("user_123", 100)
            return [MemoryRecord(id="mem_name", text="用户的名字是小荔。")]

    class FakeFastGPTClient:
        async def stream_chat(self, **kwargs):
            raise AssertionError("明确的个人资料问答不应交给校园知识库")
            yield

    configured = Settings(
        _env_file=None,
        fastgpt_base_url="https://rag.example.com/api",
        fastgpt_api_key="test-only-key",
        fastgpt_app_id="app_test",
    )
    app.dependency_overrides[get_settings] = lambda: configured
    app.dependency_overrides[get_fastgpt_client] = lambda: FakeFastGPTClient()
    app.dependency_overrides[get_memory_store] = lambda: FakeMemoryStore()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="user_123",
        authenticated=True,
        memory_consent=True,
    )
    try:
        response = client.post(
            "/api/chat/stream",
            json={"requestId": "request-name-01", "message": "我叫什么名字？"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert any(item.get("text") == "你叫小荔。" for item in parse_sse_data(response.text))
    assert '"sourceCount": 0' in response.text


def test_explicit_activity_is_saved_and_recalled_across_conversations() -> None:
    assert _explicit_profile_fact("我在实习。") == "用户目前在实习。"
    assert _explicit_profile_facts("我叫小荔，我最近在腾讯实习。") == [
        "用户的名字是小荔。",
        "用户目前在腾讯实习。",
    ]
    assert _asks_current_activity("我最近在干嘛？") is True
    assert _activity_from_memories(["用户目前在腾讯实习。"]) == "在腾讯实习"

    class FakeMemoryStore:
        configured = True

        async def search(self, user_id, query, limit):
            assert (user_id, query, limit) == ("user_123", "我最近在干嘛？", 5)
            return []

        async def list_memories(self, user_id, limit):
            assert (user_id, limit) == ("user_123", 100)
            return [MemoryRecord(id="mem_activity", text="用户目前在腾讯实习。")]

    class FakeFastGPTClient:
        async def stream_chat(self, **kwargs):
            raise AssertionError("明确的近况回忆不应交给校园知识库")
            yield

    configured = Settings(
        _env_file=None,
        fastgpt_base_url="https://rag.example.com/api",
        fastgpt_api_key="test-only-key",
        fastgpt_app_id="app_test",
    )
    app.dependency_overrides[get_settings] = lambda: configured
    app.dependency_overrides[get_fastgpt_client] = lambda: FakeFastGPTClient()
    app.dependency_overrides[get_memory_store] = lambda: FakeMemoryStore()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="user_123",
        authenticated=True,
        memory_consent=True,
    )
    try:
        response = client.post(
            "/api/chat/stream",
            json={"requestId": "request-activity-01", "message": "我最近在干嘛？"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert any(item.get("text") == "你最近在腾讯实习。" for item in parse_sse_data(response.text))
    assert '"sourceCount": 0' in response.text


def test_only_campus_fact_questions_are_eligible_for_sources() -> None:
    assert _is_campus_fact_query("丽湖图书馆几点关门？") is True
    assert _is_campus_fact_query("我最近在干嘛？") is False
    assert _is_campus_fact_query("帮我看看这张图片") is False


def test_casual_chat_drops_unrelated_campus_sources() -> None:
    class FakeFastGPTClient:
        async def stream_chat(self, **kwargs):
            assert "自然的陪伴对话" in kwargs["message"]
            yield FastGPTEvent(event="delta", data={"text": "实习辛苦啦，今天还顺利吗？"})
            yield FastGPTEvent(
                event="sources",
                data={
                    "items": [
                        {
                            "title": "校区通勤车安排",
                            "url": "https://www.szu.edu.cn/bus",
                            "department": "深圳大学",
                        }
                    ]
                },
            )

    configured = Settings(
        _env_file=None,
        fastgpt_base_url="https://rag.example.com/api",
        fastgpt_api_key="test-only-key",
        fastgpt_app_id="app_test",
    )
    app.dependency_overrides[get_settings] = lambda: configured
    app.dependency_overrides[get_fastgpt_client] = lambda: FakeFastGPTClient()
    try:
        response = client.post(
            "/api/chat/stream",
            json={"requestId": "request-casual-01", "message": "其实我在实习。"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert not any(item.get("items") for item in parse_sse_data(response.text))
    assert '"sourceCount": 0' in response.text


def test_chat_rejects_blank_message() -> None:
    response = client.post(
        "/api/chat/stream",
        json={"requestId": "request-0002", "message": "   "},
    )

    assert response.status_code == 422


def test_feedback_is_validated_but_not_persisted() -> None:
    response = client.post(
        "/api/answers/ans_demo/feedback",
        json={"rating": "unhelpful", "reason": "outdated"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "not_persisted"


def test_reason_requires_unhelpful_rating() -> None:
    response = client.post(
        "/api/answers/ans_demo/feedback",
        json={"rating": "helpful", "reason": "outdated"},
    )

    assert response.status_code == 422


def test_guest_has_no_formal_liko() -> None:
    response = client.get("/api/users/me/liko")

    assert response.status_code == 200
    assert response.json()["authenticated"] is False
    assert response.json()["companion"] is None


def test_guest_cannot_list_memories() -> None:
    response = client.get("/api/users/me/memories")

    assert response.status_code == 401


def test_authenticated_user_without_consent_can_still_manage_existing_memories() -> None:
    class FakeMemoryStore:
        configured = True

        async def list_memories(self, user_id, limit):
            assert user_id == "user_123"
            assert limit == 50
            return [MemoryRecord(id="mem_1", text="关闭前保存的记忆")]

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="user_123",
        authenticated=True,
        memory_consent=False,
    )
    app.dependency_overrides[get_memory_store] = lambda: FakeMemoryStore()
    try:
        response = client.get("/api/users/me/memories")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["text"] == "关闭前保存的记忆"


def test_authenticated_user_can_manage_own_memories() -> None:
    calls: list[tuple] = []

    class FakeMemoryStore:
        configured = True

        async def list_memories(self, user_id, limit):
            calls.append(("list", user_id, limit))
            return [MemoryRecord(id="mem_1", text="用户喜欢跑步")]

        async def update_memory(self, user_id, memory_id, text):
            calls.append(("update", user_id, memory_id, text))
            return MemoryRecord(id=memory_id, text=text)

        async def delete_memory(self, user_id, memory_id):
            calls.append(("delete", user_id, memory_id))

        async def delete_all(self, user_id):
            calls.append(("delete_all", user_id))

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="user_123",
        authenticated=True,
        memory_consent=True,
    )
    app.dependency_overrides[get_memory_store] = lambda: FakeMemoryStore()
    try:
        listed = client.get("/api/users/me/memories")
        updated = client.patch(
            "/api/users/me/memories/mem_1",
            json={"text": "用户每周跑步三次"},
        )
        deleted = client.delete("/api/users/me/memories/mem_1")
        cleared = client.delete("/api/users/me/memories")
    finally:
        app.dependency_overrides.clear()

    assert listed.status_code == 200
    assert listed.json()[0]["text"] == "用户喜欢跑步"
    assert updated.status_code == 200
    assert updated.json()["text"] == "用户每周跑步三次"
    assert deleted.status_code == 204
    assert cleared.status_code == 204
    assert calls == [
        ("list", "user_123", 50),
        ("update", "user_123", "mem_1", "用户每周跑步三次"),
        ("delete", "user_123", "mem_1"),
        ("delete_all", "user_123"),
    ]
