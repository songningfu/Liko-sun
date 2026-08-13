# Liko 知识工厂

知识库的可视化、自动化生产工具：来源管理 → 自动抓取/文字收件 → 清洗去重 → 大模型辅助整理 → 人工审核 → FastGPT 暂存发布，全链路在浏览器里操作。默认只监听本机。

## 启动

双击 `启动.command`，或手动：

```bash
cd 知识工厂
./.venv/bin/python server.py
```

浏览器访问 `http://127.0.0.1:8020`。测试服务器由 Nginx 通过 `http://43.161.217.43/admin/` 提供统一管理入口，所有知识工厂 API 都需要管理员会话。首次使用如果没有 `.venv`：

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m playwright install chromium   # 仅内网采集需要
```

如需启用模型加工和 FastGPT 增量同步，复制 `.env.example` 为 `.env` 后在服务器端填写。真实密钥不得填写在网页、H5、日志或 Git 中。

## 管理员登录

统一后台使用独立管理员账号，不复用普通 Liko 用户账号。密码只保存 PBKDF2-SHA256 摘要；会话 8 小时有效，修改数据的请求还会校验页面令牌。生成密码摘要：

```bash
cd 知识工厂
./.venv/bin/python -m core.admin_auth
```

将输出的摘要写入 `FACTORY_ADMIN_PASSWORD_HASH`，并分别为 `FACTORY_SESSION_SECRET`、`LIKO_ADMIN_API_TOKEN` 生成高强度随机值。后一个值需要与 Liko 后端的 `ADMIN_API_TOKEN` 一致，只用于读取汇总统计。测试 IP 仍是 HTTP，管理员页面仅供临时验收；正式使用前必须绑定域名并启用 HTTPS，再把 `FACTORY_COOKIE_SECURE` 改为 `true`。

## 功能

| 模块 | 说明 |
| --- | --- |
| 全站大搜查 | 从 12 个内置种子起步，并允许后台继续添加 `https://*.szu.edu.cn` 官方站点；自动 BFS 发现同站页面。每轮默认最多 60 页，中断后续跑；已抓页面 30 天后才重新检查变更 |
| 来源管理 | 手动登记单个来源（官网/内网/公众号），或批量粘贴公众号链接导入 |
| 资料收件箱 | 接收用户粘贴的文字、聊天通知和无链接资料；原文永久保留，按权威官方/校内机构/他人提供/社区线索四级标记并审核 |
| 大模型辅助整理 | 可选。提取摘要、主题、部门、时效、风险和事实证据；模型输出只作辅助标签，不覆盖原文；警告和风险会阻止自动发布 |
| 内网采集 | 设置页打开登录窗口，手动完成统一身份认证后关闭窗口即可；Playwright 持久会话复用，过期会提示重登 |
| 定时任务 | 全站搜查默认每周一 04:00（可改每两周/每月/关闭）；登记来源可单独设每日增量 |
| 导出 | 每次运行自动生成 FastGPT zip，按主题合并；`官方来源/` 与 `非官方待核验/` 分目录 |
| FastGPT 同步 | 每篇资料独立版本。可开启加工后自动禁用暂存；低风险官方资料训练完成后自动发布，系统每 10 分钟补查训练状态；公众号、人工投递和风险资料仍需审核，旧版本可回滚 |
| Liko 表达 | 可修改语气、长度、段落和习惯用语，支持草稿、发布和回滚；身份、事实依据、不编造等规则不可关闭 |
| Liko 概览 | 只展示注册用户数、今日新增、有效会话、今日问答数与集成状态；不展示密码、API Key、用户名列表或对话正文 |

表达优先级固定为：身份/事实/安全规则 → 用户本次明确要求 → 该用户长期记忆中的偏好 → Liko 全局表达习惯。长期记忆只做个性化，不会改写校园事实；旧偏好与本次要求冲突时，以本次要求为准。

## 数据边界与规则

- 抓取仅限 `szu.edu.cn` 及子域名；robots.txt 无法确认时保守跳过该站；请求间隔默认 2 秒。
- 公众号内容一律标记「待核验」，不进入官方事实目录；核验后再使用。
- 自动化不会无条件上线：低可信、人工投递、模型警告、费用/时效/联系方式等高风险资料只能暂存；FastGPT 新集合训练未完成时禁止发布。
- `设置 → FastGPT 自动迭代` 分为两个开关：自动暂存和低风险官方资料自动发布。开启自动发布时后端会同时开启自动暂存。
- 大模型只能提取和提示冲突，不能改写原文，也不能把用户记忆当校园事实。
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
│   ├── manual.py        # 人工文字资料与审核
│   ├── enrichment.py    # 可选的大模型结构化加工
│   ├── fastgpt_sync.py  # 暂存、发布、回滚
│   ├── liko_style.py    # Liko 表达规则版本
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

## FastGPT 与 Liko 表达接线

1. 在 FastGPT 应用中新增文本变量 `style_context`，并在角色提示词中加入它；`memory_context` 仍只用于用户背景。
2. 知识工厂点击“Liko 表达 → 发布新版”，只发布草稿，不会把未发布修改带到手机端。
3. 后端设置 `LIKO_STYLE_FILE` 为服务器上 `知识工厂/data/liko_style.json` 的绝对路径，再重启 API。
4. 发布后用“你是谁？”和一个校园事实问题验收；Liko 身份与来源约束始终优先。
