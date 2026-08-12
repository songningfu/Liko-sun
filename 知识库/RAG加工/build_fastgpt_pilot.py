from __future__ import annotations

import csv
import json
import shutil
import zipfile
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "output"
PILOT = ROOT / "FastGPT首轮导入包"
DOCS = PILOT / "01-上传文件"

SELECTED_IDS = {
    "SZU-00012", "SZU-00021", "SZU-00097", "SZU-00098", "SZU-00146", "SZU-00156",
    "SZU-00195", "SZU-00215", "SZU-00222", "SZU-00249", "SZU-00253", "SZU-00264",
    "SZU-00279", "SZU-00283", "SZU-00320", "SZU-00329", "SZU-00338", "SZU-00345",
    "SZU-00362", "SZU-00372", "SZU-00384", "SZU-00393", "SZU-00588", "SZU-00590",
    "SZU-00591", "SZU-00592", "SZU-00594", "SZU-00596", "SZU-00597", "SZU-00598",
    "SZU-00599", "SZU-00600",
}

TOPIC_FILES = {
    "新生报到": "01-新生报到与招生.md",
    "招生录取": "01-新生报到与招生.md",
    "宿舍住宿": "02-住宿与校园生活.md",
    "餐饮生活": "02-住宿与校园生活.md",
    "医疗心理": "02-住宿与校园生活.md",
    "奖助资助": "03-奖助学金与缴费.md",
    "校园卡": "04-校园卡与网络.md",
    "校园网络": "04-校园卡与网络.md",
    "课程教务": "05-教务与学籍.md",
    "考试成绩": "05-教务与学籍.md",
    "学籍证明": "05-教务与学籍.md",
    "图书馆": "06-图书馆服务.md",
    "交通地图": "07-交通安全与失物.md",
    "安全保卫": "07-交通安全与失物.md",
    "办事联系": "08-校园办事联系.md",
}


def read_manifest() -> list[dict[str, str]]:
    with (SOURCE / "document_manifest.csv").open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def read_chunks() -> list[dict]:
    return [
        json.loads(line)
        for line in (SOURCE / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_pilot() -> dict:
    if PILOT.exists():
        shutil.rmtree(PILOT)
    DOCS.mkdir(parents=True)

    manifest = read_manifest()
    selected = [row for row in manifest if row["document_id"] in SELECTED_IDS and row["status"] == "可导入"]
    missing = sorted(SELECTED_IDS - {row["document_id"] for row in selected})
    if missing:
        raise RuntimeError(f"首轮文档不存在或不可导入：{missing}")

    chunks = [chunk for chunk in read_chunks() if chunk["document_id"] in SELECTED_IDS]
    chunks_by_document: dict[str, list[dict]] = {}
    for chunk in chunks:
        chunks_by_document.setdefault(chunk["document_id"], []).append(chunk)

    grouped: dict[str, list[dict[str, str]]] = {}
    for row in selected:
        filename = TOPIC_FILES.get(row["topic"], "08-校园办事联系.md")
        grouped.setdefault(filename, []).append(row)

    for filename, rows in sorted(grouped.items()):
        parts = [
            f"# {Path(filename).stem[3:]}\n\n",
            "> 仅依据深圳大学官方公开资料整理。回答具体时间、费用、地点和电话时，应注明适用年份并附官方原文。\n",
        ]
        for row in sorted(rows, key=lambda item: item["title"]):
            parts.extend([
                "\n---\n\n",
                f"## {row['title']}\n\n",
                f"- 资料编号：{row['document_id']}\n",
                f"- 主题：{row['topic']}\n",
                f"- 适用校区：{row['campus']}\n",
                f"- 适用人群：{row['audience']}\n",
                f"- 时效标记：{row['freshness']}\n",
                f"- 官方原文：{row['source_url']}\n\n",
            ])
            for chunk in sorted(chunks_by_document.get(row["document_id"], []), key=lambda item: item["chunk_index"]):
                parts.append(chunk["content"].strip() + "\n\n")
        (DOCS / filename).write_text("".join(parts).strip() + "\n", encoding="utf-8")

    with (PILOT / "02-首轮资料清单.csv").open("w", encoding="utf-8-sig", newline="") as file:
        fields = [
            "document_id", "title", "topic", "campus", "audience", "freshness", "risk_level",
            "source_url", "chunk_count",
        ]
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in selected:
            writer.writerow({**row, "chunk_count": len(chunks_by_document.get(row["document_id"], []))})

    questions = list(csv.DictReader((SOURCE / "rag_test_questions.csv").open(encoding="utf-8-sig", newline="")))
    with (PILOT / "03-联调问题.csv").open("w", encoding="utf-8-sig", newline="") as file:
        fields = ["question_id", "question", "campus_requirement", "readiness", "source_1_title", "source_1_url"]
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(questions)

    guide = """# FastGPT 首轮操作说明

这个包用于验证第一版校园问答，不是最终全量知识库。

## 1. 创建知识库

1. 登录 FastGPT，进入“知识库”。
2. 新建知识库，名称填写：`Liko 深圳大学校园知识库 v1`。
3. 索引模型先使用平台默认配置即可。

## 2. 上传资料

进入本目录的 `01-上传文件`，将其中 7 个 Markdown 文件一起上传。

导入建议：

- 处理方式：按文本长度分块（chunk）。
- 分块长度：900；如果界面只能选择预设值，选最接近的 800～1000。
- 索引长度：512。
- 开启标题或文件名索引。
- 暂时不要开启“自动生成问答对”，避免额外费用和错误改写。

## 3. 创建问答应用

1. 新建“对话 Agent”，名称填写：`Liko 校园问答测试`。
2. 添加知识库搜索节点，选择刚创建的知识库。
3. 首轮召回数量设置为 6；如有重排模型，开启重排并最终保留 4 条引用。
4. AI 回复必须展示知识库引用来源。

系统提示词可先填写：

```text
你是深圳大学校园伴生助手 Liko。请只根据检索到的深圳大学官方资料回答校园事实问题。
先直接回答结论，再说明步骤、地点、时间和注意事项。
区分本科生与研究生，区分粤海校区与丽湖校区；资料没有明确说明时，不得自行推断。
涉及日期、费用、开放时间、联系电话或申请截止时间时，说明资料年份，并提醒用户以官方最新通知为准。
如果检索资料不足或互相冲突，明确说“暂未查到可靠的校内来源”，并给出对应官方入口，不要编造。
语言自然、简洁，避免“作为AI”等套话。
```

## 4. 测试

用 `03-联调问题.csv` 逐条测试。第一轮重点关注：

- 回答有没有引用官方来源。
- 是否误把研究生规则回答给本科生。
- 是否混淆粤海与丽湖校区。
- 旧资料有没有被当成当前规定。
- 没有资料时是否明确拒绝编造。

## 已知缺口

2026级本科新生入学须知和2026—2027学年校历尚未收录；当前快递点、校医院完整安排、社团招新等资料仍不足。遇到这些问题，正确行为是说明暂未查到可靠来源。
"""
    (PILOT / "README-操作说明.md").write_text(guide, encoding="utf-8")

    zip_path = ROOT / "FastGPT首轮导入包.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PILOT.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(PILOT.parent))

    return {
        "documents": len(selected),
        "chunks": len(chunks),
        "files": len(grouped),
        "topics": dict(Counter(row["topic"] for row in selected)),
        "questions": len(questions),
        "zip": str(zip_path),
    }


if __name__ == "__main__":
    print(json.dumps(write_pilot(), ensure_ascii=False, indent=2))
