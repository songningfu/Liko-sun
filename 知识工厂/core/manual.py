"""人工提供文字资料的收件箱与原文落盘。"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .process import DATA_ROOT, PAGES_ROOT, write_page_markdown

INDEX_FILE = DATA_ROOT / "manual_submissions.json"
_lock = threading.Lock()

TRUST_LEVELS = {"authoritative", "institutional", "provided", "community"}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read() -> dict:
    if not INDEX_FILE.exists():
        return {"next_id": 1, "items": []}
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"next_id": 1, "items": []}


def _write(data: dict) -> None:
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(INDEX_FILE)


def _is_szu_url(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "szu.edu.cn" or host.endswith(".szu.edu.cn"))


class ManualSubmissionStore:
    def list(self) -> list[dict]:
        with _lock:
            return list(reversed(_read()["items"]))

    def get(self, submission_id: str) -> dict | None:
        with _lock:
            return next((item for item in _read()["items"] if item["id"] == submission_id), None)

    def add(
        self,
        *,
        title: str,
        text: str,
        source_label: str = "",
        source_url: str = "",
        department: str = "",
        campus: str = "通用",
        category: str = "",
        trust_level: str = "provided",
        notes: str = "",
    ) -> dict:
        title = title.strip()
        text = text.strip()
        source_url = source_url.strip()
        if not title:
            raise ValueError("请填写资料标题")
        if len(text) < 20:
            raise ValueError("正文至少需要 20 个字符")
        if trust_level not in TRUST_LEVELS:
            raise ValueError("不支持的可信等级")
        if trust_level == "authoritative" and not _is_szu_url(source_url):
            raise ValueError("权威官方资料必须提供深大官方 HTTPS 原文链接")

        with _lock:
            data = _read()
            submission_id = f"TXT-{data['next_id']:05d}"
            data["next_id"] += 1
            created_at = _now()
            official = trust_level == "authoritative"
            page_path = PAGES_ROOT / f"{submission_id}.md"
            write_page_markdown(
                page_path,
                meta={
                    "source_id": submission_id,
                    "site_id": "MANUAL",
                    "site_name": source_label.strip() or "人工提供资料",
                    "title": title,
                    "url": source_url,
                    "category": category.strip(),
                    "department": department.strip(),
                    "campus": campus.strip() or "通用",
                    "source_type": "manual",
                    "trust_level": trust_level,
                    "official": "true" if official else "false",
                    "notes": notes.strip(),
                    "collected_at": created_at,
                },
                body=text,
            )
            item = {
                "id": submission_id,
                "title": title,
                "source_label": source_label.strip(),
                "source_url": source_url,
                "department": department.strip(),
                "campus": campus.strip() or "通用",
                "category": category.strip(),
                "trust_level": trust_level,
                "official": official,
                "notes": notes.strip(),
                "created_at": created_at,
                "page_path": str(page_path),
                "review_status": "pending",
            }
            data["items"].append(item)
            _write(data)
            return item

    def set_review(self, submission_id: str, status: str, note: str = "") -> dict:
        if status not in {"pending", "approved", "rejected"}:
            raise ValueError("审核状态只能是 pending / approved / rejected")
        with _lock:
            data = _read()
            for item in data["items"]:
                if item["id"] == submission_id:
                    item["review_status"] = status
                    item["review_note"] = note.strip()
                    item["reviewed_at"] = _now()
                    _write(data)
                    return item
        raise KeyError(f"文字资料不存在: {submission_id}")

    def set_enrichment(self, submission_id: str, result: dict) -> dict:
        """保存模型整理摘要；完整原文仍保存在独立 Markdown，不被覆盖。"""
        with _lock:
            data = _read()
            for item in data["items"]:
                if item["id"] == submission_id:
                    item["enrichment"] = {
                        key: result.get(key)
                        for key in ("summary", "topic", "campus", "audience", "department",
                                    "document_type", "published_at", "valid_until", "risk_level", "warnings")
                    }
                    item["enriched_at"] = _now()
                    _write(data)
                    return item
        raise KeyError(f"文字资料不存在: {submission_id}")
