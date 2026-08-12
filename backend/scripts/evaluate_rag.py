import argparse
import asyncio
import csv
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.fastgpt import FastGPTClient, FastGPTUpstreamError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
DEFAULT_QUESTIONS = (
    REPOSITORY_ROOT
    / "知识库"
    / "RAG加工"
    / "FastGPT首轮导入包"
    / "03-联调问题.csv"
)


def campus_from_requirement(requirement: str) -> str:
    has_yuehai = "粤海" in requirement
    has_lihu = "丽湖" in requirement
    if has_yuehai and not has_lihu:
        return "粤海"
    if has_lihu and not has_yuehai:
        return "丽湖"
    return "通用"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 Liko 首轮 FastGPT 问答评测")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int, default=0, help="仅运行前 N 条；0 表示全部")
    parser.add_argument("--delay", type=float, default=0.5, help="相邻问题间隔秒数")
    return parser.parse_args()


async def evaluate(args: argparse.Namespace) -> int:
    settings = Settings()
    if not settings.fastgpt_configured:
        print("FastGPT 尚未配置。请先在 backend/.env 填写 BASE_URL、API_KEY 和 APP_ID。")
        return 2
    if not args.questions.is_file():
        print(f"找不到评测问题文件：{args.questions}")
        return 2

    with args.questions.open("r", encoding="utf-8-sig", newline="") as source_file:
        rows = list(csv.DictReader(source_file))
    if args.limit > 0:
        rows = rows[: args.limit]

    output = args.output or (
        BACKEND_ROOT
        / "evaluation-output"
        / f"fastgpt-evaluation-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, str | int]] = []
    errors = 0
    client = FastGPTClient(settings)
    total = len(rows)
    for index, row in enumerate(rows, start=1):
        question_id = row.get("question_id", f"Q{index:03d}")
        question = (row.get("question") or "").strip()
        campus = campus_from_requirement(row.get("campus_requirement", ""))
        answer_parts: list[str] = []
        sources: list[dict] = []
        error_message = ""
        print(f"[{index}/{total}] {question_id} {question}")
        try:
            async for event in client.stream_chat(
                message=question,
                conversation_id=f"evaluation_{question_id}_{uuid4().hex}",
                answer_id=f"evaluation_answer_{uuid4().hex}",
                campus=campus,
            ):
                if event.event == "delta":
                    answer_parts.append(event.data.get("text", ""))
                elif event.event == "sources":
                    sources = event.data.get("items", [])
        except FastGPTUpstreamError as error:
            errors += 1
            error_message = str(error)

        answer = "".join(answer_parts).strip()
        status = "error" if error_message else "completed" if answer else "empty"
        results.append(
            {
                "question_id": question_id,
                "question": question,
                "campus": campus,
                "readiness": row.get("readiness", ""),
                "status": status,
                "answer": answer,
                "source_count": len(sources),
                "source_urls": " | ".join(item.get("url", "") for item in sources),
                "error": error_message,
            }
        )
        if index < total and args.delay > 0:
            await asyncio.sleep(args.delay)

    fieldnames = [
        "question_id",
        "question",
        "campus",
        "readiness",
        "status",
        "answer",
        "source_count",
        "source_urls",
        "error",
    ]
    with output.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    without_sources = sum(
        1 for row in results if row["status"] == "completed" and row["source_count"] == 0
    )
    print(f"评测完成：{total} 条，错误 {errors} 条，有答案但无官方来源 {without_sources} 条。")
    print(f"结果文件：{output}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(evaluate(parse_args())))
