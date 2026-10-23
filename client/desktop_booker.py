"""Linux 桌面飞书小程序 UI 自动化 — 预热首页后到点抢场。

预热：0 点前只 applink 打开订场小程序并停在首页（不点预约入口）。
开火：0 点后第一枪点预约入口，再选场/时段/提交。
成功：页面 OCR / 窗口标题出现成功文案才算成功，否则重试。
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from client.venue_api import BookingResult
from client.window_utils import (
    DEFAULT_EXCLUDE_TITLES,
    DEFAULT_TARGET_TITLES,
    WindowGeom,
    activate_window,
    assert_foreground_is_booking,
    ensure_feishu_window,
    ensure_window_focused,
    find_booking_window,
    get_active_window_id,
    get_window_geometry,
    get_window_name,
    is_booking_title,
    is_excluded_title,
    is_main_feishu_title,
    paste_text,
    resolve_click_xy,
    wake_idle_session,
)

logger = logging.getLogger(__name__)

FAST_LABEL_KEYWORDS = ("时段", "预约原因", "提交", "确定", "下一步")
DEFAULT_SUCCESS_TITLES = ["提交成功"]
DEFAULT_SUCCESS_OCR_KEYWORDS = ["提交成功", "预约成功"]


class DesktopBooker:
    """applink 打开后锁定订场小程序弹窗；前台不是订场窗则立即失败。"""

    def __init__(self, pw_config: dict[str, Any]):
        self.manual_wait_seconds = int(pw_config.get("manual_wait_seconds", 15))
        self.start_url = str(pw_config.get("start_url") or "")
        desktop_cfg = pw_config.get("desktop") or {}
        self.pause = float(desktop_cfg.get("pause_before_clicks", 0.05))
        self.pause_after_nav = float(desktop_cfg.get("pause_after_nav", 0.12))
        self.pause_fast = float(desktop_cfg.get("pause_fast", 0.02))
        self.clicks: list[dict[str, Any]] = list(desktop_cfg.get("clicks") or [])
        self.pre_clicks: list[dict[str, Any]] = list(desktop_cfg.get("pre_clicks") or [])
        self.type_text: list[dict[str, Any]] = list(desktop_cfg.get("type_text") or [])
        self.skip_last_n = int(desktop_cfg.get("skip_last_n_on_dry_run", 1))
        self.skip_last_on_dry_run = bool(
            desktop_cfg.get("skip_last_click_on_dry_run", True)
        )
        self.auto_launch = bool(desktop_cfg.get("auto_launch_feishu", True))
        self.open_applink_flag = bool(desktop_cfg.get("open_applink", True))
        self.open_applink_via = str(desktop_cfg.get("open_applink_via") or "client")
        self.reason_text = str(desktop_cfg.get("reason_text") or "锻炼身体")
        self.lock_window = bool(desktop_cfg.get("lock_window", True))
        self.warmup_before_open = bool(desktop_cfg.get("warmup_before_open", True))
        self.success_titles = list(
            desktop_cfg.get("success_window_titles") or DEFAULT_SUCCESS_TITLES
        )
        self.success_use_ocr = bool(desktop_cfg.get("success_use_ocr", True))
        self.success_ocr_keywords = list(
            desktop_cfg.get("success_ocr_keywords") or DEFAULT_SUCCESS_OCR_KEYWORDS
        )
        self.success_wait_seconds = float(
            desktop_cfg.get("success_wait_seconds") or 5.0
        )
        self.home_ready_wait_seconds = float(
            desktop_cfg.get("home_ready_wait_seconds") or 12.0
        )
        self._last_ocr_snippet = ""
        self._last_home_rms: float | None = None
        self.foreground_wait_seconds = int(
            desktop_cfg.get("foreground_wait_seconds")
            or pw_config.get("manual_wait_seconds")
            or 15
        )
        self.applink_wait_seconds = float(
            desktop_cfg.get("applink_wait_seconds") or 25
        )
        self.default_scroll_clicks = int(desktop_cfg.get("scroll_clicks", -6))
        self.target_titles = list(
            desktop_cfg.get("target_window_titles") or DEFAULT_TARGET_TITLES
        )
        self.exclude_titles = list(
            desktop_cfg.get("exclude_window_titles") or DEFAULT_EXCLUDE_TITLES
        )
        self.search_keywords = list(desktop_cfg.get("search_keywords") or []) or None
        self.dry_run = bool(pw_config.get("dry_run", False))
        self._locked_wid: str | None = None
        self._geom: WindowGeom | None = None

    def _pause_after_step(self, label: str) -> float:
        """时段/提交等用极短间隔；换页稍长；点完预约入口后短停再选场。"""
        for kw in FAST_LABEL_KEYWORDS:
            if kw in label:
                return self.pause_fast
        if "预约" in label:
            return 0.2
        if "时段" in label:
            return self.pause
        return self.pause_after_nav

    def _import_pyautogui(self):
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError(
                "桌面模式需要 pyautogui: pip install pyautogui\n"
                "若缺 tk: sudo apt-get install -y python3-tk python3-dev"
            ) from exc
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.02
        return pyautogui

    def _assert_locked_is_booking(self) -> str:
        if not self._locked_wid:
            raise RuntimeError("未锁定订场窗口")
        name = get_window_name(self._locked_wid)
        if is_excluded_title(name, self.exclude_titles):
            raise RuntimeError(
                f"拒绝锁定排除窗口 {name!r}（Cursor/Chrome/文档等）。"
            )
        # 成功页标题不是订场关键词，单独处理
        if any(t and t in name for t in self.success_titles):
            return name
        return assert_foreground_is_booking(
            self._locked_wid,
            self.target_titles,
            exclude_titles=self.exclude_titles,
        )

    def _refresh_locked_geom(self) -> WindowGeom:
        if not self._locked_wid:
            raise RuntimeError("未锁定订场窗口")
        title = self._assert_locked_is_booking()
        if any(t and t in title for t in self.success_titles):
            raise RuntimeError(
                f"前台已是成功页 {title!r}，本轮点击状态异常，将重试。"
            )
        if not is_booking_title(title, self.target_titles):
            raise RuntimeError(
                f"前台不是订场窗 {title!r}。请确认停在订场小程序首页再抢。"
            )
        try:
            geom = get_window_geometry(self._locked_wid)
        except Exception as exc:
            raise RuntimeError(
                f"锁定的订场窗口丢失 (wid={self._locked_wid}): {exc}。"
                "请重新打开订场小程序弹窗。"
            ) from exc
        geom.name = title or geom.name
        self._geom = geom
        return geom

    def _try_lock_found(self, found: WindowGeom) -> WindowGeom | None:
        name = found.name or get_window_name(found.wid)
        if (
            is_excluded_title(name, self.exclude_titles)
            or is_main_feishu_title(name)
            or not is_booking_title(name, self.target_titles)
        ):
            return None

        focused = False
        try:
            ensure_window_focused(found.wid)
            focused = True
        except RuntimeError as exc:
            logger.warning("标准激活失败，唤醒后再试: %s", exc)
            wake_idle_session()
            try:
                ensure_window_focused(found.wid, wake_on_none=False)
                focused = True
            except RuntimeError as exc2:
                # 窗已找到：锁定 wid 继续，勿因 got=None 当成没找到窗
                logger.warning(
                    "唤醒后仍无法校验前台，将锁定 wid 继续: %s",
                    exc2,
                )

        try:
            activate_window(found.wid)
            geom = get_window_geometry(found.wid)
        except Exception as exc:
            logger.warning("无法读取订场窗几何: %s", exc)
            return None
        geom.name = get_window_name(found.wid) or name
        if not is_booking_title(geom.name, self.target_titles):
            return None
        self._locked_wid = geom.wid
        self._geom = geom
        if focused:
            logger.info(
                "已自动激活订场弹窗 wid=%s name=%r size=%dx%d",
                geom.wid,
                geom.name,
                geom.w,
                geom.h,
            )
        else:
            logger.info(
                "已锁定订场窗（前台未确认） wid=%s name=%r size=%dx%d",
                geom.wid,
                geom.name,
                geom.w,
                geom.h,
            )
        return geom

    def _wait_and_lock_booking(self) -> WindowGeom:
        wait = max(0, self.foreground_wait_seconds)
        deadline = time.time() + wait
        last_log = -1
        woke_once = False

        while time.time() <= deadline:
            remaining = max(0, int(deadline - time.time()))
            if remaining != last_log and remaining % 3 == 0:
                last_log = remaining
                active = get_active_window_id()
                aname = get_window_name(active) if active else ""
                logger.info("剩余 %d 秒... 当前前台标题=%r", remaining, aname)

            if not woke_once and get_active_window_id() is None:
                logger.warning("等待锁定时前台为空，先唤醒会话")
                wake_idle_session()
                woke_once = True

            found = find_booking_window(
                target_titles=self.target_titles,
                exclude_titles=self.exclude_titles,
                search_keywords=self.search_keywords,
            )
            if found is not None:
                locked = self._try_lock_found(found)
                if locked is not None:
                    return locked

            active = get_active_window_id()
            if active:
                aname = get_window_name(active)
                if (
                    is_booking_title(aname, self.target_titles)
                    and not is_excluded_title(aname, self.exclude_titles)
                    and not is_main_feishu_title(aname)
                ):
                    geom = get_window_geometry(active)
                    geom.name = aname
                    self._locked_wid = geom.wid
                    logger.info(
                        "已检测到订场前台 wid=%s name=%r",
                        geom.wid,
                        aname,
                    )
                    return geom

            time.sleep(0.6)

        active = get_active_window_id()
        if active is None:
            raise RuntimeError(
                "超时：会话无前台窗口（got=None）。"
                "多半是屏保/外接屏灭了或已锁屏需密码；"
                "脚本无法输入解锁密码，请保持会话可交互后再试。"
            )
        raise RuntimeError(
            "超时：未锁定订场小程序手机形弹窗。"
            "请确认飞书已登录且小程序能弹出，然后运行: "
            "python3 scripts/list_feishu_windows.py"
        )

    def _prepare_window(self) -> WindowGeom:
        logger.info(
            "准备订场弹窗：open_applink=%s auto_launch=%s url=%s",
            self.open_applink_flag,
            self.auto_launch,
            self.start_url[:60] if self.start_url else "",
        )

        if self.open_applink_flag and self.start_url:
            try:
                ensure_feishu_window(
                    target_titles=self.target_titles,
                    exclude_titles=self.exclude_titles,
                    search_keywords=self.search_keywords,
                    auto_launch=self.auto_launch,
                    open_applink_url=self.start_url,
                    open_applink_via=self.open_applink_via,
                    wait_seconds=self.applink_wait_seconds,
                )
            except RuntimeError as exc:
                logger.warning("applink/自动启动阶段: %s", exc)

        geom = self._wait_and_lock_booking()
        self._locked_wid = geom.wid
        title = self._assert_locked_is_booking()
        logger.info(
            "会话锁定 wid=%s name=%r（后续前台必须一直是订场弹窗）",
            geom.wid,
            title,
        )
        self._geom = geom
        return geom

    def warmup_open_only(self) -> WindowGeom:
        """0 点前预热：只打开小程序停在首页，禁止任何业务点击。"""
        logger.info(
            "预热：applink 打开小程序并停在首页（不点预约入口，等新一天刷新）"
        )
        geom = self._prepare_window()
        self._wait_for_home_ready()
        logger.info(
            "预热完成：已打开首页 wid=%s name=%r %dx%d，等待放场时间",
            geom.wid,
            geom.name,
            geom.w,
            geom.h,
        )
        return geom

    def _clicks_to_run(self) -> list[dict[str, Any]]:
        if not self.clicks:
            return []
        if self.dry_run and self.skip_last_on_dry_run:
            n = max(1, self.skip_last_n)
            if len(self.clicks) >= n:
                for s in self.clicks[-n:]:
                    logger.info(
                        "dry_run: 跳过「%s」，避免误提交",
                        s.get("label", "?"),
                    )
                return self.clicks[:-n]
        return self.clicks

    def _paste_for_step(
        self, step: dict[str, Any], booking_date: str, court: str, slot: str
    ) -> None:
        paste_val = step.get("paste")
        if paste_val is None and step.get("action") == "paste_reason":
            paste_val = self.reason_text
        if not paste_val:
            return
        text = str(paste_val).format(
            court_name=court,
            time_slot=slot,
            booking_date=booking_date,
            reason=self.reason_text,
        )
        logger.info("粘贴: %s", text[:50])
        # 焦点校验交给 paste_text 一次完成，避免重复 activate
        paste_text(text, require_wid=self._locked_wid)

    def _do_scroll(self, pyautogui, step: dict[str, Any], geom: WindowGeom) -> None:
        clicks = int(step.get("scroll_clicks", self.default_scroll_clicks))
        label = step.get("label", "下滑")
        cx = geom.x + int(geom.w * 0.85)
        cy = geom.y + int(geom.h * 0.55)
        logger.info(
            "锁定窗口 name=%r wid=%s 本步「%s」scroll_clicks=%d move@(%d,%d) 无点击",
            geom.name,
            geom.wid,
            label,
            clicks,
            cx,
            cy,
        )
        pyautogui.moveTo(cx, cy)
        time.sleep(0.05)
        pyautogui.scroll(clicks)
        time.sleep(0.12)

    def _entry_btn_roi(self, w: int, h: int) -> tuple[int, int, int, int]:
        """预约入口红点图标相对区域（由首页截图标定）。"""
        return (
            int(w * 0.178),
            int(h * 0.377),
            int(w * 0.314),
            int(h * 0.446),
        )

    def _roi_redish_ratio(self, img, box: tuple[int, int, int, int]) -> float:
        patch = img.crop(box).resize((24, 24))
        pixels = list(patch.getdata())
        if not pixels:
            return 0.0
        redish = 0
        for r, g, b in pixels:
            if r > 150 and r > g + 30 and r > b + 30:
                redish += 1
        return redish / len(pixels)

    def _home_looks_ready(self, img) -> bool:
        """首页已画出预约入口：模板命中 + 红点图标。"""
        from PIL import Image

        assets = self._assets_dir()
        tpl_path = assets / "entry_btn_crop.png"
        if not tpl_path.is_file():
            logger.warning("首页预约入口模板缺失: %s", tpl_path)
            return False

        hay = img.convert("RGB")
        w, h = hay.size
        box = self._entry_btn_roi(w, h)
        if box[2] <= box[0] + 4 or box[3] <= box[1] + 4:
            return False
        patch = hay.crop(box)
        needle = Image.open(tpl_path).convert("RGB").resize(patch.size)
        rms = self._image_rms(patch, needle)
        self._last_home_rms = rms
        red = self._roi_redish_ratio(hay, box)
        # 首页自测 rms≈0–3；白屏≈28
        ok = rms <= 18.0 and red >= 0.04
        logger.info(
            "首页就绪校验 rms=%.1f red=%.2f threshold=(18,0.04) %s",
            rms,
            red,
            "命中" if ok else "未命中",
        )
        return ok

    def _save_home_fail_shot(self, img) -> Path | None:
        """首页未就绪时保存截图，便于对照屏保/白屏/锁屏。"""
        try:
            logs = Path(__file__).resolve().parent.parent / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            path = logs / f"home_fail_{stamp}.png"
            img.convert("RGB").save(path)
            logger.warning("已保存首页失败截图: %s", path)
            return path
        except Exception as exc:
            logger.warning("保存首页失败截图失败: %s", exc)
            return None

    def _wait_for_home_ready(self, timeout: float | None = None) -> None:
        """等到预约入口可见；超时抛错，避免加载中乱点。"""
        if not self._locked_wid:
            raise RuntimeError("未锁定订场窗口，无法等待首页")
        wait = self.home_ready_wait_seconds if timeout is None else timeout
        deadline = time.time() + max(0.5, wait)
        last_rms = None
        last_img = None
        woke = False
        while time.time() < deadline:
            try:
                if get_active_window_id() is None and not woke:
                    wake_idle_session()
                    woke = True
                activate_window(self._locked_wid)
                geom = get_window_geometry(self._locked_wid)
                geom.name = get_window_name(self._locked_wid)
                self._geom = geom
                img = self._grab_window_image(geom)
                if img is not None:
                    last_img = img
                    if self._home_looks_ready(img):
                        logger.info(
                            "首页已就绪（预约入口可见） wid=%s rms=%s",
                            self._locked_wid,
                            self._last_home_rms,
                        )
                        return
                last_rms = self._last_home_rms
            except Exception as exc:
                logger.warning("等待首页就绪时: %s", exc)
            time.sleep(0.35)

        shot = self._save_home_fail_shot(last_img) if last_img is not None else None
        active = get_active_window_id()
        if active is None:
            raise RuntimeError(
                f"等待首页预约入口超时 {wait:.0f}s（最后 rms={last_rms}，"
                f"截图={shot}）。前台仍为空：会话可能已锁屏需密码，"
                "脚本无法自动解锁。"
            )
        raise RuntimeError(
            f"等待首页预约入口超时 {wait:.0f}s"
            f"（最后 rms={last_rms}，截图={shot}）。页面可能未加载完。"
        )

    def _title_looks_success(self, name: str) -> bool:
        return any(t and t in name for t in self.success_titles)

    def _assets_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent / "assets"

    def _grab_window_image(self, geom: WindowGeom):
        """截取锁定窗区域（mss，不依赖 gnome-screenshot）。"""
        try:
            import mss
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError(
                "成功页截图需要 mss + Pillow: pip install mss Pillow"
            ) from exc

        left = int(geom.x)
        top = int(geom.y)
        width = int(geom.w)
        height = int(geom.h)
        if width <= 0 or height <= 0:
            return None

        monitor = {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        }
        with mss.MSS() as sct:
            shot = sct.grab(monitor)
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    @staticmethod
    def _image_rms(a, b) -> float:
        from PIL import ImageChops, ImageStat

        if a.size != b.size:
            b = b.resize(a.size)
        diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
        stat = ImageStat.Stat(diff)
        means = stat.mean or [255.0]
        return (sum(m * m for m in means) / len(means)) ** 0.5

    def _template_match_success(self, img) -> bool:
        """无 tesseract 时：比对红顶栏「提交成功」区域（颜色特征强，不易误判白屏）。"""
        from PIL import Image

        assets = self._assets_dir()
        tpl_path = assets / "success_header_crop.png"
        if not tpl_path.is_file():
            logger.warning("成功页顶栏模板缺失: %s", tpl_path)
            return False

        hay = img.convert("RGB")
        w, h = hay.size
        # 顶栏中央标题带
        box = (int(w * 0.22), int(h * 0.02), int(w * 0.78), int(h * 0.14))
        if box[2] <= box[0] + 4 or box[3] <= box[1] + 4:
            return False
        patch = hay.crop(box)
        needle = Image.open(tpl_path).convert("RGB").resize(patch.size)
        rms = self._image_rms(patch, needle)
        # 成功页实测 ~12；纯红条 ~34；白屏 ~90
        ok = rms <= 22.0
        logger.info(
            "成功页顶栏模板 rms=%.1f threshold=22 %s",
            rms,
            "命中" if ok else "未命中",
        )
        return ok

    def _red_header_heuristic(self, img) -> bool:
        """成功页顶栏为红色；粗检避免误判首页。"""
        w, h = img.size
        band = img.crop((int(w * 0.2), 0, int(w * 0.8), max(2, int(h * 0.08))))
        pixels = list(band.resize((40, 8)).getdata())
        if not pixels:
            return False
        redish = 0
        for r, g, b in pixels:
            if r > 140 and r > g + 40 and r > b + 40:
                redish += 1
        return redish >= len(pixels) * 0.35

    def _ocr_image_text(self, img) -> str:
        """有 tesseract 则 OCR；否则返回空串（不抛错）。"""
        try:
            import pytesseract
        except ImportError:
            return ""
        try:
            return pytesseract.image_to_string(img, lang="chi_sim") or ""
        except pytesseract.TesseractNotFoundError:
            logger.warning(
                "系统未安装 tesseract，改用成功页模板匹配。"
                "建议: sudo apt-get install -y tesseract-ocr tesseract-ocr-chi_sim"
            )
            return ""
        except Exception as exc:
            logger.warning("OCR chi_sim 失败 (%s)，尝试 eng", exc)
            try:
                return pytesseract.image_to_string(img, lang="eng") or ""
            except Exception:
                return ""

    def _ocr_window_text(self, pyautogui, geom: WindowGeom) -> str:
        """截取锁定窗并 OCR（可选）。"""
        del pyautogui
        img = self._grab_window_image(geom)
        if img is None:
            return ""
        return self._ocr_image_text(img)

    @staticmethod
    def _normalize_ocr(text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    def _ocr_looks_success(self, text: str) -> bool:
        compact = self._normalize_ocr(text)
        self._last_ocr_snippet = compact[:80]
        for kw in self.success_ocr_keywords:
            needle = self._normalize_ocr(str(kw))
            if needle and needle in compact:
                return True
        return False

    def _image_looks_success(self, img) -> bool:
        """OCR 优先；无 tesseract 时用「红顶栏 + 模板」兜底。"""
        text = self._ocr_image_text(img)
        if text:
            logger.info(
                "成功校验 OCR 片段=%r",
                self._normalize_ocr(text)[:80],
            )
            if self._ocr_looks_success(text):
                return True
        has_red = self._red_header_heuristic(img)
        matched = self._template_match_success(img)
        if matched and has_red:
            self._last_ocr_snippet = "template+red:提交成功"
            return True
        if matched and not has_red:
            logger.info("模板有相似块但无红顶栏，忽略（防误判）")
        if has_red and not matched:
            logger.info("检测到红顶栏，但模板未确认成功文案")
        return False

    def _wait_for_success_ui(self, pyautogui) -> bool:
        """标题旁路 + mss 截窗 OCR/模板判定成功页。"""
        if not self._locked_wid:
            return False
        deadline = time.time() + max(0.5, self.success_wait_seconds)
        self._last_ocr_snippet = ""
        while time.time() < deadline:
            name = get_window_name(self._locked_wid)
            logger.info("成功校验: 前台标题=%r wid=%s", name, self._locked_wid)
            if self._title_looks_success(name):
                logger.info("已确认预约成功页(窗口标题): %r", name)
                return True

            active = get_active_window_id()
            if active:
                aname = get_window_name(active)
                if self._title_looks_success(aname):
                    logger.info("已确认预约成功页(前台标题): %r", aname)
                    return True

            if self.success_use_ocr:
                try:
                    geom = get_window_geometry(self._locked_wid)
                    geom.name = name or geom.name
                    img = self._grab_window_image(geom)
                    if img is not None and self._image_looks_success(img):
                        logger.info(
                            "已确认预约成功页(画面): %r",
                            self._last_ocr_snippet,
                        )
                        return True
                except Exception as exc:
                    logger.warning("成功页画面校验暂不可用: %s", exc)
                    time.sleep(0.4)
                    continue

            time.sleep(0.25)
        return False

    def _execute_steps(
        self,
        pyautogui,
        steps: list[dict[str, Any]],
        court_name: str,
        time_slot: str,
        booking_date: str,
        *,
        screen_points: list[tuple[int, int]],
    ) -> None:
        if steps and "预约" not in str(steps[0].get("label", "")):
            logger.warning(
                "fire 第一步 label=%r，期望为预约入口。请确认坐标对准首页入口按钮。",
                steps[0].get("label"),
            )

        for i, step in enumerate(steps, 1):
            geom = self._refresh_locked_geom()
            label = step.get("label", f"步骤{i}")
            action = str(step.get("action") or "").lower().strip()

            if action == "scroll":
                self._do_scroll(pyautogui, step, geom)
                continue

            image = step.get("image", "")

            if image:
                img_path = Path(image)
                if not img_path.is_absolute():
                    img_path = Path.cwd() / img_path
                if not img_path.exists():
                    raise RuntimeError(f"找不到图片: {img_path}")
                loc = pyautogui.locateCenterOnScreen(str(img_path), confidence=0.8)
                if loc is None:
                    raise RuntimeError(f"屏幕上未找到图片: {img_path}")
                x, y = int(loc.x), int(loc.y)
            else:
                x, y = resolve_click_xy(step, geom)
                if not geom.contains(x, y, margin=8):
                    raise RuntimeError(
                        f"步骤「{label}」坐标 ({x},{y}) 落在锁定窗口外 "
                        f"(name={geom.name!r} {geom.w}x{geom.h})。"
                        "请对着订场小程序首页重录坐标: "
                        "python3 scripts/record_desktop_clicks.py --apply"
                    )

            screen_points.append((x, y))
            if len(screen_points) >= 3:
                a, b, c = screen_points[-3], screen_points[-2], screen_points[-1]
                if a == b == c:
                    raise RuntimeError(
                        f"连续 3 步点击落在同一点 {a}。"
                        "请确认订场小程序弹窗在最上层并重录坐标。"
                    )

            logger.info(
                "锁定窗口 name=%r wid=%s 本步「%s」屏幕坐标=(%d,%d)",
                geom.name,
                geom.wid,
                label,
                x,
                y,
            )
            pyautogui.click(x, y)
            time.sleep(self._pause_after_step(label))
            self._paste_for_step(step, booking_date, court_name, time_slot)

        for item in self.type_text:
            self._assert_locked_is_booking()
            text = str(item.get("text", "")).format(
                court_name=court_name,
                time_slot=time_slot,
                booking_date=booking_date,
                reason=self.reason_text,
            )
            if text:
                logger.info("输入文本: %s", text[:40])
                pyautogui.write(text, interval=0.03)
                time.sleep(0.15)

    def book(
        self,
        court_name: str,
        time_slot: str,
        booking_date: str,
        *,
        dry_run: bool | None = None,
        wait_for_open: Callable[[], None] | None = None,
    ) -> BookingResult:
        if dry_run is not None:
            self.dry_run = dry_run
        if not self.clicks and not self.pre_clicks:
            return BookingResult(
                False,
                "desktop.clicks 为空。请先: "
                "python3 scripts/record_desktop_clicks.py --apply",
            )

        pyautogui = self._import_pyautogui()
        screen_points: list[tuple[int, int]] = []
        try:
            if self.warmup_before_open:
                self.warmup_open_only()
                if wait_for_open is not None:
                    wait_for_open()
                # 等到点后可能又灭屏：先唤醒再确认首页
                wake_idle_session()
                # 到点后重新确认仍在订场首页
                title = self._assert_locked_is_booking()
                if not is_booking_title(title, self.target_titles):
                    raise RuntimeError(
                        f"放场时前台不是订场首页 {title!r}，将重试打开。"
                    )
                logger.info(
                    "放场时刻到，开始开火：第一枪预约入口 wid=%s name=%r",
                    self._locked_wid,
                    title,
                )
                try:
                    ensure_window_focused(self._locked_wid)  # type: ignore[arg-type]
                except RuntimeError:
                    # 焦点丢失：若首页仍可见则继续；否则重新打开
                    try:
                        wake_idle_session()
                        activate_window(self._locked_wid)  # type: ignore[arg-type]
                        geom = get_window_geometry(self._locked_wid)  # type: ignore[arg-type]
                        img = self._grab_window_image(geom)
                        if img is None or not self._home_looks_ready(img):
                            self._prepare_window()
                            self._wait_for_home_ready()
                    except Exception:
                        self._prepare_window()
                        self._wait_for_home_ready()
                # 开火前再确认预约入口已画出（--now / 重试也会走到这里）
                self._wait_for_home_ready()
            else:
                if wait_for_open is not None:
                    wait_for_open()
                wake_idle_session()
                self._prepare_window()
                self._wait_for_home_ready()

            if self.pre_clicks:
                self._execute_steps(
                    pyautogui,
                    self.pre_clicks,
                    court_name,
                    time_slot,
                    booking_date,
                    screen_points=screen_points,
                )
            steps = self._clicks_to_run()
            self._execute_steps(
                pyautogui,
                steps,
                court_name,
                time_slot,
                booking_date,
                screen_points=screen_points,
            )

            if self.dry_run:
                return BookingResult(
                    True,
                    f"[dry_run] 已在锁定订场窗内验证(跳过末步): "
                    f"{court_name} {time_slot}",
                )

            if not self._wait_for_success_ui(pyautogui):
                active = get_active_window_id()
                aname = get_window_name(active) if active else ""
                ocr_hint = self._last_ocr_snippet or "(无)"
                return BookingResult(
                    False,
                    f"未出现提交成功页（当前前台={aname!r} OCR={ocr_hint!r}）。"
                    "可能点偏或场地已被抢，将重试。",
                )

            return BookingResult(
                True,
                f"已确认提交成功: {court_name} {time_slot}",
            )
        except Exception as exc:
            logger.error("桌面预约失败: %s", exc)
            return BookingResult(False, str(exc))
        finally:
            self._locked_wid = None
            self._geom = None


def attempt_desktop_booking(
    pw_config: dict[str, Any],
    preferences: list[dict[str, str]],
    booking_date: str,
    retry_count: int,
    retry_interval_ms: int,
    *,
    dry_run: bool = False,
    wait_for_open: Callable[[], None] | None = None,
) -> BookingResult:
    """桌面模式入口：按偏好重试抢场。"""
    cfg = dict(pw_config)
    cfg["dry_run"] = dry_run
    last = BookingResult(success=False, message="未执行")
    for pref in preferences:
        court = pref.get("court", "")
        time_slot = pref.get("time", "")
        for attempt in range(1, retry_count + 1):
            logger.info(
                "桌面模式尝试 %d/%d: %s %s",
                attempt,
                retry_count,
                court,
                time_slot,
            )
            booker = DesktopBooker(cfg)
            # 每轮都带 wait_for_open：未到放场时间继续等；已过则立即开火
            result = booker.book(
                court,
                time_slot,
                booking_date,
                dry_run=dry_run,
                wait_for_open=wait_for_open,
            )
            if result.success:
                return result
            last = result
            if attempt < retry_count:
                logger.info(
                    "本轮未成功 (%s)，%.0f ms 后重试…",
                    result.message,
                    retry_interval_ms,
                )
                time.sleep(retry_interval_ms / 1000.0)
    return last
