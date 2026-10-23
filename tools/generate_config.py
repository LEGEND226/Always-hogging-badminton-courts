#!/usr/bin/env python3
"""从 HAR 或 cURL 自动生成 config.yaml 片段和 .env 凭证。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

# 复用 parse_har / parse_curl
sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_curl import parse_curl  # noqa: E402
from parse_har import HarLoadError, extract_requests, load_har, suggest_config  # noqa: E402

LIST_KEYWORDS = ("list", "query", "search", "venue", "court", "schedule", "calendar")
BOOK_KEYWORDS = ("book", "order", "reserve", "submit", "create", "appointment")
DATE_PARAMS = ("date", "day", "booking_date", "bookDate", "reserve_date")

# 飞书监控/埋点/统计 — 误识别为订场 API 时需排除
NOISE_DOMAINS = frozenset({
    "slardar-bd.feishu.cn",
    "applog-bd.feishu.cn",
    "mcs-bd.feishu.cn",
    "mon.zijieapi.com",
    "api.feelgood.cn",
    "dualstack-restios.amap.com",
    "www.mescroll.com",
    "gs-loc-cn.apple.com",
    "gsp85-cn-ssl.ls.apple.com",
    "gateway.icloud.com.cn",
})

# 订场 API 更可能出现的域名特征
BOOKING_DOMAINS_HINT = (
    "venue", "court", "sport", "gym", "book", "order", "reserve",
    "badminton", "ichery", "openapi", "mini", "appointment",
)


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def is_noise_request(req: dict[str, Any]) -> bool:
    host = _host(req["url"])
    if host in NOISE_DOMAINS:
        return True
    if host.endswith(".feishu.cn") and any(
        x in req["url"] for x in ("/monitor/", "/app_log", "/v1/list", "/settings/v3")
    ):
        return True
    return False


def filter_booking_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in requests if not is_noise_request(r)]


def classify_request(req: dict[str, Any]) -> str:
    if is_noise_request(req):
        return "other"

    url_lower = req["url"].lower()
    method = req["method"].upper()
    body_lower = (req.get("body") or "").lower()

    for kw in BOOK_KEYWORDS:
        if kw in url_lower or kw in body_lower:
            if method == "POST":
                return "book"

    parsed = urlparse(req["url"])
    qs = parse_qs(parsed.query)
    if method == "GET" and any(p in qs or p in url_lower for p in DATE_PARAMS):
        if any(h in url_lower for h in BOOKING_DOMAINS_HINT):
            return "list"
    for kw in LIST_KEYWORDS:
        if kw in url_lower and any(h in url_lower for h in BOOKING_DOMAINS_HINT):
            return "list"

    return "other"


def _pick_base_url(requests: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for req in requests:
        if classify_request(req) == "other":
            continue
        base = f"{urlparse(req['url']).scheme}://{urlparse(req['url']).netloc}"
        counts[base] = counts.get(base, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    for req in requests:
        host = _host(req["url"])
        if any(h in host for h in BOOKING_DOMAINS_HINT):
            return f"{urlparse(req['url']).scheme}://{host}"
    return suggest_config(requests).get("base_url", "")


def build_api_config(requests: list[dict[str, Any]]) -> dict[str, Any]:
    booking_reqs = filter_booking_requests(requests)
    suggestion = suggest_config(booking_reqs or requests)
    base_url = _pick_base_url(booking_reqs or requests)

    classified: dict[str, dict[str, Any]] = {}
    for req in booking_reqs:
        kind = classify_request(req)
        if kind == "other":
            continue
        if kind not in classified:
            classified[kind] = req

    api: dict[str, Any] = {
        "base_url": base_url,
        "headers": {
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
        },
    }

    if "list" in classified:
        req = classified["list"]
        parsed = urlparse(req["url"])
        params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()}
        # 将日期参数替换为模板
        for key in params:
            if key.lower() in DATE_PARAMS or "date" in key.lower():
                params[key] = "{{date}}"
        api["list_courts"] = {
            "method": req["method"],
            "path": parsed.path or "/",
            "params": params or {"date": "{{date}}"},
            "response": {
                "items_path": "data.list",
                "court_id": "id",
                "court_name": "name",
                "time_slot": "time",
                "available_field": "status",
                "available_value": "free",
            },
        }

    if "book" in classified:
        req = classified["book"]
        parsed = urlparse(req["url"])
        body_template: dict[str, str] = {}
        body_text = req.get("body", "")
        if body_text:
            try:
                body_json = json.loads(body_text)
                if isinstance(body_json, dict):
                    for key, val in body_json.items():
                        key_lower = key.lower()
                        if "id" in key_lower or "venue" in key_lower or "court" in key_lower:
                            body_template[key] = "{{court_id}}"
                        elif "date" in key_lower or "day" in key_lower:
                            body_template[key] = "{{date}}"
                        elif "time" in key_lower:
                            body_template[key] = "{{time_slot}}"
                        else:
                            body_template[key] = str(val)
            except json.JSONDecodeError:
                body_template = {"raw": "{{body}}"}
        api["book_court"] = {
            "method": req["method"],
            "path": parsed.path or "/",
            "body": body_template or {
                "venue_id": "{{court_id}}",
                "date": "{{date}}",
                "time": "{{time_slot}}",
            },
        }
        api["book_response"] = {
            "success_code_path": "code",
            "success_code_value": 0,
            "message_path": "message",
        }

    return api


def build_env_content(suggestion: dict[str, Any]) -> str:
    lines = [
        "# 由 generate_config.py 自动生成，请检查并补充 FEISHU_WEBHOOK_URL",
        "",
    ]
    auth = suggestion.get("authorization", "")
    lines.append(f"AUTHORIZATION={auth}" if auth else "AUTHORIZATION=")
    lines.append("")
    cookie = suggestion.get("cookie", "")
    if not cookie or cookie.endswith("..."):
        cookie = ""
    lines.append(f"COOKIE={cookie}" if cookie else "COOKIE=")
    lines.append("")
    lines.append("FEISHU_WEBHOOK_URL=")
    lines.append("")
    return "\n".join(lines)


def merge_config(existing: dict[str, Any], api: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    merged["api"] = api
    return merged


def apply_config(
    project_dir: Path,
    api: dict[str, Any],
    env_content: str,
) -> None:
    config_path = project_dir / "config.yaml"
    env_path = project_dir / ".env"

    existing: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open(encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}

    merged = merge_config(existing, api)
    with config_path.open("w", encoding="utf-8") as f:
        yaml.dump(merged, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    if not env_path.exists():
        env_path.write_text(env_content, encoding="utf-8")
        print(f"已创建 {env_path}")
    else:
        print(f".env 已存在，跳过覆盖。请手动对比更新 COOKIE/AUTHORIZATION")

    print(f"已更新 {config_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="从 HAR/cURL 生成 config.yaml 和 .env")
    parser.add_argument("input", type=Path, nargs="?", help="HAR 文件路径")
    parser.add_argument("-k", "--keyword", default="", help="URL 关键词过滤")
    parser.add_argument("--curl", type=Path, help="从 cURL 文件生成（单条请求）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="写入 config.yaml 和 .env（项目根目录）",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="项目根目录",
    )
    args = parser.parse_args()

    requests_list: list[dict[str, Any]] = []

    if args.curl:
        curl_text = args.curl.read_text(encoding="utf-8")
        curl_text = re.sub(r"^\s*\$\s*", "", curl_text.strip())
        parsed = parse_curl(curl_text)
        requests_list = [
            {
                "method": parsed["method"],
                "url": parsed["url"],
                "headers": parsed.get("headers", {}),
                "body": parsed.get("body", ""),
            }
        ]
    elif args.input:
        try:
            har = load_har(args.input)
        except HarLoadError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        requests_list = extract_requests(har, args.keyword)
    else:
        print("请提供 HAR 文件或 --curl cURL文件", file=sys.stderr)
        return 1

    if not requests_list:
        print("未找到匹配的请求，尝试去掉 -k 关键词过滤", file=sys.stderr)
        return 1

    booking_reqs = filter_booking_requests(requests_list)
    suggestion = suggest_config(booking_reqs or requests_list)
    api = build_api_config(requests_list)
    env_content = build_env_content(suggestion)

    # 从 HAR 请求中提取完整 cookie
    for req in requests_list:
        if req.get("headers", {}).get("Cookie"):
            suggestion["cookie"] = req["headers"]["Cookie"]
            break
    if suggestion.get("cookie"):
        env_content = build_env_content(suggestion)

    output = {
        "noise_filtered_count": len(requests_list) - len(booking_reqs),
        "classified": {
            classify_request(r): r["url"][:100]
            for r in booking_reqs
            if classify_request(r) != "other"
        },
        "suggestion": suggestion,
        "api": api,
    }
    print(yaml.dump(output, allow_unicode=True, default_flow_style=False, sort_keys=False))

    if args.apply:
        if not api.get("list_courts") and not api.get("book_court"):
            print(
                "警告: 未能自动识别查场/下单 API（可能只有飞书监控流量）。",
                file=sys.stderr,
            )
            print(
                "  请试: bash scripts/start_feishu_desktop.sh",
                file=sys.stderr,
            )
            print(
                "  或改用: bash scripts/start_feishu_playwright.sh codegen",
                file=sys.stderr,
            )
            return 3
        apply_config(args.project_dir, api, env_content)
        print("\n下一步:")
        print("  1. 检查 config.yaml 中 api.response 字段映射是否正确")
        print("  2. 检查 .env 中 COOKIE/AUTHORIZATION 是否完整")
        print("  3. python3 main.py --now   # dry_run 测试")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
