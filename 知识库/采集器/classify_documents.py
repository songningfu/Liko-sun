from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output_full"
GENERAL_RESOURCE_SOURCE = re.compile(r"/(?:learning/ai|research/|er/)|/node/17697$", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_json_atomic(path: Path, data: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def main() -> int:
    state_path = OUTPUT / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    changed = 0
    for row in state.get("documents", {}).values():
        if row.get("status") != "待下载解析":
            continue
        if not GENERAL_RESOURCE_SOURCE.search(str(row.get("found_on", ""))):
            continue
        row["status"] = "待范围审核（通用资源）"
        row["scope_note"] = "由图书馆通用学习、科研或数据库资源页发现，需确认与校园问答直接相关后再下载。"
        changed += 1
    state["updated_at"] = now_iso()
    write_json_atomic(state_path, state)
    write_json_atomic(OUTPUT / "documents.json", list(state.get("documents", {}).values()))
    print(f"已把 {changed} 个通用资源附件标记为待范围审核。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
