from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


ROOT = Path(__file__).resolve().parent
DEFAULT_SITES = ROOT / "sites.json"
DEFAULT_OUTPUT = ROOT / "output_full"
USER_AGENT = "LikoCampusKnowledgeCollector/0.2 (+public academic sources; contact: project maintainer)"
ALLOWED_ROOT = "szu.edu.cn"

HTML_EXTENSIONS = {"", ".htm", ".html", ".shtml", ".asp", ".aspx", ".php", ".jsp", ".vsb"}
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv"}
SKIP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".mp3", ".wav", ".mp4", ".avi", ".mov", ".webm",
    ".css", ".js", ".json", ".xml", ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".exe", ".apk",
}
SKIP_PATH_PARTS = (
    "/login", "/logout", "/signin", "/cas/", "/auth/", "/admin/",
    "/user/", "/api/", "/search", "/sousuo", "/oa/", "/webvpn/",
)
TRACKING_QUERY_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "from"}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="分批采集公开的深圳大学官方网页，可断点续跑。")
    parser.add_argument("--sites", type=Path, default=DEFAULT_SITES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--site-id", action="append", help="只处理指定站点，可重复传入。")
    parser.add_argument("--max-pages", type=int, default=120, help="本次最多尝试的网页数量。")
    parser.add_argument("--max-depth", type=int, default=4, help="从各站首页向内发现的最大深度。")
    parser.add_argument("--delay", type=float, default=2.0, help="相邻页面请求间隔秒数。")
    parser.add_argument("--retry-failed", action="store_true", help="把以前失败的页面重新放回队列。")
    parser.add_argument("--status", action="store_true", help="只显示当前进度，不访问网页。")
    return parser.parse_args()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def validate_site(site: dict[str, Any]) -> None:
    required = {"site_id", "name", "seed_url", "category"}
    missing = required - site.keys()
    if missing:
        raise ValueError(f"站点配置缺少字段：{sorted(missing)}")
    parsed = urlparse(str(site["seed_url"]))
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise ValueError(f"只允许 HTTPS 站点：{site['seed_url']}")
    if host != ALLOWED_ROOT and not host.endswith(f".{ALLOWED_ROOT}"):
        raise ValueError(f"不是深圳大学官方域名：{site['seed_url']}")


def load_sites(path: Path, selected: set[str]) -> list[dict[str, Any]]:
    sites = read_json(path, [])
    if not isinstance(sites, list):
        raise ValueError("sites.json 顶层必须是数组。")
    seen: set[str] = set()
    for site in sites:
        validate_site(site)
        site_id = str(site["site_id"])
        if site_id in seen:
            raise ValueError(f"site_id 重复：{site_id}")
        seen.add(site_id)
    if selected:
        missing = sorted(selected - seen)
        if missing:
            raise ValueError(f"找不到 site_id：{', '.join(missing)}")
        sites = [site for site in sites if site["site_id"] in selected]
    return sites


def path_extension(parsed: Any) -> str:
    suffix = Path(parsed.path).suffix.lower()
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    hinted = str(query.get("e", "")).lower()
    if hinted.startswith(".") and hinted in DOCUMENT_EXTENSIONS:
        return hinted
    return suffix


def normalize_url(raw_url: str, base_url: str, allowed_host: str) -> tuple[str | None, str]:
    if not raw_url or raw_url.startswith(("javascript:", "mailto:", "tel:", "data:")):
        return None, "unsupported_scheme"
    absolute = urljoin(base_url, raw_url.strip())
    parsed = urlparse(absolute)
    host = (parsed.hostname or "").lower()
    if host != allowed_host:
        return None, "outside_site"
    if parsed.scheme not in {"http", "https"}:
        return None, "unsupported_scheme"

    lowered_path = parsed.path.lower()
    if any(part in lowered_path for part in SKIP_PATH_PARTS):
        return None, "blocked_path"

    extension = path_extension(parsed)
    if extension in DOCUMENT_EXTENSIONS:
        clean_document = urlunparse(("https", parsed.netloc.lower(), parsed.path, "", parsed.query, ""))
        return clean_document, "document"
    if extension in SKIP_EXTENSIONS or extension not in HTML_EXTENSIONS:
        return None, "non_html"

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key.lower() not in TRACKING_QUERY_KEYS and not key.lower().startswith("utm_")
    ]
    # Avoid infinite calendars/search combinations while preserving stable article identifiers.
    query_pairs = sorted(query_pairs)[:6]
    query = urlencode(query_pairs, doseq=True)
    normalized_path = re.sub(r"/{2,}", "/", parsed.path or "/")
    normalized = urlunparse(("https", parsed.netloc.lower(), normalized_path, "", query, ""))
    return normalized, "html"


