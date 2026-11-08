from fastapi.testclient import TestClient
import pytest

from app.config import Settings, get_settings
from app.database import Database, get_database
from app.pocket_cloud import create_app


@pytest.fixture
def cloud():
    config = Settings(_env_file=None, environment="production", pocket_enabled=True,
                      database_url="sqlite+pysqlite:///:memory:",
                      session_secret="cloud-test-secret-more-than-32-characters")
    database = Database(config.database_url)
    app = create_app()
    inner = app.routes[0].app
    inner.dependency_overrides[get_settings] = lambda: config
    inner.dependency_overrides[get_database] = lambda: database
    with TestClient(app, base_url="https://songningfu.site") as client:
        yield client, database
    database.engine.dispose()


def register(client):
    return client.post("/liko-pocket/api/auth/register",
                       json={"username": "cloud_alice", "password": "test-only-password"})


def test_cloud_keeps_its_routes_under_its_own_prefix(cloud):
    client, _ = cloud
    home = client.get("/liko-pocket/")
    assert home.status_code == 200
    assert '<meta name="pocket-api-prefix" content="/liko-pocket">' in home.text
    assert client.get("/liko-pocket/app.js").status_code == 200
    assert client.get("/api/auth/me").status_code == 404
    assert client.post("/liko-pocket/api/chat/stream").status_code in {404, 405}
    assert client.get("/liko-pocket/docs").status_code == 404
    assert client.get("/liko-pocket/api/health").json()["database"] == "ok"
    assert client.get("/liko-pocket/api/pocket/records").status_code == 401


def test_cookie_namespace_path_and_logout_are_isolated(cloud):
    client, _ = cloud
    result = register(client)
    assert result.status_code == 201
    header = result.headers["set-cookie"]
    assert header.startswith("liko_pocket_session=")
    assert "Path=/liko-pocket/" in header and "Secure" in header and "HttpOnly" in header
    assert "no-store" in result.headers["cache-control"]
    assert client.get("/liko-pocket/api/auth/me").json()["authenticated"]
    session = client.cookies.get("liko_pocket_session")
    client.cookies.clear()
    # Even a valid Pocket token using the legacy cookie name is ignored.
    client.cookies.set("liko_session", session)
    assert not client.get("/liko-pocket/api/auth/me").json()["authenticated"]
    client.cookies.clear()
    client.cookies.set("liko_pocket_session", session, domain="songningfu.site", path="/liko-pocket/")
    assert client.post("/liko-pocket/api/auth/logout").status_code == 204
    assert not client.get("/liko-pocket/api/auth/me").json()["authenticated"]


def test_foreign_origins_cannot_create_accounts_or_modify_notes(cloud):
    client, _ = cloud
    assert client.post("/liko-pocket/api/auth/register", headers={"Origin": "https://other.example"},
                       json={"username": "cloud_alice", "password": "test-only-password"}).status_code == 403
    assert register(client).status_code == 201
    assert client.post("/liko-pocket/api/pocket/records", headers={"Origin": "https://songningfu.site"},
                       json={"requestId": "cloud-note-01", "text": "周五前提交访谈提纲"}).status_code == 201


def test_daily_cloud_budget_does_not_block_reading_existing_notes(cloud):
    client, database = cloud
    assert register(client).status_code == 201
    original = database.consume_counter
    database.consume_counter = lambda **kwargs: (False, 50)
    try:
        assert client.post("/liko-pocket/api/pocket/records",
                           json={"requestId": "cloud-note-01", "text": "保留原文"}).status_code == 429
        assert client.get("/liko-pocket/api/pocket/records").status_code == 200
    finally:
        database.consume_counter = original
