# Liko Pocket 浏览器原型

这是 2026-11-08 开始的独立实验入口，不改动现有两页校园 H5，也不新增底部导航或重绘 Liko 形象。当前尚无真实硬件连接。

## 已配置的隔离预览

用户确认优先复用现有配置后，已在原服务器建立 `liko-pocket-preview.service`，仅监听 `127.0.0.1:8012`，使用独立目录 `/opt/liko-pocket-preview` 和独立 SQLite。原 Liko、知识工厂及球译服务配置未改动。

原 DeepSeek 模型调用返回 HTTP 402（余额不足）；当前预览复用原服务器硅基流动凭据，使用 `FunAudioLLM/SenseVoiceSmall` 做识别、`Qwen/Qwen2.5-7B-Instruct` 做记录整理。密钥仍在服务器，不复制到电脑或 Git。

在已配置 `liko-server` SSH 别名的 Mac 上，双击 [`open_pocket_preview.command`](../scripts/open_pocket_preview.command) 会检查连接、必要时恢复独立后台隧道并打开 `http://localhost:8012/pocket/`。本机另有便利入口 `简历/打开 Liko Pocket.command`。关闭终端或结束开发任务不会主动关闭这个后台连接；电脑重启或长时间断网后重新双击即可。

也可手动重新连接：

```sh
ssh -fNT -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:8012:127.0.0.1:8012 liko-server
```

这是当前 Mac 的私人试用入口，手机或其他电脑不能直接使用这个 localhost 地址。2026-11-08 已将服务器服务改为持久 systemd 单元，异常退出自动恢复、服务器重启自动启动；配置在 [`liko-pocket-preview.service`](../deploy/liko-pocket-preview.service)，适用于当前服务器目录布局。页面仍仅监听服务器回环地址，没有新增公网路由。真实验证结果见 [`预览验证记录`](../../项目文档/Liko-Pocket_预览验证记录_20261108.md)。

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
5. 2026-11-08 已通过 SSH 确认原 Liko 在服务器 `127.0.0.1:8001` 正常，公网旧 IP 默认路由指向球译；Pocket 预览通过独立的 8012 服务与 SSH 隧道访问，没有修改 Nginx 公网路由。

## 验证

```sh
backend/.venv/bin/python -m pytest backend/tests/test_pocket.py
```

测试涵盖账号隔离、去重、文字修改与原文追溯、删除、关键词字面匹配、空记录、模型故障、旧结果覆盖防护和音频识别接口边界。
