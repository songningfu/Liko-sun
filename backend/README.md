# Liko 最小后端

这是第一版业务后端，使用 FastAPI。H5 流式接口与安全边界已经冻结；未配置 FastGPT 时返回明确的联调占位，配置完成后由后端代理 FastGPT 流式问答。账号密码登录、会话、恢复码、Liko 身份和问答额度已实现；服务器仍需配置 PostgreSQL 与环境变量后才会启用账号服务。

## 当前接口

- `GET /api/health`：服务状态，以及 FastGPT、数据库是否已配置；不会返回密钥内容。
- `GET /api/admin/summary`：使用后端共享管理令牌读取用户数、有效会话数、今日问答数和集成状态；仅供知识工厂统一后台调用，不返回账号明细或对话正文。
- `GET /api/auth/me`：读取服务端会话、专属 Liko 与当日剩余额度；游客首次访问时设置匿名 HttpOnly Cookie。
- `POST /api/auth/register`：账号密码注册，按顺序生成 Liko 编号与全 sRGB 双层随机配色，并只在本次响应展示恢复码。
- `POST /api/auth/login`、`POST /api/auth/logout`：登录与退出。
- `POST /api/auth/password`：登录状态下修改密码；旧会话全部失效。
- `POST /api/auth/recover`：使用一次性恢复码重设密码，并换发新的恢复码。
- `PATCH /api/auth/memory-consent`：开启或关闭长期记忆。
- `PATCH /api/auth/liko-colors`：登录用户使用注册赠送的一次机会确认外壳色与肤色；确认后由服务端锁定，未来换色需道具系统重新授权。
- `GET /api/recommendations`：返回近 7 天匿名聚合后的校园高频问题；样本不足时自动补充固定常用问题。
- `POST /api/chat/stream`：使用 SSE 返回 `meta`、`delta`、`sources`、`done` 或 `error`。FastGPT 配置完整时代理真实问答，并兼容从工作流汇总事件恢复未进入标准文本流的最终答案；否则返回明确的联调占位提示。
- `POST /api/answers/{answerId}/feedback`：校验反馈格式。数据库未接入前返回 `not_persisted`，不会假装保存成功。
- `GET /api/users/me/liko`：返回当前账号正式编号、年龄与固定配色；游客不生成编号。
- `GET /api/users/me/memories`：登录且 Mem0 已配置时查看或搜索自己的记忆；关闭长期记忆后仍可管理旧内容。
- `PATCH /api/users/me/memories/{memoryId}`：修改自己的单条记忆。
- `DELETE /api/users/me/memories/{memoryId}`：删除自己的单条记忆。
- `DELETE /api/users/me/memories`：清空自己的长期记忆。
- `GET /api/users/me/conversations`：列出当前登录用户保存的对话。
- `GET /api/users/me/conversations/{conversationId}/messages`：读取当前用户某次对话的完整消息与来源。
- `DELETE /api/users/me/conversations/{conversationId}`：删除当前用户自己的整次对话；不能读取或删除其他用户的数据。

## 本地启动

需要 Python 3.10 或更高版本：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\python -m uvicorn app.main:app --port 8001 --reload
```

浏览器访问 `http://127.0.0.1:8001/api/health`，接口文档位于 `http://127.0.0.1:8001/docs`。

运行测试：

```powershell
cd backend
.\.venv\Scripts\python -m pytest
```

也可以从仓库根目录运行：

```powershell
.\backend\.venv\Scripts\python -m pytest backend
```

## 环境变量

真实密钥只能填写在未提交的 `backend/.env` 或部署平台的后端环境变量中。仓库中的 `.env.example` 只有空占位。

FastGPT 需要同时配置 `FASTGPT_BASE_URL`、`FASTGPT_API_KEY` 和 `FASTGPT_APP_ID` 才会启用。`FASTGPT_BASE_URL` 可以填写实例根地址、以 `/api` 结尾的 OpenAPI BaseURL，或完整的 `chat/completions` 地址。

FastGPT 管理后台中的应用配置分为调试草稿和 API 使用的已发布版本。修改角色提示词、知识库或检索参数后，必须点击 **保存 → 保存并发布**，并在版本弹窗中再次点击 **确认**；只显示“已保存”并不代表手机端 API 已更新。发布后至少验证“你是谁？”和一个应命中知识库的校园问题，确认身份为 Liko、运行链路包含知识库搜索且返回官方来源。

