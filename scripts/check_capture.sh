#!/bin/bash
# 检查 captures/booking.har 是否包含订场 API（排除飞书监控域名）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
HAR="${1:-${PROJECT_DIR}/captures/booking.har}"

if [ ! -s "${HAR}" ]; then
    echo "错误: HAR 文件不存在或为空: ${HAR}"
    echo "请先运行 bash scripts/start_capture.sh 并完成订场操作"
    exit 1
fi

echo "分析 ${HAR} ..."
python3 "${PROJECT_DIR}/tools/check_har.py" "${HAR}"
