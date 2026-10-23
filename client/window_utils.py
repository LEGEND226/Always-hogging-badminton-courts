"""Linux 桌面窗口定位与相对坐标换算（依赖 xdotool）。

优先锁定手机形小程序弹窗（目标窗口标题通过 config.yaml
playwright.desktop.target_window_titles / search_keywords 配置）。
绝不点飞书消息主窗、工作台、Cursor/Chrome。
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

# systemd-inhibit 子进程：持有 idle:sleep 抑制直到本进程退出
_inhibit_proc: subprocess.Popen[bytes] | None = None

FEISHU_BIN_CANDIDATES = (
    "feishu",
    "lark",
    "/opt/bytedance/feishu/feishu",
    "/opt/bytedance/lark/lark",
)

# 只搜订场小程序相关标题；不搜裸「飞书」/企业工作台（会命中消息主窗/工作台）
# 默认值是通用词，实际部署时请在 config.yaml playwright.desktop.search_keywords
# 填入你的小程序弹窗窗口名
SEARCH_KEYWORDS = [
    "预约",
    "订场",
    "场馆",
]

DEFAULT_TARGET_TITLES = [
    "预约",
    "订场",
    "场馆",
]

DEFAULT_EXCLUDE_TITLES = [
    "飞书文档",
    "文档",
    "Docs",
    "doc",
    "Untitled",
    ".docx",
    "云文档",
    "消息",
    "会话",
    "聊天",
    "Messenger",
    "Inbox",
    "Cursor",
    "Chrome",
    "Firefox",
    ".md",
    "jiaoben",
    "Google Chrome",
]

# 列表工具用：额外搜主窗/工作台以便标注「禁止点击」
LIST_EXTRA_KEYWORDS = [
    "飞书",
    "Feishu",
    "Lark",
    "LarkSuite",
]

# 手机形小程序约 350x700；允许略窄
MIN_WINDOW_W = 300
MIN_WINDOW_H = 400
# 工作台/大屏窗口面积过大则拒绝当作订场弹窗
MAX_BOOKING_AREA = 900_000
LARGE_DOC_AREA = 2_000_000
# 手机形加分：宽 300–520 且竖屏
PHONE_W_MIN = 300
PHONE_W_MAX = 520


@dataclass
class WindowGeom:
    wid: str
    x: int
    y: int
    w: int
    h: int
    name: str = ""

    @property
    def area(self) -> int:
        return self.w * self.h

    def contains(self, px: int, py: int, *, margin: int = 0) -> bool:
        return (
            self.x - margin <= px <= self.x + self.w + margin
            and self.y - margin <= py <= self.y + self.h + margin
        )


def require_xdotool() -> None:
    if not shutil.which("xdotool"):
        raise RuntimeError(
            "未找到 xdotool，请安装: sudo apt-get install -y xdotool"
        )


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=True,
        text=True,
    )


def _x11_socket_path(display: str) -> Path | None:
    """DISPLAY=:0 / :1.0 → /tmp/.X11-unix/X0"""
    name = (display or "").strip()
    if not name.startswith(":"):
        return None
    num = name[1:].split(".", 1)[0]
    if not num.isdigit():
        return None
    return Path("/tmp/.X11-unix") / f"X{num}"


def _display_socket_exists(display: str) -> bool:
    sock = _x11_socket_path(display)
    return bool(sock and sock.exists())


def _list_local_displays() -> list[str]:
    """扫描 /tmp/.X11-unix/X*，优先 :0 再 :1。"""
    root = Path("/tmp/.X11-unix")
    if not root.is_dir():
        return []
    nums: list[int] = []
    for p in root.iterdir():
        name = p.name
        if name.startswith("X") and name[1:].isdigit():
            nums.append(int(name[1:]))
    nums.sort()
    # 常见桌面会话优先 :0
    preferred = [n for n in (0, 1) if n in nums]
    rest = [n for n in nums if n not in preferred]
    return [f":{n}" for n in preferred + rest]


def _resolve_xauthority() -> str | None:
    candidates = [
        os.environ.get("XAUTHORITY", ""),
        str(Path.home() / ".Xauthority"),
        f"/run/user/{os.getuid()}/gdm/Xauthority",
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    return None


def ensure_display() -> str:
    """确保进程能连上图形会话：纠错 DISPLAY，补齐 XAUTHORITY。

    cron 常写死 :1，重启后会话可能在 :0。返回最终 DISPLAY。
    """
    current = os.environ.get("DISPLAY", "").strip()
    if current and _display_socket_exists(current):
        display = current
    else:
        if current:
            logger.warning(
                "DISPLAY=%s 的 X11 socket 不存在，尝试自动探测",
                current,
            )
        candidates = _list_local_displays()
        if not candidates:
            raise RuntimeError(
                "未找到可用 X11 显示器（/tmp/.X11-unix/X* 为空）。"
                "请保持已登录桌面会话，并确认 crontab 的 DISPLAY 正确。"
            )
        display = candidates[0]
        os.environ["DISPLAY"] = display
        logger.info("已切换 DISPLAY=%s（候选=%s）", display, candidates)

    xauth = _resolve_xauthority()
    if xauth:
        os.environ["XAUTHORITY"] = xauth
        logger.info("使用 XAUTHORITY=%s DISPLAY=%s", xauth, display)
    else:
        logger.warning(
            "未找到 XAUTHORITY 文件，DISPLAY=%s 可能无法鉴权",
            display,
        )
    return display


def ensure_session_bus() -> str | None:
    """cron 常缺 DBUS_SESSION_BUS_ADDRESS；从 /run/user/<uid>/bus 补齐。"""
    current = (os.environ.get("DBUS_SESSION_BUS_ADDRESS") or "").strip()
    if current:
        return current
    bus = Path(f"/run/user/{os.getuid()}/bus")
    if bus.exists():
        addr = f"unix:path={bus}"
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = addr
        logger.info("已补齐 DBUS_SESSION_BUS_ADDRESS=%s", addr)
        return addr
    logger.warning(
        "未找到 session bus (%s)，DBus 关屏保/inhibit 可能无效",
        bus,
    )
    return None


def wake_idle_session() -> None:
    """点亮外接屏、退出 GNOME 屏保，并微动鼠标（不输解锁密码）。"""
    ensure_session_bus()
    if shutil.which("xset"):
        _run(["xset", "dpms", "force", "on"], check=False)
        _run(["xset", "s", "reset"], check=False)
    else:
        logger.warning("未找到 xset，跳过 DPMS 唤醒")

    dbus_cmds: list[list[str]] = []
    if shutil.which("gdbus"):
        dbus_cmds.append(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.gnome.ScreenSaver",
                "--object-path",
                "/org/gnome/ScreenSaver",
                "--method",
                "org.gnome.ScreenSaver.SetActive",
                "false",
            ]
        )
    if shutil.which("dbus-send"):
        dbus_cmds.append(
            [
                "dbus-send",
                "--session",
                "--dest=org.gnome.ScreenSaver",
                "--type=method_call",
                "/org/gnome/ScreenSaver",
                "org.gnome.ScreenSaver.SetActive",
                "boolean:false",
            ]
        )
    for cmd in dbus_cmds:
        out = _run(cmd, check=False)
        if out.returncode == 0:
            logger.info("已请求关闭 GNOME 屏保: %s", cmd[0])
            break
        logger.debug("关屏保失败 (%s): %s", cmd[0], (out.stderr or "").strip())

    if shutil.which("xdotool"):
        _run(["xdotool", "mousemove_relative", "--", "1", "0"], check=False)
        _run(["xdotool", "mousemove_relative", "--", "-1", "0"], check=False)
    time.sleep(0.55)
    try:
        active = get_active_window_id()
        name = get_window_name(active) if active else ""
        logger.info("唤醒会话后前台 wid=%s name=%r", active, name)
    except Exception as exc:
        logger.warning("唤醒后读取前台失败: %s", exc)


def _stop_idle_inhibit() -> None:
    global _inhibit_proc
    proc = _inhibit_proc
    _inhibit_proc = None
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def start_idle_inhibit() -> None:
    """抢场期间禁止空闲灭屏/休眠（systemd-inhibit 持有到进程结束）。"""
    global _inhibit_proc
    if _inhibit_proc is not None and _inhibit_proc.poll() is None:
        return
    if not shutil.which("systemd-inhibit"):
        logger.warning("未找到 systemd-inhibit，无法禁止空闲灭屏")
        return
    try:
        _inhibit_proc = subprocess.Popen(
            [
                "systemd-inhibit",
                "--what=idle:sleep",
                "--who=feishu-booking",
                "--why=warmup-and-book",
                "--mode=block",
                "sleep",
                "infinity",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        atexit.register(_stop_idle_inhibit)
        logger.info(
            "已启动 idle:sleep 抑制 (pid=%s)，直到本进程结束",
            _inhibit_proc.pid,
        )
    except Exception as exc:
        logger.warning("启动 systemd-inhibit 失败: %s", exc)
        _inhibit_proc = None


def find_feishu_bin() -> str | None:
    env_bin = os.environ.get("FEISHU_BIN", "")
    if env_bin and shutil.which(env_bin):
        return shutil.which(env_bin)
    if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
        return env_bin
    for cand in FEISHU_BIN_CANDIDATES:
        if "/" in cand:
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        else:
            found = shutil.which(cand)
            if found:
                return found
    return None


def get_window_name(wid: str) -> str:
    require_xdotool()
    out = _run(["xdotool", "getwindowname", wid], check=False)
    return (out.stdout or "").strip()


def get_active_window_id() -> str | None:
    require_xdotool()
    out = _run(["xdotool", "getactivewindow"], check=False)
    wid = (out.stdout or "").strip()
    return wid or None


def search_window_ids(title_keywords: Iterable[str]) -> list[str]:
    require_xdotool()
    found: list[str] = []
    for kw in title_keywords:
        out = _run(
            ["xdotool", "search", "--onlyvisible", "--name", kw],
            check=False,
        )
        for line in out.stdout.splitlines():
            wid = line.strip()
            if wid and wid not in found:
                found.append(wid)
    return found


def get_window_geometry(wid: str) -> WindowGeom:
    require_xdotool()
    out = _run(["xdotool", "getwindowgeometry", "--shell", wid])
    vals: dict[str, int] = {}
    for line in out.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip()] = int(v.strip())
    return WindowGeom(
        wid=wid,
        x=vals.get("X", 0),
        y=vals.get("Y", 0),
        w=vals.get("WIDTH", 0),
        h=vals.get("HEIGHT", 0),
        name=get_window_name(wid),
    )


def activate_window(wid: str) -> None:
    require_xdotool()
    _run(["xdotool", "windowactivate", "--sync", wid], check=False)
    _run(["xdotool", "windowraise", wid], check=False)
    time.sleep(0.35)


def ensure_window_focused(
    wid: str,
    *,
    retries: int = 3,
    wake_on_none: bool = True,
) -> None:
    """激活并校验前台窗口必须是指定 wid。got=None 时先唤醒屏保再试。"""
    woke = False
    for attempt in range(1, retries + 1):
        activate_window(wid)
        active = get_active_window_id()
        if active == wid:
            return
        if wake_on_none and active is None and not woke:
            logger.warning("前台为空 (got=None)，尝试唤醒屏保/显示器后再激活")
            wake_idle_session()
            woke = True
            continue
        logger.warning(
            "前台窗口不是目标 (want=%s got=%s) 重试 %d/%d",
            wid,
            active,
            attempt,
            retries,
        )
        time.sleep(0.4)
    active = get_active_window_id()
    name = get_window_name(active) if active else ""
    raise RuntimeError(
        f"无法把订场窗口置于前台 (目标 wid={wid}，当前={active} name={name!r})。"
        "请关掉飞书文档等遮挡窗口，把订场弹窗点到最上层后再试。"
        "若已锁屏需密码，脚本无法自动解锁。"
    )


def is_large_enough(geom: WindowGeom) -> bool:
    return geom.w >= MIN_WINDOW_W and geom.h >= MIN_WINDOW_H


def _name_matches_any(name: str, keywords: list[str]) -> bool:
    """子串匹配（用于排除列表）。"""
    lower = name.lower()
    for kw in keywords:
        if not kw:
            continue
        if kw.lower() in lower or kw in name:
            return True
    return False


def _normalize_title(name: str) -> str:
    return "".join((name or "").split())


def is_excluded_title(name: str, exclude: list[str] | None = None) -> bool:
    return _name_matches_any(name, exclude or DEFAULT_EXCLUDE_TITLES)


def is_booking_title(name: str, target_titles: list[str] | None = None) -> bool:
    """严格判断是否为订场小程序标题。

    接受：标题去空白后等于关键词，或以关键词开头且总长很短（≤ 关键词+6）。
    拒绝：Cursor 文件名、企业工作台大窗等。
    """
    targets = target_titles or DEFAULT_TARGET_TITLES
    if is_excluded_title(name):
        return False
    norm = _normalize_title(name)
    if not norm:
        return False
    for kw in targets:
        if not kw:
            continue
        k = _normalize_title(kw)
        if not k:
            continue
        if norm == k:
            return True
        # 允许极短后缀，如「订场小程序 - xxx」，但不允许长文件名
        if norm.startswith(k) and len(norm) <= len(k) + 6:
            return True
    return False


def is_main_feishu_title(name: str) -> bool:
    """是否像飞书消息主窗或企业工作台（禁止点击）。"""
    if is_booking_title(name):
        return False
    # 企业工作台大窗不是订场弹窗
    norm = _normalize_title(name)
    if norm in {"飞书", "Feishu", "Lark", "LarkSuite"}:
        return True
    return _name_matches_any(
        name, ["飞书", "Feishu", "Lark", "LarkSuite", "消息", "会话", "聊天"]
    )


def is_phone_shaped(geom: WindowGeom) -> bool:
    return (
        PHONE_W_MIN <= geom.w <= PHONE_W_MAX
        and geom.h >= geom.w
        and geom.h >= MIN_WINDOW_H
    )


def score_booking_window(
    geom: WindowGeom,
    *,
    target_titles: list[str] | None = None,
    prefer_new: set[str] | None = None,
) -> int:
    """分数越高越像订场弹窗。无严格标题匹配或过大 → -1。"""
    targets = target_titles or DEFAULT_TARGET_TITLES
    name = geom.name or get_window_name(geom.wid)

    if is_excluded_title(name) or is_main_feishu_title(name):
        return -1

    if not is_booking_title(name, targets):
        return -1

    if geom.area > MAX_BOOKING_AREA:
        return -1

    score = 0
    norm = _normalize_title(name)
    for i, kw in enumerate(targets):
        k = _normalize_title(kw)
        if not k:
            continue
        if norm == k:
            score += 120 - i * 5
        elif norm.startswith(k):
            score += 100 - i * 5

    if prefer_new and geom.wid in prefer_new:
        score += 80

    if is_phone_shaped(geom):
        score += 50
    elif geom.area >= LARGE_DOC_AREA:
        return -1
    elif geom.w > 900:
        score -= 80

    return score


def assert_foreground_is_booking(
    wid: str,
    target_titles: list[str] | None = None,
    *,
    exclude_titles: list[str] | None = None,
) -> str:
    """校验前台窗口是指定 wid，且标题是订场窗。返回前台标题。"""
    ensure_window_focused(wid)
    active = get_active_window_id()
    if active != wid:
        aname = get_window_name(active) if active else ""
        raise RuntimeError(
            f"前台窗口不是锁定的订场窗 (want={wid} got={active} name={aname!r})。"
            "请把订场小程序窗口点到最上层后再试。"
        )
    name = get_window_name(wid)
    logger.info("前台标题=%r wid=%s", name, wid)
    if is_excluded_title(name, exclude_titles):
        raise RuntimeError(
            f"前台是被排除窗口 {name!r}，拒绝点击（避免点到文档/消息）。"
        )
    if is_main_feishu_title(name) or not is_booking_title(name, target_titles):
        raise RuntimeError(
            f"前台不是订场窗 {name!r}。"
            "需要标题恰好匹配 target_window_titles 的手机形小程序弹窗"
            "（不要停在消息页/工作台/Cursor）。"
        )
    return name


def list_candidate_windows(
    search_keywords: list[str] | None = None,
    *,
    exclude_titles: list[str] | None = None,
    min_w: int = MIN_WINDOW_W,
    min_h: int = MIN_WINDOW_H,
) -> list[WindowGeom]:
    """列出可见、够大、且不在黑名单中的候选窗口。"""
    keywords = search_keywords or list(SEARCH_KEYWORDS)
    exclude = exclude_titles or list(DEFAULT_EXCLUDE_TITLES)
    wids = search_window_ids(keywords)
    out: list[WindowGeom] = []
    for wid in wids:
        try:
            geom = get_window_geometry(wid)
        except Exception:
            continue
        if geom.w < min_w or geom.h < min_h:
            continue
        name = geom.name or get_window_name(wid)
        geom.name = name
        if is_excluded_title(name, exclude) or is_main_feishu_title(name):
            logger.info("排除窗口: wid=%s name=%r", wid, name)
            continue
        if not is_booking_title(name):
            logger.info("跳过非订场标题: wid=%s name=%r", wid, name)
            continue
        if geom.area > MAX_BOOKING_AREA:
            logger.info(
                "跳过过大窗口(非手机弹窗): wid=%s name=%r %dx%d",
                wid,
                name,
                geom.w,
                geom.h,
            )
            continue
        out.append(geom)
    return out


def find_booking_window(
    *,
    target_titles: list[str] | None = None,
    exclude_titles: list[str] | None = None,
    search_keywords: list[str] | None = None,
    prefer_wids: set[str] | None = None,
    min_w: int = MIN_WINDOW_W,
    min_h: int = MIN_WINDOW_H,
) -> WindowGeom | None:
    """选订场弹窗：按目标标题评分，同分偏好较小/新建窗口，绝不选文档。"""
    candidates = list_candidate_windows(
        search_keywords,
        exclude_titles=exclude_titles,
        min_w=min_w,
        min_h=min_h,
    )
    if not candidates:
        return None

    targets = target_titles or DEFAULT_TARGET_TITLES
    scored: list[tuple[int, WindowGeom]] = []
    for geom in candidates:
        s = score_booking_window(
            geom, target_titles=targets, prefer_new=prefer_wids
        )
        scored.append((s, geom))
        logger.debug(
            "候选 wid=%s name=%r score=%d size=%dx%d",
            geom.wid,
            geom.name,
            s,
            geom.w,
            geom.h,
        )

    # 分数高优先；同分选面积较小（弹窗）
    scored.sort(key=lambda t: (-t[0], t[1].area))
    best_score, best = scored[0]
    if best_score <= 0:
        logger.warning(
            "未找到高分订场窗口，最高分=%d name=%r；请确认订场小程序已打开",
            best_score,
            best.name,
        )
        return None

    logger.info(
        "选定订场窗口 wid=%s name=%r score=%d geom=(%d,%d %dx%d)",
        best.wid,
        best.name,
        best_score,
        best.x,
        best.y,
        best.w,
        best.h,
    )
    return best


def find_feishu_window(
    title_keywords: list[str] | None = None,
    *,
    min_w: int = MIN_WINDOW_W,
    min_h: int = MIN_WINDOW_H,
) -> WindowGeom | None:
    """兼容旧接口：委托给 find_booking_window。"""
    return find_booking_window(
        target_titles=title_keywords or DEFAULT_TARGET_TITLES,
        search_keywords=list(SEARCH_KEYWORDS),
        min_w=min_w,
        min_h=min_h,
    )


def find_window_under_cursor(
    title_keywords: list[str] | None = None,
    *,
    cursor_x: int,
    cursor_y: int,
    exclude_titles: list[str] | None = None,
    min_w: int = MIN_WINDOW_W,
    min_h: int = MIN_WINDOW_H,
) -> WindowGeom | None:
    """选中包含鼠标位置的订场相关窗口（录制时用）。"""
    candidates = list_candidate_windows(
        list(SEARCH_KEYWORDS),
        exclude_titles=exclude_titles,
        min_w=min_w,
        min_h=min_h,
    )
    under = [g for g in candidates if g.contains(cursor_x, cursor_y, margin=20)]
    if not under:
        return find_booking_window(
            target_titles=title_keywords or DEFAULT_TARGET_TITLES,
            exclude_titles=exclude_titles,
            min_w=min_w,
            min_h=min_h,
        )
    # 鼠标下多个窗口时，偏好小窗口（弹窗在文档上面）
    under.sort(key=lambda g: g.area)
    return under[0]


def wait_for_booking_window(
    *,
    target_titles: list[str] | None = None,
    exclude_titles: list[str] | None = None,
    search_keywords: list[str] | None = None,
    prefer_wids: set[str] | None = None,
    wait_seconds: float = 30.0,
    min_w: int = MIN_WINDOW_W,
    min_h: int = MIN_WINDOW_H,
) -> WindowGeom | None:
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        geom = find_booking_window(
            target_titles=target_titles,
            exclude_titles=exclude_titles,
            search_keywords=search_keywords,
            prefer_wids=prefer_wids,
            min_w=min_w,
            min_h=min_h,
        )
        if geom is not None:
            return geom
        time.sleep(0.8)
    return None


def snapshot_window_ids(
    search_keywords: list[str] | None = None,
) -> set[str]:
    return set(search_window_ids(search_keywords or SEARCH_KEYWORDS))


def launch_feishu(applink: str = "") -> None:
    bin_path = find_feishu_bin()
    if bin_path:
        cmd = [bin_path]
        if applink:
            cmd.append(applink)
        logger.info("启动飞书: %s", " ".join(cmd))
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return
    raise RuntimeError(
        "未找到飞书客户端。请安装桌面飞书或设置 FEISHU_BIN=/path/to/feishu"
    )


def open_applink(url: str, *, via: str = "client") -> None:
    if not url:
        return
    via = (via or "client").lower().strip()
    if via == "browser":
        if not shutil.which("xdg-open"):
            raise RuntimeError("open_applink_via=browser 但未找到 xdg-open")
        logger.info("xdg-open applink (browser): %s", url)
        subprocess.Popen(
            ["xdg-open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return

    bin_path = find_feishu_bin()
    if not bin_path:
        raise RuntimeError(
            "open_applink_via=client 需要飞书客户端。"
            "请安装飞书或改 desktop.open_applink_via: browser"
        )
    logger.info("飞书客户端打开 applink: %s %s", bin_path, url)
    subprocess.Popen(
        [bin_path, url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def ensure_feishu_window(
    *,
    title_keywords: list[str] | None = None,
    target_titles: list[str] | None = None,
    exclude_titles: list[str] | None = None,
    search_keywords: list[str] | None = None,
    auto_launch: bool = True,
    open_applink_url: str = "",
    open_applink_via: str = "client",
    wait_seconds: float = 25.0,
) -> WindowGeom:
    """通过 applink（带 URL）打开并锁定订场弹窗；优先新弹出窗口。"""
    targets = target_titles or title_keywords or list(DEFAULT_TARGET_TITLES)
    exclude = exclude_titles or list(DEFAULT_EXCLUDE_TITLES)
    before_ids = snapshot_window_ids(search_keywords)

    if open_applink_url:
        # 冷/热启动都走 applink，绝不裸启动飞书（否则小程序不弹）
        logger.info("通过 applink 打开订场小程序: %s", open_applink_url)
        try:
            open_applink(open_applink_url, via=open_applink_via)
        except RuntimeError:
            if auto_launch and open_applink_via != "browser":
                # 客户端未就绪时用带 URL 的启动
                launch_feishu(open_applink_url)
            else:
                raise
        time.sleep(1.5)
        after_ids = snapshot_window_ids(search_keywords)
        new_ids = after_ids - before_ids
        geom = wait_for_booking_window(
            target_titles=targets,
            exclude_titles=exclude,
            search_keywords=search_keywords,
            prefer_wids=new_ids or None,
            wait_seconds=wait_seconds,
        )
        if geom is None:
            # 可能复用了已有窗，再试不限 prefer
            geom = wait_for_booking_window(
                target_titles=targets,
                exclude_titles=exclude,
                search_keywords=search_keywords,
                wait_seconds=min(8.0, wait_seconds),
            )
        if geom is None:
            raise RuntimeError(
                "打开 applink 后仍未找到订场弹窗（标题需匹配 target_window_titles）。"
                "请确认飞书已登录，再运行: python3 scripts/list_feishu_windows.py"
            )
    else:
        geom = find_booking_window(
            target_titles=targets,
            exclude_titles=exclude,
            search_keywords=search_keywords,
        )
        if geom is None and auto_launch:
            launch_feishu()
            geom = wait_for_booking_window(
                target_titles=targets,
                exclude_titles=exclude,
                search_keywords=search_keywords,
                wait_seconds=wait_seconds,
            )
        if geom is None:
            raise RuntimeError(
                "未找到订场弹窗（xdotool 标题需匹配 target_window_titles）。"
                "请确认小程序已弹出（约 350x700），再运行: "
                "python3 scripts/list_feishu_windows.py"
            )

    ensure_window_focused(geom.wid)
    geom = get_window_geometry(geom.wid)

    if is_excluded_title(geom.name, exclude):
        raise RuntimeError(
            f"选中的仍是被排除窗口: {geom.name!r}。请关掉文档，只留订场弹窗。"
        )

    logger.info(
        "锁定订场窗口 wid=%s name=%r geom=(%d,%d %dx%d)",
        geom.wid,
        geom.name,
        geom.x,
        geom.y,
        geom.w,
        geom.h,
    )
    return geom


def resolve_click_xy(step: dict, geom: WindowGeom) -> tuple[int, int]:
    """优先 rx/ry，再 px/py，再绝对 x/y。"""
    if step.get("rx") is not None and step.get("ry") is not None:
        return int(geom.x + int(step["rx"])), int(geom.y + int(step["ry"]))
    if step.get("px") is not None and step.get("py") is not None:
        px = float(step["px"])
        py = float(step["py"])
        if px >= 0.95 and py >= 0.95:
            logger.warning(
                "步骤「%s」px/py 接近 1.0，可能是坏坐标，请重新标定",
                step.get("label", "?"),
            )
        return int(geom.x + px * geom.w), int(geom.y + py * geom.h)
    if step.get("x") is not None and step.get("y") is not None:
        return int(step["x"]), int(step["y"])
    raise RuntimeError(f"点击步骤缺少坐标字段: {step.get('label', step)}")


def absolute_to_relative(abs_x: int, abs_y: int, geom: WindowGeom) -> dict[str, float | int]:
    rx = abs_x - geom.x
    ry = abs_y - geom.y
    px = (rx / geom.w) if geom.w else 0.0
    py = (ry / geom.h) if geom.h else 0.0
    return {
        "px": round(px, 4),
        "py": round(py, 4),
        "rx": int(rx),
        "ry": int(ry),
    }


def paste_text(text: str, *, require_wid: str | None = None) -> None:
    """通过剪贴板粘贴中文。若指定 require_wid，粘贴前必须前台为该窗口。"""
    if not text:
        return

    ok = False
    if shutil.which("xclip"):
        try:
            p = subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text.encode("utf-8"),
                check=True,
            )
            ok = p.returncode == 0
        except Exception as exc:
            logger.warning("xclip 失败: %s", exc)
    if not ok:
        try:
            import pyperclip

            pyperclip.copy(text)
            ok = True
        except Exception as exc:
            raise RuntimeError(
                "无法写入剪贴板。请安装: sudo apt-get install -y xclip"
                " 或 pip install pyperclip"
            ) from exc

    require_xdotool()
    if require_wid:
        ensure_window_focused(require_wid)
    # 一次发出全选+粘贴，少一次进程启动和中间 sleep
    _run(["xdotool", "key", "ctrl+a", "ctrl+v"], check=False)
    logger.info("已粘贴文本: %s", text[:40])
