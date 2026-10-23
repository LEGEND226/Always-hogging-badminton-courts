#!/bin/bash
# 注册 crontab 定时任务：每天 23:59 启动，脚本内部精确等到 0 点再抢场

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-/usr/bin/python3}"
BOOKING_MODE="${BOOKING_MODE:-api}"
CRON_MARKER="# feishu-badminton-booking"

# 探测当前可用 DISPLAY（优先已有环境，否则扫 /tmp/.X11-unix）
detect_display() {
    if [ -n "${DISPLAY_FOR_CRON:-}" ]; then
        echo "${DISPLAY_FOR_CRON}"
        return
    fi
    if [ -n "${DISPLAY:-}" ]; then
        local num="${DISPLAY#:}"
        num="${num%%.*}"
        if [ -n "${num}" ] && [ -S "/tmp/.X11-unix/X${num}" ]; then
            echo ":${num}"
            return
        fi
    fi
    for n in 0 1 2 3; do
        if [ -S "/tmp/.X11-unix/X${n}" ]; then
            echo ":${n}"
            return
        fi
    done
    echo "${DISPLAY:-:0}"
}

detect_xauthority() {
    if [ -n "${XAUTHORITY_FOR_CRON:-}" ] && [ -f "${XAUTHORITY_FOR_CRON}" ]; then
        echo "${XAUTHORITY_FOR_CRON}"
        return
    fi
    if [ -n "${XAUTHORITY:-}" ] && [ -f "${XAUTHORITY}" ]; then
        echo "${XAUTHORITY}"
        return
    fi
    if [ -f "${HOME}/.Xauthority" ]; then
        echo "${HOME}/.Xauthority"
        return
    fi
    local gdm_xauth="/run/user/$(id -u)/gdm/Xauthority"
    if [ -f "${gdm_xauth}" ]; then
        echo "${gdm_xauth}"
        return
    fi
    echo ""
}

DISPLAY_FOR_CRON="$(detect_display)"
XAUTHORITY_FOR_CRON="$(detect_xauthority)"

mkdir -p "${SCRIPT_DIR}/logs"

if ! command -v python3 &>/dev/null && [ ! -x "${PYTHON}" ]; then
    echo "错误: 未找到 python3" >&2
    exit 1
fi

if [ ! -f "${SCRIPT_DIR}/.env" ]; then
    echo "提示: 尚未创建 .env，请从 .env.example 复制并填入凭证"
    echo "  cp .env.example .env"
fi

MODE_ARGS=""
ENV_PREFIX=""
if [ "${BOOKING_MODE}" = "playwright" ]; then
    MODE_ARGS="--mode playwright"
    # pyautogui / xdotool 需要 DISPLAY；尽量带上 XAUTHORITY + session bus
    # 注意：main.py 启动时还会 ensure_display() / wake_idle_session() 再纠错一次
    ENV_PREFIX="DISPLAY=${DISPLAY_FOR_CRON}"
    if [ -n "${XAUTHORITY_FOR_CRON}" ]; then
        ENV_PREFIX="${ENV_PREFIX} XAUTHORITY=${XAUTHORITY_FOR_CRON}"
    fi
    DBUS_PATH="/run/user/$(id -u)/bus"
    if [ -S "${DBUS_PATH}" ] || [ -e "${DBUS_PATH}" ]; then
        ENV_PREFIX="${ENV_PREFIX} DBUS_SESSION_BUS_ADDRESS=unix:path=${DBUS_PATH}"
    fi
    echo "模式: Playwright UI 自动化（cron 将注入 ${ENV_PREFIX}）"
    if [ ! -S "/tmp/.X11-unix/X${DISPLAY_FOR_CRON#:}" ]; then
        echo "警告: 当前探测到的 DISPLAY=${DISPLAY_FOR_CRON} 无 X11 socket，今晚可能失败"
    fi
else
    echo "模式: API 直连"
fi

if [ -n "${ENV_PREFIX}" ]; then
    # 不要包 systemd-inhibit：cron 常 Access denied，导致 Python 根本没启动
    # 空闲抑制改由 main.py 内 start_idle_inhibit() 处理
    CRON_LINE="59 23 * * * cd ${SCRIPT_DIR} && ${ENV_PREFIX} ${PYTHON} main.py ${MODE_ARGS} >> logs/run.log 2>&1"
else
    CRON_LINE="59 23 * * * cd ${SCRIPT_DIR} && ${PYTHON} main.py ${MODE_ARGS} >> logs/run.log 2>&1"
fi

CURRENT_CRON="$(crontab -l 2>/dev/null || true)"
if echo "${CURRENT_CRON}" | grep -q "${CRON_MARKER}"; then
    echo "定时任务已存在，正在更新..."
    NEW_CRON="$(echo "${CURRENT_CRON}" | grep -v "${CRON_MARKER}" | grep -v "${SCRIPT_DIR}.*main.py" || true)"
else
    NEW_CRON="${CURRENT_CRON}"
fi

{
    echo "${NEW_CRON}" | sed '/^$/d'
    echo "${CRON_MARKER}"
    echo "${CRON_LINE}"
} | crontab -

echo "已注册 crontab 定时任务:"
echo "  ${CRON_LINE}"
echo ""
echo "切换模式:"
echo "  BOOKING_MODE=api         bash install_cron.sh   # 抓包 API 模式（默认）"
echo "  BOOKING_MODE=playwright  bash install_cron.sh   # UI 自动化模式"
echo ""
echo "正式抢场前请确认 config.yaml:"
echo "  booking.dry_run: false"
echo "  booking.open_time: 00:00:00"
echo ""
echo "当晚请保持: 已登录桌面、飞书已登录、不休眠/尽量不锁屏"
echo "日志: tail -f ${SCRIPT_DIR}/logs/run.log"
echo ""
echo "查看当前 crontab: crontab -l"
echo "手动测试:"
echo "  cd ${SCRIPT_DIR} && python3 main.py ${MODE_ARGS} --now"
echo "取消定时: crontab -e  （删除带 ${CRON_MARKER} / main.py 的行）"
