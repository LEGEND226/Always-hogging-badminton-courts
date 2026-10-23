"""mitmproxy 插件：自动将流量保存为 HAR 文件。

用法:
  mitmweb -p 8888 -s tools/save_har_addon.py
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from mitmproxy import ctx, http

CAPTURE_DIR = Path(__file__).resolve().parent.parent / "captures"
HAR_PATH = CAPTURE_DIR / "booking.har"
SAVE_EVERY_N = 5


def safe_text(data: bytes | str | None) -> str:
    """将请求/响应体转为可安全写入 UTF-8 JSON 的字符串。"""
    if data is None:
        return ""
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            preview = base64.b64encode(data[:256]).decode("ascii")
            return f"[binary:{len(data)} bytes, base64_prefix:{preview}]"
        return safe_text(text)
    # 清除 surrogate 等非法 UTF-8 字符
    return data.encode("utf-8", errors="replace").decode("utf-8")


def _flow_body(content: http.Message) -> str:
    raw = content.raw_content
    if raw is None:
        try:
            return safe_text(content.get_text(strict=False))
        except Exception:
            return ""
    return safe_text(raw)


def write_har(path: Path, entries: list[dict]) -> None:
    """写入 HAR，失败时用 ensure_ascii 兜底。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    har = {"log": {"entries": entries}}
    try:
        text = json.dumps(har, ensure_ascii=False, indent=2)
        path.write_text(text, encoding="utf-8")
    except (UnicodeEncodeError, ValueError):
        text = json.dumps(har, ensure_ascii=True, indent=2)
        path.write_text(text, encoding="utf-8")


class AutoSaveHar:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def _make_entry(self, flow: http.HTTPFlow) -> dict:
        req = flow.request
        resp = flow.response
        assert resp is not None
        return {
            "startedDateTime": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "request": {
                "method": req.method,
                "url": req.pretty_url,
                "headers": [
                    {"name": safe_text(k), "value": safe_text(v)}
                    for k, v in req.headers.items()
                ],
                "postData": {"text": _flow_body(req)},
            },
            "response": {
                "status": resp.status_code,
                "headers": [
                    {"name": safe_text(k), "value": safe_text(v)}
                    for k, v in resp.headers.items()
                ],
                "content": {"text": _flow_body(resp)},
            },
        }

    def _save(self) -> None:
        try:
            write_har(HAR_PATH, self.entries)
        except Exception as exc:
            ctx.log.error(f"HAR 增量保存失败: {exc}")

    def response(self, flow: http.HTTPFlow) -> None:
        resp = flow.response
        if resp is None:
            return

        content_type = resp.headers.get("content-type", "")
        if any(
            t in content_type
            for t in ("image/", "font/", "text/css", "javascript")
        ):
            return

        self.entries.append(self._make_entry(flow))
        ctx.log.info(f"已记录: {flow.request.method} {flow.request.pretty_url[:80]}")

        if len(self.entries) % SAVE_EVERY_N == 0:
            self._save()
            ctx.log.info(f"增量保存: {HAR_PATH} ({len(self.entries)} 条)")

    def done(self) -> None:
        try:
            write_har(HAR_PATH, self.entries)
            ctx.log.info(f"已保存 HAR: {HAR_PATH} ({len(self.entries)} 条请求)")
        except Exception as exc:
            ctx.log.error(f"HAR 保存失败: {exc}")


addons = [AutoSaveHar()]
