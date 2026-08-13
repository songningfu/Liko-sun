import json
from pathlib import Path

import httpx
import pytest
from types import SimpleNamespace

from core import fastgpt_sync, pipeline


def test_fastgpt_stage_promote_and_rollback(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(fastgpt_sync, "STATE_FILE", tmp_path / "sync.json")
    monkeypatch.setenv("FACTORY_FASTGPT_BASE_URL", "https://fastgpt.example/api")
    monkeypatch.setenv("FACTORY_FASTGPT_API_KEY", "secret")
    monkeypatch.setenv("FACTORY_FASTGPT_DATASET_ID", "dataset-1")
    created = []
    updates = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        if request.url.path.endswith("/collection/create"):
            collection_id = f"collection-{len(created) + 1}"
            created.append(payload)
            return httpx.Response(200, json={"code": 200, "data": collection_id})
        if request.url.path.endswith("/collection/update"):
            updates.append(payload)
            return httpx.Response(200, json={"code": 200, "data": None})
        if request.url.path.endswith("/data/pushData"):
            assert len(payload["data"]) <= 200
            return httpx.Response(200, json={"code": 200, "data": {"insertLen": len(payload["data"])}})
        if request.url.path.endswith("/collection/listV2"):
            return httpx.Response(200, json={"code": 200, "data": {"list": [
                {"_id": f"collection-{index + 1}", "dataAmount": 1, "trainingAmount": 0}
                for index in range(len(created))
            ]}})
        return httpx.Response(404)

    sync = fastgpt_sync.FastGPTSync(transport=httpx.MockTransport(handler))
    v1 = sync.stage(source_id="TXT-00001", title="通知", chunks=["第一版正文"])
    assert v1["status"] == "staged"
    assert updates[0] == {"id": "collection-1", "forbid": True}
    sync.promote(source_id="TXT-00001", collection_id=v1["collection_id"])

    v2 = sync.stage(source_id="TXT-00001", title="通知", chunks=["第二版正文"])
    sync.promote(source_id="TXT-00001", collection_id=v2["collection_id"])
    rolled_back = sync.rollback(source_id="TXT-00001")

    assert rolled_back["collection_id"] == "collection-1"
    assert rolled_back["status"] == "published"
    assert not any("secret" in json.dumps(row) for row in sync.list_versions())


def test_promotion_waits_for_fastgpt_training(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(fastgpt_sync, "STATE_FILE", tmp_path / "sync.json")
    monkeypatch.setenv("FACTORY_FASTGPT_BASE_URL", "https://fastgpt.example/api")
    monkeypatch.setenv("FACTORY_FASTGPT_API_KEY", "secret")
    monkeypatch.setenv("FACTORY_FASTGPT_DATASET_ID", "dataset-1")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/collection/create"):
            return httpx.Response(200, json={"code": 200, "data": "collection-training"})
        if request.url.path.endswith("/collection/update"):
            return httpx.Response(200, json={"code": 200, "data": None})
        if request.url.path.endswith("/data/pushData"):
            return httpx.Response(200, json={"code": 200, "data": {"insertLen": 1}})
        if request.url.path.endswith("/collection/listV2"):
            return httpx.Response(200, json={"code": 200, "data": {"list": [
                {"_id": "collection-training", "dataAmount": 0, "trainingAmount": 1}
            ]}})
        return httpx.Response(404)

    sync = fastgpt_sync.FastGPTSync(transport=httpx.MockTransport(handler))
    version = sync.stage(source_id="SRC-0001", title="训练中", chunks=["正文"])
    with pytest.raises(RuntimeError, match="仍在训练"):
        sync.promote(source_id="SRC-0001", collection_id=version["collection_id"])


def test_failed_stage_can_retry_same_content(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(fastgpt_sync, "STATE_FILE", tmp_path / "sync.json")
    monkeypatch.setenv("FACTORY_FASTGPT_BASE_URL", "https://fastgpt.example/api")
    monkeypatch.setenv("FACTORY_FASTGPT_API_KEY", "secret")
    monkeypatch.setenv("FACTORY_FASTGPT_DATASET_ID", "dataset-1")
    attempts = {"create": 0, "push": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/collection/create"):
            attempts["create"] += 1
            return httpx.Response(200, json={"code": 200, "data": f"collection-{attempts['create']}"})
        if request.url.path.endswith("/collection/update"):
            return httpx.Response(200, json={"code": 200, "data": None})
        if request.url.path.endswith("/data/pushData"):
            attempts["push"] += 1
            if attempts["push"] == 1:
                return httpx.Response(503, json={"message": "temporary"})
            return httpx.Response(200, json={"code": 200, "data": {"insertLen": 1}})
        return httpx.Response(404)

    sync = fastgpt_sync.FastGPTSync(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError):
        sync.stage(source_id="SRC-RETRY", title="重试资料", chunks=["相同正文"])
    retried = sync.stage(source_id="SRC-RETRY", title="重试资料", chunks=["相同正文"])
    assert retried["status"] == "staged"
    assert retried["collection_id"] == "collection-2"


def _factory_doc(source_id: str, *, risk: str = "普通", official: bool = True,
                 warnings: list[str] | None = None) -> dict:
    return {
        "source_id": source_id,
        "title": f"资料 {source_id}",
        "url": f"https://www.szu.edu.cn/{source_id}",
        "status": "可导入" if official else "待核验",
        "official": official,
        "trust_level": "authoritative" if official else "community",
        "risk": risk,
        "duplicate_check": {"status": "unique", "score": 0},
        "quality_check": {"status": "passed"},
        "campus": "通用",
        "topic": "校务",
        "model_enrichment": {"risk_level": "low", "warnings": warnings or []},
        "_doc": SimpleNamespace(chunks=[{"content": "这是一段足够完整的测试资料正文。"}]),
    }


def test_auto_sync_only_publishes_low_risk_official_documents(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "load", lambda: {
        "auto_sync_enabled": True,
        "auto_publish_enabled": True,
        "model_enrichment_enabled": True,
    })

    class FakeSync:
        configured = True

        def __init__(self):
            self.staged = []
            self.published = []

        def stage(self, **kwargs):
            self.staged.append(kwargs)
            return {"source_id": kwargs["source_id"], "collection_id": f"c-{kwargs['source_id']}",
                    "status": "staged", "unchanged": False}

        def promote(self, *, source_id, collection_id):
            self.published.append((source_id, collection_id))
            return {"status": "published"}

    sync = FakeSync()
    events = []
    result = pipeline.auto_sync_documents([
        _factory_doc("SAFE"),
        _factory_doc("RISK", risk="需注意时效/费用/联系方式等字段"),
        _factory_doc("WARN", warnings=["发布日期不清楚"]),
        _factory_doc("WECHAT", official=False),
    ], events.append, sync=sync)

    assert [row["source_id"] for row in sync.staged] == ["SAFE", "RISK", "WARN", "WECHAT"]
    assert sync.published == [("SAFE", "c-SAFE")]
    assert result == {"staged": 4, "unchanged": 0, "published": 1,
                      "pending": 0, "review": 3, "failed": 0}
    assert sync.staged[0]["metadata"]["autoPublishEligible"] is True
    assert sync.staged[1]["metadata"]["autoPublishEligible"] is False


def test_periodic_promoter_only_uses_auto_publish_eligible_versions(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "load", lambda: {
        "auto_sync_enabled": True,
        "auto_publish_enabled": True,
    })

    class FakeSync:
        configured = True

        def __init__(self):
            self.published = []

        def list_versions(self):
            return [
                {"source_id": "SAFE", "collection_id": "c-safe", "title": "安全资料",
                 "status": "staged", "metadata": {"autoPublishEligible": True}},
                {"source_id": "REVIEW", "collection_id": "c-review", "title": "待审资料",
                 "status": "staged", "metadata": {"autoPublishEligible": False}},
            ]

        def promote(self, *, source_id, collection_id):
            self.published.append((source_id, collection_id))

    sync = FakeSync()
    result = pipeline.promote_ready_versions(sync=sync)
    assert sync.published == [("SAFE", "c-safe")]
    assert result == {"published": 1, "training": 0, "failed": 0}
