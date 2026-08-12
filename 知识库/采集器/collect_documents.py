from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

import pdfplumber
from docx import Document


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "output_full"
USER_AGENT = "LikoCampusKnowledgeCollector/0.2 (+public academic sources; contact: project maintainer)"
ALLOWED_ROOT = "szu.edu.cn"
MAX_BYTES = 50 * 1024 * 1024


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_name(value: str, fallback: str) -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", unquote(value))
    value = re.sub(r"\s+", "_", value).strip(" ._")
    return value[:120] or fallback


def write_json_atomic(path: Path, data: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or (host != ALLOWED_ROOT and not host.endswith(f".{ALLOWED_ROOT}")):
        raise ValueError("只允许深圳大学官方 HTTPS 附件")


def download(url: str) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,*/*;q=0.5"})
    with urlopen(request, timeout=45) as response:  # noqa: S310 - URL is validated to szu.edu.cn
        validate_url(response.geturl())
        length = int(response.headers.get("Content-Length", "0") or 0)
        if length > MAX_BYTES:
            raise RuntimeError(f"附件超过 {MAX_BYTES // 1024 // 1024} MB 限制")
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise RuntimeError(f"附件超过 {MAX_BYTES // 1024 // 1024} MB 限制")
        return data, response.headers.get("Content-Type", "")


def normalize_pdf_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").splitlines():
        line = re.sub(r"[ \t]{2,}", " ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def pdf_to_markdown(path: Path, row: dict[str, Any], sha256: str, collected_at: str) -> tuple[str, int]:
    parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        for index, page in enumerate(pdf.pages, start=1):
            text = normalize_pdf_text(page.extract_text(layout=True) or page.extract_text() or "")
            if text:
                parts.append(f"## 第 {index} 页\n\n{text}")
    body = "\n\n".join(parts).strip()
    if len(body) < 40:
        raise RuntimeError("PDF 未提取到足够文字，可能是扫描件，需 OCR")
    metadata = {
        "site_id": row["site_id"],
        "url": row["url"],
        "found_on": row.get("found_on", ""),
        "collected_at": collected_at,
        "review_status": "附件原始文本，待清洗与人工核验",
        "sha256": sha256,
        "page_count": page_count,
    }
    frontmatter = ["---", *(f"{key}: {json.dumps(str(value), ensure_ascii=False)}" for key, value in metadata.items()), "---", ""]
    return "\n".join(frontmatter) + body + "\n", page_count


def docx_to_markdown(path: Path, row: dict[str, Any], sha256: str, collected_at: str) -> str:
    document = Document(path)
    parts: list[str] = []
    for paragraph in document.paragraphs:
        text = re.sub(r"\s+", " ", paragraph.text).strip()
        if text:
            parts.append(text)
    for index, table in enumerate(document.tables, start=1):
        rows: list[str] = []
        for cells in table.rows:
            values = [re.sub(r"\s+", " ", cell.text).strip().replace("|", "\\|") for cell in cells.cells]
            rows.append("| " + " | ".join(values) + " |")
        if rows:
            width = len(table.rows[0].cells)
            header = rows[0]
            divider = "| " + " | ".join(["---"] * width) + " |"
            parts.append(f"## 表格 {index}\n\n" + "\n".join([header, divider, *rows[1:]]))
    body = "\n\n".join(parts).strip()
    if len(body) < 20:
        raise RuntimeError("DOCX 未提取到足够文字")
    metadata = {
        "site_id": row["site_id"],
        "url": row["url"],
        "found_on": row.get("found_on", ""),
        "collected_at": collected_at,
        "review_status": "附件原始文本，待清洗、版面复核与人工核验",
        "sha256": sha256,
    }
    frontmatter = ["---", *(f"{key}: {json.dumps(str(value), ensure_ascii=False)}" for key, value in metadata.items()), "---", ""]
    return "\n".join(frontmatter) + body + "\n"


def run(output: Path, retry_failed: bool, reparse: bool, max_documents: int, found_on_regex: str) -> int:
    state_path = output / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    documents: dict[str, dict[str, Any]] = state.get("documents", {})
    raw_dir = output / "附件" / "原文件"
    text_dir = output / "附件" / "正文"
    raw_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)

    attempted = 0
    for key, row in documents.items():
        allowed_statuses = {"待下载解析"}
        if retry_failed:
            allowed_statuses.add("下载或解析失败")
        if reparse:
            allowed_statuses.add("已解析，待人工核验")
        if row.get("status") not in allowed_statuses:
            continue
        if found_on_regex and not re.search(found_on_regex, str(row.get("found_on", "")), flags=re.I):
            continue
        if max_documents > 0 and attempted >= max_documents:
            break
        attempted += 1
        collected_at = now_iso()
        try:
            validate_url(row["url"])
            data, content_type = download(row["url"])
            parsed = urlparse(row["url"])
            original_name = safe_name(Path(parsed.path).name, f"document_{attempted}")
            digest = hashlib.sha256(data).hexdigest()
            target = raw_dir / f"{row['site_id']}_{digest[:12]}_{original_name}"
            target.write_bytes(data)
            row.update(
                content_type=content_type,
                size_bytes=len(data),
                sha256=digest,
                downloaded_at=collected_at,
                raw_file=str(target.relative_to(output)),
                error="",
            )

            extension = target.suffix.lower()
            if data.startswith(b"%PDF-") or extension == ".pdf":
                markdown, page_count = pdf_to_markdown(target, row, digest, collected_at)
                text_target = text_dir / f"{target.stem}.md"
                text_target.write_text(markdown, encoding="utf-8")
                row.update(status="已解析，待人工核验", text_file=str(text_target.relative_to(output)), page_count=page_count)
            elif extension == ".docx" and data.startswith(b"PK"):
                markdown = docx_to_markdown(target, row, digest, collected_at)
                text_target = text_dir / f"{target.stem}.md"
                text_target.write_text(markdown, encoding="utf-8")
                row.update(status="已解析，待版面与人工核验", text_file=str(text_target.relative_to(output)))
            elif data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")) or extension == ".doc":
                row.update(status="已下载，待旧版Word解析", text_file="")
            else:
                row.update(status="已下载，待格式解析", text_file="")
        except Exception as exc:  # noqa: BLE001 - each item keeps its own failure
            row.update(status="下载或解析失败", error=str(exc)[:500], checked_at=collected_at)
        state["updated_at"] = now_iso()
        write_json_atomic(state_path, state)
        print(f"[{attempted}] {row['status']}: {row['url']}", flush=True)

    write_json_atomic(output / "documents.json", list(documents.values()))
    print(f"本次处理 {attempted} 个附件。", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="下载并解析采集队列中的公开官方附件。")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--reparse", action="store_true", help="重新下载并解析已经成功的 PDF。")
    parser.add_argument("--max-documents", type=int, default=20, help="本次最多处理的附件数；0 表示不限。")
    parser.add_argument("--found-on-regex", default="", help="只处理来源页匹配该正则表达式的附件。")
    args = parser.parse_args()
    return run(args.output, args.retry_failed, args.reparse, args.max_documents, args.found_on_regex)


if __name__ == "__main__":
    raise SystemExit(main())