def get_markdown(result: Any) -> str:
    markdown = getattr(result, "markdown", "")
    if isinstance(markdown, str):
        return markdown.strip()
    return (getattr(markdown, "fit_markdown", None) or getattr(markdown, "raw_markdown", None) or "").strip()


def clean_markdown(markdown: str) -> str:
    text = markdown.replace("\r\n", "\n").strip()
    footer_markers = (
        "\n上一篇：", "\n下一篇：", "\n[上一篇", "\n[下一篇",
        "\n版权所有", "\n#### 友情链接", "\n## 友情链接",
    )
    ends = [text.find(marker) for marker in footer_markers if text.find(marker) >= 0]
    if ends:
        text = text[: min(ends)]

    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if "javascript:;" in stripped:
            continue
        if re.fullmatch(r"!?\[[^\]]*\]\(https?://[^)]+\.(?:png|jpg|jpeg|gif|svg|webp)(?:\?[^)]*)?\)", stripped, re.I):
            continue
        lines.append(line.rstrip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text


def looks_like_error_page(title: str, markdown: str) -> bool:
    sample = f"{title}\n{markdown[:500]}".lower()
    markers = (
        "访问出错 - 502", "502 bad gateway", "503 service unavailable",
        "504 gateway timeout", "403 forbidden", "404 not found",
        "网站防火墙", "页面不存在", "即将访问外部页面",
    )
    return any(marker.lower() in sample for marker in markers)


def static_html_fallback(url: str) -> tuple[str, str, list[str]]:
    """Read a public HTML response without executing scripts.

    This is only used when the browser extractor reports a script-heavy empty shell.
    It does not retry authentication, challenge, rate-limit, or robots denials.
    """
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=35, allow_redirects=True)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        raise RuntimeError(f"兼容读取返回非 HTML：{content_type or '未知类型'}")

    soup = BeautifulSoup(response.content, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else urlparse(url).path.rsplit("/", 1)[-1]
    hrefs = [str(anchor.get("href", "")).strip() for anchor in soup.find_all("a", href=True)]

    root = (
        soup.select_one("article")
        or soup.select_one("main")
        or soup.select_one("[role='main']")
        or soup.select_one(".region-content")
        or soup.select_one("#content")
        or soup.body
    )
    if root is None:
        raise RuntimeError("兼容读取未找到正文容器")
    for node in root.select("script, style, noscript, nav, footer, form, svg, template"):
        node.decompose()

    lines: list[str] = []
    for node in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "th", "td"]):
        value = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
        if not value or (lines and value == lines[-1].lstrip("#- ")):
            continue
        if node.name and node.name.startswith("h"):
            level = min(max(int(node.name[1]), 1), 6)
            lines.append(f"{'#' * level} {value}")
        elif node.name == "li":
            lines.append(f"- {value}")
        else:
            lines.append(value)
    markdown = clean_markdown("\n\n".join(lines))
    if len(markdown) < 80:
        raise RuntimeError("兼容读取正文过短")
    return title.strip(), markdown, hrefs


def safe_slug(value: str, fallback: str = "page") -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value)
    value = re.sub(r"\s+", "_", value).strip(" ._")
    return value[:60] or fallback


def page_file(site_id: str, url: str, title: str, output: Path) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return output / "网页" / site_id / f"{digest}_{safe_slug(title)}.md"


