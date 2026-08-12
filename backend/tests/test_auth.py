import re

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app


def test_account_lifecycle_liko_identity_and_quota() -> None:
    configured = Settings(
        _env_file=None,
        environment="testing",
        database_url="sqlite+pysqlite:///:memory:",
        session_secret="test-secret-that-is-at-least-32-characters-long",
        guest_daily_limit=2,
        user_daily_limit=3,
    )
    app.dependency_overrides[get_settings] = lambda: configured
    registered = TestClient(app)
    try:
        first = registered.post(
            "/api/auth/register",
            json={"username": "alice_1", "password": "correct-horse"},
        )
        assert first.status_code == 201
        first_body = first.json()
        assert first_body["authenticated"] is True
        assert first_body["user"] == {"username": "alice_1", "memoryConsent": True}
        assert first_body["companion"]["displayNumber"] == "No. 000001"
        assert re.fullmatch(r"#[0-9A-F]{6}", first_body["companion"]["palette"]["shell"])
        assert "password" not in first.text.lower()
        assert "HttpOnly" in first.headers["set-cookie"]
        assert "SameSite=lax" in first.headers["set-cookie"]
        recovery_code = first_body["recoveryCode"]

        me = registered.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["companion"]["palette"] == first_body["companion"]["palette"]
        assert "recoveryCode" not in me.json()

        disabled = registered.patch("/api/auth/memory-consent", json={"enabled": False})
        assert disabled.status_code == 200
        assert disabled.json()["user"]["memoryConsent"] is False
        enabled = registered.patch("/api/auth/memory-consent", json={"enabled": True})
        assert enabled.status_code == 200
        assert enabled.json()["user"]["memoryConsent"] is True

        registered.post("/api/auth/logout")
        wrong = registered.post(
            "/api/auth/login",
            json={"username": "alice_1", "password": "wrong-password"},
        )
        assert wrong.status_code == 401
        logged_in = registered.post(
            "/api/auth/login",
            json={"username": "ALICE_1", "password": "correct-horse"},
        )
        assert logged_in.status_code == 200

        changed = registered.post(
            "/api/auth/password",
            json={"currentPassword": "correct-horse", "newPassword": "new-password-123"},
        )
        assert changed.status_code == 200
        registered.post("/api/auth/logout")

        recovered = registered.post(
            "/api/auth/recover",
            json={
                "username": "alice_1",
                "recoveryCode": recovery_code,
                "newPassword": "recovered-password-123",
            },
        )
        assert recovered.status_code == 200
        assert recovered.json()["recoveryCode"] != recovery_code
        reused = registered.post(
            "/api/auth/recover",
            json={
                "username": "alice_1",
                "recoveryCode": recovery_code,
                "newPassword": "another-recovered-password",
            },
        )
        assert reused.status_code == 400

        second_client = TestClient(app)
        second = second_client.post(
            "/api/auth/register",
            json={"username": "bob_2", "password": "another-password"},
        )
        assert second.status_code == 201
        assert second.json()["companion"]["displayNumber"] == "No. 000002"

        for index in range(3):
            response = second_client.post(
                "/api/chat/stream",
                json={"requestId": f"registered-{index}", "message": "测试额度"},
            )
            assert response.status_code == 200
        exhausted = second_client.post(
            "/api/chat/stream",
            json={"requestId": "registered-3", "message": "测试额度"},
        )
        assert exhausted.status_code == 429

        guest = TestClient(app)
        guest.get("/api/auth/me")
        for index in range(2):
            response = guest.post(
                "/api/chat/stream",
                json={"requestId": f"guest-000{index}", "message": "游客测试"},
            )
            assert response.status_code == 200
        exhausted_guest = guest.post(
            "/api/chat/stream",
            json={"requestId": "guest-0002", "message": "游客测试"},
        )
        assert exhausted_guest.status_code == 429
        assert "登录后" in exhausted_guest.json()["detail"]
    finally:
        app.dependency_overrides.clear()
