#!/usr/bin/env bash
# 安装到本机。必须重新签名：PyInstaller 出来的包是 ad-hoc 签名的，
# 一旦复制/覆盖，签名就对不上了，Finder 双击会被静默拒绝
# （终端直接跑二进制却能起来，所以这个坑很容易误判成「包坏了」）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/dist/JanCode 智能体.app"

# 必须重新打包再安装。
# 之前这里只复制 dist 里的现成包，导致改了源码却装的是旧构建——
# 界面上的新功能根本没进去，测试时看到的一直是旧版，白排查好几轮。
# 必须先杀掉正在运行的旧实例。
# 之前没有这一步：覆盖了 .app 之后 open 只是把旧窗口切到前面，
# 内存里跑的还是旧代码，用户以为在测新版，其实一直在看旧界面。
pkill -f "MacOS/JanCode 智能体" 2>/dev/null || true
sleep 2

echo "重新打包…"
bash "$ROOT/scripts/build-desktop.sh" >/dev/null
[ -d "$APP" ] || { echo "打包失败：dist 里没有产物"; exit 1; }
echo "打包完成，开始安装。"

# 先清扩展属性，否则签名会报 resource fork not allowed 而失败。
# 不用 --deep：bundle 里没有 CodeResources，--deep 生成的资源封印
# 会让 codesign -v 报 "code has no resources but signature indicates
# they must be present"，那份包双击照样打不开。
sign_bundle() {
  local APP="$1"
  # iCloud 文件提供器会给 Python.framework 重打 fpfs 属性，光
  # xattr -cr 一次常常不够——手工装时每次都要清两遍才签上。
  # 实测固化三步：删 AppleDouble、逐文件清、定点删 fpfs/FinderInfo。
  find "$APP" -name '._*' -delete 2>/dev/null || true
  find "$APP" -name '.DS_Store' -delete 2>/dev/null || true
  xattr -cr "$APP" 2>/dev/null || true
  find "$APP" -exec xattr -c {} \; 2>/dev/null || true
  xattr -dr com.apple.fileprovider.fpfs#P "$APP" 2>/dev/null || true
  xattr -dr com.apple.FinderInfo "$APP" 2>/dev/null || true
  find "$APP/Contents/MacOS" -type f -perm +111 2>/dev/null | while read -r f; do
    codesign --force --sign - "$f" >/dev/null 2>&1 || true
  done
  [ -d "$APP/Contents/Frameworks" ] && codesign --force --sign - "$APP/Contents/Frameworks" >/dev/null 2>&1 || true
  codesign --force --sign - "$APP" >/dev/null 2>&1 || true
}

sign_bundle "$APP"
rm -rf "/Applications/JanCode 智能体.app"
# ditto 不带扩展属性；cp -R 会把 fpfs 属性原样带进 /Applications，
# 装完的包验证签名就失败。
ditto "$APP" "/Applications/JanCode 智能体.app"
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
