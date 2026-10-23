"""从 HAR 文件提取 API 请求，辅助抓包分析。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class HarLoadError(Exception):
    """HAR 文件加载错误。"""


def load_har(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise HarLoadError(f"HAR 文件不存在: {path}")
    if path.stat().st_size == 0:
        raise HarLoadError(
            f"HAR 文件为空: {path}\n"
            "可能原因: mitmweb 退出时 save_har_addon 保存失败（已修复，请重新抓包）\n"
            "或从 mitmweb (http://127.0.0.1:8081) 手动 Export HAR 到 captures/booking.har"
        )
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise HarLoadError(
            f"HAR 文件损坏（非合法 JSON）: {path}\n"
            f"详情: {exc}\n"
            "请重新抓包，或从 mitmweb 手动导出 HAR"
        ) from exc


def extract_requests(har: dict[str, Any], keyword: str = "") -> list[dict[str, Any]]:
    entries = har.get("log", {}).get("entries", [])
    results: list[dict[str, Any]] = []
    keyword_lower = keyword.lower()

    for entry in entries:
        req = entry.get("request", {})
        url = req.get("url", "")
        if keyword_lower and keyword_lower not in url.lower():
            continue
        method = req.get("method", "")
        headers = {h["name"]: h["value"] for h in req.get("headers", [])}
        post_data = req.get("postData", {})
        body = post_data.get("text", "")
        results.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": body,
                "status": entry.get("response", {}).get("status"),
            }
        )
    return results


def to_curl(req: dict[str, Any]) -> str:
    parts = [f"curl -X {req['method']} '{req['url']}'"]
    for key, value in req.get("headers", {}).items():
        if key.lower() in ("content-length", "host"):
            continue
        parts.append(f"-H '{key}: {value}'")
    body = req.get("body", "")
    if body:
        escaped = body.replace("'", "'\\''")
        parts.append(f"-d '{escaped}'")
    return " \\\n  ".join(parts)


def suggest_config(requests: list[dict[str, Any]]) -> dict[str, Any]:
    if not requests:
        return {}

    base_urls: dict[str, int] = {}
    for req in requests:
        parsed = urlparse(req["url"])
        base = f"{parsed.scheme}://{parsed.netloc}"
        base_urls[base] = base_urls.get(base, 0) + 1

    base_url = max(base_urls, key=base_urls.get) if base_urls else ""
    auth_header = ""
    cookie = ""
    for req in requests:
        headers = req.get("headers", {})
        if "Authorization" in headers:
            auth_header = headers["Authorization"]
        if "Cookie" in headers:
            cookie = headers["Cookie"]

    return {
        "base_url": base_url,
        "authorization": auth_header,
        "cookie_preview": cookie[:80] + "..." if len(cookie) > 80 else cookie,
        "requests_found": len(requests),
        "sample_urls": [r["url"] for r in requests[:5]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="从 HAR 文件提取订场 API 请求")
    parser.add_argument("har_file", type=Path, help="HAR 文件路径")
    parser.add_argument("-k", "--keyword", default="", help="URL 关键词过滤")
    parser.add_argument("--curl", action="store_true", help="输出 cURL 命令")
    parser.add_argument(
        "--write-captures",
        type=Path,
        metavar="DIR",
        help="将 cURL 写入目录（如 captures/）",
    )
    args = parser.parse_args()

    try:
        har = load_har(args.har_file)
    except HarLoadError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    requests_list = extract_requests(har, args.keyword)

    print(f"找到 {len(requests_list)} 个请求")
    suggestion = suggest_config(requests_list)
    print("\n建议配置:")
    print(json.dumps(suggestion, ensure_ascii=False, indent=2))

    if args.curl:
        print("\n--- cURL 命令 ---")
        for i, req in enumerate(requests_list[:10], 1):
            print(f"\n# 请求 {i}: {req['method']} {req['url'][:80]}")
            print(to_curl(req))

    if args.write_captures:
        args.write_captures.mkdir(parents=True, exist_ok=True)
        for i, req in enumerate(requests_list, 1):
            curl_path = args.write_captures / f"request_{i:03d}.curl"
            curl_path.write_text(to_curl(req) + "\n", encoding="utf-8")
        print(f"\n已导出 {len(requests_list)} 条 cURL 到 {args.write_captures}/")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
