"""从 cURL 命令提取 headers 和 body，辅助配置 .env 和 config.yaml。"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from typing import Any


def parse_curl(curl_command: str) -> dict[str, Any]:
    tokens = shlex.split(curl_command.replace("\\\n", " ").replace("\\", ""))
    method = "GET"
    url = ""
    headers: dict[str, str] = {}
    body = ""

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in ("curl", "curl.exe"):
            i += 1
            continue
        if token in ("-X", "--request") and i + 1 < len(tokens):
            method = tokens[i + 1].upper()
            i += 2
            continue
        if token in ("-H", "--header") and i + 1 < len(tokens):
            header = tokens[i + 1]
            if ":" in header:
                key, _, value = header.partition(":")
                headers[key.strip()] = value.strip()
            i += 2
            continue
        if token in ("-d", "--data", "--data-raw", "--data-binary") and i + 1 < len(tokens):
            body = tokens[i + 1]
            if method == "GET":
                method = "POST"
            i += 2
            continue
        if token.startswith("http"):
            url = token.strip("'\"")
            i += 1
            continue
        i += 1

    from urllib.parse import urlparse

    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path or "/"

    result: dict[str, Any] = {
        "method": method,
        "url": url,
        "base_url": base_url,
        "path": path,
        "headers": headers,
        "body": body,
    }

    if body:
        try:
            result["body_json"] = json.loads(body)
        except json.JSONDecodeError:
            pass

    if "Authorization" in headers:
        result["authorization"] = headers["Authorization"]
    if "Cookie" in headers:
        result["cookie"] = headers["Cookie"]

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="解析 cURL 命令")
    parser.add_argument("curl", nargs="?", help="cURL 命令字符串")
    parser.add_argument("-f", "--file", type=str, help="从文件读取 cURL")
    args = parser.parse_args()

    curl_text = args.curl or ""
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            curl_text = f.read()

    if not curl_text.strip():
        print("请提供 cURL 命令或通过 -f 指定文件", file=sys.stderr)
        return 1

    curl_text = re.sub(r"^\s*\$\s*", "", curl_text.strip())
    result = parse_curl(curl_text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
