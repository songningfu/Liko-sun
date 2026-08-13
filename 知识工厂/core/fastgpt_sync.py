"""FastGPT 增量同步：新版本先禁用暂存，审核后切换，保留可回滚版本。"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .process import DATA_ROOT

STATE_FILE = DATA_ROOT / "fastgpt_sync.json"
_lock = threading.Lock()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _initial_state() -> dict:
    return {"sources": {}}


def _read() -> dict:
    if not STATE_FILE.exists():
        return _initial_state()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("sources"), dict) else _initial_state()
    except (json.JSONDecodeError, OSError):
        return _initial_state()


def _write(data: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _content_hash(chunks: list[str]) -> str:
    return hashlib.sha256("\n\n".join(chunks).encode("utf-8")).hexdigest()


class FastGPTSync:
    """只使用服务端环境变量连接 FastGPT，不把密钥暴露给前端。"""

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.base_url = os.getenv("FACTORY_FASTGPT_BASE_URL", "http://127.0.0.1:3100/api").rstrip("/")
        self.api_key = os.getenv("FACTORY_FASTGPT_API_KEY", "")
        self.dataset_id = os.getenv("FACTORY_FASTGPT_DATASET_ID", "")
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.dataset_id)

    def status(self) -> dict:
        return {
            "configured": self.configured,
            "base_url": self.base_url if self.configured else "",
            "dataset_id_tail": self.dataset_id[-6:] if self.configured else "",
        }

    def list_versions(self, source_id: str | None = None) -> list[dict]:
        with _lock:
            sources = _read()["sources"]
        rows: list[dict] = []
        for sid, entry in sources.items():
            if source_id and source_id != sid:
                continue
            for version in entry.get("versions", []):
                rows.append({"source_id": sid, **version})
        return sorted(rows, key=lambda row: row.get("staged_at", ""), reverse=True)

    def _request(self, method: str, endpoint: str, payload: dict) -> Any:
        if not self.configured:
            raise RuntimeError("FastGPT 同步尚未配置")
        try:
            with httpx.Client(timeout=90, transport=self.transport) as client:
                response = client.request(
                    method,
                    f"{self.base_url}{endpoint}",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise RuntimeError("无法连接 FastGPT 数据集接口，请检查服务器配置和服务状态") from exc
        if body.get("code") not in {None, 200}:
            raise RuntimeError(body.get("message") or body.get("statusText") or "FastGPT 请求失败")
        return body.get("data")

    def _set_forbid(self, collection_id: str, forbid: bool) -> None:
        self._request("POST", "/core/dataset/collection/update", {
            "id": collection_id,
            "forbid": forbid,
        })

    def stage(self, *, source_id: str, title: str, chunks: list[str], metadata: dict | None = None) -> dict:
        chunks = [chunk.strip() for chunk in chunks if chunk and chunk.strip()]
        if not chunks:
            raise ValueError("没有可同步的知识块")
        content_hash = _content_hash(chunks)
        with _lock:
            current = _read()["sources"].get(source_id, {})
            same = next((v for v in current.get("versions", [])
                         if v.get("content_hash") == content_hash and v.get("status") != "failed"), None)
        if same:
            return {**same, "source_id": source_id, "unchanged": True}

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"{title[:72]} [{source_id} {stamp}]"
        collection_id = self._request("POST", "/core/dataset/collection/create", {
            "datasetId": self.dataset_id,
            "parentId": None,
            "name": name,
            "type": "virtual",
            "metadata": metadata or {},
        })
        if isinstance(collection_id, dict):
            collection_id = collection_id.get("collectionId")
        if not collection_id:
            raise RuntimeError("FastGPT 未返回集合 ID")

        # 最关键的安全门：任何数据入库前先禁用集合，避免半成品进入线上检索。
        self._set_forbid(collection_id, True)
        try:
            for start in range(0, len(chunks), 200):
                batch = chunks[start:start + 200]
                self._request("POST", "/core/dataset/data/pushData", {
                    "collectionId": collection_id,
                    "trainingType": "chunk",
                    "data": [{"q": chunk, "a": ""} for chunk in batch],
                })
        except Exception:
            # 集合保持禁用并写入失败记录，便于后台定位，不冒险删除现场。
            with _lock:
                data = _read()
                entry = data["sources"].setdefault(source_id, {"versions": []})
                entry["versions"].append({
                    "collection_id": collection_id,
                    "title": title,
                    "content_hash": content_hash,
                    "chunks": len(chunks),
                    "status": "failed",
                    "staged_at": _now(),
                    "metadata": metadata or {},
                })
                _write(data)
            raise

        with _lock:
            data = _read()
            entry = data["sources"].setdefault(source_id, {"versions": []})
            version = {
                "version": len(entry["versions"]) + 1,
                "collection_id": collection_id,
                "title": title,
                "collection_name": name,
                "content_hash": content_hash,
                "chunks": len(chunks),
                "status": "staged",
                "staged_at": _now(),
                "metadata": metadata or {},
            }
            entry["versions"].append(version)
            _write(data)
        return {"source_id": source_id, **version, "unchanged": False}

    def _ensure_ready(self, version: dict) -> None:
        result = self._request("POST", "/core/dataset/collection/listV2", {
            "offset": 0,
            "pageSize": 30,
            "datasetId": self.dataset_id,
            "parentId": None,
            "searchText": version.get("collection_name", version.get("title", "")),
        })
        rows = result.get("list", []) if isinstance(result, dict) else []
        row = next((item for item in rows if item.get("_id") == version.get("collection_id")), None)
        if not row:
            raise RuntimeError("无法确认 FastGPT 暂存集合的训练状态")
        training = int(row.get("trainingAmount") or 0)
        data_amount = int(row.get("dataAmount") or 0)
        expected = int(version.get("chunks") or 0)
        if training > 0 or data_amount < expected:
            raise RuntimeError(f"FastGPT 仍在训练知识块（已完成 {data_amount}/{expected}，队列 {training}），请稍后再发布")

    def promote(self, *, source_id: str, collection_id: str) -> dict:
        with _lock:
            data = _read()
            entry = data["sources"].get(source_id)
            if not entry:
                raise KeyError(f"同步来源不存在: {source_id}")
            target = next((v for v in entry["versions"] if v.get("collection_id") == collection_id), None)
            if not target or target.get("status") not in {"staged", "published"}:
                raise ValueError("只能发布已成功暂存的版本")
            previous = next((v for v in entry["versions"] if v.get("status") == "published"), None)

        self._ensure_ready(target)
        self._set_forbid(collection_id, False)
        try:
            if previous and previous.get("collection_id") != collection_id:
                self._set_forbid(previous["collection_id"], True)
        except Exception:
            self._set_forbid(collection_id, True)
            raise

        with _lock:
            data = _read()
            entry = data["sources"][source_id]
            for version in entry["versions"]:
                if version.get("status") == "published":
                    version["status"] = "superseded"
                if version.get("collection_id") == collection_id:
                    version["status"] = "published"
                    version["published_at"] = _now()
            history = entry.setdefault("publication_history", [])
            if previous and not history:
                history.append(previous["collection_id"])
            if not history or history[-1] != collection_id:
                history.append(collection_id)
            _write(data)
            target = next(v for v in entry["versions"] if v.get("collection_id") == collection_id)
        return {"source_id": source_id, **target}

    def rollback(self, *, source_id: str) -> dict:
        with _lock:
            data = _read()
            entry = data["sources"].get(source_id)
            if not entry:
                raise KeyError(f"同步来源不存在: {source_id}")
            history = entry.get("publication_history", [])
            if len(history) < 2:
                raise ValueError("还没有可回滚的历史版本")
            current = next((v for v in entry["versions"] if v.get("collection_id") == history[-1]), None)
            target = next((v for v in entry["versions"] if v.get("collection_id") == history[-2]), None)
            if not current or not target:
                raise ValueError("历史版本记录不完整，暂不能自动回滚")

        self._set_forbid(target["collection_id"], False)
        try:
            self._set_forbid(current["collection_id"], True)
        except Exception:
            self._set_forbid(target["collection_id"], True)
            raise

        with _lock:
            data = _read()
            entry = data["sources"][source_id]
            for version in entry["versions"]:
                if version.get("collection_id") == current["collection_id"]:
                    version["status"] = "superseded"
                if version.get("collection_id") == target["collection_id"]:
                    version["status"] = "published"
                    version["rolled_back_at"] = _now()
            entry["publication_history"].pop()
            _write(data)
            target = next(v for v in entry["versions"] if v.get("collection_id") == target["collection_id"])
        return {"source_id": source_id, **target}