账号服务需同时配置 `DATABASE_URL` 和至少 32 字符的随机 `SESSION_SECRET`。生产数据库使用 PostgreSQL，例如 `postgresql://...`；首次连接会自动创建当前版本所需表。密码使用 Argon2id 摘要，会话令牌和恢复码只保存不可逆摘要。默认会话 30 天、游客每天 10 次、登录用户每天 50 次，均可通过环境变量调整。生产环境 Cookie 自动启用 `Secure`、`HttpOnly` 与 `SameSite=Lax`。

统一管理后台还需要在后端设置 `ADMIN_API_TOKEN`，并在知识工厂设置相同的 `LIKO_ADMIN_API_TOKEN`。该令牌只允许读取聚合统计，不能读取或修改具体账号；不得放进浏览器或 Git。

长期记忆默认关闭。只有 `MEMORY_ENABLED=true` 且 Mem0 的模型、向量模型和独立 PostgreSQL 配置完整时，健康检查才会返回 `memoryConfigured: true`。当前采用：

- Mem0 OSS Python 库，Apache 2.0 许可。
- `deepseek-v4-flash` 负责从对话中提取和更新记忆。
- `BAAI/bge-m3` 负责记忆向量化，当前按 1024 维配置。
- 独立 PostgreSQL + PgVector 保存不同用户的记忆，集合名默认为 `liko_memories`。
- 本地 `backend/data/` 只保存 Mem0 的修改历史文件，已被 Git 忽略。

Mem0 与校园知识库职责分离：Mem0 只存“关于用户的了解”，FastGPT 继续负责深圳大学事实检索。FastGPT 工作流已发布 `memory_context` 文本变量，并在角色提示词中明确它只是用户背景、不能作为校园事实来源。姓名等结构明确的资料会先写入用户专属记忆；用户询问自己的姓名时由后端确定性读取，不再交给校园 RAG 猜测或附加无关来源。

知识工厂发布的 Liko 表达规则通过 `style_context` 文本变量进入 FastGPT。FastGPT 应用需要预先创建该变量并在角色提示词引用；后端环境变量 `LIKO_STYLE_FILE` 指向服务器上 `知识工厂/data/liko_style.json`。后端只读取 `published_prompt`，草稿不会影响线上问答；如果文件缺失则保持现有 FastGPT 提示词，不会导致问答中断。

`style_context` 是全局默认表达，`memory_context` 是每个用户的个性化背景。冲突优先级为：身份/事实/安全规则 → 当前用户明确要求 → 长期记忆偏好 → 全局表达习惯。长期记忆不得作为校园事实来源，也不得覆盖用户本轮刚提出的新要求。

部署后运行 `python -m scripts.verify_memory` 做真实验收。脚本使用随机临时用户检查写入、检索、跨用户隔离、修改和删除，并在结束时清理测试数据；它会产生少量模型调用费用。`/api/health` 中的 `memoryConfigured` 只说明必要环境变量齐全，不能代替这项验收。

SiliconFlow 的 `BAAI/bge-m3` 接口不接收 OpenAI 的可选 `dimensions` 请求参数，因此嵌入调用配置中不要传该字段；1024 维只配置在 PgVector 集合侧。测试服务器已于 2026-08-13 完成上述真实验收，健康检查返回 `memoryConfigured: true`。

游客永远不会读写长期记忆。登录用户也必须先在账号中开启长期记忆；身份和同意状态只能由服务端会话提供，不能相信前端自行传入的 `userId`。未配置账号服务时默认依赖会把所有请求视为游客，便于本地只联调问答；生产环境必须同时配置数据库和会话密钥。

关闭长期记忆只会停止聊天中的读取和新增，不会替用户静默删除旧内容。登录用户即使关闭后仍能查看、修改或删除已有记忆；清空操作必须由用户明确确认。

## 运行 34 个问题评测

先在 FastGPT 创建测试应用、关联首轮知识库，并确认应用本身能够返回引用；随后运行：

```powershell
cd backend
.\.venv\Scripts\python -m scripts.evaluate_rag
```

建议先限制为 3 条检查配置和费用：

```powershell
.\.venv\Scripts\python -m scripts.evaluate_rag --limit 3
```

