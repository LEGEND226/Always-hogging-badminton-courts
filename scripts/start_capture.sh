#!/bin/bash
# 启动 mitmproxy 抓包环境，用于飞书 App 订场小程序（无需网页版）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
CAPTURE_DIR="${PROJECT_DIR}/captures"
PORT="${MITM_PORT:-8888}"
WEB_PORT="${MITM_WEB_PORT:-8081}"

mkdir -p "${CAPTURE_DIR}"

if ! command -v mitmweb &>/dev/null; then
    echo "未安装 mitmproxy，正在安装..."
    pip install mitmproxy
fi

LAN_IP="$("${SCRIPT_DIR}/get_lan_ip.sh")"

cat <<EOF

========================================
  飞书订场小程序抓包 — 操作指南
========================================

【电脑端】mitmproxy 即将启动
  代理端口: ${PORT}
  管理界面: http://127.0.0.1:${WEB_PORT}
  抓包文件: ${CAPTURE_DIR}/

【手机端 — iPhone】
  1. 手机与电脑连接同一 Wi-Fi
  2. Wi-Fi → 配置代理 → 手动: ${LAN_IP}:${PORT}
  3. Safari 打开 http://mitm.it → 安装 Apple 证书
  4. 设置 → 通用 → 关于本机 → 证书信任设置 → 开启 mitmproxy 完全信任

【手机端 — 安卓】
  1. Wi-Fi 代理 → ${LAN_IP}:${PORT}
  2. 浏览器打开 http://mitm.it 安装证书

【桌面飞书 — 推荐再试】
  终端1: bash scripts/start_capture.sh
  终端2: bash scripts/install_mitm_ca.sh   # 安装 CA 到系统
         bash scripts/start_feishu_desktop.sh

【飞书 App / 桌面 操作】
  1. 打开飞书 → 进入羽毛球预约小程序
  2. 完整走一遍: 选日期 → 选场地 → 点提交
  3. 回到 mitmweb 界面 (http://127.0.0.1:${WEB_PORT})
  4. 过滤关键词: venue / court / book / order / reserve
  5. 导出（任选其一）:
     a) 自动: Ctrl+C 停 mitmweb → captures/booking.har 自动保存
     b) 手动: mitmweb 界面 → 选中请求 → Export → 保存到
        ${CAPTURE_DIR}/booking.har
     c) 复制单条 cURL 到 ${CAPTURE_DIR}/book.curl

  若 Ctrl+C 后 booking.har 仍为空，请用方式 b 手动导出

【抓包完成后】
  cd ${PROJECT_DIR}
  bash scripts/check_capture.sh captures/booking.har
  python3 tools/generate_config.py captures/booking.har --apply
  python3 main.py --now

【Linux 桌面飞书】
  bash scripts/install_mitm_ca.sh
  bash scripts/start_feishu_desktop.sh

【排查】
  - check_capture 报未找到订场 API → 手机端 LGW 绕过代理，改桌面飞书
  - 桌面也不行 → Playwright 降级:
      bash scripts/start_feishu_playwright.sh codegen
      python3 main.py --mode playwright --now

========================================

EOF

echo "启动 mitmweb（含自动保存 HAR 插件）..."
exec mitmweb -p "${PORT}" --web-port "${WEB_PORT}" --set block_global=false \
    -s "${PROJECT_DIR}/tools/save_har_addon.py"
