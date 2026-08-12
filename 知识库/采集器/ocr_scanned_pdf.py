from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "output_full"
DEFAULT_TMP = ROOT.parent.parent / "tmp" / "ocr"
DEFAULT_PDFTOPPM = (
    Path.home()
    / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_json_atomic(path: Path, data: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def result_to_rows(result: list[Any]) -> tuple[list[str], list[float]]:
    items: list[dict[str, Any]] = []
    scores: list[float] = []
    for box, text, score in result or []:
        clean = re.sub(r"\s+", " ", str(text)).strip()
        if not clean:
            continue
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        items.append(
            {
                "text": clean,
                "x": min(xs),
                "y": (min(ys) + max(ys)) / 2,
                "height": max(ys) - min(ys),
                "score": float(score),
            }
        )
        scores.append(float(score))
    if not items:
        return [], []

    typical_height = statistics.median(max(item["height"], 1.0) for item in items)
    tolerance = max(5.0, typical_height * 0.55)
    rows: list[list[dict[str, Any]]] = []
    for item in sorted(items, key=lambda value: (value["y"], value["x"])):
        if not rows:
            rows.append([item])
            continue
        current_y = statistics.mean(value["y"] for value in rows[-1])
        if abs(item["y"] - current_y) <= tolerance:
            rows[-1].append(item)
        else:
            rows.append([item])

    output = []
    for row in rows:
        ordered = sorted(row, key=lambda value: value["x"])
        output.append("\t".join(value["text"] for value in ordered))
    return output, scores


def ocr_in_bands(engine: RapidOCR, image_path: Path, band_height: int = 1000, overlap: int = 100) -> tuple[list[Any], list[Any]]:
    merged: list[Any] = []
    timings: list[Any] = []
    with Image.open(image_path) as image:
        width, height = image.size
        step = max(band_height - overlap, 1)
        starts = list(range(0, height, step))
        for band_index, top in enumerate(starts):
            bottom = min(top + band_height, height)
            crop = image.crop((0, top, width, bottom))
            result, elapsed = engine(np.asarray(crop.convert("RGB")))
            timings.append(elapsed)
            core_top = top if band_index == 0 else top + overlap / 2
            core_bottom = bottom if bottom == height else bottom - overlap / 2
            for box, text, score in result or []:
                global_box = [[float(point[0]), float(point[1]) + top] for point in box]
                center_y = statistics.mean(point[1] for point in global_box)
                if core_top <= center_y <= core_bottom:
                    merged.append([global_box, text, score])
            if bottom == height:
                break
    return merged, timings


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR 解析已下载的扫描版 PDF，并保留逐页表格行。")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--site-id", default="ZS")
    parser.add_argument("--sha-prefix", default="6edea1c9b57a")
    parser.add_argument("--pdftoppm", type=Path, default=DEFAULT_PDFTOPPM)
    args = parser.parse_args()

    state_path = args.output / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    candidates = [
        row
        for row in state.get("documents", {}).values()
        if row.get("site_id") == args.site_id and str(row.get("sha256", "")).startswith(args.sha_prefix)
    ]
    if len(candidates) != 1:
        raise RuntimeError(f"OCR 目标不唯一：找到 {len(candidates)} 个")
    row = candidates[0]
    pdf_path = args.output / row["raw_file"]
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    if not args.pdftoppm.exists():
        raise FileNotFoundError(f"找不到 pdftoppm：{args.pdftoppm}")

    preview_dir = DEFAULT_TMP / row["sha256"][:12]
    if preview_dir.exists():
        shutil.rmtree(preview_dir)
    preview_dir.mkdir(parents=True, exist_ok=True)
    prefix = preview_dir / "page"
    completed = subprocess.run(
        [str(args.pdftoppm), "-png", "-r", "160", str(pdf_path), str(prefix)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(f"PDF 渲染失败：{completed.stderr[-500:]}")
    images = sorted(preview_dir.glob("page-*.png"), key=lambda path: int(path.stem.rsplit("-", 1)[-1]))
    if not images:
        raise RuntimeError("PDF 渲染后没有页面图片")

    engine = RapidOCR()
    sections: list[str] = []
    all_scores: list[float] = []
    total_items = 0
    for index, image_path in enumerate(images, start=1):
        result, elapsed = ocr_in_bands(engine, image_path)
        lines, scores = result_to_rows(result or [])
        if not lines:
            raise RuntimeError(f"第 {index} 页没有识别到文字")
        total_items += len(result or [])
        all_scores.extend(scores)
        sections.append(f"## 第 {index} 页\n\n```tsv\n" + "\n".join(lines) + "\n```")
        print(f"第 {index}/{len(images)} 页：{len(result or [])} 个文本块，耗时 {elapsed}", flush=True)

    avg_score = statistics.mean(all_scores) if all_scores else 0.0
    frontmatter = [
        "---",
        f"site_id: {json.dumps(str(row['site_id']), ensure_ascii=False)}",
        f"url: {json.dumps(str(row['url']), ensure_ascii=False)}",
        f"found_on: {json.dumps(str(row.get('found_on', '')), ensure_ascii=False)}",
        f"sha256: {json.dumps(str(row['sha256']), ensure_ascii=False)}",
        f"page_count: {json.dumps(str(len(images)), ensure_ascii=False)}",
        'extraction_method: "RapidOCR local OCR"',
        'review_status: "OCR原始表格，专业代码、计划数和排名必须人工核验"',
        f"ocr_average_confidence: {json.dumps(f'{avg_score:.4f}')}",
        "---",
        "",
    ]
    text_dir = args.output / "附件" / "正文"
    text_dir.mkdir(parents=True, exist_ok=True)
    target = text_dir / f"{pdf_path.stem}_OCR.md"
    target.write_text("\n".join(frontmatter) + "\n\n".join(sections) + "\n", encoding="utf-8")
    row.update(
        status="OCR已解析，待人工核验",
        text_file=str(target.relative_to(args.output)),
        page_count=len(images),
        ocr_engine="rapidocr_onnxruntime",
        ocr_average_confidence=round(avg_score, 4),
        ocr_items=total_items,
        error="",
    )
    state["updated_at"] = now_iso()
    write_json_atomic(state_path, state)
    write_json_atomic(args.output / "documents.json", list(state.get("documents", {}).values()))
    print(f"OCR 完成：{target}；平均置信度 {avg_score:.4f}。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
