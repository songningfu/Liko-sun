"""流水线编排：抓取 → 变更检测 → 清洗切块 → 导出 FastGPT 包。

run_pipeline() 是同步生成器，逐步产出事件 dict；server 层负责放进队列做 SSE。
"""
from __future__ import annotations

import hashlib
import json
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from . import internal_fetch, settings
from .fetch import fetch_source
from .enrichment import KnowledgeEnricher
from .process import CLEAN_ROOT, DATA_ROOT, EXPORT_ROOT, PAGES_ROOT, load_rag_module, process_pages, write_page_markdown
from .registry import Registry, now_iso

Event = dict


def _event(step: str, message: str, **extra) -> Event:
    return {"step": step, "message": message, "time": now_iso(), **extra}


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch_all(registry: Registry, only_ids: set[str] | None = None,
              emit: Callable[[Event], None] | None = None) -> list[Path]:
    """抓取所有启用来源，返回本次有内容更新的页面文件列表。"""
    emit = emit or (lambda e: None)
    delay = settings.load()["request_delay_seconds"]
    changed_files: list[Path] = []
    sources = [s for s in registry.list() if s["enabled"] and (only_ids is None or s["id"] in only_ids)]
    emit(_event("fetch", f"开始抓取 {len(sources)} 个启用来源"))

    for index, src in enumerate(sources):
        label = src["title"] or src["url"]
        emit(_event("fetch", f"[{index + 1}/{len(sources)}] {label}", source_id=src["id"]))
        if src["source_type"] == "internal":
            result = internal_fetch.fetch_internal(src["url"])
        else:
            result = fetch_source(src)

        if not result.ok:
            status = "auth_expired" if result.meta.get("auth_expired") else "failed"
            registry.update(src["id"], last_status="failed", last_error=result.error,
                            last_fetched_at=now_iso())
            emit(_event("fetch", f"✗ {label}：{result.error}", source_id=src["id"], level="error",
                        auth_expired=status == "auth_expired"))
        else:
            body = result.body_md
            new_hash = _content_hash(body)
            title = result.title or src["title"] or src["url"]
            meta = {
                "source_id": src["id"],
                "site_id": "FAC",
                "site_name": "知识工厂",
                "title": title,
                "url": result.final_url or src["url"],
                "category": src["category"],
                "department": src["department"],
                "campus": src["campus"],
                "source_type": src["source_type"],
                "trust_level": src.get("trust_level", "authoritative" if src["official"] else "community"),
                "official": "true" if src["official"] else "false",
                "author": result.author,
                "published_at": result.published_at,
                "collected_at": now_iso(),
            }
            page_path = PAGES_ROOT / f"{src['id']}.md"
            if new_hash == src["last_hash"] and page_path.exists():
                registry.update(src["id"], last_status="unchanged", last_error="",
                                last_fetched_at=now_iso(), fetch_count=src["fetch_count"] + 1)
                emit(_event("fetch", f"= {label}：内容无变化，跳过", source_id=src["id"]))
            else:
                write_page_markdown(page_path, meta=meta, body=body)
                registry.update(src["id"], title=src["title"] or title, last_status="changed",
                                last_error="", last_hash=new_hash, last_fetched_at=now_iso(),
                                fetch_count=src["fetch_count"] + 1)
                changed_files.append(page_path)
                emit(_event("fetch", f"✓ {label}：{len(body)} 字符，已更新", source_id=src["id"]))
        if index < len(sources) - 1:
            time.sleep(delay)
    return changed_files


