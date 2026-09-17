#!/usr/bin/env bash
# JanCode Agent 一键接入（中转站专用）
#
# 对齐站点上 codex 接入脚本的用法，但装的是 jancode-agent——
# 一个只接国内模型、配置透明的开源终端智能体。没有 codex 也能用。
#
# 用法（在终端里粘贴运行）：
#   curl -sL https://你的站点/setup-jancode.sh | bash -s -- \
#        --url https://你的站点/v1 --key <你的密钥>
#
# 可选参数：
#   --model 模型ID     指定默认模型（不填则取站点模型清单第一个）
#   --list             只看站点上有哪些模型，不安装不写配置
#   --agent 名称       命令名（默认 jancode-agent）
#
# 脚本会做四件事：
#   1. 确认本机有 jancode-agent（没有就用官方 install.sh 装）
#   2. 把站点地址与密钥写进 ~/.jancode-agent/config.toml（已有时先备份）
#   3. 真发一次请求验证连通，失败时把站点的原始报错带出来
#
# 脚本只写 ~/.jancode-agent/ 一个目录，不碰系统 Python、不动其他配置。
set -euo pipefail

URL=""; KEY=""; MODEL=""; LIST=0; AGENT="jancode-agent"
REPO_RAW="https://raw.githubusercontent.com/janzhao838-star/jancode-agent/main"

say() { printf '%s\n' "$*"; }
ok()  { printf '  ✓ %s\n' "$*"; }
die() { printf '\n接入中止：%s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --url)   URL="${2-}"; shift 2 ;;
    --key)   KEY="${2-}"; shift 2 ;;
    --model) MODEL="${2-}"; shift 2 ;;
    --agent) AGENT="${2-}"; shift 2 ;;
    --list)  LIST=1; shift ;;
    --help|-h)
      sed -n "2,26p" "$0"; exit 0 ;;
    *) die "不认识的参数：$1（用 --help 看用法）" ;;
  esac
done

[ -n "$URL" ] || die "缺少 --url。例如：--url https://你的站点/v1"
case "$URL" in
  http://*|https://*) ;;
  *) die "--url 必须以 http(s):// 开头，当前是：${URL}" ;;
