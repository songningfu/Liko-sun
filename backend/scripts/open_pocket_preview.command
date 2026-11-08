#!/bin/zsh
# macOS: reconnect the existing private preview, then open it in the browser.
set -eu

preview_url='http://localhost:8012/pocket/'
status_url='http://127.0.0.1:8012/api/pocket/status'

preview_ready() {
  local status
  status=$(/usr/bin/curl --noproxy '*' -fsS --max-time 3 "$status_url" 2>/dev/null) || return 1
  [[ "$status" == *'"mode":"browser_prototype"'* ]]
}

if ! preview_ready; then
  print '正在连接 Liko Pocket…'
  if ! /usr/bin/ssh -fNT \
    -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes \
    -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    -L 127.0.0.1:8012:127.0.0.1:8012 liko-server; then
    print -u2 '连接失败：请确认网络正常，并已在这台 Mac 配置 liko-server 的 SSH 访问。'
    exit 1
  fi
fi

for attempt in {1..10}; do
  if preview_ready; then
    print "已连接：$preview_url"
    /usr/bin/open "$preview_url"
    exit 0
  fi
  sleep 1
done

print -u2 '连接已建立，但页面暂时没有响应，请稍后重新双击打开。'
exit 1
