from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "采集器" / "output_full"
WEB_ROOT = RAW_ROOT / "网页"
ATTACHMENT_ROOT = RAW_ROOT / "附件" / "正文"
CURATED_ROOTS = [
    ROOT / "采集器" / "output" / "原始网页",
    ROOT / "采集器" / "output_targeted" / "原始网页",
]
QUESTION_FILE = ROOT / "首批问题清单-v1.md"
OUT_ROOT = Path(__file__).resolve().parent / "output"
CLEAN_ROOT = OUT_ROOT / "clean_documents"
FASTGPT_ROOT = OUT_ROOT / "fastgpt_markdown"

GENERATED_AT = datetime.now().astimezone().isoformat(timespec="seconds")

SITE_NAMES = {
    "BAOWEI": "安全保卫部",
    "GRA": "研究生院",
    "HQB": "后勤保障部",
    "IT": "信息中心",
    "JWB": "教务部",
    "LIB": "图书馆",
    "XSB": "学生部",
    "YZ": "研究生招生网",
    "ZS": "本科招生网",
}

CATEGORY_KEYWORDS = [
    ("新生报到", ("新生", "报到", "入学须知", "迎新")),
    ("宿舍住宿", ("宿舍", "住宿", "入住", "宿管", "电费")),
    ("校园卡", ("校园卡", "一卡通", "挂失", "补卡", "充值")),
    ("校园网络", ("校园网", "网络", "Wi-Fi", "无线网络", "上网")),
    ("课程教务", ("选课", "退课", "加课", "课程", "上课时间", "教学周")),
    ("考试成绩", ("考试", "成绩", "缓考", "补考", "重修")),
    ("学籍证明", ("学籍", "成绩单", "在读证明", "转专业", "毕业", "学位")),
    ("奖助资助", ("奖学金", "助学金", "资助", "困难认定", "绿色通道", "勤工助学")),
    ("图书馆", ("图书馆", "借书", "还书", "阅览", "数据库", "电子资源")),
    ("餐饮生活", ("食堂", "餐厅", "餐饮", "美食", "快递", "商铺")),
    ("交通地图", ("校车", "交通", "乘车", "校区", "地图")),
    ("医疗心理", ("校医院", "医院", "就诊", "心理", "健康")),
    ("安全保卫", ("保卫", "安全", "户口", "消防", "失物", "诈骗")),
    ("办事联系", ("办事", "服务指南", "联系电话", "联系我们", "办公地点")),
    ("招生录取", ("招生", "录取", "专业", "志愿", "考生")),
    ("校园活动", ("活动", "讲座", "社团", "比赛", "社区")),
]

HIGH_RISK_TERMS = (
    "收费", "费用", "学费", "住宿费", "违约金", "赔偿", "招生计划", "专业代码",
    "录取", "报到日期", "开放时间", "校车时刻", "联系电话", "奖学金", "处分",
)

QUESTION_CATEGORY_BONUS = {
    "Q001": ("新生报到", "招生录取"), "Q002": ("新生报到", "招生录取"),
    "Q003": ("新生报到", "招生录取", "办事联系"), "Q004": ("宿舍住宿",),
    "Q005": ("宿舍住宿",), "Q006": ("宿舍住宿", "办事联系"),
    "Q007": ("交通地图",), "Q008": ("交通地图",), "Q009": ("校园卡",),
    "Q010": ("校园网络",), "Q011": ("办事联系", "新生报到"),
    "Q012": ("奖助资助",), "Q013": ("餐饮生活",), "Q014": ("餐饮生活",),
    "Q015": ("医疗心理",), "Q016": ("图书馆",), "Q017": ("课程教务",),
    "Q018": ("办事联系",), "Q019": ("课程教务",), "Q020": ("课程教务",),
    "Q021": ("课程教务",), "Q022": ("课程教务", "学籍证明"),
    "Q023": ("学籍证明",), "Q024": ("课程教务",), "Q025": ("考试成绩",),
    "Q026": ("奖助资助",), "Q027": ("课程教务", "校园活动"),
    "Q028": ("办事联系",), "Q029": ("学籍证明",), "Q030": ("安全保卫", "办事联系"),
    "Q031": ("办事联系",), "Q032": ("奖助资助",), "Q033": ("校园活动",),
    "Q034": ("安全保卫",),
}