def markdown_document(site: dict[str, Any], url: str, depth: int, title: str, markdown: str, collected_at: str) -> str:
    content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    metadata = {
        "site_id": site["site_id"],
        "site_name": site["name"],
        "category": site["category"],
        "title": title,
        "url": url,
        "depth": depth,
        "collected_at": collected_at,
        "review_status": "批量原始资料，待清洗与人工核验",
        "sha256": content_hash,
    }
    frontmatter = ["---"]
    frontmatter.extend(f"{key}: {json.dumps(str(value), ensure_ascii=False)}" for key, value in metadata.items())
    frontmatter.extend(["---", ""])
    return "\n".join(frontmatter) + markdown.rstrip() + "\n"


def initial_state(sites: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": 1,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "queue": [
            {"site_id": site["site_id"], "url": site["seed_url"], "depth": 0}
            for site in sites
        ],
        "pages": {},
        "documents": {},
    }


def ensure_site_seeds(state: dict[str, Any], sites: list[dict[str, Any]]) -> None:
    queued = {(entry["site_id"], entry["url"]) for entry in state.get("queue", [])}
    processed = set(state.get("pages", {}))
    for site in sites:
        normalized, kind = normalize_url(site["seed_url"], site["seed_url"], urlparse(site["seed_url"]).hostname.lower())
        key = f"{site['site_id']}|{normalized}"
        if kind == "html" and key not in processed and (site["site_id"], normalized) not in queued:
            state.setdefault("queue", []).append({"site_id": site["site_id"], "url": normalized, "depth": 0})


def status_summary(state: dict[str, Any]) -> str:
    counts = Counter(row.get("status", "unknown") for row in state.get("pages", {}).values())
    return (
        f"已处理 {len(state.get('pages', {}))} 页；成功 {counts['success']}；"
        f"失败 {counts['failed']}；robots 拒绝 {counts['robots_blocked']}；"
        f"待采集 {len(state.get('queue', []))}；发现文件 {len(state.get('documents', {}))}。"
    )


