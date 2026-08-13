"""来源注册表：data/registry.json 的读写与来源生命周期管理。"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
REGISTRY_FILE = DATA_ROOT / "registry.json"

_lock = threading.Lock()

SOURCE_TYPES = {"official", "internal", "wechat"}
TRUST_LEVELS = {"authoritative", "institutional", "provided", "community"}
# 官方与内网页面属于"官方事实"通道；公众号内容一律标记为非官方待核验。
OFFICIAL_TYPES = {"official", "internal"}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Registry:
    def __init__(self, path: Path = REGISTRY_FILE):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"next_id": 1, "sources": []})

    def _read(self) -> dict:
        with _lock:
            return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        with _lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def list(self) -> list[dict]:
        return self._read()["sources"]

    def get(self, source_id: str) -> dict | None:
        for src in self._read()["sources"]:
            if src["id"] == source_id:
                return src
        return None

    def add(self, *, url: str, source_type: str, title: str = "", department: str = "",
            campus: str = "通用", category: str = "", notes: str = "",
            trust_level: str = "") -> dict:
        url = url.strip()
        if source_type not in SOURCE_TYPES:
            raise ValueError(f"不支持的来源类型: {source_type}")
        if not re.match(r"^https?://", url):
            raise ValueError("URL 必须以 http:// 或 https:// 开头")
        if source_type == "official":
            host = re.sub(r"^https?://", "", url).split("/")[0].lower()
            if not (host == "szu.edu.cn" or host.endswith(".szu.edu.cn")):
                raise ValueError("官方来源仅限 szu.edu.cn 及其子域名；内网页面请选择 internal 类型")
        if source_type == "wechat" and "mp.weixin.qq.com" not in url:
            raise ValueError("公众号来源目前仅支持 mp.weixin.qq.com 文章链接")
        trust_level = trust_level or {
            "official": "authoritative", "internal": "institutional", "wechat": "community"
        }[source_type]
        if trust_level not in TRUST_LEVELS:
            raise ValueError("不支持的可信等级")
        if source_type == "wechat" and trust_level == "authoritative":
            raise ValueError("公众号文章不能直接标为权威官方资料")

        data = self._read()
        for src in data["sources"]:
            if src["url"] == url:
                raise ValueError(f"该 URL 已存在: {src['id']}")
        src = {
            "id": f"SRC-{data['next_id']:04d}",
            "title": title.strip(),
            "url": url,
            "source_type": source_type,
            "official": source_type in OFFICIAL_TYPES,
            "trust_level": trust_level,
            "department": department.strip(),
            "campus": campus.strip() or "通用",
            "category": category.strip(),
            "notes": notes.strip(),
            "enabled": True,
            "created_at": now_iso(),
            "last_fetched_at": "",
            "last_hash": "",
            "last_status": "pending",
            "last_error": "",
            "fetch_count": 0,
        }
        data["next_id"] += 1
        data["sources"].append(src)
        self._write(data)
        return src

    def update(self, source_id: str, **fields) -> dict:
        data = self._read()
        for src in data["sources"]:
            if src["id"] == source_id:
                allowed = {"title", "department", "campus", "category", "notes", "enabled", "trust_level",
                           "last_fetched_at", "last_hash", "last_status", "last_error", "fetch_count"}
                for key, value in fields.items():
                    if key in allowed:
                        src[key] = value
                self._write(data)
                return src
        raise KeyError(f"来源不存在: {source_id}")

    def remove(self, source_id: str) -> None:
        data = self._read()
        before = len(data["sources"])
        data["sources"] = [s for s in data["sources"] if s["id"] != source_id]
        if len(data["sources"]) == before:
            raise KeyError(f"来源不存在: {source_id}")
        self._write(data)

    def stats(self) -> dict:
        sources = self.list()
        return {
            "total": len(sources),
            "enabled": sum(1 for s in sources if s["enabled"]),
            "official": sum(1 for s in sources if s["official"]),
            "unofficial": sum(1 for s in sources if not s["official"]),
            "authoritative": sum(1 for s in sources if s.get("trust_level") == "authoritative"),
            "institutional": sum(1 for s in sources if s.get("trust_level") == "institutional"),
            "ok": sum(1 for s in sources if s["last_status"] in {"ok", "changed", "unchanged"}),
            "failed": sum(1 for s in sources if s["last_status"] == "failed"),
            "pending": sum(1 for s in sources if s["last_status"] == "pending"),
        }
