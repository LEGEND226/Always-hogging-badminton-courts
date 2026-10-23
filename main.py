#!/usr/bin/env python3
"""飞书场馆预约抢场 — 主入口。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

from client.auth import AuthError, load_auth, validate_auth
from client.notifier import notify_failure, notify_success, notify_token_expired
from client.utils import setup_logging, target_booking_date, wait_until_open
from client.playwright_booker import attempt_playwright_booking
from client.venue_api import DryRunVenueAPI, VenueAPI, attempt_booking

logger = logging.getLogger(__name__)


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def run(
    config_path: Path,
    *,
    skip_wait: bool = False,
    mode: str = "api",
) -> int:
    config = load_config(config_path)
    setup_logging(config.get("logging", {}).get("level", "INFO"))

    booking_cfg = config.get("booking", {})
    dry_run = booking_cfg.get("dry_run", True)
    days_ahead = booking_cfg.get("days_ahead", 3)
    open_time = booking_cfg.get("open_time", "00:00:00")
    pre_fire_ms = booking_cfg.get("pre_fire_ms", 200)
    preferences = config.get("preferences", [])
    retry_cfg = config.get("retry", {})

    auth = load_auth()
    playwright_mode = mode == "playwright"
    validate_auth(auth, dry_run=dry_run or playwright_mode)

    booking_date = target_booking_date(days_ahead)

    if playwright_mode:
        # cron 可能写死错误 DISPLAY（如 :1），运行时自动纠到可用会话（如 :0）
        from client.window_utils import (
            ensure_display,
            ensure_session_bus,
            start_idle_inhibit,
            wake_idle_session,
        )

        display = ensure_display()
        logger.info("桌面模式图形会话 DISPLAY=%s", display)
        ensure_session_bus()
        wake_idle_session()
        start_idle_inhibit()

        # 桌面模式：可在等到点前先打开小程序首页（不点预约入口）
        wait_cb = None
        if not skip_wait:
            wait_cb = lambda: wait_until_open(open_time, pre_fire_ms)
        result = attempt_playwright_booking(
            config.get("playwright", {}),
            preferences,
            booking_date,
            retry_count=retry_cfg.get("count", 10),
            retry_interval_ms=retry_cfg.get("interval_ms", 100),
            dry_run=dry_run,
            wait_for_open=wait_cb,
        )
    elif dry_run:
        if not skip_wait:
            wait_until_open(open_time, pre_fire_ms)
        api = DryRunVenueAPI(config.get("api", {}), auth)
        result = attempt_booking(
            api,
            preferences,
            booking_date,
            retry_count=retry_cfg.get("count", 10),
            retry_interval_ms=retry_cfg.get("interval_ms", 100),
        )
    else:
        if not skip_wait:
            wait_until_open(open_time, pre_fire_ms)
        api = VenueAPI(config.get("api", {}), auth)
        result = attempt_booking(
            api,
            preferences,
            booking_date,
            retry_count=retry_cfg.get("count", 10),
            retry_interval_ms=retry_cfg.get("interval_ms", 100),
        )

    if result.auth_expired:
        notify_token_expired()
        notify_failure("鉴权已过期，请更新 .env")
        return 2

    if result.success:
        pref = preferences[0] if preferences else {}
        notify_success(
            court=pref.get("court", "未知"),
            time_slot=pref.get("time", "未知"),
            date=booking_date,
            detail=result.message,
        )
        print(f"抢场成功: {result.message}")
        return 0

    notify_failure(f"日期 {booking_date}: {result.message}")
    print(f"抢场失败: {result.message}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="飞书场馆预约自动化")
    parser.add_argument(
        "-c", "--config",
        type=Path,
        default=Path(__file__).parent / "config.yaml",
        help="配置文件路径",
    )
    parser.add_argument(
        "--now",
        action="store_true",
        help="跳过等待，立即执行（用于测试）",
    )
    parser.add_argument(
        "--mode",
        choices=["api", "playwright"],
        default=None,
        help="api=HTTP直连; playwright=UI自动化。默认读 config.yaml booking.mode",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    mode = args.mode or config.get("booking", {}).get("mode", "api")

    try:
        return run(args.config, skip_wait=args.now, mode=mode)
    except AuthError as exc:
        notify_failure(str(exc))
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        notify_failure(f"运行异常: {exc}")
        print(f"异常: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
