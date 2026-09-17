#!/usr/bin/env bash
# 安装到本机。必须重新签名：PyInstaller 出来的包是 ad-hoc 签名的，
# 一旦复制/覆盖，签名就对不上了，Finder 双击会被静默拒绝
# （终端直接跑二进制却能起来，所以这个坑很容易误判成「包坏了」）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/dist/JanCode 智能体.app"
[ -d "$APP" ] || { echo "先运行 scripts/build-desktop.sh 打包"; exit 1; }

codesign --force --deep --sign - "$APP"
rm -rf "/Applications/JanCode 智能体.app"
cp -R "$APP" "/Applications/"
codesign --force --deep --sign - "/Applications/JanCode 智能体.app"
xattr -dr com.apple.quarantine "/Applications/JanCode 智能体.app" 2>/dev/null || true

ln -sfn "$APP" "$HOME/Desktop/JanCode 智能体.app" 2>/dev/null || true
mkdir -p "$HOME/.jancode-agent/bin"
cat > "$HOME/.jancode-agent/bin/jancode" <<'INNER'
#!/bin/sh
APP="/Applications/JanCode 智能体.app"
[ -d "$APP" ] || APP="$HOME/Applications/JanCode 智能体.app"
exec "$APP/Contents/MacOS/JanCode 智能体" "$@"
INNER
chmod +x "$HOME/.jancode-agent/bin/jancode"
open "/Applications/JanCode 智能体.app"
echo "已安装并启动。"