async def run(args: argparse.Namespace) -> int:
    selected = set(args.site_id or [])
    sites = load_sites(args.sites, selected)
    site_map = {str(site["site_id"]): site for site in sites}
    args.output.mkdir(parents=True, exist_ok=True)
    state_path = args.output / "state.json"
    state = read_json(state_path, initial_state(sites))
    ensure_site_seeds(state, sites)

    if args.retry_failed:
        for key, row in list(state.get("pages", {}).items()):
            if row.get("status") == "failed" and row.get("site_id") in site_map:
                state["queue"].append({"site_id": row["site_id"], "url": row["url"], "depth": row.get("depth", 0)})
                del state["pages"][key]

    if args.status:
        print(status_summary(state))
        return 0

    browser_config = BrowserConfig(headless=True, verbose=False, user_agent=USER_AGENT, text_mode=True)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.ENABLED,
        check_robots_txt=True,
        user_agent=USER_AGENT,
        page_timeout=35_000,
        wait_until="domcontentloaded",
        excluded_tags=["script", "style", "noscript", "nav", "footer", "form"],
        remove_forms=True,
        remove_overlay_elements=True,
        exclude_all_images=True,
        exclude_external_links=True,
        exclude_social_media_links=True,
        preserve_https_for_internal_links=True,
        word_count_threshold=5,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.35, threshold_type="fixed")
        ),
        verbose=False,
    )

    attempts = 0
    async with AsyncWebCrawler(config=browser_config) as crawler:
        while state["queue"] and attempts < max(args.max_pages, 0):
            if selected:
                selected_index = next(
                    (index for index, queued in enumerate(state["queue"]) if str(queued["site_id"]) in site_map),
                    None,
                )
                if selected_index is None:
                    break
                item = state["queue"].pop(selected_index)
            else:
                item = state["queue"].pop(0)
            site_id = str(item["site_id"])
            if site_id not in site_map:
                continue
            site = site_map[site_id]
            allowed_host = (urlparse(site["seed_url"]).hostname or "").lower()
            url, kind = normalize_url(str(item["url"]), site["seed_url"], allowed_host)
            if not url or kind != "html":
                continue
            key = f"{site_id}|{url}"
            if key in state["pages"]:
                continue

            attempts += 1
            depth = int(item.get("depth", 0))
            collected_at = now_iso()
            row: dict[str, Any] = {
                "site_id": site_id,
                "url": url,
                "depth": depth,
                "collected_at": collected_at,
                "status": "failed",
                "status_code": "",
                "title": "",
                "chars": 0,
                "output_file": "",
                "error": "",
            }

            try:
                result = await crawler.arun(url=url, config=run_config)
                row["status_code"] = str(getattr(result, "status_code", "") or "")
                fallback_hrefs: list[str] = []
                if not getattr(result, "success", False):
                    message = str(getattr(result, "error_message", "采集失败"))
                    if getattr(result, "status_code", None) == 403 and "robots" in message.lower():
                        row["status"] = "robots_blocked"
                        row["error"] = message[:500]
                        raise RuntimeError(message)
                    fallback_allowed = (
                        getattr(result, "status_code", None) not in {401, 403, 429}
                        and any(marker in message.lower() for marker in ("minimal_text", "no_content_elements", "script_heavy_shell"))
                    )
                    if not fallback_allowed:
                        row["error"] = message[:500]
                        raise RuntimeError(message)
                    title, markdown, fallback_hrefs = await asyncio.to_thread(static_html_fallback, url)
                else:
                    metadata = getattr(result, "metadata", {}) or {}
                    title = str(metadata.get("title") or urlparse(url).path.rsplit("/", 1)[-1] or site["name"]).strip()
                    markdown = clean_markdown(get_markdown(result))
                    if len(markdown) < 80:
                        title, markdown, fallback_hrefs = await asyncio.to_thread(static_html_fallback, url)

                if looks_like_error_page(title, markdown):
                    raise RuntimeError(f"检测到站点错误页：{title}")

                target = page_file(site_id, url, title, args.output)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(markdown_document(site, url, depth, title, markdown, collected_at), encoding="utf-8")
                row.update(
                    status="success",
                    title=title,
                    chars=len(markdown),
                    output_file=str(target.relative_to(args.output)),
                    error="",
                )

                links = getattr(result, "links", {}) or {}
                hrefs = list(fallback_hrefs)
                for group in ("internal", "external"):
                    for link in links.get(group, []) or []:
                        hrefs.append(link.get("href", "") if isinstance(link, dict) else str(link))
                if depth < args.max_depth:
                    known_queue = {(q["site_id"], q["url"]) for q in state["queue"]}
                    for href in hrefs:
                        normalized, link_kind = normalize_url(href, url, allowed_host)
                        if not normalized:
                            continue
                        if link_kind == "document":
                            document_key = f"{site_id}|{normalized}"
                            if document_key not in state["documents"]:
                                state["documents"][document_key] = {
                                    "site_id": site_id,
                                    "url": normalized,
                                    "found_on": url,
                                    "discovered_at": collected_at,
                                    "status": "待下载解析",
                                }
                            continue
                        next_key = f"{site_id}|{normalized}"
                        if next_key in state["pages"] or (site_id, normalized) in known_queue:
                            continue
                        state["queue"].append({"site_id": site_id, "url": normalized, "depth": depth + 1})
                        known_queue.add((site_id, normalized))
            except Exception as exc:  # noqa: BLE001 - each failure is persisted for review
                row["error"] = str(exc)[:500]

            state["pages"][key] = row
            append_jsonl(args.output / "pages.jsonl", row)
            state["updated_at"] = now_iso()
            write_json_atomic(state_path, state)
            print(f"[{attempts}/{args.max_pages}] {row['status']}: {url}", flush=True)
            if attempts < args.max_pages and state["queue"]:
                await asyncio.sleep(max(args.delay, 0.0))

    write_json_atomic(args.output / "documents.json", list(state.get("documents", {}).values()))
    print(status_summary(state), flush=True)
    return 0


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(args))
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
