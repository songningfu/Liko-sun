"""读取知识工厂已发布的 Liko 表达规则。草稿不会进入线上请求。"""
from __future__ import annotations

import json
from pathlib import Path


def load_published_style(path_value: str | None) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    prompt = data.get("published_prompt")
    return prompt.strip()[:6000] if isinstance(prompt, str) else ""