BOILERPLATE_MARKERS = (
    "您当前所在的位置", "当前位置：", "当前位置:", "导航菜单", "事务导航：",
    "版权所有", "copyright", "网站地图", "返回顶部", "访问量：", "打印本页",
)

STOP_TEXT = {
    "首页", "正文", "更多", "详情", "通知公告", "新闻动态", "网站首页", "English",
    "ENG", "关于我们", "部门概况", "机构设置", "党建工作", "下载专区", "相关链接",
}

QUESTION_HINTS = {
    "Q001": ("新生", "报到", "流程", "温馨提示"),
    "Q002": ("新生", "报到", "材料", "证件", "入学须知"),
    "Q003": ("报到", "请假", "不能按时", "联系"),
    "Q004": ("宿舍", "住宿安排", "入住", "新生"),
    "Q005": ("宿舍", "床", "供电", "住宿管理"),
    "Q006": ("宿舍", "电费", "缴费", "后勤"),
    "Q007": ("粤海校区", "丽湖校区", "交通", "到达"),
    "Q008": ("校车", "时刻", "乘车点", "班车"),
    "Q009": ("校园卡", "充值", "挂失", "补办"),
    "Q010": ("校园网", "开通", "续费", "网络服务"),
    "Q011": ("学费", "住宿费", "缴费", "财务"),
    "Q012": ("奖助学金", "绿色通道", "困难认定", "资助"),
    "Q013": ("食堂", "开放时间", "支付", "餐饮"),
    "Q014": ("快递", "开放时间", "快递点"),
    "Q015": ("校医院", "就诊", "开放时间"),
    "Q016": ("图书馆", "入馆", "借书", "电子资源"),
    "Q017": ("校历", "教学周", "开学", "放假"),
    "Q018": ("办事", "服务指南", "部门", "入口"),
    "Q019": ("选课", "选不上", "原因", "教务"),
    "Q020": ("加课", "退课", "申请", "选课"),
    "Q021": ("慕课", "MOOC", "学分", "选课"),
    "Q022": ("培养方案", "毕业要求", "专业"),
    "Q023": ("转专业", "申请", "条件"),
    "Q024": ("通识学分", "思政学分", "拓展学分", "认定"),
    "Q025": ("成绩", "缓考", "补考", "重修"),
    "Q026": ("奖学金", "评定", "申请", "材料"),
    "Q027": ("空教室", "运动场", "场地", "预约"),
    "Q028": ("公文通", "校园公告", "通知"),
    "Q029": ("学籍证明", "成绩单", "申请"),
    "Q030": ("档案", "户口", "党团关系"),
    "Q031": ("学院", "辅导员", "联系方式", "办公室"),
    "Q032": ("就业去向", "勤工助学", "岗位"),
    "Q033": ("校园活动", "社团", "招新"),
    "Q034": ("失物招领", "遗失", "保卫"),
}


@dataclass
class Document:
    source_path: Path
    site_id: str
    site_name: str
    category: str
    title: str
    url: str
    collected_at: str
    raw_body: str
    clean_body: str = ""
    topic: str = "其他校务"
    campus: str = "未注明"
    audience: str = "通用"
    document_type: str = "说明"
    freshness: str = "未注明日期"
    latest_year: int | None = None
    risk_level: str = "普通"
    priority: str = "P2"
    status: str = "待处理"
    reason: str = ""
    canonical_id: str = ""
    duplicate_of: str = ""
    content_hash: str = ""
    chunks: list[dict] = field(default_factory=list)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, parts[2].lstrip()


def normalize_line(line: str) -> str:
    line = re.sub(r"!\[[^]]*]\([^)]*\)", "", line)
    line = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", line)
    line = re.sub(r"<https?://[^>]+>", "", line)
    line = re.sub(r"https?://\S+", "", line)
    line = re.sub(r"^[\s>*#`~_\-+•·]+", "", line)
    line = re.sub(r"\s+", " ", line).strip().lower()
    return line


def document_line_frequencies(paths: list[Path]) -> dict[str, Counter[str]]:
    site_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        meta, body = parse_frontmatter(text)
        site = meta.get("site_id") or path.parent.name
        unique = {normalize_line(line) for line in body.splitlines()}
        for line in unique:
            if 2 <= len(line) <= 180:
                site_counts[site][line] += 1
    return site_counts


