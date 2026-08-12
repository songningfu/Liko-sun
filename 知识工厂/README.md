# Liko 知识工厂

知识库的可视化、自动化生产工具：来源管理 → 自动抓取 → 清洗去重 → 切块 → FastGPT 导出，全链路在浏览器里操作。本地运行，数据不出本机。

## 启动

双击 `启动.command`，或手动：

```bash
cd 知识工厂
./.venv/bin/python server.py
```

浏览器访问 `http://127.0.0.1:8020`。首次使用如果没有 `.venv`：

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m playwright install chromium   # 仅内网采集需要
```

## 功能

| 模块 | 说明 |
| --- | --- |
| 全站大搜查 | 从 `知识库/采集器/sites.json` 的 12 个官方站点种子自动 BFS 发现页面；部门/分类/主题/校区/人群全部自动推断，不用手填。每轮默认最多 60 页，中断后自动续跑；已抓页面 30 天后才重新检查变更 |
| 来源管理 | 手动登记单个来源（官网/内网/公众号），或批量粘贴公众号链接导入 |
| 内网采集 | 设置页打开登录窗口，手动完成统一身份认证后关闭窗口即可；Playwright 持久会话复用，过期会提示重登 |
| 定时任务 | 全站搜查默认每周一 04:00（可改每两周/每月/关闭）；登记来源可单独设每日增量 |
| 导出 | 每次运行自动生成 FastGPT zip，按主题合并；`官方来源/` 与 `非官方待核验/` 分目录 |

## 数据边界与规则

- 抓取仅限 `szu.edu.cn` 及子域名；robots.txt 无法确认时保守跳过该站；请求间隔默认 2 秒。
- 公众号内容一律标记「待核验」，不进入官方事实目录；核验后再使用。
- 清洗、去重、分类、切块直接复用 `知识库/RAG加工/process_rag.py`，规则演进两边自动同步。
- `data/`（注册表、页面、导出包、内网会话）和 `.venv/` 不纳入 Git。
- 发现的 PDF/DOCX 附件链接记录在 `data/crawl_documents.jsonl`，由 `知识库/采集器/collect_documents.py` 另行解析。

## 目录

```
知识工厂/
├── server.py            # FastAPI：API + SSE 进度 + 定时调度
├── core/
│   ├── registry.py      # 来源注册表（data/registry.json）
│   ├── crawler.py       # 全站搜查爬虫（断点续跑、变更检测）
│   ├── fetch.py         # 官网/公众号抓取
│   ├── internal_fetch.py# 内网 Playwright 持久会话
│   ├── html2md.py       # HTML → Markdown（段落密度定位正文）
│   ├── process.py       # 复用 process_rag 的清洗/切块
│   ├── pipeline.py      # 流水线编排与 FastGPT 导出
│   └── settings.py      # 设置（频率、间隔、内网入口）
├── static/index.html    # 工作台单页
├── 启动.command
└── data/                # 全部运行数据（Git 忽略）
```
