import json

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select, update

from app.config import Settings, get_settings
from app.database import Database, get_database
from app.main import app
from app.pocket import PocketNote, PocketProvider, get_pocket_provider


@pytest.fixture
def pocket():
    config = Settings(_env_file=None, environment="testing", pocket_enabled=True,
                      database_url="sqlite+pysqlite:///:memory:",
                      session_secret="pocket-test-session-secret-at-least-32-characters")
    database = Database(config.database_url)
    app.dependency_overrides[get_settings] = lambda: config
    app.dependency_overrides[get_database] = lambda: database
    with TestClient(app) as client:
        response = client.post("/api/auth/register", json={"username": "pocket_alice", "password": "test-only-password"})
        assert response.status_code == 201
        yield client, config, database
    app.dependency_overrides.clear()
    database.engine.dispose()


def create(client, text="周五前把访谈提纲发给老师。", request_id="pocket-request-01", source="text"):
    return client.post("/api/pocket/records", json={"requestId": request_id, "text": text, "source": source})


def test_feature_is_opt_in_and_requires_login():
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    try:
        client = TestClient(app)
        assert client.get("/api/pocket/status").status_code == 404
        assert client.get("/api/pocket/records").status_code == 404
        app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, pocket_enabled=True)
        assert client.get("/api/pocket/records").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_original_survives_refresh_and_idempotent_retry(pocket):
    client, _, _ = pocket
    first = create(client)
    assert first.status_code == 201
    note = first.json()
    assert note["analysisState"] == "original"
    assert note["summary"] == ""
    assert note["originalText"] == note["text"]
    assert create(client).json()["id"] == note["id"]
    assert len(client.get("/api/pocket/records").json()) == 1
    assert create(client, text="另一条内容").status_code == 409
    assert create(client, source="browser_voice").status_code == 409
    assert client.get("/api/pocket/status").json() == {
        "mode": "browser_prototype", "hardwareConnected": False,
        "analysisAvailable": False, "voiceAvailable": False, "searchMode": "keyword",
    }


def test_edit_preserves_initial_source_and_rejects_stale_write(pocket):
    client, _, _ = pocket
    note = create(client).json()
    edit = {"text": "周四前把访谈提纲发给老师。", "kind": "task", "revision": note["revision"]}
    response = client.patch(f"/api/pocket/records/{note['id']}", json=edit)
    assert response.status_code == 200
    assert response.json()["text"].startswith("周四")
    assert response.json()["originalText"].startswith("周五")
    assert response.json()["kind"] == "task"
    assert client.patch(f"/api/pocket/records/{note['id']}", json=edit).status_code == 409
    assert client.get("/api/pocket/records?q=访谈").json()[0]["id"] == note["id"]
    assert client.get("/api/pocket/records?q=不存在").json() == []
    assert client.get("/api/pocket/records?kind=idea").json() == []


def test_record_ownership_covers_search_edit_analysis_and_delete(pocket):
    alice, _, _ = pocket
    note = create(alice).json()
    bob = TestClient(app)
    assert bob.post("/api/auth/register", json={"username": "pocket_bob", "password": "test-only-password"}).status_code == 201
    assert bob.get("/api/pocket/records").json() == []
    path = f"/api/pocket/records/{note['id']}"
    assert bob.patch(path, json={"text": "其他内容", "revision": note["revision"]}).status_code == 404
    assert bob.post(path + "/analyze").status_code == 404
    assert bob.delete(path).status_code == 404
    assert alice.delete(path).status_code == 204
    assert alice.get("/api/pocket/records").json() == []


def test_search_treats_sql_wildcards_as_literal_and_validates_blank(pocket):
    client, _, _ = pocket
    create(client, text="课题已经完成 50%", request_id="note-percent")
    create(client, text="另一个普通记录", request_id="note-ordinary")
    assert len(client.get("/api/pocket/records", params={"q": "%"}).json()) == 1
    assert client.get("/api/pocket/records", params={"q": "_"}).json() == []
    assert create(client, text=" \n ").status_code == 422


def test_provider_failure_cannot_lose_original_or_leak_upstream(pocket):
    client, config, _ = pocket
    config.pocket_llm_api_key = "never-expose-this-test-key"
    config.pocket_llm_model = "test-model"
    def fail(request):
        return httpx.Response(500, text="private provider details and never-expose-this-test-key")
    app.dependency_overrides[get_pocket_provider] = lambda: PocketProvider(config, transport=httpx.MockTransport(fail))
    response = create(client)
    assert response.status_code == 201
    assert response.json()["analysisState"] == "failed"
    assert response.json()["text"].startswith("周五")
    assert "never-expose" not in response.text
    assert "private provider" not in response.text
    assert len(client.get("/api/pocket/records").json()) == 1


def test_configured_analysis_uses_actual_provider_response(pocket):
    client, config, _ = pocket
    config.pocket_llm_api_key = "test-key"
    config.pocket_llm_model = "test-model"
    def reply(request):
        body = json.loads(request.content)
        assert body["messages"][1]["content"] == "周五前把访谈提纲发给老师。"
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "title": "发送访谈提纲", "summary": "周五前把访谈提纲发给老师。", "kind": "task",
            "tasks": [{"text": "把访谈提纲发给老师", "dueText": "周五前"}],
        })}}]})
    app.dependency_overrides[get_pocket_provider] = lambda: PocketProvider(config, transport=httpx.MockTransport(reply))
    note = create(client).json()
    assert note["analysisState"] == "ready"
    assert note["title"] == "发送访谈提纲"
    assert note["tasks"][0]["dueText"] == "周五前"
    assert note["originalText"] == "周五前把访谈提纲发给老师。"


def test_delayed_analysis_does_not_overwrite_user_correction(pocket):
    client, config, database = pocket
    note = create(client).json()
    from app.pocket import Analysis
    class DelayedProvider:
        def __init__(self):
            self.config = config
        async def analyze(self, text):
            with database.session() as db, db.begin():
                db.execute(update(PocketNote).where(PocketNote.id == note["id"]).values(
                    text="用户刚刚纠正的内容", title="用户刚刚纠正的内容", revision=note["revision"] + 1))
            return Analysis(title="旧内容", summary="不应覆盖", kind="note")
    app.dependency_overrides[get_pocket_provider] = lambda: DelayedProvider()
    response = client.post(f"/api/pocket/records/{note['id']}/analyze")
    assert response.status_code == 200
    assert response.json()["text"] == "用户刚刚纠正的内容"
    assert response.json()["summary"] == ""


def test_voice_requires_configuration_and_transcription_is_not_autosaved(pocket):
    client, config, _ = pocket
    assert client.post("/api/pocket/transcribe", files={"file": ("recording.webm", b"test", "audio/webm")}).status_code == 503
    config.pocket_asr_base_url = "https://speech.example.com/v1"
    config.pocket_asr_api_key = "test-key"
    config.pocket_asr_model = "test-asr"
    def reply(request):
        assert request.url.path == "/v1/audio/transcriptions"
        return httpx.Response(200, json={"text": "  这是识别后的原文。  "})
    app.dependency_overrides[get_pocket_provider] = lambda: PocketProvider(config, transport=httpx.MockTransport(reply))
    response = client.post("/api/pocket/transcribe", files={"file": ("recording.webm", b"test", "audio/webm;codecs=opus")})
    assert response.status_code == 200
    assert response.json() == {"text": "这是识别后的原文。", "saved": False}
    assert client.get("/api/pocket/records").json() == []
    assert client.post("/api/pocket/transcribe", files={"file": ("file.txt", b"test", "text/plain")}).status_code == 415
