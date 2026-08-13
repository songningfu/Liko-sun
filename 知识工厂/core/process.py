"""清洗与切块：直接复用 知识库/RAG加工/process_rag.py 的纯函数，不重复实现。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

FACTORY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = FACTORY_ROOT.parent
RAG_SCRIPT = REPO_ROOT / "知识库" / "RAG加工" / "process_rag.py"

DATA_ROOT = FACTORY_ROOT / "data"
PAGES_ROOT = DATA_ROOT / "pages"      # 抓取原始 Markdown（含 frontmatter）
CLEAN_ROOT = DATA_ROOT / "clean"      # 清洗后文档
EXPORT_ROOT = DATA_ROOT / "exports"   # FastGPT 导出包


def load_rag_module():
    """以文件路径加载 process_rag.py（目录名为中文，不能用常规 import）。"""
    spec = importlib.util.spec_from_file_location("liko_process_rag", RAG_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 RAG 加工脚本: {RAG_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("liko_process_rag", module)
    spec.loader.exec_module(module)
    return module


def write_page_markdown(path: Path, *, meta: dict[str, str], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for key, value in meta.items():
        safe = str(value).replace("\n", " ").strip()
        lines.append(f"{key}: {safe}")
    lines.append("---")
    lines.append("")
    lines.append(body.strip())
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def process_pages(rag, page_files: list[Path]) -> list[dict]:
    """清洗 + 标注 + 切块一批页面，返回文档结果列表。

    复用 process_rag 的 clean_body / classify_* / chunk_markdown / canonical_hash。
    官方通道与非官方（公众号）通道分开判定：非官方文档一律标记「待核验」，
    不与官方事实混合导出。
    """
    docs = []
    line_freq = rag.document_line_frequencies(page_files)
    seen_hashes: dict[str, str] = {}  # hash -> doc_id，本批内去重

    for index, path in enumerate(page_files, 1):
        text = path.read_text(encoding="utf-8", errors="replace")
        meta, body = rag.parse_frontmatter(text)
        source_id = meta.get("source_id", path.stem)
        doc = rag.Document(
            source_path=path,
            site_id=meta.get("site_id", "FAC"),
            site_name=meta.get("site_name", "知识工厂"),
            category=meta.get("category", ""),
            title=meta.get("title", path.stem),
            url=meta.get("url", ""),
            collected_at=meta.get("collected_at", ""),
            raw_body=body,
        )
        doc.clean_body = rag.clean_body(doc, line_freq, len(page_files))
        plain = rag.plain_text(doc.clean_body)
        text_for_classification = f"{doc.title}\n{plain}"
        doc.topic = rag.classify_topic(text_for_classification, doc.category or "其他")
        doc.campus = rag.classify_campus(text_for_classification)
        doc.audience = rag.classify_audience(text_for_classification)
        doc.document_type = rag.classify_type(doc.title, doc.clean_body)
        doc.freshness, doc.latest_year = rag.assess_freshness(doc.title, doc.clean_body)
        doc.content_hash = rag.canonical_hash(doc.clean_body)
        is_official = meta.get("official", "true") == "true"
        trust_level = meta.get("trust_level", "authoritative" if is_official else "community")

        doc_id = f"FAC-{index:05d}"
        result = {
            "doc_id": doc_id,
            "source_id": source_id,
            "title": doc.title,
            "url": doc.url,
            "official": is_official,
            "trust_level": trust_level,
            "topic": doc.topic,
            "campus": doc.campus,
            "audience": doc.audience,
            "department": meta.get("department", ""),
            "author": meta.get("author", ""),
            "published_at": meta.get("published_at", ""),
            "document_type": doc.document_type,
            "freshness": doc.freshness,
            "content_hash": doc.content_hash,
            "char_count": len(plain),
        }

        if len(plain) < 60:
            result.update(status="技术排除", reason="清洗后有效正文过短", chunks=0)
        elif doc.content_hash in seen_hashes:
            result.update(status="重复排除", reason=f"与 {seen_hashes[doc.content_hash]} 正文相同", chunks=0)
        elif not is_official:
            doc.chunks = rag.chunk_markdown(doc)
            seen_hashes[doc.content_hash] = doc_id
            result.update(status="待核验", reason="非官方来源（公众号），人工核验后才能进入知识库",
                          chunks=len(doc.chunks))
        else:
            doc.chunks = rag.chunk_markdown(doc)
            seen_hashes[doc.content_hash] = doc_id
            result.update(status="可导入", reason="官方来源，已完成清洗", chunks=len(doc.chunks))

        # 高风险字段沿用项目规则，只打标签不拦截
        if any(term in text_for_classification for term in rag.HIGH_RISK_TERMS):
            result["risk"] = "需注意时效/费用/联系方式等字段"
        else:
            result["risk"] = "普通"

        if result["status"] in {"可导入", "待核验"}:
            clean_path = CLEAN_ROOT / f"{source_id}.md"
            header = [
                "---",
                f"doc_id: {doc_id}",
                f"source_id: {source_id}",
                f"title: {doc.title}",
                f"url: {doc.url}",
                f"official: {'true' if is_official else 'false'}",
                f"trust_level: {trust_level}",
                f"topic: {doc.topic}",
                f"campus: {doc.campus}",
                f"audience: {doc.audience}",
                f"document_type: {doc.document_type}",
                f"freshness: {doc.freshness}",
                f"status: {result['status']}",
                "---",
                "",
            ]
            clean_path.parent.mkdir(parents=True, exist_ok=True)
            clean_path.write_text("\n".join(header) + doc.clean_body.strip() + "\n", encoding="utf-8")
            result["clean_path"] = str(clean_path.relative_to(FACTORY_ROOT))
            result["_doc"] = doc  # 导出阶段使用

        docs.append(result)
    return docs
