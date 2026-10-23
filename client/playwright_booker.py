"""Playwright UI 自动化抢场 — SSL Pinning 抓包失败时的降级方案。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from client.venue_api import BookingResult

logger = logging.getLogger(__name__)


@dataclass
class PlaywrightConfig:
    headless: bool = False
    slow_mo_ms: int = 0
    timeout_ms: int = 15000
    launch_args: list[str] | None = None
    selectors: dict[str, str] | None = None


DEFAULT_SELECTORS = {
    "app_entry": "",           # 订场小程序入口（桌面飞书内嵌页时用）
    "date_input": "input[type='date']",
    "court_by_name": "text={court_name}",
    "time_by_slot": "text={time_slot}",
    "submit_button": "button:has-text('提交'), button:has-text('预约'), button:has-text('确认')",
    "success_indicator": "text=预约成功, text=提交成功, text=已预约",
}


class PlaywrightBooker:
    """通过 Playwright 模拟点击完成订场。

    使用前需在 config.yaml 的 playwright 段配置选择器。
    桌面飞书若无法嵌入浏览器，可改用 feishu_desktop 模式手动打开小程序后 attach。
    """

    def __init__(self, pw_config: dict[str, Any]):
        self.cfg = PlaywrightConfig(
            headless=pw_config.get("headless", False),
            slow_mo_ms=pw_config.get("slow_mo_ms", 0),
            timeout_ms=pw_config.get("timeout_ms", 15000),
            launch_args=pw_config.get("launch_args", []),
            selectors={**DEFAULT_SELECTORS, **(pw_config.get("selectors") or {})},
        )
        self.start_url = pw_config.get("start_url", "")
        self.manual_wait_seconds = int(pw_config.get("manual_wait_seconds", 0))
        self._playwright = None
        self._browser = None
        self._page = None

    def _ensure_browser(self):
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "未安装 playwright，请执行: pip install playwright && playwright install chromium"
            ) from exc

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.cfg.headless,
            slow_mo=self.cfg.slow_mo_ms,
            args=self.cfg.launch_args or [],
        )
        context = self._browser.new_context()
        self._page = context.new_page()
        self._page.set_default_timeout(self.cfg.timeout_ms)

        if self.start_url:
            logger.info("打开页面: %s", self.start_url)
            self._page.goto(self.start_url)
        elif self.manual_wait_seconds > 0:
            logger.info(
                "请在 %d 秒内手动打开订场页面（飞书小程序或浏览器）...",
                self.manual_wait_seconds,
            )
            time.sleep(self.manual_wait_seconds)

    def _click_if_set(self, page, selector: str, label: str) -> None:
        if not selector:
            return
        logger.info("点击: %s", label)
        page.locator(selector).first.click()

    def close(self) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        self._page = None
        self._browser = None
        self._playwright = None

    def book(
        self,
        court_name: str,
        time_slot: str,
        booking_date: str,
    ) -> BookingResult:
        try:
            self._ensure_browser()
            page = self._page
            sel = self.cfg.selectors or {}

            app_entry = sel.get("app_entry", "")
            if app_entry:
                self._click_if_set(page, app_entry, "小程序入口")

            logger.info("选择日期: %s", booking_date)
            date_sel = sel.get("date_input", "")
            if date_sel:
                page.locator(date_sel).fill(booking_date)

            court_sel = sel.get("court_by_name", "text={court_name}").format(
                court_name=court_name
            )
            logger.info("点击场地: %s", court_name)
            page.locator(court_sel).first.click()

            time_sel = sel.get("time_by_slot", "text={time_slot}").format(
                time_slot=time_slot
            )
            logger.info("点击时段: %s", time_slot)
            page.locator(time_sel).first.click()

            submit_sel = sel.get("submit_button", "")
            logger.info("提交预约")
            page.locator(submit_sel).first.click()

            success_sel = sel.get("success_indicator", "")
            page.locator(success_sel).first.wait_for(state="visible", timeout=self.cfg.timeout_ms)

            return BookingResult(success=True, message=f"UI 预约成功: {court_name} {time_slot}")

        except Exception as exc:
            logger.error("Playwright 预约失败: %s", exc)
            return BookingResult(success=False, message=str(exc))
        finally:
            self.close()


def attempt_playwright_booking(
    pw_config: dict[str, Any],
    preferences: list[dict[str, str]],
    booking_date: str,
    retry_count: int,
    retry_interval_ms: int,
    *,
    dry_run: bool = False,
    wait_for_open=None,
) -> BookingResult:
    mode = pw_config.get("mode", "browser")
    if mode == "desktop":
        from client.desktop_booker import attempt_desktop_booking

        return attempt_desktop_booking(
            pw_config,
            preferences,
            booking_date,
            retry_count,
            retry_interval_ms,
            dry_run=dry_run,
            wait_for_open=wait_for_open,
        )

    last = BookingResult(success=False, message="未执行")
    for pref in preferences:
        court = pref.get("court", "")
        time_slot = pref.get("time", "")
        for attempt in range(1, retry_count + 1):
            logger.info("Playwright 尝试 %d/%d: %s %s", attempt, retry_count, court, time_slot)
            booker = PlaywrightBooker(pw_config)
            result = booker.book(court, time_slot, booking_date)
            if result.success:
                return result
            last = result
            if attempt < retry_count:
                time.sleep(retry_interval_ms / 1000.0)
    return last
