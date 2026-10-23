#!/bin/bash
# Playwright 降级：录制订场页面选择器，或测试 UI 自动化

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
cd "${PROJECT_DIR}"

install_playwright() {
    echo "安装 Playwright ..."
    pip cache purge 2>/dev/null || true
    PIP_REQUIRE_HASHES=0 pip install playwright --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple
    playwright install chromium
}

if ! python3 -c "import playwright" 2>/dev/null; then
    install_playwright
fi

if ! python3 -c "import playwright" 2>/dev/null; then
    echo "Playwright 安装失败，请手动执行:" >&2
    echo "  pip cache purge && PIP_REQUIRE_HASHES=0 pip install playwright --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple" >&2
    echo "  playwright install chromium" >&2
    exit 1
fi

START_URL="${PLAYWRIGHT_START_URL:-https://applink.feishu.cn/YOUR_APPLINK}"

cat <<EOF
========================================
  Playwright 订场自动化（抓包失败降级方案）
========================================

【1. 探测 applink 是否可在浏览器打开】
  python3 tools/probe_applink.py

【2. 录制选择器 — browser 模式】
  PLAYWRIGHT_START_URL='${START_URL}' bash scripts/start_feishu_playwright.sh codegen

【3. 标定坐标 — desktop（锁定订场弹窗，防止点到文档）】
  依赖: sudo apt-get install -y xdotool xclip python3-tk python3-dev
  先关掉飞书文档，只留订场小程序弹窗，确认窗口列表:
    python3 scripts/list_feishu_windows.py
  再标定:
    python3 scripts/record_desktop_clicks.py --apply --no-applink
  按提示移到: 阵地预约→2号场→日期→18/19/20/21→下一步→原因框→提交→确定
  运行时全程锁定同一 wid，不会切到飞书文档

【4. 测试（dry_run 跳过最后「确定」）】
  python3 main.py --mode playwright --now
  脚本用飞书客户端打开 applink（open_applink_via: client），避免过渡页
  确认无误后改 config.yaml: dry_run: false

【5. 0 点定时】
  BOOKING_MODE=playwright bash install_cron.sh

说明: 保持飞书已登录。若仍出现浏览器过渡页，可:
  python3 scripts/record_desktop_clicks.py --apply --record-pre-open
========================================
EOF

if [ "${1:-}" = "codegen" ]; then
    if [ -n "${START_URL}" ]; then
        exec playwright codegen "${START_URL}"
    else
        echo "未设置 PLAYWRIGHT_START_URL，启动空白 codegen（手动导航到订场页）"
        exec playwright codegen
    fi
elif [ "${1:-}" = "record" ]; then
    pip install pyautogui 2>/dev/null || true
    exec python3 scripts/record_desktop_clicks.py --apply --no-applink --prepare "${2:-10}"
elif [ "${1:-}" = "probe" ]; then
    exec python3 tools/probe_applink.py
elif [ "${1:-}" = "test" ]; then
    exec python3 main.py --mode playwright --now
else
    echo "用法:"
    echo "  bash scripts/start_feishu_playwright.sh probe    # 探测 applink"
    echo "  bash scripts/start_feishu_playwright.sh codegen  # 录制 browser selector"
    echo "  bash scripts/start_feishu_playwright.sh record   # 标定 desktop 坐标"
    echo "  bash scripts/start_feishu_playwright.sh test     # 立即测试"
fi
