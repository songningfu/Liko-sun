"""知识工厂去重：同时检查首轮人工整理知识与本轮自动采集内容。"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

FACTORY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = FACTORY_ROOT.parent
BASELINE_CHUNKS = REPO_ROOT / "知识库" / "RAG加工" / "output" / "chunks.jsonl"
REPORT_FILE = FACTORY_ROOT / "data" / "duplicate_report.json"

_NORMALIZE_RE = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")


def normalize_text(value: str) -> str:
    """保留语义字符并去掉排版差异，避免 Markdown 格式影响比较。"""
    value = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", value or "")
    return _NORMALIZE_RE.sub("", value).lower()


def _fingerprint(value: str, size: int = 5) -> set[str]:
    normalized = normalize_text(value)
    if len(normalized) <= size:
        return {normalized} if normalized else set()
    return {normalized[index:index + size] for index in range(len(normalized) - size + 1)}


def _prepare(value: str) -> tuple[str, set[str]]:
    normalized = normalize_text(value)
    return normalized, _fingerprint(normalized)


def _compare_prepared(left: tuple[str, set[str]], right: tuple[str, set[str]]) -> dict:
    left_normalized, left_parts = left
    right_normalized, right_parts = right
    if not left_normalized or not right_normalized:
        return {"exact": False, "score": 0.0, "jaccard": 0.0, "containment": 0.0}
    if hashlib.sha256(left_normalized.encode()).digest() == hashlib.sha256(right_normalized.encode()).digest():
        return {"exact": True, "score": 1.0, "jaccard": 1.0, "containment": 1.0}
    common = len(left_parts & right_parts)
    if common < 12:
        return {"exact": False, "score": 0.0, "jaccard": 0.0, "containment": 0.0}
    union = len(left_parts | right_parts)
    smaller = min(len(left_parts), len(right_parts))
    jaccard = common / union if union else 0.0
    containment = common / smaller if smaller else 0.0
    score = max(jaccard, containment * 0.9)
    return {
        "exact": False,
        "score": round(score, 4),
        "jaccard": round(jaccard, 4),
        "containment": round(containment, 4),
    }


def compare_text(left: str, right: str) -> dict:
    """返回精确/相似判断；包含度能识别“同一原文被不同方式切块”的情况。"""
    return _compare_prepared(_prepare(left), _prepare(right))


def load_curated_references(path: Path = BASELINE_CHUNKS) -> list[dict]:
    """把首轮分块重新按文档合并，作为线上已有知识的去重基线。"""
    if not path.exists():
        return []
    groups: dict[str, list[dict]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(row, dict) and row.get("document_id"):
            groups[str(row["document_id"])].append(row)

    result = []
    for document_id, rows in groups.items():
        rows.sort(key=lambda item: int(item.get("chunk_index") or 0))
        first = rows[0]
        result.append({
            "id": document_id,
            "origin": "curated",
            "title": first.get("title") or document_id,
            "url": first.get("source_url") or "",
            "text": "\n".join(str(item.get("content") or "") for item in rows),
        })
    return result


def _canonical_url(value: str) -> str:
    return (value or "").strip().lower().rstrip("/")


def analyze_documents(docs: list[dict], baseline_path: Path = BASELINE_CHUNKS) -> dict:
    """给加工结果附加去重结论，并把精确重复排除、相似内容转入复核。"""
    references = load_curated_references(baseline_path)
    for reference in references:
        reference["_prepared"] = _prepare(reference["text"])
    rows: list[dict] = []
    summary = {"total": 0, "unique": 0, "near": 0, "exact": 0, "baseline_documents": len(references)}

    for item in docs:
        doc = item.get("_doc")
        if doc is None or item.get("status") == "技术排除":
            item["duplicate_check"] = {"status": "skipped", "score": 0.0}
            continue
        summary["total"] += 1
        candidate_text = doc.clean_body
        candidate_prepared = _prepare(candidate_text)
        candidate_url = _canonical_url(item.get("url", ""))
        best: dict | None = None
        for reference in references:
            result = _compare_prepared(candidate_prepared, reference["_prepared"])
            same_url = bool(candidate_url and candidate_url == _canonical_url(reference.get("url", "")))
            if same_url:
                result = {**result, "exact": result["exact"], "score": max(result["score"], 0.99)}
            if best is None or result["score"] > best["score"]:
                best = {**result, "reference": reference, "same_url": same_url}

        if best and (best["exact"] or best["same_url"] or best["score"] >= 0.72):
            reference = best["reference"]
            # 相同 URL 说明已有一版知识，但正文可能已经更新；只有正文指纹相同才算精确重复。
            exact = bool(best["exact"])
            status = "exact_duplicate" if exact else "near_duplicate"
            check = {
                "status": status,
                "score": round(float(best["score"]), 4),
                "matched_id": reference["id"],
                "matched_title": reference["title"],
                "matched_url": reference.get("url", ""),
                "matched_origin": reference["origin"],
            }
            item["duplicate_check"] = check
            if exact:
                item.update(status="重复排除", reason=f"与已有知识《{reference['title']}》相同", chunks=0)
                summary["exact"] += 1
            else:
                item.update(status="待核验", reason=f"与已有知识《{reference['title']}》相似，暂不自动发布")
                summary["near"] += 1
            rows.append({"source_id": item.get("source_id"), "title": item.get("title"), **check})
        else:
            item["duplicate_check"] = {"status": "unique", "score": round(float(best["score"]), 4) if best else 0.0}
            summary["unique"] += 1

        # 只有已保留的工厂文档才成为后续比较对象，避免重复链指向已排除页面。
        if item.get("status") != "重复排除":
            references.append({
                "id": item.get("source_id") or item.get("doc_id"),
                "origin": "factory",
                "title": item.get("title") or "未命名资料",
                "url": item.get("url") or "",
                "text": candidate_text,
                "_prepared": candidate_prepared,
            })

    report = {"summary": summary, "rows": rows}
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
