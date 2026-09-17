#!/usr/bin/env bash
# 安装到本机。必须重新签名：PyInstaller 出来的包是 ad-hoc 签名的，
# 一旦复制/覆盖，签名就对不上了，Finder 双击会被静默拒绝
# （终端直接跑二进制却能起来，所以这个坑很容易误判成「包坏了」）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/dist/JanCode 智能体.app"
[ -d "$APP" ] || { echo "先运行 scripts/build-desktop.sh 打包"; exit 1; }

# 先清扩展属性，否则签名会报 resource fork not allowed 而失败。
# 不用 --deep：bundle 里没有 CodeResources，--deep 生成的资源封印
# 会让 codesign -v 报 "code has no resources but signature indicates
# they must be present"，那份包双击照样打不开。
sign_bundle() {
  local APP="$1"
  xattr -cr "$APP" 2>/dev/null || true
  find "$APP/Contents/MacOS" -type f -perm +111 2>/dev/null | while read -r f; do
    codesign --force --sign - "$f" >/dev/null 2>&1 || true
  done
  [ -d "$APP/Contents/Frameworks" ] && codesign --force --sign - "$APP/Contents/Frameworks" >/dev/null 2>&1 || true
  codesign --force --sign - "$APP" >/dev/null 2>&1 || true
}

sign_bundle "$APP"
rm -rf "/Applications/JanCode 智能体.app"
cp -R "$APP" "/Applications/"
sign_bundle "/Applications/JanCode 智能体.app"
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
