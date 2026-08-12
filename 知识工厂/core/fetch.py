"""抓取器：官网/内网公开页面与微信公众号文章。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup

from .html2md import html_to_markdown

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

ERROR_PAGE_HINTS = ("404", "页面不存在", "访问出错", "Internal Server Error")


@dataclass
class FetchResult:
    ok: bool
    title: str = ""
    body_md: str = ""
    author: str = ""
    published_at: str = ""
    final_url: str = ""
    error: str = ""
    meta: dict = field(default_factory=dict)


def _looks_like_error_page(title: str, body: str) -> str | None:
    probe = f"{title}\n{body[:300]}"
    for hint in ERROR_PAGE_HINTS:
        if hint in probe:
            return f"疑似错误页（包含“{hint}”）"
    return None


def _decode(response: httpx.Response) -> str:
    if response.encoding and response.encoding.lower() != "iso-8859-1":
        return response.text
    # 学校旧站常见 GBK/GB2312
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def fetch_official(url: str) -> FetchResult:
    """抓取公网官方页面（szu.edu.cn 及子域）。"""
    try:
        resp = httpx.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError as exc:
        return FetchResult(ok=False, error=f"网络请求失败: {exc}")
    if resp.status_code >= 400:
        return FetchResult(ok=False, error=f"HTTP {resp.status_code}")
    html = _decode(resp)
    title, body = html_to_markdown(html)
    err = _looks_like_error_page(title, body)
    if err:
        return FetchResult(ok=False, error=err)
    if len(body) < 80:
        return FetchResult(ok=False, error="正文过短，可能是脚本渲染页面；可改用内网（浏览器会话）方式抓取")
    return FetchResult(ok=True, title=title, body_md=body, final_url=str(resp.url))


def fetch_wechat(url: str) -> FetchResult:
    """抓取微信公众号文章：标题、公众号名、发布时间、正文。"""
    if "mp.weixin.qq.com" not in url:
        return FetchResult(ok=False, error="仅支持 mp.weixin.qq.com 文章链接")
    try:
        resp = httpx.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError as exc:
        return FetchResult(ok=False, error=f"网络请求失败: {exc}")
    if resp.status_code >= 400:
        return FetchResult(ok=False, error=f"HTTP {resp.status_code}")
    html = resp.text
    if "环境异常" in html or "完成验证" in html or "验证" in html[:2000] and "js_content" not in html:
        return FetchResult(ok=False, error="微信要求人机验证，请稍后在浏览器打开该链接、复制为本地 HTML 再导入，或更换网络环境重试")

    soup = BeautifulSoup(html, "lxml")
    title = ""
    name_node = soup.select_one("#activity-name")
    if name_node:
        title = name_node.get_text(strip=True)
    if not title:
        meta_node = soup.find("meta", property="og:title")
        if meta_node:
            title = meta_node.get("content", "").strip()
    account = soup.select_one("#js_name")
    author = account.get_text(strip=True) if account else ""
    published = ""
    match = re.search(r'var\s+ct\s*=\s*"?(\d{9,10})"?', html)
    if match:
        from datetime import datetime
        published = datetime.fromtimestamp(int(match.group(1))).strftime("%Y-%m-%d")
    time_node = soup.select_one("#publish_time")
    if time_node and not published:
        published = time_node.get_text(strip=True)

    content = soup.select_one("#js_content")
    if not content or len(content.get_text(strip=True)) < 30:
        return FetchResult(ok=False, error="未能提取正文，文章可能已删除或需要验证")
    # js_content 初始为 visibility:hidden，属正常，不影响文本提取
    _, body = html_to_markdown(str(content))
    if len(body) < 30:
        return FetchResult(ok=False, error="正文提取结果为空")
    return FetchResult(ok=True, title=title, body_md=body, author=author,
                       published_at=published, final_url=str(resp.url))


def fetch_source(source: dict) -> FetchResult:
    """按来源类型分发抓取。内网类型由 internal_fetch 提供，这里只做公网。"""
    stype = source["source_type"]
    if stype == "wechat":
        return fetch_wechat(source["url"])
    return fetch_official(source["url"])
