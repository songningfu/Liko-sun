"""轻量 HTML → Markdown 转换，面向学校官网与公众号正文结构。"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString, Tag

CONTENT_SELECTORS = [
    ".v_news_content", ".news_content", ".article-content", ".article_content",
    ".content_detail", ".detail_content", "#js_content", "#content", ".content",
    ".article", ".con_main", ".main_content", "main", "article",
]
REMOVE_TAGS = ["script", "style", "noscript", "iframe", "form", "button", "select", "textarea"]
REMOVE_SELECTORS = ["nav", "footer", "header", ".nav", ".footer", ".header", ".breadcrumb",
                    ".share", ".qr_code", "#js_qr_code", ".reward_area"]
# 学校站群 CMS（VSB）模板残留的固定噪声行
JUNK_PATTERNS = [
    re.compile(r"^网站logo图片地址"),
    re.compile(r"^(nav|banner|phone|nyMain|EN|搜索|share|menu)$", re.IGNORECASE),
    re.compile(r"^\d+(\.\d+)+版本$"),
]


def _is_junk(line: str) -> bool:
    return any(p.search(line) for p in JUNK_PATTERNS)


def _paragraph_text_len(node: Tag) -> int:
    """节点内段落级文本（p/li/h/td）总长度；导航栏通常是纯链接列表，得分很低。"""
    total = 0
    for tag in node.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "pre"]):
        text = tag.get_text(strip=True)
        # 链接占比高的段落视为导航，不计分
        link_text = sum(len(a.get_text(strip=True)) for a in tag.find_all("a"))
        if len(text) > 0 and link_text / max(len(text), 1) < 0.6:
            total += len(text)
    return total


def pick_content_node(soup: BeautifulSoup) -> Tag:
    for selector in CONTENT_SELECTORS:
        node = soup.select_one(selector)
        if node and len(node.get_text(strip=True)) > 200:
            return node
    # 段落密度兜底：找包含最多"非链接段落文本"的容器
    best, best_score = soup.body or soup, 0
    for node in soup.find_all(["div", "section", "article", "main", "td"]):
        score = _paragraph_text_len(node)
        if score > best_score:
            best, best_score = node, score
    return best


def _inline(node, parts: list[str]) -> None:
    if isinstance(node, NavigableString):
        parts.append(re.sub(r"\s+", " ", str(node)))
        return
    if not isinstance(node, Tag):
        return
    name = node.name.lower()
    if name == "br":
        parts.append("\n")
        return
    if name in {"strong", "b"}:
        text = node.get_text()
        if text.strip():
            parts.append(f"**{text.strip()}**")
        return
    if name == "a":
        text = node.get_text(strip=True)
        href = node.get("href", "")
        if text and href and href.startswith(("http://", "https://")):
            parts.append(f"[{text}]({href})")
        elif text:
            parts.append(text)
        return
    if name == "img":
        return  # 图片不进入知识库正文
    for child in node.children:
        _inline(child, parts)


def _block(node, lines: list[str], depth: int = 0) -> None:
    if isinstance(node, NavigableString):
        text = re.sub(r"\s+", " ", str(node)).strip()
        if text:
            lines.append(text)
        return
    if not isinstance(node, Tag):
        return
    name = node.name.lower()
    if name in REMOVE_TAGS:
        return
    if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = min(int(name[1]) + 1, 6)  # 一级标题留给文档标题
        text = node.get_text(strip=True)
        if text:
            lines.append(f"{'#' * level} {text}")
        return
    if name in {"p", "section", "div", "blockquote", "td", "center"}:
        # 若内部还有块级结构则递归，否则按内联处理
        if node.find(["p", "div", "section", "table", "ul", "ol", "h1", "h2", "h3", "h4"]):
            for child in node.children:
                _block(child, lines, depth)
        else:
            parts: list[str] = []
            for child in node.children:
                _inline(child, parts)
            text = "".join(parts).strip()
            if text:
                lines.append(("> " if name == "blockquote" else "") + text)
        return
    if name in {"ul", "ol"}:
        # 链接占比高的短列表是导航菜单，不是正文
        total_chars = len(node.get_text(strip=True)) or 1
        link_chars = sum(len(a.get_text(strip=True)) for a in node.find_all("a"))
        if total_chars < 500 and link_chars / total_chars > 0.6:
            return
        for i, li in enumerate(node.find_all("li", recursive=False), 1):
            parts = []
            for child in li.children:
                _inline(child, parts)
            text = "".join(parts).strip()
            if text:
                prefix = f"{i}." if name == "ol" else "-"
                lines.append(f"{prefix} {text}")
        return
    if name == "table":
        rows = []
        for tr in node.find_all("tr"):
            cells = [re.sub(r"\s+", " ", c.get_text(strip=True)) for c in tr.find_all(["th", "td"])]
            if any(cells):
                rows.append(cells)
        if rows:
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            lines.append("| " + " | ".join(rows[0]) + " |")
            lines.append("| " + " | ".join(["---"] * width) + " |")
            for row in rows[1:]:
                lines.append("| " + " | ".join(row) + " |")
        return
    for child in node.children:
        _block(child, lines, depth)


def html_to_markdown(html: str, *, extra_remove: list[str] | None = None) -> tuple[str, str]:
    """返回 (页面<title>, 正文 markdown)。"""
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.string or "").strip() if soup.title else ""
    for tag in soup.find_all(REMOVE_TAGS):
        tag.decompose()
    for selector in REMOVE_SELECTORS + (extra_remove or []):
        for node in soup.select(selector):
            node.decompose()
    content = pick_content_node(soup)
    lines: list[str] = []
    _block(content, lines)
    # 折叠连续空行与重复行
    out: list[str] = []
    prev = None
    for line in lines:
        line = line.strip()
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        if _is_junk(line):
            continue
        if line != prev:
            out.append(line)
        prev = line
    return title, "\n".join(out).strip()