结果默认写入未纳入版本管理的 `backend/evaluation-output/`，包括答案、官方来源数、来源 URL 和安全错误信息。脚本只接受 HTTPS 的深圳大学官方域名作为有效来源。

## 当前边界

- 第一版只使用账号密码，不使用手机号、邮箱或验证码；忘记密码依赖注册时的一次性恢复码。
- 当前自动建表适合首次部署；正式公开上线前仍需引入数据库迁移版本管理。
- Mem0 适配器、确定性姓名记忆和记忆管理接口已有测试覆盖；测试服务器已安装 Mem0 OSS、独立 PgVector 并安全配置模型服务，真实验收已通过，服务已启用。
- 已有 FastGPT 上游代理、标准回答流与工作流最终答案兼容、连接/读取超时和安全错误转换；尚未实现自动重试、用户主动停止和限流。
- 没有保存完整用户问题正文的日志。
- Liko 状态轮换仍保存在浏览器，尚未服务端同步；编号和颜色已经由数据库持久化。
- 游客每次打开页面随机显示外壳色与肤色；新注册用户在确认首次自选后跨设备同步。升级前已有账号不自动补发机会，未来换色或补发统一由道具/运营授权处理。

## 测试服务器部署

测试服务器采用同机隔离部署：

- H5 由 Nginx 提供，`/api/` 反向代理到仅监听本机的 FastAPI。
- FastAPI 由 `liko-api.service` 托管，部署目录为 `/opt/liko/backend`。
- 账号数据使用独立的 `liko-postgres` 容器与持久化卷，仅监听服务器本机 `127.0.0.1:5433`，不与 FastGPT 业务表混用，也不暴露公网。
- Mem0 使用第三套独立 PgVector，部署文件位于 `backend/deploy/memory/`，默认仅监听服务器本机 `127.0.0.1:5434`。
- FastGPT 使用官方 Docker Compose PgVector 方案，部署目录为 `/opt/liko/fastgpt`；MongoDB、PostgreSQL、Redis、对象存储和沙箱端口均不直接暴露公网。
- 首轮 FastGPT 导入包已上传到 `/opt/liko/release/知识库/RAG加工/FastGPT首轮导入包`。

服务器环境文件不纳入仓库。完成模型提供商配置、知识库导入并创建应用后，再把 FastGPT 应用 API Key 与 App ID 写入服务器端 `/opt/liko/backend/.env`，然后重启 `liko-api.service`。

账号系统已于 2026-08-12 随提交 `a99afc5` 部署到测试服务器。当前测试入口仍是 IP HTTP，因此暂用 `ENVIRONMENT=development` 让登录 Cookie 可在测试阶段保存；Cookie 仍为 HttpOnly 与 SameSite=Lax。正式开放注册前必须绑定独立域名、启用 HTTPS，并把 `ENVIRONMENT` 改回 `production`，恢复 Secure Cookie。

2026-08-12 已继续部署 H5 聊天开场、回答排版、来源按标题去重和配色文案优化，并修复 FastGPT 偶发只返回来源、不返回正文的兼容问题。

2026-08-12 已部署游客随机配色与注册首次选色：新注册账号可确认一次外壳色和肤色，确认后服务端返回冲突并阻止重复使用；升级前已有账号不自动补发。部署时已完成账号库备份和真实临时账号验收。

同日已在 `/opt/liko/memory` 部署 Mem0 独立 PgVector 0.8.6，容器健康且仅监听 `127.0.0.1:5434`。模型密钥随后已安全写入服务器环境并通过 `python -m scripts.verify_memory` 的真实写入、检索、隔离、修改和删除验收，健康检查返回 `memoryConfigured: true`。Mem0 本地配置目录通过 `MEM0_DIR` 指向后端可写的 `backend/data/.mem0`，避免 systemd 的主目录保护导致读取失败。

2026-08-13 已部署手机端“Liko 对我的了解”页面，支持开关、查看、修改、单条删除和全部清空；同时允许用户在关闭长期记忆后继续管理旧内容。模型服务已启用，页面与聊天链路均完成真实临时账号验收。

同日已部署登录用户对话持久化、逐条删除和近 7 天匿名热度推荐。推荐点击只按自然输入的 15% 计权，动态问题至少需要两个不同账号共同出现且必须属于可识别的校园主题；当前用户正文不会通过管理统计接口暴露。
