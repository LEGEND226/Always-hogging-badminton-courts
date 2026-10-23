"""订场 API 客户端 — 基于 config.yaml 中的 API 配置。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from client.auth import AuthConfig
from client.utils import get_nested, render_template

logger = logging.getLogger(__name__)


@dataclass
class CourtSlot:
    court_id: str
    court_name: str
    time_slot: str
    available: bool
    raw: dict[str, Any]


@dataclass
class BookingResult:
    success: bool
    message: str
    raw: dict[str, Any] | None = None
    auth_expired: bool = False


class VenueAPIError(Exception):
    """API 调用错误。"""


class VenueAPI:
    def __init__(self, api_config: dict[str, Any], auth: AuthConfig):
        self.config = api_config
        self.auth = auth
        self.base_url = api_config.get("base_url", "").rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(api_config.get("headers", {}))
        self.session.headers.update(auth.get_headers())
        if auth.cookie:
            self.session.headers["Cookie"] = auth.cookie

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        logger.debug("%s %s params=%s body=%s", method, url, params, json_body)
        try:
            resp = self.session.request(
                method=method.upper(),
                url=url,
                params=params,
                json=json_body,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise VenueAPIError(f"请求失败: {exc}") from exc

        if resp.status_code == 401:
            return {"_auth_expired": True, "_status": 401, "_text": resp.text}

        try:
            data = resp.json()
        except ValueError:
            raise VenueAPIError(
                f"响应非 JSON (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        if resp.status_code >= 400:
            raise VenueAPIError(f"HTTP {resp.status_code}: {data}")

        return data

    def list_courts(self, booking_date: str) -> list[CourtSlot]:
        cfg = self.config.get("list_courts", {})
        context = {"date": booking_date}
        params = render_template(cfg.get("params", {}), context)
        data = self._request(cfg.get("method", "GET"), cfg["path"], params=params)

        if data.get("_auth_expired"):
            raise VenueAPIError("鉴权已过期，请更新 .env 中的凭证")

        resp_cfg = cfg.get("response", {})
        items = get_nested(data, resp_cfg.get("items_path", "data"), default=[])
        if not isinstance(items, list):
            items = []

        slots: list[CourtSlot] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            available_val = str(item.get(resp_cfg.get("available_field", "status"), ""))
            slots.append(
                CourtSlot(
                    court_id=str(item.get(resp_cfg.get("court_id", "id"), "")),
                    court_name=str(item.get(resp_cfg.get("court_name", "name"), "")),
                    time_slot=str(item.get(resp_cfg.get("time_slot", "time"), "")),
                    available=available_val == str(resp_cfg.get("available_value", "free")),
                    raw=item,
                )
            )
        return slots

    def book_court(
        self,
        court_id: str,
        court_name: str,
        time_slot: str,
        booking_date: str,
    ) -> BookingResult:
        cfg = self.config.get("book_court", {})
        resp_cfg = self.config.get("book_response", {})
        context = {
            "court_id": court_id,
            "court_name": court_name,
            "time_slot": time_slot,
            "date": booking_date,
        }
        body = render_template(cfg.get("body", {}), context)
        data = self._request(
            cfg.get("method", "POST"),
            cfg["path"],
            json_body=body,
        )

        if data.get("_auth_expired"):
            return BookingResult(
                success=False,
                message="鉴权已过期",
                auth_expired=True,
            )

        code = get_nested(data, resp_cfg.get("success_code_path", "code"))
        success_val = resp_cfg.get("success_code_value", 0)
        success = code == success_val or str(code) == str(success_val)
        message = str(get_nested(data, resp_cfg.get("message_path", "message"), ""))

        return BookingResult(success=success, message=message or str(data), raw=data)


class DryRunVenueAPI(VenueAPI):
    """模拟 API，用于 dry_run 模式测试流程。"""

    def list_courts(self, booking_date: str) -> list[CourtSlot]:
        logger.info("[dry_run] 查询场地列表 date=%s", booking_date)
        return [
            CourtSlot("1", "1号场", "18:00-19:00", True, {}),
            CourtSlot("2", "2号场", "18:00-19:00", True, {}),
            CourtSlot("3", "3号场", "19:00-20:00", True, {}),
            CourtSlot("4", "3号场", "19:00-20:00", False, {}),
        ]

    def book_court(
        self,
        court_id: str,
        court_name: str,
        time_slot: str,
        booking_date: str,
    ) -> BookingResult:
        logger.info(
            "[dry_run] 模拟下单 court=%s time=%s date=%s",
            court_name,
            time_slot,
            booking_date,
        )
        return BookingResult(
            success=True,
            message=f"模拟成功: {court_name} {time_slot} @ {booking_date}",
        )


def match_preference(
    slots: list[CourtSlot],
    court_name: str,
    time_slot: str,
) -> CourtSlot | None:
    for slot in slots:
        if not slot.available:
            continue
        name_match = court_name in slot.court_name or slot.court_name in court_name
        time_match = time_slot in slot.time_slot or slot.time_slot in time_slot
        if name_match and time_match:
            return slot
    return None


def attempt_booking(
    api: VenueAPI,
    preferences: list[dict[str, str]],
    booking_date: str,
    retry_count: int,
    retry_interval_ms: int,
) -> BookingResult:
    last_result = BookingResult(success=False, message="未找到匹配场地")

    for pref in preferences:
        court_name = pref.get("court", "")
        time_slot = pref.get("time", "")
        logger.info("尝试偏好: %s %s", court_name, time_slot)

        for attempt in range(1, retry_count + 1):
            try:
                slots = api.list_courts(booking_date)
            except VenueAPIError as exc:
                logger.warning("查场失败 (第 %d 次): %s", attempt, exc)
                if attempt < retry_count:
                    time.sleep(retry_interval_ms / 1000.0)
                last_result = BookingResult(success=False, message=str(exc))
                continue

            slot = match_preference(slots, court_name, time_slot)
            if slot is None:
                logger.info("暂无匹配空闲场地，重试...")
                if attempt < retry_count:
                    time.sleep(retry_interval_ms / 1000.0)
                last_result = BookingResult(
                    success=False,
                    message=f"未找到空闲: {court_name} {time_slot}",
                )
                continue

            result = api.book_court(
                slot.court_id,
                slot.court_name,
                slot.time_slot,
                booking_date,
            )
            if result.auth_expired:
                return result
            if result.success:
                return result

            logger.warning("下单失败 (第 %d 次): %s", attempt, result.message)
            last_result = result
            if attempt < retry_count:
                time.sleep(retry_interval_ms / 1000.0)

    return last_result
