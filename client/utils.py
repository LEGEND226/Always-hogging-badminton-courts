"""通用工具函数。"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def setup_logging(level: str = "INFO") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def get_nested(data: Any, path: str, default: Any = None) -> Any:
    if not path:
        return data
    current = data
    for key in path.split("."):
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def render_template(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, str):
        result = value
        for key, val in context.items():
            result = result.replace(f"{{{{{key}}}}}", val)
        return result
    if isinstance(value, dict):
        return {k: render_template(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render_template(v, context) for v in value]
    return value


def target_booking_date(days_ahead: int) -> str:
    return (date.today() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def parse_open_time(open_time: str) -> tuple[int, int, int]:
    parts = open_time.split(":")
    if len(parts) != 3:
        raise ValueError(f"open_time 格式错误，应为 HH:MM:SS: {open_time}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def seconds_until_open(open_time: str, pre_fire_ms: int = 0) -> float:
    now = datetime.now()
    hour, minute, second = parse_open_time(open_time)
    target = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    wait_seconds = (target - now).total_seconds() - pre_fire_ms / 1000.0
    return max(0.0, wait_seconds)


def _open_datetime(open_time: str, pre_fire_ms: int, *, reference: datetime | None = None) -> datetime:
    hour, minute, second = parse_open_time(open_time)
    now = reference or datetime.now()
    target = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target - timedelta(milliseconds=pre_fire_ms)


def wait_until_open(open_time: str, pre_fire_ms: int = 0) -> None:
    import time

    wait = seconds_until_open(open_time, pre_fire_ms)
    deadline = _open_datetime(open_time, pre_fire_ms)
    if wait <= 0:
        logger.info("已到达放场时间，立即执行")
        return
    logger.info("等待 %.2f 秒至放场时间 %s（提前 %d ms）", wait, open_time, pre_fire_ms)
    if wait > 1:
        time.sleep(wait - 0.05)
    while datetime.now() < deadline:
        time.sleep(0.001)
