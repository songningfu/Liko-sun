"""可选的大模型资料理解层；原文永不被模型覆盖。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx

from .process import DATA_ROOT

CACHE_ROOT = DATA_ROOT / "enrichment"

SYSTEM_PROMPT = """你是 Liko 知识工厂的资料整理器。只做信息抽取，不修改事实，不补充原文没有的内容。
返回一个 JSON 对象，字段必须包含：summary、topic、campus、audience、department、document_type、
published_at、valid_until、risk_level、facts、warnings。facts 是数组，每项包含 subject、predicate、value、evidence。
无法确定时使用空字符串或空数组。不要输出思考过程，不要使用 Markdown 代码块。"""


def _cache_key(title: str, text: str) -> str:
    return hashlib.sha256(f"{title}\n{text}".encode("utf-8")).hexdigest()


def _extract_json(value: str) -> dict[str, Any]:
    value = value.strip()
    if value.startswith("```"):
        value = value.strip("`")
        if value.startswith("json"):
            value = value[4:].lstrip()
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON 对象")
    data = json.loads(value[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("模型返回格式不是对象")
    return data


class KnowledgeEnricher:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.base_url = os.getenv("FACTORY_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.api_key = os.getenv("FACTORY_LLM_API_KEY", "")
        self.model = os.getenv("FACTORY_LLM_MODEL", "deepseek-v4-flash")
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def status(self) -> dict:
        return {"configured": self.configured, "model": self.model if self.configured else ""}

    def enrich(self, *, title: str, text: str, force: bool = False) -> dict:
        if not self.configured:
            raise RuntimeError("知识加工模型尚未配置")
        key = _cache_key(title, text)
        cache_path = CACHE_ROOT / f"{key}.json"
        if cache_path.exists() and not force:
            return json.loads(cache_path.read_text(encoding="utf-8"))

        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"标题：{title}\n\n原文：\n{text[:30000]}"},
            ],
        }
        try:
            with httpx.Client(timeout=90, transport=self.transport) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise RuntimeError("知识加工模型暂时不可用，请检查服务器配置和余额") from exc
        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        result = _extract_json(content)
        result["content_hash"] = key
        result["model"] = self.model
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
