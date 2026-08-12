# Liko 官方资料采集器

这是知识库建设第 2 步的最小工具，只采集公开的深圳大学官方网页。它不会登录校内系统、不会绕过访问限制，也不会把页面直接标记为“可回答”。

## 当前能力

- 只允许 `szu.edu.cn` 及其子域名的 HTTPS 地址。
- 每次采集前检查站点 `robots.txt`；无法确认时保守跳过。
- 默认相邻请求间隔 2 秒，避免给校方网站造成压力。
- 用 Crawl4AI 将网页正文整理成 Markdown。
- 对脚本型公开页面提供普通 HTML 兼容读取，但不绕过登录、验证码、403、429 或 robots 限制。
- 自动识别常见 4xx/5xx 错误页并拒绝写入可用正文。
- 保存来源、部门、校区、发布日期、核验状态和内容哈希。
- 过期或禁用来源会保留在登记中，但不会采集。

## 首轮范围

- 校历：目前监测教务部官方入口，等待 2026—2027 学年校历发布。
- 新生报到：区分本科生和研究生；本科入学须知尚未发布时不拼凑答案。
- 校园卡：采集信息中心现有制度，办理地点和开放时间仍需人工核验。
- 校园网：先采集信息中心服务入口，继续寻找最新操作指南。

配置位于 `sources.json`，采集结果默认写入 `output/`。`output/资料登记.csv` 可直接用 Excel 打开。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\crawl4ai-setup
.\.venv\Scripts\python collect.py --dry-run
.\.venv\Scripts\python collect.py
```

也可以只采集一条来源：

```powershell
.\.venv\Scripts\python collect.py --source-id CARD-001
```

## 批量采集与续跑

`bulk_collect.py` 用于分批发现并采集多个深圳大学官方站点。它会在每处理一页后保存进度，中断后重复运行同一命令即可继续：

```powershell
.\.venv\Scripts\python bulk_collect.py --max-pages 120 --max-depth 4 --delay 2
```

查看进度而不访问网页：

```powershell
.\.venv\Scripts\python bulk_collect.py --status
```

站点入口配置位于 `sites.json`。批量结果写入 `output_full/`：

- `state.json`：断点、队列和每页状态。
- `pages.jsonl`：每次采集结果的追加日志。
- `documents.json`：发现的 PDF、Word、Excel 等公开文件，留给后续单独解析。
- `网页/`：按站点保存的 Markdown 原始正文。
- `隔离/错误页/`：质量复核时发现的错误响应，保留用于排查但不会进入 RAG。

如果历史批次误收了站点错误页，可运行：

```powershell
.\.venv\Scripts\python repair_error_pages.py
```

下载并解析发现的公开附件：

```powershell
# PDF 与 DOCX 会提取正文；旧版 .doc 会先安全下载并标记待转换
.\.venv\Scripts\python collect_documents.py --max-documents 20
```

扫描版 PDF 可使用本地 OCR。OCR 结果只作为原始表格，代码、计划数、排名等字段仍须人工核验：

```powershell
.\.venv\Scripts\python ocr_scanned_pdf.py --site-id ZS --sha-prefix 6edea1c9b57a
```

批量结果统一标记为“待清洗与人工核验”，不会直接进入 RAG。

## 人工核验规则

采集成功只代表正文已保存。进入 RAG 前必须确认：

1. 页面确实由对应校内部门发布。
2. 内容仍然有效，并标明适用年份或有效期。
3. 本科、研究生以及粤海、丽湖的规则没有混用。
4. 涉及日期、费用、地点、电话和入口的内容已再次核验。
5. 能打开原文链接；有冲突时暂不进入知识库。
