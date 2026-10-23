#!/bin/bash
# 显示 mitmproxy CA 证书路径与安装说明（桌面飞书抓包 HTTPS 解密必需）

set -euo pipefail

CA_DIR="${HOME}/.mitmproxy"
CA_PEM="${CA_DIR}/mitmproxy-ca-cert.pem"
CA_CER="${CA_DIR}/mitmproxy-ca-cert.cer"

if [ ! -f "${CA_PEM}" ]; then
    echo "未找到 mitmproxy 证书，先运行一次 mitmweb 生成证书:"
    echo "  bash scripts/start_capture.sh"
    echo "  (Ctrl+C 退出即可)"
    exit 1
fi

echo "mitmproxy CA 证书:"
echo "  ${CA_PEM}"
[ -f "${CA_CER}" ] && echo "  ${CA_CER}"

cat <<'EOF'

【Ubuntu / GNOME 安装步骤】
  1. 设置 → 隐私 → 安全 → 管理证书
  2. 选择「证书颁发机构」→ 导入
  3. 选择 ~/.mitmproxy/mitmproxy-ca-cert.pem
  4. 勾选「信任此 CA 以标识网站」

【命令行备选（需 sudo）】
  sudo cp ~/.mitmproxy/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/mitmproxy.crt
  sudo update-ca-certificates

安装完成后，用代理启动桌面飞书:
  bash scripts/start_feishu_desktop.sh

EOF
