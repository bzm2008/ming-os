#!/bin/bash
# 冷启动热键的**一条命令验收**：等你按一次热键，然后自动判定整条链路。
#
# 判定链（每一步都给出「过了/没过 + 为什么」）：
#   ① 守护进程收到了按键吗？   → ~/Library/Logs/铭荼/hotkey.log 里有没有「触发 <键>」
#   ② 应用被 URL 拉起来了吗？   → ~/Library/Logs/铭荼/app.log 里有没有「被 summon URL 拉起」
#   ③ 只弹面板、没弹主窗口吗？  → app.log 里 [main] 行数应为 0
#   ④ 面板真的在屏幕上、位置对吗？→ scripts/winlist.c（问 WindowServer 要窗口几何）
#
# 用法：
#   scripts/check_summon_coldstart.sh            # 先把应用关掉，然后等你按键
#   scripts/check_summon_coldstart.sh 60         # 最多等 60 秒
#
# 退出码 0 = 四项全过；非 0 = 有项没过（输出里会说明是哪一项、以及该怎么查）。

set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOTKEY_LOG="$HOME/Library/Logs/铭荼/hotkey.log"
APP_LOG="$HOME/Library/Logs/铭荼/app.log"
WAIT="${1:-120}"
WINLIST="/tmp/ming-tea-winlist"

