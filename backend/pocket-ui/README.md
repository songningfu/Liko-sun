# Liko Pocket 浏览器原型

这是 2026-11-08 开始的独立实验入口，不改动现有两页校园 H5，也不新增底部导航或重绘 Liko 形象。当前尚无真实硬件连接。

## 当前云端入口

**https://songningfu.site/liko-pocket/**，电脑和手机浏览器均可直接访问，无须本机隧道。使用之前 Pocket 试用账号的账号密码登录；校园 Liko 账号不混用。目前仍是浏览器与云端原型，没有连接真实硬件。

用户要求保留旧校园 Liko 后，已在 `/opt/liko-pocket` 独立部署代码、Python 环境、SQLite 与 `liko-pocket.service`，监听回环 8013。Nginx 仅在现有 HTTPS 站点增加 `/liko-pocket/` 前缀；旧 Liko 的已部署代码、环境配置和服务未改动。验收见 [`云端部署与项目检测`](../../项目文档/Liko-Pocket_云端部署与项目检测_20261108.md)。

- 账号 Cookie 使用独立名称 `liko_pocket_session` / `liko_pocket_guest`，路径限制为 `/liko-pocket/`，启用 Secure、HttpOnly 和 SameSite=Lax；拒绝跨来源写请求。
- 云端每日每账号最多 50 次记录保存/整理请求、20 次转写请求（按 UTC 日期）；超限后仍可查阅、修改和删除已有记录。注册与登录另有既有频控。模型故障不丢已保存原文。
- `liko-pocket-backup.timer` 每天 04:15（服务器 Asia/Shanghai）备份新数据库，保留最近 14 份并检查完整性。同机备份不能抵御整台服务器销毁；现有套餐在 2026-11-11 19:53 到期，需提前安排续费或异地迁移。
- macOS 的 [`open_pocket_preview.command`](../scripts/open_pocket_preview.command) 已改为直接打开 HTTPS 网址。本机旧 `localhost:8012` 隧道尚在时会跳转到同一网址，后续不再依赖它。

服务与路由模板：`backend/deploy/liko-pocket.service`、`pocket-nginx-location.conf` 和 `liko-pocket-backup.*`。独立环境安装 `requirements-pocket.txt`；仅在服务器私有 `model.env` 配置 `POCKET_LLM_*` / `POCKET_ASR_*`，权限为 0600，不纳入 Git。

```sh
cd /opt/liko-pocket/backend
.venv/bin/python -m scripts.run_pocket_cloud --data-dir /opt/liko-pocket/data
python3 scripts/backup_pocket.py --data-dir /opt/liko-pocket/data --backup-dir /opt/liko-pocket/backups/database
```

新入口 `app.pocket_cloud.create_app()` 只挂载账号、Pocket 和健康检查，不导入校园主应用。复用既有模型凭据时，仅在服务器复制选定模型字段到独立受保护配置；不复用旧校园 Liko 的数据库、会话密钥或运行环境。

原 DeepSeek 模型在先前调用中返回 HTTP 402；新 Pocket 使用原服务器现有硅基流动凭据，接入 `FunAudioLLM/SenseVoiceSmall` 与 `Qwen/Qwen2.5-7B-Instruct`。密钥仍留在服务器。

## 历史隔离预览（已停用）

最初的 `/opt/liko-pocket-preview` 曾通过 8012 端口与 SSH 隧道供当前 Mac 访问，现已被 HTTPS 独立部署取代。旧预览目录和数据留存，预览服务已停止并取消自启，避免两套数据库继续分叉；4 条既有 Pocket 记录已一致性迁移。迁移的是 Pocket 原型账号和记录，不是校园 Liko 数据。不要重新启动旧预览服务占用跳转端口。

历史验证见 [`预览验证记录`](../../项目文档/Liko-Pocket_预览验证记录_20261108.md)。

## 本地启动

在仓库根目录使用 Python 3.10+：

```sh
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-pocket.txt
cd backend
.venv/bin/python -m scripts.run_pocket
```

浏览器访问 `http://127.0.0.1:8011/pocket/`。Windows 使用 `backend\.venv\Scripts\python` 的对应路径。

启动器固定使用 `backend/data/pocket-local/pocket.db`，会生成仅本机可读写的会话密钥；这些文件由 Git 忽略。本地账号与现有服务器账号独立。只绑定本机，不会改动线上数据。

## 已实现

- 复用 Liko 账号、密码与服务端会话机制，创建本地试用账号。
- 文字记录持久化、按原文关键词查找、类型筛选、修改、删除、初始原文追溯。
- 同一请求重试去重、不同内容使用同一请求标识时拒绝覆盖。
- 修改版本冲突提示；晚到的模型结果不覆盖用户已经修改的内容。
- 可选模型整理：先保存原文，再调用模型提取标题、摘要、类型与待办；模型失败不丢原文。
- 可选浏览器录音：最长 60 秒，后端识别后供用户核对，用户明确保存才写入记录。失败后可回听或重试；服务端不保留原始音频文件，上传解析时可能使用框架临时文件，请求结束后关闭。

## 模型接入

在未提交的 `backend/.env` 设置独立的 `POCKET_LLM_*` 和 `POCKET_ASR_*`，重启本地服务。

- 整理服务接收兼容 `POST /chat/completions` 的接口，要求返回 JSON 对象；Base URL 应填写接口前缀，如 `https://供应商域名/v1`。
- 识别服务接收兼容 `POST /audio/transcriptions` 的 multipart 音频接口；Base URL 同样不包含末尾方法路径。
- Base URL、Key 和模型名必须全部设置才开启对应功能。服务商还需支持浏览器生成的 WebM/MP4/Ogg 音频格式，具体兼容性需真实联调。
- 无模型配置时，页面明确标为原文记录；搜索始终是原文关键词匹配，尚不提供自然语言记忆问答。
- 自动测试使用模拟上游；隔离预览已完成真实模型和合成语音调用验证。尚未完成人工麦克风实测、真实用户试用和硬件联调，不将单条冒烟测试视为整体效果指标。

## 当前边界与下一步

1. 硬件采购前，先在隔离预览完成原文记录与浏览器麦克风的真实可用性测试。
2. 检查人工语音识别准确性、待办提取、响应时间；合成语音已发现“访谈”识别错误，需保留核对与纠正步骤。
3. 购买经过上游确认兼容的 ESP32 设备，再接小智语音链路；设备绑定、设备凭证、断连与重连均待实现。
4. 当前接口不接收硬件事件，也不宣称实现离线录音、BLE 同步、后台保活或功耗管理。
5. 旧校园 Liko 内网页面与 API 存活，但旧简历公网 IP 返回 403、真实校园问答返回 500、原记忆模型返回 402；不能把“配置齐全”当作功能正常。按用户要求仅检测，未修改旧版。

## 验证

```sh
backend/.venv/bin/python -m pytest backend/tests/test_pocket.py backend/tests/test_pocket_cloud.py
```

测试涵盖账号隔离、去重、文字修改与原文追溯、删除、关键词字面匹配、空记录、模型故障、旧结果覆盖防护和音频识别接口边界。
