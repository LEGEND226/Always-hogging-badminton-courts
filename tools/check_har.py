#!/usr/bin/env python3
"""检查 HAR 是否包含订场 API，排除飞书监控/埋点域名。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_config import (  # noqa: E402
    BOOKING_DOMAINS_HINT,
    NOISE_DOMAINS,
    classify_request,
    filter_booking_requests,
)
from parse_har import HarLoadError, extract_requests, load_har  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 HAR 订场 API")
    parser.add_argument("har_file", type=Path)
    args = parser.parse_args()

    if not args.har_file.exists() or args.har_file.stat().st_size == 0:
        print(f"错误: HAR 文件不存在或为空: {args.har_file}", file=sys.stderr)
        print("请先运行 bash scripts/start_capture.sh 并完成订场操作", file=sys.stderr)
        print("或从 mitmweb 手动 Export HAR 到 captures/booking.har", file=sys.stderr)
        return 1

    try:
        har = load_har(args.har_file)
    except HarLoadError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    all_reqs = extract_requests(har)
    booking_reqs = filter_booking_requests(all_reqs)

    print(f"总请求: {len(all_reqs)}")
    print(f"订场相关（排除监控）: {len(booking_reqs)}")

    classified = {}
    for req in booking_reqs:
        kind = classify_request(req)
        if kind != "other":
            classified.setdefault(kind, []).append(req["url"][:120])

    if classified:
        print("\n识别到的 API:")
        for kind, urls in classified.items():
            print(f"  [{kind}]")
            for u in urls[:3]:
                print(f"    - {u}")
        print("\n结论: 抓包成功，可运行 generate_config.py --apply")
        return 0

    print("\n排除的监控域名示例:", ", ".join(sorted(NOISE_DOMAINS)[:5]), "...")
    print("\n结论: 未找到订场 API，可能原因:")
    print("  - 飞书 LGW/Bridge 绕过代理（手机端常见）")
    print("  - 桌面飞书未走系统代理 → 试 bash scripts/start_feishu_desktop.sh")
    print("  - SSL Pinning → 改用 Playwright:")
    print("      bash scripts/start_feishu_playwright.sh codegen")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
