#!/usr/bin/env bash
# 一键起「手机端联调环境」：模拟器 + 桌面端 + APK + 前台 App。
#
# 为什么有这个脚本：这些路径和坑在 memory/environment.md 里早就记全了，
# 但每次手工重走一遍要 10 分钟以上（找错 SDK、AVD 列表空、找 JAVA_HOME、
# 软件渲染 ANR、截图坐标错位、桌面端缺 PYTHONPATH）。跑这个脚本约 60 秒。
#
# 用法：
#   bash tools/mobile-env.sh              # 起环境，不动已装的 App
#   bash tools/mobile-env.sh --install    # 顺带重装 dist 里最新的 APK
#   bash tools/mobile-env.sh --status     # 只看状态，不启动任何东西
#   bash tools/mobile-env.sh --stop       # 关模拟器 + 桌面端

set -u

SDK="D:/app/Android/Sdk"
AVD_HOME_WIN='D:\\app\\Android\\avd'
AVD_NAME="goal11_api36_x86_64"
JDK="D:/app/Android/jdk21"
ADB="$SDK/platform-tools/adb.exe"
EMU="$SDK/emulator/emulator.exe"
HOST_ALIAS="10.0.2.2"      # 模拟器访问宿主机的固定地址
LISTEN_PORT=5001           # 手机端第二监听

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv-win/Scripts/python.exe"
[ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python.exe"

export ANDROID_AVD_HOME="$AVD_HOME_WIN"
export ANDROID_SDK_ROOT="$SDK"
export ANDROID_HOME="$SDK"
export JAVA_HOME="$JDK"
export PATH="$JDK/bin:$SDK/platform-tools:$PATH"

say() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '\n[错误] %s\n' "$*" >&2; exit 1; }

# ---------- 状态 ----------
emu_running() {
  "$ADB" devices 2>/dev/null | grep -qE '^emulator-[0-9]+[[:space:]]+device'
}
desktop_running() {
  netstat -ano 2>/dev/null | grep LISTENING | grep -qE ":$LISTEN_PORT[[:space:]]"
}
show_status() {
  say "模拟器：$(emu_running && echo 运行中 || echo 未运行)"
  say "桌面端 :$LISTEN_PORT：$(desktop_running && echo 监听中 || echo 未监听)"
  if emu_running; then
    say "已装 App：$("$ADB" shell pm list packages 2>/dev/null | grep -c simpleerp) 个"
    say "App 前台：$("$ADB" shell dumpsys window 2>/dev/null | grep -c 'mCurrentFocus.*simpleerp') "
  fi
}

case "${1:-}" in
  --status) show_status; exit 0 ;;
  --stop)
    emu_running && "$ADB" emu kill >/dev/null 2>&1 && say "模拟器已关闭"
    pkill -f 'desktop_app.py' 2>/dev/null && say "桌面端已关闭"
    exit 0 ;;
esac

WANT_INSTALL=0
[ "${1:-}" = "--install" ] && WANT_INSTALL=1

# ---------- 1. 模拟器 ----------
if emu_running; then
  say "模拟器已在运行，跳过启动"
else
  say "启动模拟器 $AVD_NAME（必须 -gpu host：swiftshader 会让 systemui ANR）"
  # 带窗口，绝不加 -no-window：用户要看得见画面
  nohup "$EMU" -avd "$AVD_NAME" -gpu host -no-boot-anim \
    >"$ROOT/logs/emulator.log" 2>&1 &
  "$ADB" wait-for-device || die "adb 等不到设备"
  say "等待系统启动完成…"
  for _ in $(seq 1 60); do
    [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] && break
    sleep 2
  done
  [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] \
    || die "系统 120 秒没启动完，看 logs/emulator.log"
  say "系统已启动"
fi

# ---------- 2. 桌面端（5001 第二监听） ----------
if desktop_running; then
  say "桌面端已在监听 :$LISTEN_PORT，跳过启动"
else
  say "启动桌面端（PYTHONPATH 必须指到 app/，否则 ModuleNotFoundError: erp）"
  ( cd "$ROOT" && PYTHONPATH="$ROOT/app" nohup "$PY" desktop_app.py \
      >>"$ROOT/logs/desktop.log" 2>&1 & )
  for _ in $(seq 1 30); do
    desktop_running && break
    sleep 1
  done
  desktop_running || die "桌面端 30 秒内没监听 :$LISTEN_PORT，看 logs/desktop.log"
  say "桌面端已就绪"
fi

# ---------- 3. APK ----------
APK="$(ls -t "$ROOT"/dist/simple-erp-mobile-v*.apk 2>/dev/null | head -1)"
if [ "$WANT_INSTALL" = "1" ] && [ -n "$APK" ]; then
  say "安装 $APK"
  "$ADB" install -r "$APK" 2>&1 | tail -2
elif [ -n "$APK" ]; then
  say "已装 App 保持不动（要重装加 --install）：$(basename "$APK")"
fi

# ---------- 4. 起 App ----------
"$ADB" shell am start -n com.simpleerp.mobile/.MainActivity >/dev/null 2>&1 \
  && say "App 已拉起" || say "App 拉起失败（装了吗？加 --install）"

say "环境就绪。配对地址填：$HOST_ALIAS:$LISTEN_PORT"
say "模拟器窗口留着，可以直接点。"