def export_fastgpt(docs: list[dict], emit: Callable[[Event], None]) -> Path | None:
    """把可导入/待核验文档按主题合并为 FastGPT Markdown 包并压缩。"""
    importable = [d for d in docs if d.get("status") in {"可导入", "待核验"} and d.get("_doc")]
    if not importable:
        emit(_event("export", "没有可导出的文档，跳过打包"))
        return None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = EXPORT_ROOT / f"fastgpt-{stamp}"
    official_dir = out_dir / "官方来源"
    unofficial_dir = out_dir / "非官方待核验"

    groups: dict[tuple[bool, str], list[dict]] = {}
    for d in importable:
        groups.setdefault((d["official"], d["topic"]), []).append(d)

    manifest_rows = []
    for (is_official, topic), items in sorted(groups.items(), key=lambda kv: kv[0][1]):
        target_dir = official_dir if is_official else unofficial_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        lines = [f"# 深圳大学知识库：{topic}" if is_official else f"# 待核验资料（非官方）：{topic}", ""]
        if is_official:
            lines.append("> 以下资料来自深圳大学官方页面。回答时应显示原文链接，并优先使用年份更新、校区和人群匹配的资料。")
        else:
            lines.append("> 以下资料来自公众号等非官方渠道，未经人工核验，不得作为校园事实依据。")
        lines.append("")
        for d in items:
            doc = d["_doc"]
            lines.append("---")
            lines.append(f"## {doc.title}")
            if d.get("department"):
                lines.append(f"- 来源部门：{d['department']}")
            if d.get("author"):
                lines.append(f"- 发布账号：{d['author']}")
            if d.get("published_at"):
                lines.append(f"- 发布时间：{d['published_at']}")
            lines.append(f"- 适用校区：{doc.campus}")
            lines.append(f"- 适用人群：{doc.audience}")
            lines.append(f"- 时效标记：{doc.freshness}")
            lines.append(f"- 原文链接：{doc.url}")
            lines.append(f"- 文档编号：{d['doc_id']}")
            if not is_official:
                lines.append("- 核验状态：待人工核验")
            lines.append("")
            for chunk in doc.chunks:
                lines.append(chunk["content"].strip())
                lines.append("")
            manifest_rows.append({
                "doc_id": d["doc_id"], "source_id": d["source_id"], "title": d["title"],
                "topic": topic, "official": "是" if is_official else "否",
                "status": d["status"], "chunks": d["chunks"], "url": d["url"],
                "risk": d.get("risk", ""),
            })
        safe_topic = "".join(c if c.isalnum() or c in "-_" else "_" for c in topic)
        (target_dir / f"{safe_topic}.md").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    import csv
    manifest_path = out_dir / "manifest.csv"
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(manifest_rows[0].keys()))
        writer.writeheader()
        writer.writerows(manifest_rows)

    zip_path = EXPORT_ROOT / f"{out_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(out_dir.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(out_dir))
    emit(_event("export", f"已生成 FastGPT 导入包：{zip_path.name}（{len(importable)} 篇文档）",
                path=str(zip_path)))
    return zip_path


def enrich_documents(docs: list[dict], emit: Callable[[Event], None]) -> None:
    """按设置为文档生成辅助结构；失败只告警，不影响原文加工与导出。"""
    if not settings.load().get("model_enrichment_enabled"):
        return
    enricher = KnowledgeEnricher()
    if not enricher.configured:
        emit(_event("enrich", "已开启模型辅助整理，但服务器尚未配置模型密钥", level="error"))
        return
    candidates = [d for d in docs if d.get("_doc") and d.get("status") in {"可导入", "待核验"}]
    emit(_event("enrich", f"开始辅助整理 {len(candidates)} 篇资料（相同原文自动使用缓存）"))
    for item in candidates:
        try:
            result = enricher.enrich(title=item["title"], text=item["_doc"].clean_body)
            item["model_enrichment"] = {
                key: result.get(key)
                for key in ("summary", "topic", "campus", "audience", "department",
                            "document_type", "published_at", "valid_until", "risk_level", "warnings")
            }
        except Exception as exc:  # noqa: BLE001 - 单篇模型失败不能破坏流水线
            emit(_event("enrich", f"△ {item['title']}：模型整理失败（{str(exc)[:100]}）", level="error"))


