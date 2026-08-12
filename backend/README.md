# Liko 最小后端

这是第一版业务后端，使用 FastAPI。H5 流式接口与安全边界已经冻结；未配置 FastGPT 时返回明确的联调占位，配置完成后由后端代理 FastGPT 流式问答。数据库仍未接入。

## 当前接口

- `GET /api/health`：服务状态，以及 FastGPT、数据库是否已配置；不会返回密钥内容。
- `POST /api/chat/stream`：使用 SSE 返回 `meta`、`delta`、`sources`、`done` 或 `error`。FastGPT 配置完整时代理真实问答，否则返回明确的联调占位提示。
- `POST /api/answers/{answerId}/feedback`：校验反馈格式。数据库未接入前返回 `not_persisted`，不会假装保存成功。
- `GET /api/users/me/liko`：当前返回游客状态，不生成正式编号。

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

FastGPT 需要同时配置 `FASTGPT_BASE_URL`、`FASTGPT_API_KEY` 和 `FASTGPT_APP_ID` 才会启用。`FASTGPT_BASE_URL` 可以填写实例根地址、以 `/api` 结尾的 OpenAPI BaseURL，或完整的 `chat/completions` 地址。PostgreSQL 即使已经配置，当前版本也不会写库。

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

- 没有登录、验证码或正式用户身份。
- 没有数据库迁移和持久化。
- 已有 FastGPT 上游代理、连接/读取超时和安全错误转换；尚未实现自动重试、用户主动停止和限流。
- 没有保存完整用户问题正文的日志。
- 正式 Liko 编号仍须由数据库注册顺序生成，不能在浏览器或本接口空壳中随机生成。

## 测试服务器部署

测试服务器采用同机隔离部署：

- H5 由 Nginx 提供，`/api/` 反向代理到仅监听本机的 FastAPI。
- FastAPI 由 `liko-api.service` 托管，部署目录为 `/opt/liko/backend`。
- FastGPT 使用官方 Docker Compose PgVector 方案，部署目录为 `/opt/liko/fastgpt`；MongoDB、PostgreSQL、Redis、对象存储和沙箱端口均不直接暴露公网。
- 首轮 FastGPT 导入包已上传到 `/opt/liko/release/知识库/RAG加工/FastGPT首轮导入包`。

服务器环境文件不纳入仓库。完成模型提供商配置、知识库导入并创建应用后，再把 FastGPT 应用 API Key 与 App ID 写入服务器端 `/opt/liko/backend/.env`，然后重启 `liko-api.service`。
