import json
from pathlib import Path

from app.liko_style import load_published_style


def test_only_published_prompt_is_loaded(tmp_path: Path) -> None:
    path = tmp_path / "style.json"
    path.write_text(json.dumps({
        "draft": {"tone": "lively"},
        "published": {"tone": "calm"},
        "published_prompt": "已发布规则",
    }, ensure_ascii=False), encoding="utf-8")
    assert load_published_style(str(path)) == "已发布规则"
    assert load_published_style(str(tmp_path / "missing.json")) == ""
