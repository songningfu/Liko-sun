import json
from pathlib import Path

import httpx
import pytest

from core import fastgpt_sync


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
