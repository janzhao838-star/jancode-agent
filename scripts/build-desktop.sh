#!/usr/bin/env bash
# 打桌面版安装包。macOS 出 .app，Windows 出单文件 .exe。
#
# 用法：bash scripts/build-desktop.sh
# 依赖装在虚拟环境里的话不用管，脚本会自己找到它。
set -euo pipefail

cd "$(dirname "$0")/.."

# 优先用仓库里的虚拟环境。直接用系统的 python3 往往会得到
# "No module named PyInstaller"——依赖装在虚拟环境里，系统解释器看不见。
if [ -z "${PYTHON:-}" ] && [ -z "${PY:-}" ]; then
  for cand in .venv-desktop/bin/python .venv/bin/python; do
    if [ -x "$cand" ]; then PY="$cand"; break; fi
  done
fi
# 兼容两种传法：PYTHON=xx 或 PY=xx，脚本内部统一用 PY。
PY="${PY:-${PYTHON:-python3}}"

if ! "$PY" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "缺少打包依赖。先执行：" >&2
  echo "  $PY -m pip install -e \".[desktop]\" pyinstaller" >&2
  exit 1
fi

echo "==> 使用解释器 $PY"
echo "==> 清理上一次的产物"
rm -rf build/pyi dist/JanCode*

echo "==> 清理产物目录的扩展属性（PyInstaller 复制旧产物时会把 Finder 垃圾一并带来，
#     而它内置的签名步骤一看到这些垃圾就会拒签）"
mkdir -p dist
xattr -cr dist 2>/dev/null || true

echo "==> 冻结应用"
"$PY" -m PyInstaller --noconfirm --clean \
  --distpath dist --workpath build/pyi \
  packaging/jancode-desktop.spec

if [ "$(uname)" = "Darwin" ]; then
  APP="dist/JanCode 智能体.app"
  # 扩展属性（Finder 信息、资源分支）会让 codesign 直接报
  # "resource fork, Finder information, or similar detritus not allowed"。
  # 不清掉的话打出来的 app 处于未签名状态，用户双击会被系统拦下。
  echo "==> 复查扩展属性并做临时签名"
  xattr -cr "$APP"
  # bundle 层的重签失败不算致命：PyInstaller 已经给内部可执行文件做过 ad-hoc
  # 签名，app 本身能正常启动。为了一次签名警告把整个构建判失败，反而打不出包。
  # 真签名要苹果开发者证书，开源项目没有，用户首次打开右键「打开」即可。
  codesign --force --deep --sign - "$APP" || echo "（bundle 重签未成功，不影响运行）"
  codesign --verify "$APP" && echo "==> 签名验证通过"

  echo "==> 打 zip（必须用 ditto：zip 会丢掉符号链接和执行权限）"
  (cd dist && ditto -c -k --sequesterRsrc --keepParent "JanCode 智能体.app" "JanCode-Agent-macos-$(uname -m).zip")

  echo "==> 产物："
  ls -lh dist/JanCode-Agent-*.zip
else
  echo "==> 产物："
  ls -lh dist/JanCode-Agent.exe
fi
