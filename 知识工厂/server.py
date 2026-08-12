"""知识工厂工作台服务：来源管理、流水线运行（SSE 进度）、导出下载、内网登录、定时调度。

启动：.venv/bin/python server.py   然后访问 http://127.0.0.1:8020
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from core import internal_fetch, settings
from core.crawler import STATE_FILE as CRAWL_STATE_FILE
from core.pipeline import EXPORT_ROOT, run_crawl, run_pipeline
from core.registry import Registry

FACTORY_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = FACTORY_ROOT / "static"

app = FastAPI(title="Liko 知识工厂", docs_url=None, redoc_url=None)
registry = Registry()

# 流水线运行状态：同一时间只允许一个任务
_run_lock = threading.Lock()
_subscribers: list[queue.Queue] = []
_last_events: list[dict] = []  # 保留最近一次运行的事件，供刷新后回看


def _broadcast(event: dict) -> None:
    _last_events.append(event)
    for q in list(_subscribers):
        q.put(event)


def _run_pipeline_thread(only_ids: set[str] | None) -> None:
    try:
        for event in run_pipeline(registry, only_ids=only_ids):
            _broadcast(event)
    finally:
        _run_lock.release()


def _run_crawl_thread(max_pages: int, max_depth: int, delay: float) -> None:
    try:
        for event in run_crawl(max_pages=max_pages, max_depth=max_depth, delay=delay):
            _broadcast(event)
    finally:
        _run_lock.release()


def _crawl_state_summary() -> dict:
    if not CRAWL_STATE_FILE.exists():
        return {"total_crawled": 0, "queue_left": 0}
    try:
        state = json.loads(CRAWL_STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"total_crawled": 0, "queue_left": 0}
    return {"total_crawled": state.get("total_crawled", 0),
            "queue_left": len(state.get("queue", []))}


class SourceIn(BaseModel):
    url: str
    source_type: str
    title: str = ""
    department: str = ""
    campus: str = "通用"
    category: str = ""
    notes: str = ""


class SourcePatch(BaseModel):
    title: str | None = None
    department: str | None = None
    campus: str | None = None
    category: str | None = None
    notes: str | None = None
    enabled: bool | None = None


class ImportIn(BaseModel):
    text: str
    source_type: str


class RunIn(BaseModel):
    source_ids: list[str] | None = None


class SettingsIn(BaseModel):
    schedule_enabled: bool | None = None
    schedule_time: str | None = None
    crawl_schedule: str | None = None
    crawl_time: str | None = None
    crawl_max_pages: int | None = None
    request_delay_seconds: int | None = None
    internal_login_url: str | None = None


class CrawlRunIn(BaseModel):
    max_pages: int | None = None


class LoginIn(BaseModel):
    url: str | None = None


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_ROOT / "index.html").read_text(encoding="utf-8")


@app.get("/api/overview")
def overview() -> dict:
    exports = sorted(EXPORT_ROOT.glob("fastgpt-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "sources": registry.stats(),
        "last_export": exports[0].name if exports else "",
        "settings": settings.load(),
        "internal": internal_fetch.login_status(),
        "crawl": _crawl_state_summary(),
        "running": _run_lock.locked(),
    }


@app.get("/api/sources")
def list_sources() -> list[dict]:
    return registry.list()


@app.post("/api/sources", status_code=201)
def add_source(payload: SourceIn) -> dict:
    try:
        return registry.add(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sources/import", status_code=201)
def import_sources(payload: ImportIn) -> dict:
    """批量粘贴导入：每行一个 URL，支持 "URL 标题" 用空格或 | 分隔。"""
    added, errors = [], []
    for lineno, raw in enumerate(payload.text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split("|") if "|" in line else line.split(None, 1)
        url = parts[0].strip()
        title = parts[1].strip() if len(parts) > 1 else ""
        try:
            src = registry.add(url=url, source_type=payload.source_type, title=title)
            added.append(src["id"])
        except ValueError as exc:
            errors.append(f"第{lineno}行: {exc}")
    return {"added": added, "errors": errors}


@app.patch("/api/sources/{source_id}")
def update_source(source_id: str, payload: SourcePatch) -> dict:
    try:
        return registry.update(source_id, **payload.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/sources/{source_id}", status_code=204)
def delete_source(source_id: str) -> None:
    try:
        registry.remove(source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/pipeline/run")
def run(payload: RunIn) -> dict:
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有流水线在运行")
    _last_events.clear()
    only = set(payload.source_ids) if payload.source_ids else None
    threading.Thread(target=_run_pipeline_thread, args=(only,), daemon=True).start()
    return {"started": True}


@app.get("/api/pipeline/events")
def events() -> StreamingResponse:
    """SSE 事件流：先入队的历史事件让后连上的页面也能看到上下文。"""
    q: queue.Queue = queue.Queue()
    for event in _last_events[-200:]:
        q.put(event)
    _subscribers.append(q)

    def stream():
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("step") in {"done", "error"}:
                        break
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            if q in _subscribers:
                _subscribers.remove(q)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/crawl/run")
def crawl_run(payload: CrawlRunIn) -> dict:
    """全站搜查：爬完自动接清洗与导出。"""
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有任务在运行")
    _last_events.clear()
    data = settings.load()
    max_pages = payload.max_pages or data["crawl_max_pages"]
    threading.Thread(target=_run_crawl_thread,
                     args=(max_pages, 3, float(data["request_delay_seconds"])),
                     daemon=True).start()
    return {"started": True, "max_pages": max_pages}


@app.get("/api/exports")
def list_exports() -> list[dict]:
    result = []
    for path in sorted(EXPORT_ROOT.glob("fastgpt-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        result.append({"name": path.name, "size": path.stat().st_size,
                       "created_at": path.stat().st_mtime})
    return result


@app.get("/api/exports/{name}")
def download_export(name: str) -> FileResponse:
    if "/" in name or ".." in name:
        raise HTTPException(status_code=400, detail="非法文件名")
    path = EXPORT_ROOT / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="导出包不存在")
    return FileResponse(path, filename=name)


@app.get("/api/settings")
def get_settings() -> dict:
    return settings.load()


@app.patch("/api/settings")
def patch_settings(payload: SettingsIn) -> dict:
    updates = payload.model_dump(exclude_none=True)
    if "crawl_schedule" in updates and updates["crawl_schedule"] not in {"off", "weekly", "biweekly", "monthly"}:
        raise HTTPException(status_code=400, detail="crawl_schedule 只能是 off / weekly / biweekly / monthly")
    data = settings.save(updates)
    _reschedule(data)
    return data


@app.post("/api/internal/login")
def internal_login(payload: LoginIn) -> dict:
    url = payload.url or settings.load()["internal_login_url"]
    try:
        internal_fetch.start_login(url)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"started": True, "url": url}


@app.get("/api/internal/status")
def internal_status() -> dict:
    return internal_fetch.login_status()


# ---------- 定时调度 ----------

def _reschedule(data: dict | None = None) -> None:
    from apscheduler.schedulers.background import BackgroundScheduler
    global _scheduler
    data = data or settings.load()
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    jobs = []
    if data["schedule_enabled"]:
        hour, minute = (int(x) for x in data["schedule_time"].split(":"))
        jobs.append((_scheduled_run, "cron", {"hour": hour, "minute": minute}))
    # 全站搜查：默认每周一，不频繁
    if data["crawl_schedule"] != "off":
        hour, minute = (int(x) for x in data["crawl_time"].split(":"))
        if data["crawl_schedule"] == "weekly":
            jobs.append((_scheduled_crawl, "cron", {"day_of_week": "mon", "hour": hour, "minute": minute}))
        elif data["crawl_schedule"] == "biweekly":
            jobs.append((_scheduled_crawl, "interval", {"weeks": 2, "start_date": _next_run_hint(hour, minute)}))
        elif data["crawl_schedule"] == "monthly":
            jobs.append((_scheduled_crawl, "cron", {"day": 1, "hour": hour, "minute": minute}))
    if jobs:
        _scheduler = BackgroundScheduler()
        for func, trigger, kwargs in jobs:
            _scheduler.add_job(func, trigger, **kwargs)
        _scheduler.start()


def _next_run_hint(hour: int, minute: int) -> str:
    from datetime import datetime, timedelta
    tomorrow = datetime.now() + timedelta(days=1)
    return tomorrow.replace(hour=hour, minute=minute, second=0).isoformat()


def _scheduled_run() -> None:
    if _run_lock.acquire(blocking=False):
        threading.Thread(target=_run_pipeline_thread, args=(None,), daemon=True).start()


def _scheduled_crawl() -> None:
    if _run_lock.acquire(blocking=False):
        data = settings.load()
        threading.Thread(target=_run_crawl_thread,
                         args=(data["crawl_max_pages"], 3, float(data["request_delay_seconds"])),
                         daemon=True).start()


_scheduler = None


from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def _lifespan(_app):
    _reschedule()
    yield
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)


app.router.lifespan_context = _lifespan


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8020)
