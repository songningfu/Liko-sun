import json
from pathlib import Path
from types import SimpleNamespace

from core import duplicates


def test_compare_text_handles_exact_near_and_unrelated_content() -> None:
    base = "深圳大学丽湖校区图书馆普通阅览室开放至晚上十点三十分，节假日安排以最新公告为准。" * 6
    exact = duplicates.compare_text(base, base)
    near = duplicates.compare_text(base, base + "如有调整请留意图书馆官方通知。")
    unrelated = duplicates.compare_text(base, "校园卡补办请携带有效证件前往服务大厅办理。" * 6)

    assert exact["exact"] is True
    assert near["exact"] is False and near["score"] >= 0.72
    assert unrelated["score"] < 0.72


def test_analyze_documents_compares_against_curated_baseline(tmp_path: Path, monkeypatch) -> None:
    baseline = tmp_path / "chunks.jsonl"
    baseline.write_text(json.dumps({
        "document_id": "SZU-00001",
        "chunk_index": 1,
        "title": "丽湖图书馆开放时间",
        "content": "丽湖校区图书馆普通阅览室开放至晚上十点三十分。" * 8,
        "source_url": "https://lib.szu.edu.cn/opening",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(duplicates, "REPORT_FILE", tmp_path / "report.json")

    docs = [{
        "source_id": "CRAWL-1",
        "doc_id": "FAC-1",
        "title": "开放安排",
        "url": "https://lib.szu.edu.cn/opening",
        "status": "可导入",
        "chunks": 1,
        "_doc": SimpleNamespace(clean_body="丽湖校区图书馆普通阅览室开放至晚上十点三十分。" * 8),
    }]
    report = duplicates.analyze_documents(docs, baseline)

    assert docs[0]["status"] == "重复排除"
    assert docs[0]["duplicate_check"]["matched_id"] == "SZU-00001"
    assert report["summary"]["exact"] == 1
