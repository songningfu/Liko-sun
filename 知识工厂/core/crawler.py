"""深大官网全站搜查爬虫。

- 种子站点来自 知识库/采集器/sites.json（只读引用，不修改）。
- BFS 逐站发现页面，同域限制、深度限制、每轮页数上限，断点续跑。
- robots.txt 无法确认时保守跳过该站；请求间隔沿用项目礼貌约定。
- 元数据全自动：部门=站点名，分类=站点类别，主题/校区/人群由 process_rag 分类器在清洗阶段推断。
- 发现的 PDF/DOCX 等附件链接记录到 data/crawl_documents.jsonl，留给附件解析工具。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.robotparser
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from .fetch import TIMEOUT, UA, _decode
from .html2md import html_to_markdown
from .process import PAGES_ROOT, write_page_markdown
from .registry import now_iso

FACTORY_ROOT = Path(__file__).resolve().parents[1]
SITES_FILE = FACTORY_ROOT.parent / "知识库" / "采集器" / "sites.json"
DATA_ROOT = FACTORY_ROOT / "data"
STATE_FILE = DATA_ROOT / "crawl_state.json"
DOCUMENTS_LOG = DATA_ROOT / "crawl_documents.jsonl"
CUSTOM_SITES_FILE = DATA_ROOT / "custom_sites.json"

ALLOWED_ROOT = "szu.edu.cn"
RECHECK_DAYS = 30  # 已抓页面超过该天数后才重新检查内容是否变化
SKIP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".mp3", ".wav", ".mp4", ".avi", ".mov", ".webm",
    ".css", ".js", ".json", ".xml", ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".exe", ".apk",
}
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
HTML_EXTENSIONS = {"", ".htm", ".html", ".shtml", ".aspx", ".jsp", ".php"}
SKIP_PATH_PARTS = (
    "/login", "/logout", "/signin", "/cas/", "/auth/", "/admin/",
    "/user/", "/api/", "/search", "/sousuo", "/oa/", "/webvpn/",
)
TRACKING_QUERY_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "from"}

Event = dict


def _event(step: str, message: str, **extra) -> Event:
    return {"step": step, "message": message, "time": now_iso(), **extra}


def load_sites() -> list[dict]:
    built_in = json.loads(SITES_FILE.read_text(encoding="utf-8"))
    if not CUSTOM_SITES_FILE.exists():
        return built_in
    try:
        custom = json.loads(CUSTOM_SITES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        custom = []
    known = {site["seed_url"].rstrip("/") for site in built_in}
    return built_in + [site for site in custom if site.get("seed_url", "").rstrip("/") not in known]


def add_site(*, name: str, seed_url: str, category: str = "其他校务") -> dict:
    """增加全站搜查种子；只允许深大 HTTPS 公网站点。"""
    parsed = urlparse(seed_url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == ALLOWED_ROOT or host.endswith(f".{ALLOWED_ROOT}")):
        raise ValueError("站点种子必须是 https://*.szu.edu.cn 官方地址")
    normalized = urlunparse(("https", parsed.netloc.lower(), parsed.path or "/", "", "", ""))
    if any(site["seed_url"].rstrip("/") == normalized.rstrip("/") for site in load_sites()):
        raise ValueError("这个站点种子已经存在")
    try:
        current = json.loads(CUSTOM_SITES_FILE.read_text(encoding="utf-8")) if CUSTOM_SITES_FILE.exists() else []
    except (json.JSONDecodeError, OSError):
        current = []
    next_number = max([int(str(s.get("site_id", "CUSTOM-0")).split("-")[-1]) for s in current] or [0]) + 1
    site = {
        "site_id": f"CUSTOM-{next_number:03d}",
        "name": name.strip() or host,
        "seed_url": normalized,
        "category": category.strip() or "其他校务",
    }
    current.append(site)
    CUSTOM_SITES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CUSTOM_SITES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CUSTOM_SITES_FILE)
    return site


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"queue": [], "seen": {}, "robots": {}, "total_crawled": 0}


def _save_state(state: dict) -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def normalize_url(raw_url: str, base_url: str, allowed_host: str) -> tuple[str | None, str]:
    """规范化链接；返回 (url, kind)，kind: html / document / 拒绝原因。"""
    if not raw_url or raw_url.startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
        return None, "unsupported"
    absolute = urljoin(base_url, raw_url.strip())
    parsed = urlparse(absolute)
    host = (parsed.hostname or "").lower()
    if host != allowed_host:
        return None, "outside_site"
    if parsed.scheme not in {"http", "https"}:
        return None, "unsupported"
    lowered_path = parsed.path.lower()
    if any(part in lowered_path for part in SKIP_PATH_PARTS):
        return None, "blocked_path"
    extension = Path(parsed.path).suffix.lower()
    if extension in DOCUMENT_EXTENSIONS:
        clean = urlunparse(("https", parsed.netloc.lower(), parsed.path, "", parsed.query, ""))
        return clean, "document"
    if extension in SKIP_EXTENSIONS or extension not in HTML_EXTENSIONS:
        return None, "non_html"
    query_pairs = sorted(
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in TRACKING_QUERY_KEYS and not k.lower().startswith("utm_")
    )[:6]
    normalized = urlunparse(("https", parsed.netloc.lower(),
                             re.sub(r"/{2,}", "/", parsed.path or "/"), "",
                             urlencode(query_pairs, doseq=True), ""))
    return normalized, "html"


def _robots_allowed(state: dict, host: str, url: str, client: httpx.Client) -> bool:
    """按主机缓存 robots 判定；robots 无法获取时保守拒绝。"""
    if host not in state["robots"]:
        robots_url = f"https://{host}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        try:
            resp = client.get(robots_url)
            if resp.status_code == 404:
                rp.parse([])  # 无 robots，全站允许
                state["robots"][host] = {"ok": True, "rules": ""}
            elif resp.status_code >= 400:
                state["robots"][host] = {"ok": False, "rules": ""}
            else:
                state["robots"][host] = {"ok": True, "rules": resp.text}
        except httpx.HTTPError:
            state["robots"][host] = {"ok": False, "rules": ""}
    entry = state["robots"][host]
    if not entry["ok"]:
        return False
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(entry["rules"].splitlines()) if entry["rules"] else rp.parse([])
    return rp.can_fetch(UA, url)


def _page_path(url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    host = (urlparse(url).hostname or "site").split(".")[0]
    return PAGES_ROOT / f"CRAWL-{host}-{digest}.md"


def crawl_sites(max_pages: int = 60, max_depth: int = 3, delay: float = 2.0,
                site_ids: set[str] | None = None) -> Iterator[Event]:
    """全站搜查主流程。每轮最多处理 max_pages 个页面，中断后再跑自动续上。"""
    sites = [s for s in load_sites() if site_ids is None or s["site_id"] in site_ids]
    state = _load_state()

    # 断点续跑：队列为空时按种子重建
    if not state["queue"]:
        for site in sites:
            state["queue"].append({"url": site["seed_url"], "site_id": site["site_id"],
                                   "depth": 0})
        yield _event("crawl", f"初始化爬取队列：{len(sites)} 个官方站点种子")

    yield _event("crawl", f"本轮计划处理至多 {max_pages} 页（已累计抓取 {state['total_crawled']} 页，"
                          f"队列剩余 {len(state['queue'])}）")

    new_or_changed = 0
    processed = 0
    with httpx.Client(headers={"User-Agent": UA}, timeout=TIMEOUT, follow_redirects=True) as client:
        while state["queue"] and processed < max_pages:
            item = state["queue"].pop(0)
            url, site_id, depth = item["url"], item["site_id"], item["depth"]
            site = next((s for s in sites if s["site_id"] == site_id), None)
            if site is None:
                continue
            host = (urlparse(site["seed_url"]).hostname or "").lower()

            seen = state["seen"].get(url)
            if seen and not seen.get("failed"):
                checked_at = seen.get("checked_at", "")
                try:
                    age_days = (datetime.now().astimezone() - datetime.fromisoformat(checked_at)).days
                except ValueError:
                    age_days = RECHECK_DAYS + 1
                if age_days < RECHECK_DAYS:
                    continue  # 近期已确认，跳过；超过 RECHECK_DAYS 才重新检查变更

            if not _robots_allowed(state, host, url, client):
                state["seen"][url] = {"failed": True, "reason": "robots"}
                _save_state(state)
                yield _event("crawl", f"⊘ robots 限制跳过：{url}", level="error")
                continue

            try:
                resp = client.get(url)
            except httpx.HTTPError as exc:
                state["seen"][url] = {"failed": True, "reason": str(exc)[:120]}
                _save_state(state)
                yield _event("crawl", f"✗ 请求失败：{url}（{str(exc)[:60]}）", level="error")
                continue
            processed += 1

            if resp.status_code >= 400:
                state["seen"][url] = {"failed": True, "reason": f"HTTP {resp.status_code}"}
                _save_state(state)
                yield _event("crawl", f"✗ HTTP {resp.status_code}：{url}", level="error")
                continue

            html = _decode(resp)
            title, body = html_to_markdown(html)
            content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

            if len(body) >= 80:
                path = _page_path(url)
                if seen and seen.get("hash") == content_hash and path.exists():
                    state["seen"][url] = {"hash": content_hash, "checked_at": now_iso()}
                    yield _event("crawl", f"= 无变化：{title[:30] or url}")
                else:
                    write_page_markdown(path, meta={
                        "source_id": path.stem,
                        "site_id": site_id,
                        "site_name": site["name"],
                        "title": title or url,
                        "url": url,
                        "category": site["category"],
                        "department": site["name"],
                        "campus": "",
                        "source_type": "official",
                        "official": "true",
                        "collected_at": now_iso(),
                    }, body=body)
                    state["seen"][url] = {"hash": content_hash, "checked_at": now_iso()}
                    state["total_crawled"] += 1
                    new_or_changed += 1
                    yield _event("crawl", f"✓ [{site['name']}] {title[:40]}（{len(body)} 字符）")
            else:
                state["seen"][url] = {"failed": True, "reason": "正文过短"}
                yield _event("crawl", f"△ 正文过短跳过：{url}")

            # 发现新链接与附件
            if depth < max_depth:
                soup = BeautifulSoup(html, "lxml")
                found = 0
                for a in soup.find_all("a", href=True):
                    link, kind = normalize_url(a["href"], url, host)
                    if not link:
                        continue
                    if kind == "document":
                        with DOCUMENTS_LOG.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps({"url": link, "found_on": url,
                                                 "site_id": site_id,
                                                 "found_at": now_iso()},
                                                ensure_ascii=False) + "\n")
                    elif link not in state["seen"] and not any(q["url"] == link for q in state["queue"]):
                        state["queue"].append({"url": link, "site_id": site_id, "depth": depth + 1})
                        found += 1
                if found:
                    yield _event("crawl", f"　↳ 新发现 {found} 个链接，队列剩余 {len(state['queue'])}")

            _save_state(state)
            time.sleep(delay)

    _save_state(state)
    yield _event("crawl", f"本轮结束：处理 {processed} 页，新增/更新 {new_or_changed} 篇，"
                          f"队列剩余 {len(state['queue'])} 页待下轮继续")
