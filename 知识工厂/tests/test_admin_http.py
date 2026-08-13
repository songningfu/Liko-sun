from fastapi.testclient import TestClient

import server
from core.admin_auth import AdminConfig, AdminSessionManager, hash_password


def test_factory_api_requires_admin_session_and_csrf(monkeypatch) -> None:
    config = AdminConfig(
        username="operator",
        password_hash=hash_password("a strong test password"),
        session_secret="z" * 32,
        cookie_secure=False,
        cookie_path="/",
        liko_api_base_url="http://127.0.0.1:8001",
        liko_admin_token="test-token",
        fastgpt_admin_url="http://127.0.0.1:3100",
    )
    monkeypatch.setattr(server, "admin_config", config)
    monkeypatch.setattr(server, "admin_sessions", AdminSessionManager(config))
    server._login_attempts.clear()

    with TestClient(server.app) as client:
        assert client.get("/api/overview").status_code == 401
        assert client.post(
            "/api/admin/login",
            json={"username": "operator", "password": "wrong password"},
        ).status_code == 401
        login = client.post(
            "/api/admin/login",
            json={"username": "operator", "password": "a strong test password"},
        )
        assert login.status_code == 200
        csrf = login.json()["csrfToken"]
        assert client.get("/api/overview").status_code == 200
        assert client.post("/api/admin/logout").status_code == 403
        assert client.post(
            "/api/admin/logout",
            headers={"X-Admin-CSRF": csrf},
        ).status_code == 204
