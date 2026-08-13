from pathlib import Path

import pytest

from core import manual


def test_manual_submission_preserves_original_and_requires_review(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(manual, "INDEX_FILE", tmp_path / "manual.json")
    monkeypatch.setattr(manual, "PAGES_ROOT", tmp_path / "pages")
    store = manual.ManualSubmissionStore()

    item = store.add(
        title="辅导员通知",
        text="这是一份由老师通过文字形式提供的通知原文，内容需要保留并等待人工审核。",
        source_label="年级群",
        trust_level="provided",
    )

    assert item["id"] == "TXT-00001"
    assert item["review_status"] == "pending"
    assert "需要保留并等待人工审核" in (tmp_path / "pages" / "TXT-00001.md").read_text(encoding="utf-8")
    assert store.set_review(item["id"], "approved")["review_status"] == "approved"


def test_authoritative_manual_text_requires_official_https_url(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(manual, "INDEX_FILE", tmp_path / "manual.json")
    monkeypatch.setattr(manual, "PAGES_ROOT", tmp_path / "pages")
    with pytest.raises(ValueError, match="官方 HTTPS"):
        manual.ManualSubmissionStore().add(
            title="没有链接的权威资料",
            text="长度足够但没有提供深大官方网站链接，所以不能直接被标成官方权威资料。",
            trust_level="authoritative",
        )

