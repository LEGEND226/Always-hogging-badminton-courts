#!/bin/bash
# 验证抢场流程：dry_run → 配置检查 → 可选真实模式

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
cd "${PROJECT_DIR}"

echo "=== 1. 检查依赖 ==="
python3 -c "import yaml, requests, dotenv; print('核心依赖 OK')"

echo ""
echo "=== 2. dry_run 模拟抢场 ==="
python3 main.py --now
echo "dry_run 通过"

echo ""
echo "=== 3. 检查配置 ==="
if [ ! -f .env ]; then
    echo "警告: .env 不存在，请 cp .env.example .env 并填入抓包凭证"
elif grep -qE '^(COOKIE|AUTHORIZATION)=[^[:space:]]' .env 2>/dev/null; then
    echo ".env 凭证已配置"
else
    echo "警告: .env 中 COOKIE/AUTHORIZATION 为空，抓包后运行:"
    echo "  python3 tools/generate_config.py captures/booking.har --apply"
fi

if grep -q 'your-booking-api.example.com' config.yaml 2>/dev/null; then
    echo "警告: config.yaml 中 api.base_url 仍为占位符，需抓包后更新"
else
    echo "config.yaml API 地址已配置"
fi

echo ""
echo "=== 4. HAR 订场 API 检查 ==="
if [ -s captures/booking.har ]; then
    bash scripts/check_capture.sh captures/booking.har || true
else
    echo "captures/booking.har 不存在，跳过"
fi

echo ""
echo "=== 5. 定时任务 ==="
crontab -l 2>/dev/null | grep -q feishu-badminton && echo "crontab 已注册" || echo "未注册，运行: bash install_cron.sh"

echo ""
echo "=== 验证完成 ==="
echo "桌面飞书抓包: bash scripts/start_feishu_desktop.sh"
echo "Playwright 降级: bash scripts/start_feishu_playwright.sh codegen"
echo "Playwright 定时: BOOKING_MODE=playwright bash install_cron.sh"
