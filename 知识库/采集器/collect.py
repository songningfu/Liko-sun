from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator


ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_FILE = ROOT / "sources.json"
DEFAULT_OUTPUT_DIR = ROOT / "output"
USER_AGENT = "LikoCampusKnowledgeCollector/0.1 (+public academic sources; contact: project maintainer)"
ALLOWED_DOMAIN = "szu.edu.cn"


@dataclass
class RobotsDecision:
    allowed: bool
    status: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集公开的深圳大学官方网页并保存为 Markdown。")
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCE_FILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--source-id", action="append", help="只采集指定 source_id，可重复传入。")
    parser.add_argument("--limit", type=int, default=0, help="最多采集多少条；0 表示不限制。")
    parser.add_argument("--delay", type=float, default=2.0, help="相邻请求的最短间隔秒数。")
    parser.add_argument("--dry-run", action="store_true", help="只检查配置和域名，不访问网页。")
    return parser.parse_args()


def load_sources(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("sources.json 顶层必须是数组。")

    seen: set[str] = set()
    required = {"source_id", "title", "url", "department", "category", "campus", "enabled"}
    for source in data:
        missing = sorted(required - source.keys())
        if missing:
            raise ValueError(f"来源缺少字段：{missing}")
        source_id = str(source["source_id"])
        if source_id in seen:
            raise ValueError(f"source_id 重复：{source_id}")
        seen.add(source_id)
        validate_official_url(str(source["url"]))
    return data


def validate_official_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise ValueError(f"只允许 HTTPS：{url}")
    if host != ALLOWED_DOMAIN and not host.endswith(f".{ALLOWED_DOMAIN}"):
        raise ValueError(f"只允许深圳大学官方域名：{url}")


def robots_decision(url: str) -> RobotsDecision:
    parsed = urllib.parse.urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    request = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read(512_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 410}:
            return RobotsDecision(True, f"robots.txt {exc.code}，按无规则处理")
        return RobotsDecision(False, f"robots.txt HTTP {exc.code}，为安全起见跳过")
    except (urllib.error.URLError, TimeoutError) as exc:
        return RobotsDecision(False, f"robots.txt 无法确认：{exc}")

    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(raw.splitlines())
    allowed = parser.can_fetch(USER_AGENT, url)
    return RobotsDecision(allowed, "robots.txt 允许" if allowed else "robots.txt 禁止")


def safe_file_name(source_id: str, title: str) -> str:
    clean = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", title).strip(" ._")
    clean = re.sub(r"\s+", "_", clean)[:72] or "untitled"
    return f"{source_id}_{clean}.md"


def get_markdown(result: Any) -> str:
    markdown = getattr(result, "markdown", "")
    if isinstance(markdown, str):
        return markdown
    fit = getattr(markdown, "fit_markdown", None)
    raw = getattr(markdown, "raw_markdown", None)
    return (fit or raw or "").strip()


def clean_markdown(source: dict[str, Any], markdown: str) -> str:
    """Trim common SZU site navigation and footer blocks without rewriting facts."""
    text = markdown.replace("\r\n", "\n").strip()
    title = str(source["title"]).strip()

    start_marker = str(source.get("start_marker", "")).strip()
    if start_marker:
        start = text.find(start_marker)
        if start < 0:
            raise RuntimeError(f"找不到正文起点标记：{start_marker}")
        text = text[start:]

    end_marker = str(source.get("end_marker", "")).strip()
    if end_marker:
        end = text.find(end_marker)
        if end < 0:
            raise RuntimeError(f"找不到正文终点标记：{end_marker}")
        text = text[:end]

    if not start_marker:
        heading_patterns = [
            f"# {title}",
            f"## {title}",
            f"### {title}",
        ]
        starts = [text.find(pattern) for pattern in heading_patterns if text.find(pattern) >= 0]
        if starts:
            text = text[min(starts) :]

    footer_patterns = [
        "\n上一篇：",
        "\n下一篇：",
        "\n[上一篇",
        "\n[下一篇",
        "\n#### 友情链接",
        "\n#### 联系我们",
        "\n版权所有",
        "\n地址：",
    ]
    ends = [text.find(pattern) for pattern in footer_patterns if text.find(pattern) >= 0]
    if ends:
        text = text[: min(ends)]

    # Decorative images and empty JavaScript links add noise to retrieval.
    kept_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.fullmatch(r"!?\[[^\]]*\]\((?:javascript:;|https?://[^)]+\.(?:png|jpg|jpeg|gif|svg)(?:\?[^)]*)?)\)", stripped, re.I):
            continue
        kept_lines.append(line.rstrip())

    text = "\n".join(kept_lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].startswith("#") and lines[2].startswith("#"):
        first_heading = lines[0].lstrip("# ").strip()
        repeated_heading = lines[2].lstrip("# ").strip()
        if first_heading == repeated_heading and "](" in lines[1]:
            text = "\n".join(lines[2:]).strip()
    return text


def frontmatter(source: dict[str, Any], collected_at: str, content_hash: str) -> str:
    fields = {
        "source_id": source["source_id"],
        "title": source["title"],
        "url": source["url"],
        "department": source["department"],
        "category": source["category"],
        "campus": source["campus"],
        "published_at": source.get("published_at", ""),
        "effective_from": source.get("effective_from", ""),
        "effective_to": source.get("effective_to", ""),
        "last_verified_at": source.get("last_verified_at", ""),
        "review_status": source.get("review_status", ""),
        "collected_at": collected_at,
        "sha256": content_hash,
    }
    lines = ["---"]
    for key, value in fields.items():
        encoded = json.dumps(str(value), ensure_ascii=False)
        lines.append(f"{key}: {encoded}")
    lines.extend(["---", ""])
    return "\n".join(lines)


def write_registry(rows: list[dict[str, Any]], output_dir: Path) -> None:
    fields = [
        "source_id",
        "category",
        "title",
        "url",
        "department",
        "campus",
        "enabled",
        "review_status",
        "collection_status",
        "robots_status",
        "collected_at",
        "content_chars",
        "output_file",
        "error",
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "资料登记.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


async def collect(args: argparse.Namespace) -> int:
    sources = load_sources(args.sources)
    selected_ids = set(args.source_id or [])
    if selected_ids:
        known_ids = {str(source["source_id"]) for source in sources}
        missing = sorted(selected_ids - known_ids)
        if missing:
            raise ValueError(f"找不到 source_id：{', '.join(missing)}")
        sources = [source for source in sources if source["source_id"] in selected_ids]

    if args.limit > 0:
        sources = sources[: args.limit]

    rows: list[dict[str, Any]] = []
    to_collect: list[tuple[dict[str, Any], RobotsDecision]] = []
    for source in sources:
        row = dict(source)
        row.update(
            collection_status="待采集",
            robots_status="未检查",
            collected_at="",
            content_chars="",
            output_file="",
            error="",
        )
        if not source.get("enabled", False):
            row["collection_status"] = "已跳过（禁用）"
            rows.append(row)
            continue
        if args.dry_run:
            row["collection_status"] = "配置有效（dry-run）"
            rows.append(row)
            continue

        decision = await asyncio.to_thread(robots_decision, str(source["url"]))
        row["robots_status"] = decision.status
        if not decision.allowed:
            row["collection_status"] = "已跳过（robots 未允许）"
            rows.append(row)
            continue
        rows.append(row)
        to_collect.append((source, decision))

    if args.dry_run or not to_collect:
        write_registry(rows, args.output)
        return 0

    browser_config = BrowserConfig(headless=True, verbose=False)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.4, threshold_type="fixed")
        ),
        page_timeout=30_000,
        wait_until="domcontentloaded",
        verbose=False,
    )

    raw_dir = args.output / "原始网页"
    raw_dir.mkdir(parents=True, exist_ok=True)
    row_by_id = {str(row["source_id"]): row for row in rows}

    async with AsyncWebCrawler(config=browser_config) as crawler:
        for index, (source, _) in enumerate(to_collect):
            if index:
                await asyncio.sleep(max(args.delay, 0.0))
            row = row_by_id[str(source["source_id"])]
            try:
                result = await crawler.arun(url=str(source["url"]), config=run_config)
                if not getattr(result, "success", False):
                    raise RuntimeError(str(getattr(result, "error_message", "采集失败")))
                markdown = clean_markdown(source, get_markdown(result))
                if len(markdown) < 80:
                    raise RuntimeError("正文过短，需要人工检查页面结构")

                collected_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
                content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
                file_name = safe_file_name(str(source["source_id"]), str(source["title"]))
                target = raw_dir / file_name
                target.write_text(
                    frontmatter(source, collected_at, content_hash) + markdown.rstrip() + "\n",
                    encoding="utf-8",
                )
                success_status = (
                    "采集成功，仅作入口监测"
                    if source.get("source_type") == "official_landing_page"
                    else "采集成功，待人工核验"
                )
                row.update(
                    collection_status=success_status,
                    collected_at=collected_at,
                    content_chars=str(len(markdown)),
                    output_file=str(target.relative_to(args.output)),
                )
            except Exception as exc:  # noqa: BLE001 - errors are recorded per source
                row["collection_status"] = "采集失败"
                row["error"] = str(exc)[:500]

    write_registry(rows, args.output)
    return 0 if all(row["collection_status"] != "采集失败" for row in rows) else 2


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(collect(args))
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