def process_and_export_pages(emit: Callable[[Event], None]) -> tuple[list[dict], Path | None]:
    """对 data/pages/ 全量页面做清洗、切块并导出 FastGPT 包。供流水线与爬虫共用。"""
    rag = load_rag_module()
    all_pages = sorted(PAGES_ROOT.glob("*.md"))
    if not all_pages:
        emit(_event("process", "没有任何已抓取页面，跳过清洗与导出"))
        return [], None
    emit(_event("process", f"开始清洗 {len(all_pages)} 篇页面"))
    docs = process_pages(rag, all_pages)
    enrich_documents(docs, emit)
    ok = sum(1 for d in docs if d["status"] == "可导入")
    review = sum(1 for d in docs if d["status"] == "待核验")
    dropped = len(docs) - ok - review
    emit(_event("process", f"清洗完成：可导入 {ok} 篇，待核验 {review} 篇，技术/重复排除 {dropped} 篇"))
    zip_path = export_fastgpt(docs, emit)
    for d in docs:
        d.pop("_doc", None)
    return docs, zip_path


def run_crawl(max_pages: int = 60, max_depth: int = 3, delay: float = 2.0) -> Iterator[Event]:
    """全站搜查 + 自动清洗导出。"""
    from .crawler import crawl_sites
    started = datetime.now()
    yield _event("start", "全站搜查启动")
    try:
        yield from crawl_sites(max_pages=max_pages, max_depth=max_depth, delay=delay)
        export_events: list[Event] = []
        docs, zip_path = process_and_export_pages(emit=export_events.append)
        yield from export_events
        _write_run_log(started, docs, zip_path)
        yield _event("done", "全站搜查完成")
    except Exception as exc:  # noqa: BLE001
        yield _event("error", f"全站搜查异常中断: {exc}", level="error")


def run_pipeline(registry: Registry, only_ids: set[str] | None = None) -> Iterator[Event]:
    """完整流水线。产出事件流；最后一条事件 step=done。"""
    started = datetime.now()
    yield _event("start", "流水线启动")
    try:
        # 1. 抓取（fetch_all 保持同步实现，事件经列表转发，顺序不变）
        events: list[Event] = []
        changed_files = fetch_all(registry, only_ids=only_ids, emit=events.append)
        yield from events

        if not changed_files:
            yield _event("process", "本次没有内容更新，跳过清洗与导出")
        else:
            # 2. 清洗本次更新的文档（用于逐条汇报）
            yield _event("process", f"开始清洗 {len(changed_files)} 篇更新文档")
            rag = load_rag_module()
            docs = process_pages(rag, changed_files)
            enrich_documents(docs, lambda event: None)
            for d in docs:
                icon = {"可导入": "✓", "待核验": "◐"}.get(d["status"], "✗")
                yield _event("process", f"{icon} {d['title']}：{d['status']}（{d.get('chunks', 0)} 块）",
                             doc={k: v for k, v in d.items() if not k.startswith("_")},
                             level="error" if d["status"].endswith("排除") else "info")

            # 3. 导出包含全部 pages 的完整 FastGPT 包，保证增量运行后导出依然全量
            yield _event("export", "开始生成 FastGPT 导入包")
            all_docs = process_pages(rag, sorted(PAGES_ROOT.glob("*.md")))
            export_events: list[Event] = []
            enrich_documents(all_docs, emit=export_events.append)
            zip_path = export_fastgpt(all_docs, emit=export_events.append)
            yield from export_events
            for d in all_docs:
                d.pop("_doc", None)
            _write_run_log(started, all_docs, zip_path)
        yield _event("done", "流水线完成")
    except Exception as exc:  # noqa: BLE001 - 顶层兜底，错误必须可见
        yield _event("error", f"流水线异常中断: {exc}", level="error")


def _write_run_log(started: datetime, docs: list[dict], zip_path: Path | None) -> None:
    log_dir = DATA_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "documents": docs,
        "export_zip": str(zip_path) if zip_path else "",
    }
    (log_dir / f"run-{started.strftime('%Y%m%d-%H%M%S')}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
