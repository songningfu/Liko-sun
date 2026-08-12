from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output_full"
STATE_PATH = OUTPUT / "state.json"
MARKERS = (
    "访问出错 - 502",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
    "403 forbidden",
    "404 not found",
    "网站防火墙",
    "页面不存在",
    "即将访问外部页面",
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def main() -> int:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    quarantine = OUTPUT / "隔离" / "错误页"
    changed = 0

    for row in state.get("pages", {}).values():
        if row.get("status") != "success":
            continue
        relative = row.get("output_file", "")
        source = OUTPUT / relative if relative else None
        sample = str(row.get("title", ""))
        if source and source.exists():
            sample += "\n" + source.read_text(encoding="utf-8", errors="replace")[:1200]
        if not any(marker.lower() in sample.lower() for marker in MARKERS):
            continue

        if source and source.exists():
            target = quarantine / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            row["quarantined_file"] = str(target.relative_to(OUTPUT))
        row["status"] = "failed"
        row["error"] = f"质量复核：检测到站点错误页（{row.get('title', '')}）"
        row["output_file"] = ""
        changed += 1

    state["updated_at"] = now_iso()
    temp = STATE_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_PATH)
    print(f"已隔离并标记 {changed} 个错误页。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
