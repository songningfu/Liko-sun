#!/bin/bash
# 双击启动 Liko 知识工厂（macOS）
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "首次运行，正在创建环境…"
  python3 -m venv .venv
  ./.venv/bin/pip install -r requirements.txt
fi
echo "知识工厂启动中：http://127.0.0.1:8020"
(sleep 2 && open "http://127.0.0.1:8020") &
./.venv/bin/python server.py