def tidy_markdown_line(line: str) -> str:
    line = line.replace("\u200b", "").replace("\ufeff", "")
    line = re.sub(r"!\[([^]]*)]\([^)]*\)", "", line)
    line = re.sub(r'\[\s*]\([^)]*\)(?:\s*"[^"]*")?', "", line)
    line = re.sub(r"[ \t]+", " ", line).strip()
    line = re.sub(r"^#{4,}\s*", "### ", line)
    return line


def is_low_information_link(line: str) -> bool:
    match = re.fullmatch(r'\s*(?:[-*+]\s*)?\[([^]]+)]\([^)]*\)(?:\s*"[^"]*")?\s*', line)
    return bool(match and match.group(1).strip() in STOP_TEXT)


def clean_body(doc: Document, line_freq: dict[str, Counter[str]], site_doc_count: int) -> str:
    output: list[str] = []
    previous = ""
    frequency_cutoff = max(5, math.ceil(site_doc_count * 0.18))
    in_fence = False
    for original in doc.raw_body.splitlines():
        line = tidy_markdown_line(original)
        if line.startswith("```"):
            in_fence = not in_fence
            output.append(line)
            previous = line
            continue
        normalized = normalize_line(line)
        if not in_fence:
            if any(marker.lower() in normalized for marker in BOILERPLATE_MARKERS):
                continue
            if normalized and line_freq.get(doc.site_id, Counter())[normalized] >= frequency_cutoff:
                continue
            if is_low_information_link(line):
                continue
            if re.fullmatch(r"[-=_*#\s]+", line or " "):
                continue
            if re.match(r"^\s*(?:访问量|浏览次数|发布时间|发布者|作者)\s*[：:]\s*$", line, re.I):
                continue
        if not line:
            if output and output[-1] != "":
                output.append("")
            continue
        if line == previous:
            continue
        output.append(line)
        previous = line

    body = "\n".join(output).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = re.sub(r"(?m)^\s*[-*]\s*$", "", body)
    return body.strip()


