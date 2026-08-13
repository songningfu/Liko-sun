from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.database import Database, get_database
from app.main import app


def test_admin_summary_requires_shared_token_and_returns_aggregates() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+pysqlite:///:memory:",
        session_secret="s" * 32,
        admin_api_token="admin-test-token",
    )
    database = Database(settings.database_url)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_database] = lambda: database
    try:
        with TestClient(app) as client:
            assert client.get("/api/admin/summary").status_code == 401
            assert (
                client.get(
                    "/api/admin/summary",
                    headers={"Authorization": "Bearer wrong"},
                ).status_code
                == 401
            )
            response = client.get(
                "/api/admin/summary",
                headers={"Authorization": "Bearer admin-test-token"},
            )
        assert response.status_code == 200
        assert response.json()["counts"] == {
            "registeredUsers": 0,
            "registeredToday": 0,
            "memoryConsentUsers": 0,
            "activeSessions": 0,
            "chatRequestsToday": 0,
        }
    finally:
        app.dependency_overrides.clear()
