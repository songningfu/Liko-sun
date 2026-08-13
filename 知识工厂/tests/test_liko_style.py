from pathlib import Path

import pytest

from core import liko_style


def test_style_publish_and_rollback_keeps_immutable_rules(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(liko_style, "STYLE_FILE", tmp_path / "style.json")
    store = liko_style.LikoStyleStore()

    store.save_draft({"tone": "calm", "paragraphs": 2})
    first = store.publish()
    assert first["published"]["tone"] == "calm"

    store.save_draft({"tone": "lively", "paragraphs": 99})
    second = store.publish()
    assert second["published"]["paragraphs"] == 8
    assert any("Liko" in rule for rule in second["immutable_rules"])

    rolled_back = store.rollback()
    assert rolled_back["published"]["tone"] == "calm"


def test_first_version_cannot_rollback(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(liko_style, "STYLE_FILE", tmp_path / "style.json")
    store = liko_style.LikoStyleStore()
    store.publish()
    with pytest.raises(ValueError, match="可回滚"):
        store.rollback()

