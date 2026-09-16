#!/usr/bin/env bash
# JanCode Agent 一键安装（macOS / Linux）
#
# 目标：让不写程序的人也能装起来。所以每一处可能出问题的地方都要
# 说清楚「发生了什么、该怎么办」，而不是丢一个报错就走。
set -euo pipefail

REPO="${JANCODE_AGENT_REPO:-https://github.com/janzhao838-star/jancode-agent.git}"
TARGET="${JANCODE_AGENT_DIR:-$HOME/.jancode-agent/src}"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  ✓ %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*" >&2; }
die()  { printf '\n安装中止：%s\n' "$*" >&2; exit 1; }

say ""
say "JanCode Agent 安装程序"
say "─────────────────────────────"

# ── 1. 找 Python ──
say ""
say "① 检查 Python"
PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    # 必须 3.11 以上：用到了 tomllib 与新语法
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      PY="$candidate"; break
    else
      warn "$candidate 版本过低（需要 3.11 以上）"
    fi
  fi
done
if [ -z "$PY" ]; then
  die "没有找到 Python 3.11 以上。
    怎么装：
      macOS   brew install python@3.12
      Ubuntu  sudo apt install python3.12 python3.12-venv
    装完再运行一次本脚本。"
fi
ok "$PY （$($PY -V 2>&1)）"

# ── 2. 取源码 ──
say ""
say "② 获取源码"
if [ -d "$TARGET/.git" ]; then
  say "  已存在，尝试更新…"
  if git -C "$TARGET" pull --ff-only --quiet 2>/dev/null; then
    ok "已更新到最新"
  else
    warn "更新失败（可能是本地有改动），继续使用现有版本"
  fi
else
  command -v git >/dev/null 2>&1 || die "没有找到 git。
    怎么装：macOS 运行 xcode-select --install；Ubuntu 运行 sudo apt install git"
  mkdir -p "$(dirname "$TARGET")"
  if ! git clone --quiet --depth 1 "$REPO" "$TARGET" 2>/dev/null; then
    die "无法下载源码。请检查网络能否访问 GitHub。
    如果网络受限，可以设代理后重试：
      export https_proxy=http://127.0.0.1:7890"
  fi
  ok "已下载到 $TARGET"
fi

# ── 3. 建虚拟环境并安装 ──
say ""
say "③ 安装到独立环境（不会污染系统 Python）"
VENV="$HOME/.jancode-agent/venv"
if [ ! -d "$VENV" ]; then
  "$PY" -m venv "$VENV" || die "创建虚拟环境失败。
    常见原因：缺少 venv 模块。Ubuntu 上运行 sudo apt install python3-venv"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
if ! "$VENV/bin/python" -m pip install --quiet -e "$TARGET" 2>/dev/null; then
  # 用默认源失败时换国内源重试——用户多半在国内
  say "  默认源失败，改用国内镜像重试…"
  "$VENV/bin/python" -m pip install --quiet -e "$TARGET" \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    || die "安装依赖失败。请检查网络。
    也可以手动指定镜像：
      $VENV/bin/python -m pip install -e $TARGET -i <你的镜像地址>"
fi
ok "已安装"

# ── 4. 放一个启动命令 ──
say ""
say "④ 创建启动命令"
BIN="$HOME/.jancode-agent/bin"
mkdir -p "$BIN"
cat > "$BIN/jancode-agent" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python" -m jancode_agent.cli "\$@"
EOF
chmod +x "$BIN/jancode-agent"
ok "$BIN/jancode-agent"

# 把 bin 目录加进 PATH（写进 shell 配置，只加一次）
MARK="# JanCode Agent"
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
  [ -f "$rc" ] || continue
  if ! grep -q "$MARK" "$rc" 2>/dev/null; then
    printf '\n%s\nexport PATH="%s:$PATH"\n' "$MARK" "$BIN" >> "$rc"
    ok "已把启动命令加入 $rc"
  fi
done

# ── 5. 密钥与收尾 ──
say ""
say "─────────────────────────────"
say "安装完成。"
say ""
if [ -n "${JANCODE_API_KEY:-}" ]; then
  ok "已检测到密钥环境变量"
else
  warn "还没有设置 API 密钥。用之前先执行（把 sk-xxx 换成你的密钥）："
  say "      export JANCODE_API_KEY=sk-xxx"
  say "    想永久生效就写进 ~/.zshrc"
fi
say ""
say "怎么用（新开一个终端窗口）："
say "    jancode-agent --web              # 图形界面"
say "    jancode-agent --list-providers   # 看有哪些模型供应商"
say "    jancode-agent \"读一下这个项目\"     # 直接派活"
say ""
say "如果提示找不到命令，执行一次："
say "    source ~/.zshrc"
say "或者直接用完整路径：$BIN/jancode-agent --web"
say ""