pass=0
fail=0
ok()   { printf '  ✅ %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf '  ❌ %s\n' "$1"; fail=$((fail + 1)); }
info() { printf '     %s\n' "$1"; }

# 0. 依赖：窗口探针
if [ ! -x "$WINLIST" ]; then
  printf '编译窗口探针…\n'
  clang -O2 -framework CoreGraphics -framework CoreFoundation "$ROOT/scripts/winlist.c" -o "$WINLIST" 2>/dev/null \
    || { echo "编译 winlist.c 失败（需要 Command Line Tools）"; exit 2; }
fi

hotkey_lines=$(wc -l < "$HOTKEY_LOG" 2>/dev/null | tr -d ' ')
hotkey_lines="${hotkey_lines:-0}"

# 1. 先把应用关掉 —— 这样按热键才是真正的冷启动
if pgrep -f "[m]ing-tea-desktop" > /dev/null; then
  printf '先退出正在运行的应用…\n'
  pkill -f "[m]ing-tea-desktop"
  for _ in $(seq 1 20); do pgrep -f "[m]ing-tea-desktop" > /dev/null || break; sleep 0.5; done
fi
: > "$APP_LOG" 2>/dev/null || true

cat <<EOF

请现在按一次热键（三个都注册了，任选其一）：
    ⌥ Space   /   ⌃⌥ Space   /   ⌘⇧ Space
（最多等 ${WAIT} 秒）

EOF

# 2. 等守护进程记下「触发」
fired=""
for _ in $(seq 1 $((WAIT * 5))); do
  line=$(tail -n "+$((hotkey_lines + 1))" "$HOTKEY_LOG" 2>/dev/null | grep -a "触发" | tail -1)
  if [ -n "${line}" ]; then fired="${line}"; break; fi
  sleep 0.2
done

printf '判定：\n'
if [ -n "${fired}" ]; then
  ok "① 守护进程收到按键并执行了动作：${fired#*INFO }"
else
  bad "① 守护进程没收到按键（${WAIT} 秒内 ${HOTKEY_LOG} 没有新增「触发」行）"
  info "先看有没有「收到热键事件 id=…（已注册的是 …）—— 未匹配」：有 ⇒ 事件到了但 id 对不上（我们的 bug，请报告）。"
  info "完全没有任何新行 ⇒ 键被别的应用抢走了（本机装了 ChatGPT.app，⌥Space 是它的默认唤起键）—— 换 ⌃⌥Space 或 ⌘⇧Space 再跑一次这个脚本。"
  printf '\n结果：%d 项通过，%d 项失败\n' "${pass}" "${fail}"
  exit 1
fi

# 3. 等应用被 URL 拉起来（冷启动）
launched=""
for _ in $(seq 1 100); do
  if grep -qa "被 summon URL 拉起" "$APP_LOG" 2>/dev/null; then launched=yes; break; fi
  sleep 0.2
done
if [ -n "${launched}" ]; then
  ok "② 应用被 summon URL 拉起，并且判定为「冷启动召唤」"
  grep -a "被 summon URL 拉起" "$APP_LOG" | tail -1 | sed 's/^/     /'
else
  bad "② 应用日志里没有「被 summon URL 拉起」（应用没起来，或起来后被当成普通启动）"
  info "看 $APP_LOG 末尾：若为空，应用可能根本没启动（检查 .app 是否还能打开）。"
fi

# 4. 主窗口不该被显示
# 注意：`grep -c` 在**没有匹配**时也会打印 `0` 并以 1 退出，所以不能写 `|| echo 0`
# （那会拼成 "0\n0"，实测把判断和报错都带偏）。
main_lines=$(grep -ac '^\[main\]' "$APP_LOG" 2>/dev/null)
main_lines="${main_lines:-0}"
if [ "${main_lines}" = "0" ]; then
  ok "③ 没有显示主窗口（[main] 行数 = 0）"
else
  # 变量紧跟全角字符时必须写 ${}：某些 locale 下 bash 会把多字节字符的首字节吃进变量名
  # （实测报 `main_lines<乱码>: unbound variable`）
  bad "③ 主窗口被显示了（[main] 行数 = ${main_lines}）——冷启动应该只弹面板"
fi

# 5. 面板窗口：在屏幕上，且贴在屏幕底部居中
panel="$("$WINLIST" --all 2>/dev/null | grep "铭荼助手" | head -1)"
# 屏幕尺寸也从同一个探针拿（它第一行会打印主显示器），这样底边期望值不是写死的
screen="$("$WINLIST" --all 2>/dev/null | sed -n 's/^display main \([0-9]*\)x\([0-9]*\).*/\2/p' | head -1)"
if [ -z "${panel}" ]; then
  bad "④ 找不到面板窗口"
else
  # 探针输出是 printf 定宽字段，字段间可能有**多个空格**，先压缩再解析（实测不压缩会匹配不上）
  geom=$(printf '%s' "${panel}" | tr -s ' ' | sed -n 's/.*onscreen=\([01]\) alpha=[0-9.]* \([0-9]*x[0-9]*\) @(\([0-9-]*\),\([0-9-]*\)).*/\1 \2 \3 \4/p')
  case "${geom}" in
    *x*) : ;;
    *) bad "④ 解析面板几何失败（原始行：${panel}）"; printf '\n结果：%d 项通过，%d 项失败\n' "${pass}" "${fail}"; exit 1 ;;
  esac
  set -- ${geom}
  onscreen="$1"; size="$2"; x="$3"; y="$4"
  height="${size#*x}"; bottom=$((y + height))
  info "面板：onscreen=${onscreen} 尺寸=${size} 位置=(${x},${y}) 底边=${bottom}（屏幕高 ${screen:-未知}）"
  if [ "${onscreen}" = "1" ]; then ok "④a 面板在屏幕上"; else
    bad "④a 面板窗口存在但 onscreen=0（在别的 Space 或被隐藏）"; fi
  expected=$(( ${screen:-1080} - 140 ))
  if [ "${bottom}" -eq "${expected}" ]; then
    ok "④b 底边贴在 ${expected}（屏幕高 − 140，与设计一致）"
  else
    bad "④b 底边是 ${bottom}，期望 ${expected}（面板位置错了）"
  fi
fi

printf '\n结果：%d 项通过，%d 项失败\n' "${pass}" "${fail}"
[ "${fail}" -eq 0 ] || exit 1
