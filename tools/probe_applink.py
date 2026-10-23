#!/usr/bin/env python3
"""探测 applink 是否能在 Playwright 浏览器中打开订场页。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


def main() -> int:
    parser = argparse.ArgumentParser(description="探测飞书 applink 是否可在浏览器打开")
    parser.add_argument(
        "-c", "--config",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "config.yaml",
    )
    parser.add_argument("--timeout", type=int, default=15000)
    args = parser.parse_args()

    with args.config.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)

    url = config.get("playwright", {}).get("start_url", "")
    if not url:
        print("config.yaml 中 playwright.start_url 为空", file=sys.stderr)
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("未安装 playwright", file=sys.stderr)
        return 1

    print(f"打开: {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(args.timeout)
        try:
            resp = page.goto(url, wait_until="domcontentloaded")
            title = page.title()
            final_url = page.url
            status = resp.status if resp else "?"
            body_snippet = page.inner_text("body")[:500] if page.locator("body").count() else ""
            print(f"HTTP 状态: {status}")
            print(f"最终 URL: {final_url}")
            print(f"页面标题: {title}")
            print(f"正文摘要:\n{body_snippet[:300]}...")
            browser.close()

            if "feishu" in final_url or "lark" in final_url:
                print("\n结论: 浏览器可访问飞书页面，可尝试 browser 模式 + codegen 录制 selector")
                return 0
            print("\n结论: 可能无法在浏览器直接打开小程序，请使用 desktop 模式 + 坐标标定")
            return 2
        except Exception as exc:
            print(f"打开失败: {exc}", file=sys.stderr)
            browser.close()
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