def plain_text(markdown: str) -> str:
    text = re.sub(r"```.*?```", " ", markdown, flags=re.S)
    text = re.sub(r"!\[[^]]*]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[#>*_`~|\-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def classify_topic(text: str, fallback: str) -> str:
    scores = []
    for topic, keywords in CATEGORY_KEYWORDS:
        score = sum(text.lower().count(word.lower()) for word in keywords)
        scores.append((score, topic))
    best_score, best_topic = max(scores)
    return best_topic if best_score else "其他校务"


def classify_campus(text: str) -> str:
    has_yuehai = "粤海" in text
    has_lihu = "丽湖" in text
    if has_yuehai and has_lihu:
        return "粤海/丽湖"
    if has_yuehai:
        return "粤海校区"
    if has_lihu:
        return "丽湖校区"
    if "沧海" in text:
        return "沧海校区相关"
    return "未注明"


def classify_audience(text: str) -> str:
    if "研究生" in text and "本科" in text:
        return "本科生/研究生"
    if "研究生" in text:
        return "研究生"
    if "本科" in text or "本科生" in text:
        return "本科生"
    if "新生" in text:
        return "新生"
    if "教职工" in text and "学生" not in text:
        return "教职工"
    return "通用"


def classify_type(title: str, text: str) -> str:
    value = title + " " + text[:600]
    if any(k in value for k in ("办法", "规定", "条例", "守则", "制度", "章程")):
        return "规章制度"
    if any(k in value for k in ("指南", "流程", "如何", "办理", "服务")):
        return "办事指南"
    if any(k in value for k in ("通知", "公告", "公示", "提示")):
        return "通知公告"
    if any(k in value for k in ("常见问题", "问答", "FAQ")):
        return "常见问答"
    if any(k in value for k in ("新闻", "活动", "顺利举行", "召开")):
        return "新闻活动"
    if "招生" in value:
        return "招生信息"
    return "说明"


def assess_freshness(title: str, text: str) -> tuple[str, int | None]:
    years = [int(y) for y in re.findall(r"(?<!\d)(20(?:1\d|2\d))(?!\d)", title + "\n" + text)]
    if not years:
        return "未注明日期", None
    latest = max(years)
    if latest >= 2026:
        return "2026及以后", latest
    if latest == 2025:
        return "2025参考", latest
    if latest >= 2023:
        return "较早资料", latest
    return "历史资料", latest


def is_english_dominant(text: str) -> bool:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    letters = len(re.findall(r"[A-Za-z]", text))
    return letters > 250 and chinese / max(chinese + letters, 1) < 0.12


def canonical_hash(text: str) -> str:
    normalized = plain_text(text).lower()
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"[，。！？；：、,.!?;:'\"“”‘’（）()\[\]{}<>《》]", "", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def split_long_piece(piece: str, limit: int) -> list[str]:
    if len(piece) <= limit:
        return [piece]
    parts = re.split(r"(?<=[。！？；\n])", piece)
    result: list[str] = []
    buffer = ""
    for part in parts:
        if len(buffer) + len(part) <= limit:
            buffer += part
        else:
            if buffer.strip():
                result.append(buffer.strip())
            while len(part) > limit:
                result.append(part[:limit].strip())
                part = part[limit:]
            buffer = part
    if buffer.strip():
        result.append(buffer.strip())
    return result


def chunk_markdown(doc: Document, target: int = 900, hard_limit: int = 1350) -> list[dict]:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", doc.clean_body) if b.strip()]
    chunks: list[str] = []
    buffer = ""
    current_heading = ""
    for block in blocks:
        if block.startswith("#"):
            current_heading = block.splitlines()[0].strip("# ")
        pieces = split_long_piece(block, hard_limit)
        for piece in pieces:
            candidate = f"{buffer}\n\n{piece}".strip() if buffer else piece
            if buffer and len(candidate) > target:
                chunks.append(buffer.strip())
                prefix = f"### {current_heading}\n\n" if current_heading and not piece.startswith("#") else ""
                buffer = prefix + piece
            else:
                buffer = candidate
    if buffer.strip():
        chunks.append(buffer.strip())

    result = []
    total = len(chunks)
    for index, content in enumerate(chunks, 1):
        chunk_id = f"{doc.canonical_id}-C{index:03d}"
        result.append({
            "chunk_id": chunk_id,
            "document_id": doc.canonical_id,
            "chunk_index": index,
            "chunk_total": total,
            "title": doc.title,
            "content": content,
            "source_url": doc.url,
            "site_id": doc.site_id,
            "site_name": doc.site_name,
            "category": doc.category,
            "topic": doc.topic,
            "campus": doc.campus,
            "audience": doc.audience,
            "document_type": doc.document_type,
            "freshness": doc.freshness,
            "latest_year": doc.latest_year,
            "risk_level": doc.risk_level,
            "priority": doc.priority,
        })
    return result


def load_documents() -> tuple[list[Document], dict[str, int]]:
    web_paths = sorted(WEB_ROOT.rglob("*.md"))
    attachment_paths = sorted(ATTACHMENT_ROOT.glob("*.md")) if ATTACHMENT_ROOT.exists() else []
    curated_paths = [path for root in CURATED_ROOTS if root.exists() for path in sorted(root.glob("*.md"))]
    paths = web_paths + attachment_paths + curated_paths
    site_counts = Counter()
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        meta, _ = parse_frontmatter(text)
        site_counts[meta.get("site_id") or path.parent.name] += 1
    line_freq = document_line_frequencies(web_paths + curated_paths)

    docs: list[Document] = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        meta, body = parse_frontmatter(text)
        site_id = meta.get("site_id") or path.parent.name
        title = meta.get("title") or re.sub(r"^[A-Z]+_[0-9a-f]+_", "", path.stem)
        doc = Document(
            source_path=path,
            site_id=site_id,
            site_name=meta.get("site_name") or SITE_NAMES.get(site_id, site_id),
            category=meta.get("category") or "附件资料",
            title=title,
            url=meta.get("url") or meta.get("found_on") or "",
            collected_at=meta.get("collected_at") or "",
            raw_body=body,
        )
        doc.clean_body = clean_body(doc, line_freq, site_counts[site_id])
        text_for_classification = f"{doc.title}\n{plain_text(doc.clean_body)}"
        doc.topic = classify_topic(text_for_classification, doc.category)
        doc.campus = classify_campus(text_for_classification)
        doc.audience = classify_audience(text_for_classification)
        doc.document_type = classify_type(doc.title, doc.clean_body)
        doc.freshness, doc.latest_year = assess_freshness(doc.title, doc.clean_body)
        doc.risk_level = "需注意时效" if any(term in text_for_classification for term in HIGH_RISK_TERMS) else "普通"
        doc.content_hash = canonical_hash(doc.clean_body)
        docs.append(doc)
    return docs, dict(site_counts)


def decide_statuses(docs: list[Document]) -> None:
    hash_owner: dict[str, Document] = {}
    next_id = 1
    for doc in docs:
        text = plain_text(doc.clean_body)
        doc.canonical_id = f"SZU-{next_id:05d}"
        next_id += 1
        link_lines = len(re.findall(r"(?m)^\s*[-*+]\s+\[[^]]+]\([^)]*\)", doc.clean_body))
        text_lines = len([line for line in doc.clean_body.splitlines() if len(normalize_line(line)) >= 12])

        hostname = (urlparse(doc.url).hostname or "").lower()
        if not hostname.endswith("szu.edu.cn"):
            doc.status, doc.reason = "技术排除", "缺少可验证的深圳大学官方来源链接"
            continue
        # 校园卡、失物招领等官方办事页可能非常简短，但依然包含关键地点、电话和时间。
        # 只排除几乎没有可读内容的页面，避免误删短而有用的服务说明。
        if len(text) < 80:
            doc.status, doc.reason = "技术排除", "清洗后有效正文过短"
            continue
        if text_lines >= 8 and link_lines / text_lines > 0.75 and len(text) < 1200:
            doc.status, doc.reason = "技术排除", "内容以导航链接为主"
            continue
        if is_english_dominant(text) and doc.site_id == "LIB":
            doc.status, doc.reason = "辅助资料", "图书馆英文镜像，中文问答默认不导入"
            continue
        if doc.content_hash in hash_owner:
            owner = hash_owner[doc.content_hash]
            doc.status, doc.reason = "重复排除", "与已保留页面正文相同"
            doc.duplicate_of = owner.canonical_id
            continue
        hash_owner[doc.content_hash] = doc
        doc.status, doc.reason = "可导入", "官方公开页面，已完成技术清洗"

        value_text = doc.title + " " + text[:2000]
        if doc.document_type in {"办事指南", "规章制度", "常见问答"}:
            doc.priority = "P0"
        elif any(term in value_text for term in ("新生", "宿舍", "校园卡", "校园网", "图书馆", "校历", "选课")):
            doc.priority = "P0"
        elif doc.document_type in {"通知公告", "招生信息", "说明"}:
            doc.priority = "P1"
        else:
            doc.priority = "P2"
        doc.chunks = chunk_markdown(doc)


def safe_filename(value: str, limit: int = 70) -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return (value[:limit] or "未命名").strip()


def prepare_output_dirs() -> None:
    for directory in (OUT_ROOT, CLEAN_ROOT, FASTGPT_ROOT):
        directory.mkdir(parents=True, exist_ok=True)
    for directory in (CLEAN_ROOT, FASTGPT_ROOT):
        for path in directory.glob("*.md"):
            path.unlink()
    for path in OUT_ROOT.glob("*.csv"):
        path.unlink()
    for path in OUT_ROOT.glob("*.jsonl"):
        path.unlink()


def write_csv(path: Path, headers: list[str], rows: Iterable[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_documents(docs: list[Document]) -> None:
    for doc in docs:
        if doc.status != "可导入":
            continue
        header = (
            f"---\n"
            f"document_id: \"{doc.canonical_id}\"\n"
            f"title: \"{doc.title.replace(chr(34), chr(39))}\"\n"
            f"source_url: \"{doc.url}\"\n"
            f"source_department: \"{doc.site_name}\"\n"
            f"category: \"{doc.category}\"\n"
            f"topic: \"{doc.topic}\"\n"
            f"campus: \"{doc.campus}\"\n"
            f"audience: \"{doc.audience}\"\n"
            f"freshness: \"{doc.freshness}\"\n"
            f"risk_level: \"{doc.risk_level}\"\n"
            f"priority: \"{doc.priority}\"\n"
            f"processed_at: \"{GENERATED_AT}\"\n"
            f"---\n\n"
        )
        filename = f"{doc.canonical_id}_{safe_filename(doc.title)}.md"
        (CLEAN_ROOT / filename).write_text(header + f"# {doc.title}\n\n" + doc.clean_body + "\n", encoding="utf-8")

    grouped: dict[str, list[Document]] = defaultdict(list)
    for doc in docs:
        if doc.status == "可导入":
            grouped[f"{doc.priority}-{doc.topic}"].append(doc)
    for group_name, group_docs in sorted(grouped.items()):
        parts = [
            f"# 深圳大学知识库：{group_name}\n",
            "> 以下资料均来自深圳大学官方公开网页。回答时应显示原文链接，并优先使用年份更新、校区和人群匹配的资料。\n",
        ]
        for doc in group_docs:
            parts.extend([
                "\n---\n",
                f"## {doc.title}\n",
                f"- 来源部门：{doc.site_name}\n",
                f"- 适用校区：{doc.campus}\n",
                f"- 适用人群：{doc.audience}\n",
                f"- 时效标记：{doc.freshness}\n",
                f"- 官方原文：{doc.url}\n",
                f"- 文档编号：{doc.canonical_id}\n\n",
                doc.clean_body,
                "\n",
            ])
        filename = safe_filename(group_name) + ".md"
        (FASTGPT_ROOT / filename).write_text("".join(parts), encoding="utf-8")


def char_ngrams(text: str, n: int = 2) -> set[str]:
    text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text.lower())
    return {text[i:i+n] for i in range(max(0, len(text) - n + 1))}


def source_score(question_id: str, question: str, doc: Document) -> float:
    hints = QUESTION_HINTS.get(question_id, ())
    title_text = doc.title.lower()
    body_text = plain_text(doc.clean_body[:5000]).lower()
    score = 0.0
    matched_hints: list[str] = []
    for hint in hints:
        hint_lower = hint.lower()
        title_hit = hint_lower in title_text
        body_count = body_text.count(hint_lower)
        if title_hit or body_count:
            matched_hints.append(hint_lower)
        score += 12 if title_hit else 0
        score += min(body_count, 3) * 2.5
    question_bigrams = char_ngrams(question)
    title_overlap = len(question_bigrams & char_ngrams(doc.title))
    body_overlap = len(question_bigrams & char_ngrams(body_text[:1500]))
    score += title_overlap * 1.7 + min(body_overlap, 12) * 0.25
    if matched_hints and doc.topic in QUESTION_CATEGORY_BONUS.get(question_id, ()):
        score += 10
    elif doc.site_id == "LIB" and question_id not in {"Q010", "Q016", "Q027", "Q034"}:
        score -= 5
    if doc.priority == "P0":
        score += 1.5
    if doc.freshness == "2026及以后":
        score += 1
    # 只有一个泛化词命中时（如“材料”“时间”“申请”），不可把页面判成强来源。
    if len(hints) >= 3 and len(matched_hints) < 2:
        score *= 0.45
    # 候选来源必须至少覆盖问题中的一个核心实体，避免“开放时间”把图书馆误配给食堂或校医院。
    generic = {"时间", "开放时间", "申请", "流程", "联系", "材料", "入口", "条件", "原因", "官方"}
    core_hints = {hint.lower() for hint in hints if hint.lower() not in generic}
    matched_core = core_hints.intersection(matched_hints)
    if not matched_core:
        score = min(score, 8.0)
    return round(score, 2)


def load_questions() -> list[dict]:
    text = QUESTION_FILE.read_text(encoding="utf-8")
    questions = []
    for line in text.splitlines():
        match = re.match(r"\|\s*(Q\d{3})\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|", line)
        if match:
            questions.append({
                "question_id": match.group(1),
                "question": match.group(2),
                "campus_requirement": match.group(3),
                "expected_department": match.group(4),
            })
    return questions


def build_question_rows(questions: list[dict], docs: list[Document]) -> list[dict]:
    candidates = [doc for doc in docs if doc.status == "可导入"]
    rows = []
    for question in questions:
        ranked = sorted(
            ((source_score(question["question_id"], question["question"], doc), doc) for doc in candidates),
            key=lambda item: item[0],
            reverse=True,
        )[:3]
        top_score = ranked[0][0] if ranked else 0
        if top_score >= 24:
            readiness = "已有较强候选来源"
        elif top_score >= 14:
            readiness = "已有候选，需问答联调确认"
        else:
            readiness = "当前来源不足，继续采集"
        row = {**question, "readiness": readiness, "top_score": top_score}
        for index in range(3):
            if index < len(ranked):
                score, doc = ranked[index]
                row[f"source_{index + 1}_title"] = doc.title
                row[f"source_{index + 1}_url"] = doc.url
                row[f"source_{index + 1}_score"] = score
            else:
                row[f"source_{index + 1}_title"] = ""
                row[f"source_{index + 1}_url"] = ""
                row[f"source_{index + 1}_score"] = ""
        rows.append(row)
    return rows


def write_outputs(docs: list[Document], site_counts: dict[str, int]) -> dict:
    prepare_output_dirs()
    write_documents(docs)

    manifest_headers = [
        "document_id", "status", "reason", "duplicate_of", "title", "source_url", "site_id",
        "site_name", "category", "topic", "campus", "audience", "document_type", "freshness",
        "latest_year", "risk_level", "priority", "raw_chars", "clean_chars", "chunk_count",
        "source_path", "content_hash",
    ]
    manifest_rows = []
    for doc in docs:
        manifest_rows.append({
            "document_id": doc.canonical_id,
            "status": doc.status,
            "reason": doc.reason,
            "duplicate_of": doc.duplicate_of,
            "title": doc.title,
            "source_url": doc.url,
            "site_id": doc.site_id,
            "site_name": doc.site_name,
            "category": doc.category,
            "topic": doc.topic,
            "campus": doc.campus,
            "audience": doc.audience,
            "document_type": doc.document_type,
            "freshness": doc.freshness,
            "latest_year": doc.latest_year or "",
            "risk_level": doc.risk_level,
            "priority": doc.priority,
            "raw_chars": len(doc.raw_body),
            "clean_chars": len(doc.clean_body),
            "chunk_count": len(doc.chunks),
            "source_path": str(doc.source_path),
            "content_hash": doc.content_hash,
        })
    write_csv(OUT_ROOT / "document_manifest.csv", manifest_headers, manifest_rows)

    chunks = [chunk for doc in docs for chunk in doc.chunks]
    with (OUT_ROOT / "chunks.jsonl").open("w", encoding="utf-8") as file:
        for chunk in chunks:
            file.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    fastgpt_rows = []
    for chunk in chunks:
        source_note = (
            f"\n\n---\n来源：{chunk['site_name']}\n"
            f"官方原文：{chunk['source_url']}\n"
            f"适用校区：{chunk['campus']}；适用人群：{chunk['audience']}；时效：{chunk['freshness']}"
        )
        fastgpt_rows.append({
            "q": f"{chunk['title']}｜{chunk['topic']}",
            "a": chunk["content"] + source_note,
            "document_id": chunk["document_id"],
            "chunk_id": chunk["chunk_id"],
            "source_url": chunk["source_url"],
            "topic": chunk["topic"],
            "campus": chunk["campus"],
            "audience": chunk["audience"],
            "freshness": chunk["freshness"],
        })
    write_csv(
        OUT_ROOT / "fastgpt_chunks.csv",
        ["q", "a", "document_id", "chunk_id", "source_url", "topic", "campus", "audience", "freshness"],
        fastgpt_rows,
    )

    question_rows = build_question_rows(load_questions(), docs)
    question_headers = [
        "question_id", "question", "campus_requirement", "expected_department", "readiness", "top_score",
        "source_1_title", "source_1_url", "source_1_score", "source_2_title", "source_2_url",
        "source_2_score", "source_3_title", "source_3_url", "source_3_score",
    ]
    write_csv(OUT_ROOT / "rag_test_questions.csv", question_headers, question_rows)

    status_counts = Counter(doc.status for doc in docs)
    priority_counts = Counter(doc.priority for doc in docs if doc.status == "可导入")
    topic_counts = Counter(doc.topic for doc in docs if doc.status == "可导入")
    freshness_counts = Counter(doc.freshness for doc in docs if doc.status == "可导入")
    risk_counts = Counter(doc.risk_level for doc in docs if doc.status == "可导入")
    readiness_counts = Counter(row["readiness"] for row in question_rows)
    raw_chars = sum(len(doc.raw_body) for doc in docs)
    clean_chars = sum(len(doc.clean_body) for doc in docs if doc.status == "可导入")
    chunk_lengths = [len(chunk["content"]) for chunk in chunks]
    report = {
        "generated_at": GENERATED_AT,
        "source_documents": len(docs),
        "site_counts": site_counts,
        "status_counts": dict(status_counts),
        "priority_counts": dict(priority_counts),
        "topic_counts": dict(topic_counts),
        "freshness_counts": dict(freshness_counts),
        "risk_counts": dict(risk_counts),
        "raw_chars": raw_chars,
        "clean_chars": clean_chars,
        "clean_ratio": round(clean_chars / raw_chars, 4) if raw_chars else 0,
        "chunks": len(chunks),
        "chunk_min": min(chunk_lengths) if chunk_lengths else 0,
        "chunk_median": sorted(chunk_lengths)[len(chunk_lengths) // 2] if chunk_lengths else 0,
        "chunk_max": max(chunk_lengths) if chunk_lengths else 0,
        "test_questions": len(question_rows),
        "test_readiness": dict(readiness_counts),
    }
    (OUT_ROOT / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def write_report(report: dict) -> None:
    def table(counter: dict) -> str:
        return "\n".join(f"| {key} | {value} |" for key, value in sorted(counter.items(), key=lambda x: (-x[1], x[0])))

    report_text = f"""# 深圳大学 RAG 数据加工报告 v1

生成时间：{report['generated_at']}

## 结果概览

| 指标 | 数量 |
| --- | ---: |
| 输入资料 | {report['source_documents']} |
| 可导入资料 | {report['status_counts'].get('可导入', 0)} |
| 技术排除 | {report['status_counts'].get('技术排除', 0)} |
| 完全重复排除 | {report['status_counts'].get('重复排除', 0)} |
| 英文辅助资料 | {report['status_counts'].get('辅助资料', 0)} |
| RAG 分块 | {report['chunks']} |
| 清洗前字符数 | {report['raw_chars']:,} |
| 可导入清洗后字符数 | {report['clean_chars']:,} |
| 清洗保留比例 | {report['clean_ratio']:.1%} |
| 分块长度（最小/中位/最大） | {report['chunk_min']} / {report['chunk_median']} / {report['chunk_max']} |

“技术排除”只包括清洗后无有效正文或几乎全是导航链接的页面；“重复排除”只包括正文完全相同的页面。其余通过用户确认，统一进入可导入范围，并保留时效和风险标签。

## 可导入资料优先级

| 优先级 | 数量 |
| --- | ---: |
{table(report['priority_counts'])}

## 主题分布

| 主题 | 数量 |
| --- | ---: |
{table(report['topic_counts'])}

## 时效标记

| 时效 | 数量 |
| --- | ---: |
{table(report['freshness_counts'])}

## 首批问题覆盖

| 状态 | 数量 |
| --- | ---: |
{table(report['test_readiness'])}

问题与候选官方来源的详细映射见 `rag_test_questions.csv`。这只是检索评测的候选集，不等于已经生成了标准答案。

## 输出文件

- `clean_documents/`：逐篇清洗后的 Markdown，适合追溯和单篇检查。
- `fastgpt_markdown/`：按优先级与主题聚合的 Markdown，可直接作为第一轮 FastGPT 导入文件。
- `fastgpt_chunks.csv`：预分块 CSV，列为 `q`、`a` 及来源元数据。
- `chunks.jsonl`：完整结构化分块，后续可供自建向量库或导入接口使用。
- `document_manifest.csv`：每篇资料的处理结果、分类、时效、风险与去重记录。
- `rag_test_questions.csv`：34 个首批问题及前三个候选来源。

## FastGPT 首轮导入建议

1. 先上传 `fastgpt_markdown/` 中以 `P0-` 开头的文件，选择“按文本长度分块”。
2. 分块大小建议先用 800～1000，索引大小使用 512；开启文件名/标题索引。
3. 检索使用混合检索并启用重排；首轮引用 4～6 条。
4. 回答必须展示官方原文链接；涉及时间、费用、地点、联系方式时，优先使用年份更新、校区和人群匹配的来源。
5. P0 调通并完成 34 个问题测试后，再导入 P1；新闻活动类 P2 最后导入。
"""
    (OUT_ROOT / "RAG加工报告-v1.md").write_text(report_text, encoding="utf-8")


def main() -> None:
    docs, site_counts = load_documents()
    decide_statuses(docs)
    report = write_outputs(docs, site_counts)
    write_report(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
