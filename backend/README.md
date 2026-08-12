# Liko 最小后端

这是第一版业务后端，使用 FastAPI。H5 流式接口与安全边界已经冻结；未配置 FastGPT 时返回明确的联调占位，配置完成后由后端代理 FastGPT 流式问答。账号密码登录、会话、恢复码、Liko 身份和问答额度已实现；服务器仍需配置 PostgreSQL 与环境变量后才会启用账号服务。

## 当前接口

- `GET /api/health`：服务状态，以及 FastGPT、数据库是否已配置；不会返回密钥内容。
- `GET /api/auth/me`：读取服务端会话、专属 Liko 与当日剩余额度；游客首次访问时设置匿名 HttpOnly Cookie。
- `POST /api/auth/register`：账号密码注册，按顺序生成 Liko 编号与全 sRGB 双层随机配色，并只在本次响应展示恢复码。
- `POST /api/auth/login`、`POST /api/auth/logout`：登录与退出。
- `POST /api/auth/password`：登录状态下修改密码；旧会话全部失效。
- `POST /api/auth/recover`：使用一次性恢复码重设密码，并换发新的恢复码。
- `PATCH /api/auth/memory-consent`：开启或关闭长期记忆。
- `POST /api/chat/stream`：使用 SSE 返回 `meta`、`delta`、`sources`、`done` 或 `error`。FastGPT 配置完整时代理真实问答，否则返回明确的联调占位提示。
- `POST /api/answers/{answerId}/feedback`：校验反馈格式。数据库未接入前返回 `not_persisted`，不会假装保存成功。
- `GET /api/users/me/liko`：返回当前账号正式编号、年龄与固定配色；游客不生成编号。
- `GET /api/users/me/memories`：登录、同意长期记忆且 Mem0 已配置时，查看或搜索自己的记忆。
- `PATCH /api/users/me/memories/{memoryId}`：修改自己的单条记忆。
- `DELETE /api/users/me/memories/{memoryId}`：删除自己的单条记忆。
- `DELETE /api/users/me/memories`：清空自己的长期记忆。

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

长期记忆默认关闭。只有 `MEMORY_ENABLED=true` 且 Mem0 的模型、向量模型和独立 PostgreSQL 配置完整时，健康检查才会返回 `memoryConfigured: true`。当前采用：

- Mem0 OSS Python 库，Apache 2.0 许可。
- `deepseek-v4-flash` 负责从对话中提取和更新记忆。
- `BAAI/bge-m3` 负责记忆向量化，当前按 1024 维配置。
- 独立 PostgreSQL + PgVector 保存不同用户的记忆，集合名默认为 `liko_memories`。
- 本地 `backend/data/` 只保存 Mem0 的修改历史文件，已被 Git 忽略。

Mem0 与校园知识库职责分离：Mem0 只存“关于用户的了解”，FastGPT 继续负责深圳大学事实检索。FastGPT 工作流后续需要新增 `memory_context` 文本变量，并在角色提示词中明确它只是用户背景、不能作为校园事实来源。

游客永远不会读写长期记忆。登录用户也必须先在账号中开启长期记忆；身份和同意状态只能由服务端会话提供，不能相信前端自行传入的 `userId`。未配置账号服务时默认依赖会把所有请求视为游客，便于本地只联调问答；生产环境必须同时配置数据库和会话密钥。

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
- Mem0 适配器与记忆管理接口已有测试覆盖，但服务器尚未安装、配置或启用 Mem0。
- 已有 FastGPT 上游代理、连接/读取超时和安全错误转换；尚未实现自动重试、用户主动停止和限流。
- 没有保存完整用户问题正文的日志。
- Liko 状态轮换仍保存在浏览器，尚未服务端同步；编号和颜色已经由数据库持久化。

## 测试服务器部署

测试服务器采用同机隔离部署：

- H5 由 Nginx 提供，`/api/` 反向代理到仅监听本机的 FastAPI。
- FastAPI 由 `liko-api.service` 托管，部署目录为 `/opt/liko/backend`。
- 账号数据使用独立的 `liko-postgres` 容器与持久化卷，仅监听服务器本机 `127.0.0.1:5433`，不与 FastGPT 业务表混用，也不暴露公网。
- FastGPT 使用官方 Docker Compose PgVector 方案，部署目录为 `/opt/liko/fastgpt`；MongoDB、PostgreSQL、Redis、对象存储和沙箱端口均不直接暴露公网。
- 首轮 FastGPT 导入包已上传到 `/opt/liko/release/知识库/RAG加工/FastGPT首轮导入包`。

服务器环境文件不纳入仓库。完成模型提供商配置、知识库导入并创建应用后，再把 FastGPT 应用 API Key 与 App ID 写入服务器端 `/opt/liko/backend/.env`，然后重启 `liko-api.service`。

账号系统已于 2026-08-12 随提交 `a99afc5` 部署到测试服务器。当前测试入口仍是 IP HTTP，因此暂用 `ENVIRONMENT=development` 让登录 Cookie 可在测试阶段保存；Cookie 仍为 HttpOnly 与 SameSite=Lax。正式开放注册前必须绑定独立域名、启用 HTTPS，并把 `ENVIRONMENT` 改回 `production`，恢复 Secure Cookie。
