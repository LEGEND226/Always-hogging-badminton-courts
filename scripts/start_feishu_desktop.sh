#!/bin/bash
# 通过系统代理启动桌面飞书，配合 mitmweb 抓包订场 API

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
PORT="${MITM_PORT:-8888}"
PROXY="http://127.0.0.1:${PORT}"

# 常见飞书桌面客户端路径
FEISHU_CANDIDATES=(
    "${FEISHU_BIN:-}"
    "feishu"
    "lark"
    "/opt/bytedance/feishu/feishu"
    "/opt/bytedance/lark/lark"
    "${HOME}/.local/bin/feishu"
    "/usr/bin/feishu"
)

find_feishu() {
    if [ -n "${FEISHU_BIN:-}" ] && [ -x "${FEISHU_BIN}" ]; then
        echo "${FEISHU_BIN}"
        return 0
    fi
    for cmd in "${FEISHU_CANDIDATES[@]}"; do
        [ -z "${cmd}" ] && continue
        if command -v "${cmd}" &>/dev/null; then
            command -v "${cmd}"
            return 0
        fi
        if [ -x "${cmd}" ]; then
            echo "${cmd}"
            return 0
        fi
    done
    return 1
}

if ! curl -s --connect-timeout 1 -x "${PROXY}" http://mitm.it &>/dev/null; then
    echo "警告: mitmproxy 似乎未在 ${PORT} 端口运行"
    echo "请先在另一终端执行: bash scripts/start_capture.sh"
    echo ""
fi

FEISHU="$(find_feishu)" || {
    echo "错误: 未找到飞书桌面客户端" >&2
    echo "请设置环境变量 FEISHU_BIN=/path/to/feishu" >&2
    exit 1
}

cat <<EOF
========================================
  桌面飞书 + 代理抓包
========================================
  代理: ${PROXY}
  飞书: ${FEISHU}
  mitmweb: http://127.0.0.1:${MITM_WEB_PORT:-8081}

操作:
  1. 飞书 → 羽毛球预约小程序
  2. 选日期 → 选场地 → 提交
  3. mitmweb 搜索: venue / court / book / order
  4. Ctrl+C 停 mitmweb 后:
     bash scripts/check_capture.sh
     python3 tools/generate_config.py captures/booking.har --apply

若仍只有 slardar/applog 监控请求 → 改用 Playwright:
  bash scripts/start_feishu_playwright.sh
========================================

EOF

export http_proxy="${PROXY}"
export https_proxy="${PROXY}"
export HTTP_PROXY="${PROXY}"
export HTTPS_PROXY="${PROXY}"
export no_proxy="localhost,127.0.0.1"
export NO_PROXY="localhost,127.0.0.1"

exec "${FEISHU}" "$@"
