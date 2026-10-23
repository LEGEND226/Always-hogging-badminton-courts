#!/bin/bash
# 获取本机局域网 IP，供手机配置 Wi-Fi 代理

get_ip() {
    ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}'
}

IP="$(get_ip)"
if [ -z "${IP}" ]; then
    IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi

if [ -z "${IP}" ]; then
    echo "无法自动检测 IP，请手动执行: ip addr" >&2
    exit 1
fi

echo "${IP}"