esac
# 接口地址按惯例以 /v1 结尾；少写了就补上（填成网站首页是常见笔误）。
URL="${URL%/}"
case "$URL" in
  */v1) ;;
  */v1/*) die "--url 应写到 /v1 为止，当前是：${URL}" ;;
  *) URL="$URL/v1" ;;
esac

# 密钥：参数 > 环境变量
[ -n "$KEY" ] || KEY="${JANCODE_API_KEY-}"
[ -n "$KEY" ] || die "缺少 --key。请在站点的「令牌」页面创建后粘贴到这里"

command -v curl >/dev/null 2>&1 || die "需要 curl，请先安装"
command -v python3 >/dev/null 2>&1 || die "需要 python3（3.11 以上），请先安装"

fetch_models() {
  curl -sS -m 15 -H "Authorization: Bearer $KEY" "$URL/models"
}

# --list：只看模型清单，不安装不写配置
if [ "$LIST" = 1 ]; then
  say "站点模型清单（${URL}）："
  fetch_models | python3 -c '
import json,sys
raw = sys.stdin.read()
try:
    data = json.loads(raw)
except ValueError:
    print("站点返回的不是 JSON："); print(raw[:400]); raise SystemExit(1)
ids = [m.get("id","") for m in data.get("data",[])] or list(data)
ids = [i for i in ids if i]
if not ids: print("（清单为空）")
for i in ids: print(" ", i)
'
  exit 0
fi

say ""
say "JanCode Agent 一键接入"
say "─────────────────────────────"
say "站点：${URL}"
say ""

# 1. 确认 jancode-agent 存在
say "① 检查 jancode-agent"
if command -v "$AGENT" >/dev/null 2>&1; then
  ok "$($AGENT --version 2>/dev/null || echo "$AGENT") 已安装：$(command -v "$AGENT")"
else
  say "  未安装，用官方脚本安装（只装到 ~/.jancode-agent/，不动系统 Python）…"
  curl -fsSL "$REPO_RAW/scripts/install.sh" -o /tmp/jancode-install.sh \
    || die "下载安装脚本失败。请检查网络，或到 https://github.com/janzhao838-star/jancode-agent 手动安装"
  bash /tmp/jancode-install.sh
  rm -f /tmp/jancode-install.sh
  # 安装脚本把命令放进 ~/.jancode-agent/bin，当前 shell 可能还没收录
  export PATH="$HOME/.jancode-agent/bin:$PATH"
  command -v "$AGENT" >/dev/null 2>&1 || die "安装后仍找不到 $AGENT，请重开一个终端再试"
  ok "已安装：$(command -v "$AGENT")"
fi

# 2. 默认模型：不填就取清单第一个
if [ -z "$MODEL" ]; then
  say "② 获取默认模型"
  MODEL=$(fetch_models | python3 -c '
import json,sys
try:
    data = json.loads(sys.stdin.read())
    ids = [m.get("id","") for m in data.get("data",[])] or list(data)
    ids = [i for i in ids if i]
    print(ids[0] if ids else "")
except Exception:
    print("")
'
  )
  if [ -z "$MODEL" ]; then
    say "  ! 拿不到模型清单，用 deepseek-v4-pro 作为默认（可在配置文件里改）"
    MODEL="deepseek-v4-pro"
  else
    ok "默认模型：${MODEL}"
  fi
else
  say "② 默认模型：$MODEL（命令行指定）"
fi

# 3. 写配置（已存在先备份，绝不静默覆盖用户手配的内容）
say "③ 写入配置"
CONF="$HOME/.jancode-agent/config.toml"
mkdir -p "$HOME/.jancode-agent"
if [ -f "$CONF" ]; then
  BAK="$CONF.bak.$(date +%Y%m%d%H%M%S)"
  cp "$CONF" "$BAK"
  say "  已备份原配置到 $BAK"
fi
cat > "$CONF" <<TOML
# 由 setup-jancode.sh 写入；手改过的话本脚本会先备份再写。
[provider]
name = "custom"
base_url = "$URL"
api_key = "$KEY"
model = "$MODEL"
wire_api = "chat"

[agent]
max_steps = 40
allow_bash = true
bash_timeout = 120
TOML
chmod 600 "$CONF"
ok "已写入 ${CONF}"

# 4. 真发一次请求验证
say "④ 验证连通"
HTTP=$(curl -sS -m 20 -o /tmp/jancode-probe.json -w '%{http_code}' \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"'"$MODEL"'","messages":[{"role":"user","content":"ping"}],"max_tokens":1}' \
  "$URL/chat/completions" || echo "000")
if [ "$HTTP" = "000" ]; then
  die "连不上 ${URL}。检查网络后重试；站点如果在内网，确认你已连上对应网络。"
fi
if [ "$HTTP" = "401" ] || [ "$HTTP" = "403" ]; then
  say "  站点原始报错：$(head -c 300 /tmp/jancode-probe.json)"
  die "密钥被拒绝（${HTTP}）。到站点的「令牌」页面重新创建一个，再跑一遍本命令。"
fi
if [ "$HTTP" != "200" ]; then
  say "  站点原始报错：$(head -c 300 /tmp/jancode-probe.json)"
  die "站点返回 ${HTTP}。把上面的报错发给站点管理员，或换个模型试试：--model 模型ID"
fi
rm -f /tmp/jancode-probe.json
ok "连通正常"

say ""
say "接入完成！直接运行："
say "  $AGENT --web          # 图形界面"
say "  $AGENT 写个快排       # 命令行直接交代任务"
say ""

