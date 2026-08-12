# Liko 长期记忆数据库部署

此目录只部署 Mem0 使用的独立 PostgreSQL + PgVector，不包含模型密钥，也不复用账号数据库或 FastGPT 数据库。

## 服务器位置与隔离

- 建议部署到 `/opt/liko/memory`。
- 容器名为 `liko-memory-postgres`。
- 默认只监听服务器本机 `127.0.0.1:5434`，不暴露公网。
- 持久化卷为 `liko-memory-postgres-data`。
- 固定使用 `pgvector/pgvector:0.8.6-pg16-bookworm`，升级前先备份并核对兼容性。

## 首次部署

1. 将本目录复制到服务器 `/opt/liko/memory`。
2. 把 `.env.example` 复制为不纳入仓库的 `.env`，生成独立强密码并填写 `POSTGRES_PASSWORD`。
3. 启动数据库：`docker compose up -d`。
4. 确认容器健康，并执行 `SELECT extversion FROM pg_extension WHERE extname = 'vector';`。
5. 在 `/opt/liko/backend/.env` 中填写 `MEM0_POSTGRES_URL`。本机默认连接形式为 `postgresql://liko_memory:<URL编码后的密码>@127.0.0.1:5434/liko_memory`。
6. 后端依赖安装且环境变量生效后，在 `/opt/liko/backend` 执行 `python -m scripts.verify_memory`。该命令会使用临时测试用户完成真实写入、检索、用户隔离、修改和删除，结束时清理测试记忆；会产生少量模型调用费用。

不要把 `.env`、数据库密码、模型 API Key 或完整连接串写入仓库、日志或截图。

## 备份

上线前至少验证一次逻辑备份和恢复。备份文件应写入权限受控、位于 `/opt/liko` 之外的备份目录，并设置保留周期；不要提交到 Git。

## Mem0 启用条件

数据库健康后，在后端私有环境文件中配置：

- `MEMORY_ENABLED=true`
- `MEM0_LLM_API_KEY`
- `MEM0_EMBEDDER_API_KEY`
- `MEM0_POSTGRES_URL`

模型默认沿用 `deepseek-v4-flash`、`BAAI/bge-m3` 和 1024 维。重启 `liko-api.service` 后不能只看 `memoryConfigured: true`，还需完成真实的记忆写入、搜索、跨用户隔离、修改、单条删除和全部删除验收。
