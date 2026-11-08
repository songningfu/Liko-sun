"""Independent Pocket deployment; never imports the campus application."""

from datetime import UTC, datetime
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import text

from .auth import GUEST_COOKIE, SESSION_COOKIE, CurrentUser, get_current_user, private_hash
from .auth_routes import router as auth_router
from .config import Settings, get_settings
from .database import Database, get_database
from .pocket import router as pocket_router


class PocketBoundary:
    """Scope existing account cookies to Pocket without changing campus auth."""

    cookie_names = {SESSION_COOKIE: "liko_pocket_session", GUEST_COOKIE: "liko_pocket_guest"}

    def __init__(self, app, *, prefix: str, origin: str):
        self.app, self.prefix, self.origin = app, prefix, origin

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = list(scope["headers"])
        incoming = dict(headers)
        origin = incoming.get(b"origin", b"").decode("latin-1")
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"} and origin and origin != self.origin:
            from fastapi.responses import JSONResponse
            return await JSONResponse({"detail": "请从 Liko Pocket 页面发起操作。"}, status_code=403)(scope, receive, send)
        cookies = SimpleCookie()
        try:
            cookies.load(incoming.get(b"cookie", b"").decode("latin-1"))
        except CookieError:
            cookies = SimpleCookie()
        mapped = SimpleCookie()
        for internal, external in self.cookie_names.items():
            if external in cookies:
                mapped[internal] = cookies[external].value
        # Cookies belonging to the campus app or other products are not forwarded.
        headers = [(key, value) for key, value in headers if key.lower() != b"cookie"]
        if mapped:
            headers.append((b"cookie", mapped.output(header="", sep=";").strip().encode("latin-1")))
        scope = {**scope, "headers": headers}

        async def scoped_send(message):
            if message["type"] == "http.response.start":
                outgoing = []
                for key, value in message["headers"]:
                    if key.lower() == b"set-cookie":
                        original = SimpleCookie()
                        original.load(value.decode("latin-1"))
                        for name, morsel in original.items():
                            if name not in self.cookie_names:
                                continue
                            replacement = SimpleCookie()
                            external = self.cookie_names[name]
                            replacement[external] = morsel.value
                            for attribute, setting in morsel.items():
                                if setting:
                                    replacement[external][attribute] = setting
                            replacement[external]["path"] = self.prefix + "/"
                            replacement[external]["secure"] = True
                            replacement[external]["httponly"] = True
                            outgoing.append((key, replacement.output(header="").strip().encode("latin-1")))
                    else:
                        outgoing.append((key, value))
                outgoing.extend([(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")])
                message = {**message, "headers": outgoing}
            await send(message)

        await self.app(scope, receive, scoped_send)


def cloud_budget(
    request: Request,
    current: Annotated[CurrentUser, Depends(get_current_user)],
    database: Annotated[Database | None, Depends(get_database)],
    config: Annotated[Settings, Depends(get_settings)],
) -> None:
    if request.method != "POST" or not current.authenticated or not database:
        return
    voice = request.url.path.endswith("/transcribe")
    allowed, _ = database.consume_counter(
        kind="pocket_voice" if voice else "pocket_analysis",
        subject_hash=private_hash(current.user_id, config.session_secret or ""),
        period_key=datetime.now(UTC).date().isoformat(),
        limit=20 if voice else 50,
    )
    if not allowed:
        raise HTTPException(429, "今天的识别或整理次数已用完，已有记录仍可查看和修改。")


def create_app(prefix: str = "/liko-pocket", origin: str = "https://songningfu.site") -> FastAPI:
    if not prefix.startswith("/") or prefix.endswith("/") or ".." in prefix:
        raise ValueError("Pocket prefix must be an absolute path without a trailing slash")
    ui = Path(__file__).resolve().parents[1] / "pocket-ui"
    pocket = FastAPI(title="Liko Pocket", docs_url=None, redoc_url=None, openapi_url=None)
    pocket.include_router(auth_router)
    pocket.include_router(pocket_router, dependencies=[Depends(cloud_budget)])

    @pocket.get("/api/health")
    def health(database: Annotated[Database | None, Depends(get_database)]):
        if not database:
            raise HTTPException(503, "记录数据库暂不可用")
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "service": "Liko Pocket", "database": "ok", "hardwareConnected": False}

    @pocket.get("/index.html", response_class=HTMLResponse, include_in_schema=False)
    @pocket.get("/", response_class=HTMLResponse)
    def home():
        return (ui / "index.html").read_text().replace(
            '<meta name="pocket-api-prefix" content="">',
            f'<meta name="pocket-api-prefix" content="{prefix}">',
        )

    @pocket.get("/{asset}", include_in_schema=False)
    def static_asset(asset: str):
        if asset not in {"app.js", "style.css"}:
            raise HTTPException(404)
        return FileResponse(ui / asset)

    pocket.add_middleware(PocketBoundary, prefix=prefix, origin=origin)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount(prefix, pocket)
    return app
