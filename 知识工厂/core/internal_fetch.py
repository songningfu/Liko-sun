"""内网采集：服务器无头浏览器、企业微信扫码登录与持久会话。"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from .fetch import FetchResult
from .html2md import html_to_markdown

AUTH_ROOT = Path(__file__).resolve().parents[1] / "data" / "auth"
PROFILE_DIR = AUTH_ROOT / "browser-profile"
LOGIN_PREVIEW = AUTH_ROOT / "login-preview.png"
LOGIN_PREVIEW_TMP = AUTH_ROOT / "login-preview.tmp.png"
SESSION_MARKER = AUTH_ROOT / "session-ready"

# 常见统一身份认证页特征，命中则说明会话已失效
SSO_HINTS = ("统一身份认证", "unified certification", "authserver", "sso", "cas/login", "login.aspx")

_login_lock = threading.Lock()
_login_status = {"state": "idle", "message": ""}  # idle | waiting | done | error


def has_session() -> bool:
    return SESSION_MARKER.exists() and PROFILE_DIR.exists() and any(PROFILE_DIR.iterdir())


def login_status() -> dict:
    return {
        **_login_status,
        "has_session": has_session(),
        "preview_available": _login_status.get("state") == "waiting" and LOGIN_PREVIEW.exists(),
    }


def _is_auth_page(url: str, body: str = "") -> bool:
    probe = f"{url}\n{body[:3000]}".lower()
    return any(hint in probe for hint in SSO_HINTS)


def _remove_preview() -> None:
    for path in (LOGIN_PREVIEW, LOGIN_PREVIEW_TMP):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def start_login(login_url: str) -> None:
    """在服务器启动无头会话；管理页展示实时二维码，用户用企业微信扫码。"""
    if not _login_lock.acquire(blocking=False):
        raise RuntimeError("已有内网登录会话在等待扫码")

    def _run() -> None:
        _login_status.update(state="waiting", message="登录二维码准备中…")
        _remove_preview()
        try:
            from playwright.sync_api import sync_playwright

            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(PROFILE_DIR),
                    headless=True,
                    viewport={"width": 1280, "height": 900},
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(login_url, wait_until="networkidle", timeout=60000)
                    # 网上办事大厅首页的按钮位于 Web Component 中，英文 Login 文本可稳定定位。
                    if urlparse(page.url).hostname == "ehall.szu.edu.cn":
                        login_button = page.get_by_text("Login", exact=True)
                        if login_button.count():
                            login_button.first.click(timeout=10000)
                            page.wait_for_load_state("domcontentloaded", timeout=30000)

                    deadline = time.monotonic() + 300
                    saw_auth_page = _is_auth_page(page.url, page.locator("body").inner_text())
                    while time.monotonic() < deadline:
                        body = page.locator("body").inner_text()
                        on_auth_page = _is_auth_page(page.url, body)
                        saw_auth_page = saw_auth_page or on_auth_page
                        if saw_auth_page and not on_auth_page:
                            SESSION_MARKER.write_text(str(int(time.time())), encoding="utf-8")
                            _login_status.update(state="done", message="内网登录成功，会话已保存")
                            return
                        page.screenshot(path=str(LOGIN_PREVIEW_TMP), full_page=False)
                        LOGIN_PREVIEW_TMP.replace(LOGIN_PREVIEW)
                        _login_status.update(
                            state="waiting",
                            message="请用企业微信扫描二维码；成功后页面会自动确认",
                        )
                        page.wait_for_timeout(2500)
                    _login_status.update(state="error", message="二维码已超时，请重新生成")
                finally:
                    context.close()
        except Exception as exc:  # noqa: BLE001 - 需要把浏览器错误透传到 UI
            _login_status.update(state="error", message=f"内网登录启动失败：{str(exc)[:240]}")
        finally:
            _remove_preview()
            _login_lock.release()

    threading.Thread(target=_run, daemon=True).start()


def fetch_internal(url: str) -> FetchResult:
    """用已保存的会话以无头方式抓取内网页面。"""
    if not has_session():
        return FetchResult(ok=False, error="尚未登录内网，请先在「设置」中完成企业微信扫码登录",
                           meta={"auth_expired": True})
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(str(PROFILE_DIR), headless=True)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url, wait_until="networkidle", timeout=45000)
                final_url = page.url
                html = page.content()
            finally:
                context.close()
    except Exception as exc:  # noqa: BLE001
        return FetchResult(ok=False, error=f"浏览器抓取失败: {str(exc)[:300]}")

    if _is_auth_page(final_url, html):
        SESSION_MARKER.unlink(missing_ok=True)
        return FetchResult(ok=False, error="内网会话已过期，请重新扫码登录",
                           meta={"auth_expired": True})
    title, body = html_to_markdown(html)
    if len(body) < 50:
        return FetchResult(ok=False, error="正文过短，页面可能需要额外交互或权限不足")
    return FetchResult(ok=True, title=title, body_md=body, final_url=final_url)
