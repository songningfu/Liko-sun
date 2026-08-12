"""内网采集：Playwright 持久浏览器会话。

第一次使用：调用 start_login() 打开真实浏览器窗口，用户手动完成统一身份认证登录；
会话保存在 data/auth/browser-profile/，之后 fetch_internal() 以无头方式复用会话抓取。
会话过期后 fetch 会返回 auth_expired，重新登录即可。
"""
from __future__ import annotations

import threading
from pathlib import Path

from .fetch import FetchResult
from .html2md import html_to_markdown

AUTH_ROOT = Path(__file__).resolve().parents[1] / "data" / "auth"
PROFILE_DIR = AUTH_ROOT / "browser-profile"

# 常见统一身份认证页特征，命中则说明会话已失效
SSO_HINTS = ("统一身份认证", "authserver", "sso", "cas/login", "login.aspx")

_login_lock = threading.Lock()
_login_status = {"state": "idle", "message": ""}  # idle | waiting | done | error


def has_session() -> bool:
    return PROFILE_DIR.exists() and any(PROFILE_DIR.iterdir())


def login_status() -> dict:
    return {**_login_status, "has_session": has_session()}


def start_login(login_url: str) -> None:
    """打开有头浏览器让用户手动登录；登录完成后用户直接关闭窗口即可。"""
    if not _login_lock.acquire(blocking=False):
        raise RuntimeError("已有登录窗口在运行")

    def _run() -> None:
        _login_status.update(state="waiting", message="浏览器已打开，请完成登录后关闭窗口")
        try:
            from playwright.sync_api import sync_playwright
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    str(PROFILE_DIR), headless=False, viewport={"width": 1200, "height": 800},
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(login_url, wait_until="domcontentloaded")
                context.wait_for_event("close", timeout=0)  # 直到用户关闭窗口
                _login_status.update(state="done", message="登录窗口已关闭，会话已保存")
        except Exception as exc:  # noqa: BLE001 - 需要把任何浏览器错误透传到 UI
            _login_status.update(state="error", message=str(exc)[:300])
        finally:
            _login_lock.release()

    threading.Thread(target=_run, daemon=True).start()


def fetch_internal(url: str) -> FetchResult:
    """用已保存的会话以无头方式抓取内网页面。"""
    if not has_session():
        return FetchResult(ok=False, error="尚未登录内网，请先在「设置」中完成内网登录",
                           meta={"auth_expired": True})
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=True)
            try:
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=45000)
                final_url = page.url
                html = page.content()
            finally:
                context.close()
    except Exception as exc:  # noqa: BLE001
        return FetchResult(ok=False, error=f"浏览器抓取失败: {str(exc)[:300]}")

    probe = final_url.lower() + html[:2000].lower()
    if any(hint in probe for hint in SSO_HINTS):
        return FetchResult(ok=False, error="内网会话已过期，请重新登录",
                           meta={"auth_expired": True})
    title, body = html_to_markdown(html)
    if len(body) < 50:
        return FetchResult(ok=False, error="正文过短，页面可能需要额外交互或权限不足")
    return FetchResult(ok=True, title=title, body_md=body, final_url=final_url)
