#!/usr/bin/env python3
"""验证 save_har_addon 的 safe_text / write_har 能处理 surrogate 和二进制。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from save_har_addon import safe_text, write_har  # noqa: E402


def test_safe_text_surrogate() -> None:
    bad = "hello\udcd0world"
    result = safe_text(bad)
    result.encode("utf-8")  # must not raise
    assert "\udcd0" not in result


def test_safe_text_binary() -> None:
    result = safe_text(b"\x00\x01\xff\xfe")
    assert result.startswith("[binary:")


def test_write_har_with_surrogate() -> None:
    entries = [
        {
            "request": {"postData": {"text": "req\ud800"}},
            "response": {"content": {"text": "resp\udcff"}},
        }
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.har"
        write_har(path, entries)
        assert path.stat().st_size > 0
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["log"]["entries"]) == 1


def test_parse_har_empty() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from parse_har import HarLoadError, load_har  # noqa: E402

    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "empty.har"
        empty.write_text("")
        try:
            load_har(empty)
            raise AssertionError("should raise")
        except HarLoadError as exc:
            assert "为空" in str(exc)


if __name__ == "__main__":
    test_safe_text_surrogate()
    test_safe_text_binary()
    test_write_har_with_surrogate()
    test_parse_har_empty()
    print("所有 HAR 保存测试通过")
