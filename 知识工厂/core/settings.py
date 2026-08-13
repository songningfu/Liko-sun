"""工作台设置：data/settings.json。"""
from __future__ import annotations

import json
import threading
from pathlib import Path

SETTINGS_FILE = Path(__file__).resolve().parents[1] / "data" / "settings.json"
_lock = threading.Lock()

DEFAULTS = {
    "schedule_enabled": False,
    "schedule_time": "03:00",          # 每日来源增量采集时间（本地时间 HH:MM）
    "crawl_schedule": "weekly",        # 全站搜查频率：off / weekly / biweekly / monthly
    "crawl_time": "04:00",             # 全站搜查执行时间
    "crawl_max_pages": 60,             # 每轮全站搜查最多处理页数（控制礼貌负载）
    "request_delay_seconds": 2,        # 相邻请求间隔，沿用采集器约定
    "internal_login_url": "https://ehall.szu.edu.cn/",  # 内网登录入口，可修改
    "model_enrichment_enabled": False, # 仅配置模型密钥后可开启；原文永不覆盖
    "auto_sync_enabled": False,        # 合格资料加工完成后自动写入 FastGPT 暂存区
    "auto_publish_enabled": False,     # 仅低风险官方资料在训练完成后自动切换上线
}


def load() -> dict:
    if not SETTINGS_FILE.exists():
        return dict(DEFAULTS)
    with _lock:
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return dict(DEFAULTS)
    return {**DEFAULTS, **data}


def save(updates: dict) -> dict:
    data = load()
    for key in DEFAULTS:
        if key in updates:
            data[key] = updates[key]
    with _lock:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
