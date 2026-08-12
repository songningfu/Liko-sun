import json

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.fastgpt import FastGPTEvent
from app.main import app, get_fastgpt_client


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
