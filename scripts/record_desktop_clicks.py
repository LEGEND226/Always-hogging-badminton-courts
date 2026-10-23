#!/usr/bin/env python3
"""倒计时标定飞书窗口「相对坐标」（抗窗口移动）。

推荐用法（默认不自动打开 applink，避免卡在浏览器过渡页）:
  1. 打开桌面飞书订场小程序弹窗（约 350x700，标题与
     config.yaml playwright.desktop.target_window_titles 一致）
     停在能点预约入口的页面
  2. sudo apt-get install -y xdotool xclip python3-tk python3-dev
  3. python3 scripts/record_desktop_clicks.py --apply --no-applink
  4. 按提示把鼠标移到目标上停住，不要点回终端
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from client.window_utils import (  # noqa: E402
    DEFAULT_EXCLUDE_TITLES,
    DEFAULT_TARGET_TITLES,
    MIN_WINDOW_H,
    MIN_WINDOW_W,
    absolute_to_relative,
    ensure_feishu_window,
    find_booking_window,
    find_window_under_cursor,
    get_window_geometry,
    get_window_name,
    is_booking_title,
    is_excluded_title,
    is_large_enough,
    is_main_feishu_title,
)

def _load_desktop_titles(config_path: Path) -> tuple[list[str], list[str], list[str] | None]:
    """从 config.yaml 读窗口标题配置，缺省回退到通用默认值。"""
    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        cfg = {}
    desktop = ((cfg.get("playwright") or {}).get("desktop") or {})
    targets = list(desktop.get("target_window_titles") or DEFAULT_TARGET_TITLES)
    excludes = list(desktop.get("exclude_window_titles") or DEFAULT_EXCLUDE_TITLES)
    search = list(desktop.get("search_keywords") or []) or None
    return targets, excludes, search

DEFAULT_CONFIG = PROJECT_DIR / "config.yaml"

# 从预约入口开始；入口由运行时 client 打开 applink 完成
DEFAULT_STEPS = [
    "预约入口",
    "场馆A-1号场地",
    "选择预约日期(放场日那天)",
    "下滑露出时段列表",
    "时段18:00",
    "时段19:00",
    "时段20:00",
    "时段21:00",
    "下一步",
    "预约原因输入框",
    "提交",
    "确定",
]

BROWSER_PRE_STEP = "立即打开(浏览器过渡页)"
REASON_STEP_LABEL = "预约原因输入框"
SCROLL_STEP_LABEL = "下滑露出时段列表"
DEFAULT_REASON = "锻炼身体"
DEFAULT_SCROLL_CLICKS = -6


def countdown(seconds: int, message: str) -> None:
    for remaining in range(seconds, 0, -1):
        print(f"\r{message} — {remaining} 秒...   ", end="", flush=True)
        time.sleep(1)
    print()


def beep() -> None:
    try:
        print("\a", end="", flush=True)
    except Exception:
        pass


def is_scroll_step(label: str, step: dict | None = None) -> bool:
    if step and str(step.get("action") or "").lower() == "scroll":
        return True
    return SCROLL_STEP_LABEL in label or label.startswith("下滑")


def format_clicks_yaml(steps: list[dict], key: str = "clicks") -> str:
    lines = [f"    {key}:"]
    for s in steps:
        if is_scroll_step(s.get("label", ""), s):
            clicks = int(s.get("scroll_clicks", DEFAULT_SCROLL_CLICKS))
            lines.append(
                f'      - {{action: scroll, scroll_clicks: {clicks}, '
                f'label: "{s["label"]}"}}'
            )
            continue
        paste = s.get("paste")
        extra = f', paste: "{paste}"' if paste else ""
        lines.append(
            f'      - {{px: {s["px"]}, py: {s["py"]}, rx: {s["rx"]}, ry: {s["ry"]}, '
            f'label: "{s["label"]}"{extra}}}'
        )
    return "\n".join(lines)


def coords_look_bad(steps: list[dict]) -> list[str]:
    """返回坏坐标步骤的警告信息。"""
    warnings: list[str] = []
    for s in steps:
        if is_scroll_step(s.get("label", ""), s):
            continue
        px, py = float(s["px"]), float(s["py"])
        if px < 0 or py < 0 or px > 1 or py > 1:
            warnings.append(
                f"「{s['label']}」越界 px={px} py={py}（鼠标不在目标窗口内）"
            )
        elif px >= 0.95 and py >= 0.95:
            warnings.append(
                f"「{s['label']}」px/py 均 ≥0.95（疑似绑定了错误小窗口，请重录）"
            )
    return warnings


def apply_to_config(
    config_path: Path,
    steps: list[dict],
    reason: str,
    *,
    pre_clicks: list[dict] | None = None,
    bound_window_name: str = "",
) -> None:
    with config_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    pw = data.setdefault("playwright", {})
    desktop = pw.setdefault("desktop", {})
    desktop["clicks"] = []
    for s in steps:
        if is_scroll_step(s.get("label", ""), s):
            desktop["clicks"].append(
                {
                    "action": "scroll",
                    "scroll_clicks": int(
                        s.get("scroll_clicks", DEFAULT_SCROLL_CLICKS)
                    ),
                    "label": s["label"],
                }
            )
            continue
        item = {
            "px": s["px"],
            "py": s["py"],
            "rx": s["rx"],
            "ry": s["ry"],
            "label": s["label"],
        }
        if s.get("paste"):
            item["paste"] = s["paste"]
        desktop["clicks"].append(item)

    if pre_clicks is not None:
        desktop["pre_clicks"] = [
            {
                "px": s["px"],
                "py": s["py"],
                "rx": s["rx"],
                "ry": s["ry"],
                "label": s["label"],
            }
            for s in pre_clicks
        ]
    else:
        desktop.setdefault("pre_clicks", [])

    desktop["pause_before_clicks"] = 0.05
    desktop["pause_after_nav"] = 0.2
    desktop["skip_last_click_on_dry_run"] = True
    desktop["skip_last_n_on_dry_run"] = 1
    desktop["auto_launch_feishu"] = True
    desktop["open_applink"] = True
    desktop["open_applink_via"] = desktop.get("open_applink_via", "client")
    desktop["reason_text"] = reason
    desktop["type_text"] = []
    desktop["lock_window"] = True
    desktop["foreground_wait_seconds"] = 15
    desktop["applink_wait_seconds"] = 25
    desktop["scroll_clicks"] = DEFAULT_SCROLL_CLICKS
    desktop["exclude_window_titles"] = list(DEFAULT_EXCLUDE_TITLES)

    # 严格目标：优先绑定的弹窗标题，再回退 config/通用默认（不含工作台大窗）
    targets = list(DEFAULT_TARGET_TITLES)
    if bound_window_name and is_booking_title(bound_window_name):
        # 完整短标题放最前
        if bound_window_name not in targets:
            targets.insert(0, bound_window_name)
        for kw in tuple(DEFAULT_TARGET_TITLES):
            if kw == bound_window_name and kw not in targets:
                targets.insert(0, kw)
    desktop["target_window_titles"] = targets

    booking = data.setdefault("booking", {})
    booking.setdefault("days_ahead", 7)
    booking.setdefault("mode", "playwright")

    if not data.get("preferences"):
        data["preferences"] = [
            {"court": "场馆A-1号场地", "time": "18:00-21:00"}
        ]

    with config_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            data, f, allow_unicode=True, sort_keys=False, default_flow_style=False
        )

    print(
        f"\n已写入: {config_path} → playwright.desktop.clicks "
        f"({len(steps)} 步, 相对坐标)"
    )
    print(f"  target_window_titles: {targets}")
    print(f"  lock_window: true（运行时锁定弹窗，不会点到文档）")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="标定飞书窗口相对坐标（窗口移动后仍可用）"
    )
    parser.add_argument("--delay", type=int, default=5, help="每步倒计时秒数")
    parser.add_argument("--prepare", type=int, default=10, help="开始前准备秒数")
    parser.add_argument(
        "--steps",
        type=str,
        default="",
        help="自定义步骤，英文逗号分隔",
    )
    parser.add_argument("--apply", action="store_true", help="写入 config.yaml")
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--reason",
        default=DEFAULT_REASON,
        help="预约原因（写到原因输入框步骤的 paste）",
    )
    parser.add_argument(
        "--applink",
        default="",
        help="仅在未加 --no-applink 时打开（默认读 config）",
    )
    parser.add_argument(
        "--no-applink",
        action="store_true",
        help="不自动打开 applink（推荐；默认行为已是不打开）",
    )
    parser.add_argument(
        "--with-applink",
        action="store_true",
        help="录制前用飞书客户端打开 applink",
    )
    parser.add_argument(
        "--record-pre-open",
        action="store_true",
        help="额外录制浏览器过渡页「立即打开」（open_applink_via=browser 时用）",
    )
    parser.add_argument(
        "prepare_positional",
        nargs="?",
        type=int,
        default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    if args.prepare_positional is not None:
        args.prepare = args.prepare_positional

    # 默认不打开 applink，避免浏览器过渡页干扰标定
    use_applink = bool(args.with_applink)

    try:
        import pyautogui
    except ImportError:
        print("请先安装: pip install pyautogui", file=sys.stderr)
        return 1

    applink = args.applink
    target_titles, exclude_titles, search_keywords = _load_desktop_titles(
        args.config
    )
    if not applink and args.config.is_file():
        try:
            cfg = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
            applink = (cfg.get("playwright") or {}).get("start_url") or ""
        except Exception:
            pass

    step_labels = (
        [s.strip() for s in args.steps.split(",") if s.strip()]
        if args.steps
        else list(DEFAULT_STEPS)
    )
    if args.record_pre_open:
        step_labels = [BROWSER_PRE_STEP] + step_labels

    print("=" * 60)
    print("  桌面飞书订场 — 窗口相对坐标标定")
    print("=" * 60)
    print()
    print("重要：")
    print("  - 请先打开订场手机形弹窗（约 350x700），关掉文档/Cursor 遮挡")
    print("  - 可先运行: python3 scripts/list_feishu_windows.py 确认弹窗标题")
    print("  - 录制时不要点回终端；坐标相对「订场弹窗」而不是文档")
    print("  - 窗口尺寸需 ≥%dx%d" % (MIN_WINDOW_W, MIN_WINDOW_H))
    print()
    print(f"将依次记录 {len(step_labels)} 步:")
    for i, label in enumerate(step_labels, 1):
        print(f"  {i}. {label}")
    print()

    try:
        geom = ensure_feishu_window(
            target_titles=target_titles,
            exclude_titles=exclude_titles,
            search_keywords=search_keywords,
            auto_launch=True,
            open_applink_url=applink if use_applink else "",
            open_applink_via="client",
            wait_seconds=20.0,
        )
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        print(
            "提示: python3 scripts/list_feishu_windows.py\n"
            "然后: python3 scripts/record_desktop_clicks.py --apply --no-applink",
            file=sys.stderr,
        )
        return 1

    name = geom.name or get_window_name(geom.wid)
    if is_excluded_title(name) or is_main_feishu_title(name) or not is_booking_title(name):
        print(
            f"错误: 当前窗口不是订场弹窗 {name!r}。\n"
            "请把订场手机形弹窗点到最上层（不要停在消息页/Cursor），再重试。\n"
            "可先运行: python3 scripts/list_feishu_windows.py\n"
            "提示: 若 config.yaml 的 target_window_titles 与实际窗口名不一致，会一直匹配失败。",
            file=sys.stderr,
        )
        return 1

    print(
        f"已定位订场窗口: name={name!r} ({geom.x},{geom.y}) "
        f"{geom.w}x{geom.h} wid={geom.wid}"
    )
    if geom.w > 520 or geom.h < geom.w:
        print(
            "警告: 当前窗口不像手机形小程序（期望约 350x700）。"
            "请确认绑定的是订场弹窗而不是工作台大窗。",
            file=sys.stderr,
        )
    if not is_large_enough(geom):
        print(
            f"错误: 窗口过小 ({geom.w}x{geom.h})，拒绝录制。",
            file=sys.stderr,
        )
        return 1

    locked_wid = geom.wid
    bound_window_name = name
    print(f"录制期间将锁定 wid={locked_wid}（不会切到文档）")
    print()
    countdown(args.prepare, "请确认订场弹窗在最上层")

    latest = find_booking_window(
        target_titles=target_titles,
        exclude_titles=exclude_titles,
        search_keywords=search_keywords,
    )
    if latest is not None:
        geom = get_window_geometry(latest.wid)
        locked_wid = geom.wid
        bound_window_name = geom.name or get_window_name(geom.wid)
        print(
            f"刷新锁定窗口: name={bound_window_name!r} "
            f"({geom.x},{geom.y}) {geom.w}x{geom.h} wid={locked_wid}"
        )

    pyautogui.FAILSAFE = True
    recorded: list[dict] = []
    pre_recorded: list[dict] = []

    for i, label in enumerate(step_labels, 1):
        print()
        print(f"[{i}/{len(step_labels)}] 下一步: {label}")

        # 下滑步：不录坐标，写入 action:scroll（录时段前必须先有这一步）
        if is_scroll_step(label):
            print(
                f"    → 无需点按钮；确认时段列表区域在弹窗内，"
                f"将写入 scroll_clicks={DEFAULT_SCROLL_CLICKS}"
            )
            print(
                "    提示: 正式运行时会在弹窗中心自动下滑，再点 18:00–21:00"
            )
            countdown(max(2, args.delay // 2), f"    确认「{label}」")
            item = {
                "label": label,
                "action": "scroll",
                "scroll_clicks": DEFAULT_SCROLL_CLICKS,
            }
            recorded.append(item)
            beep()
            print(
                f"    ✓ {label} -> action=scroll "
                f"scroll_clicks={DEFAULT_SCROLL_CLICKS}"
            )
            continue

        print("    → 鼠标移到该按钮/输入框上，保持不动")
        countdown(args.delay, f"    正在记录「{label}」")

        pos = pyautogui.position()
        # 始终相对锁定的订场弹窗；若用户把鼠标移到文档上则告警
        try:
            geom = get_window_geometry(locked_wid)
        except Exception:
            under = find_window_under_cursor(
                cursor_x=int(pos.x),
                cursor_y=int(pos.y),
                exclude_titles=list(DEFAULT_EXCLUDE_TITLES),
            )
            if under is None:
                print("错误: 锁定窗口已消失", file=sys.stderr)
                return 1
            geom = under
            locked_wid = geom.wid

        wname = geom.name or get_window_name(geom.wid)
        if (
            is_excluded_title(wname)
            or is_main_feishu_title(wname)
            or not is_booking_title(wname)
        ):
            print(
                f"    错误: 绑定窗口不是订场弹窗 {wname!r}。"
                "请只在订场手机形弹窗上录制，不要点到消息页/工作台/Cursor。",
                file=sys.stderr,
            )
            return 1

        print(
            f"    绑定窗口: {wname!r} wid={geom.wid} "
            f"{geom.w}x{geom.h} @({geom.x},{geom.y})"
        )
        if not is_large_enough(geom):
            print(
                f"    错误: 绑定窗口过小 ({geom.w}x{geom.h})",
                file=sys.stderr,
            )
            return 1

        rel = absolute_to_relative(int(pos.x), int(pos.y), geom)
        item = {
            "label": label,
            "px": rel["px"],
            "py": rel["py"],
            "rx": rel["rx"],
            "ry": rel["ry"],
        }
        if REASON_STEP_LABEL in label or "原因" in label:
            item["paste"] = args.reason

        px, py = float(rel["px"]), float(rel["py"])
        if not (0 <= px <= 1 and 0 <= py <= 1):
            print(
                f"    警告: 鼠标不在订场弹窗内 (px={px}, py={py})",
                file=sys.stderr,
            )
        if px >= 0.95 and py >= 0.95:
            print(
                "    警告: px/py 均 ≥0.95，很可能绑错窗口，本步无效",
                file=sys.stderr,
            )

        if label == BROWSER_PRE_STEP:
            pre_recorded.append(item)
        else:
            recorded.append(item)
        beep()
        print(
            f"    ✓ {label} -> 屏幕({pos.x},{pos.y}) "
            f"相对 px={rel['px']} py={rel['py']} rx={rel['rx']} ry={rel['ry']}"
            + (f' paste="{item.get("paste")}"' if item.get("paste") else "")
        )

    all_for_check = pre_recorded + recorded
    bad = coords_look_bad(all_for_check)

    print()
    print("=" * 60)
    print("录制完成（相对坐标）:")
    print()
    if pre_recorded:
        print(format_clicks_yaml(pre_recorded, "pre_clicks"))
        print()
    print(format_clicks_yaml(recorded))
    print()

    if bad:
        print("坐标校验失败，拒绝写入 config：", file=sys.stderr)
        for w in bad:
            print(f"  - {w}", file=sys.stderr)
        print(
            "\n请确认订场约 350x700 弹窗在最上层、鼠标在弹窗内，然后重录。",
            file=sys.stderr,
        )
        return 2

    if not is_booking_title(bound_window_name):
        print(
            f"错误: 绑定窗口标题 {bound_window_name!r} 不是严格订场标题"
            "（需恰好匹配 target_window_titles），拒绝 --apply。",
            file=sys.stderr,
        )
        return 2

    if args.apply:
        apply_to_config(
            args.config,
            recorded,
            args.reason,
            pre_clicks=pre_recorded if pre_recorded else [],
            bound_window_name=bound_window_name,
        )
        if pre_recorded:
            with args.config.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            data.setdefault("playwright", {}).setdefault("desktop", {})[
                "open_applink_via"
            ] = "browser"
            with args.config.open("w", encoding="utf-8") as f:
                yaml.safe_dump(
                    data,
                    f,
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                )
            print("已设置 open_applink_via: browser（因录了「立即打开」）")
    else:
        print("提示: 加 --apply 可自动写入 config.yaml")

    print()
    print("下一步验证:")
    print("  1. 关掉飞书文档，只留订场弹窗在最上层")
    print("  2. python3 main.py --mode playwright --now")
    print("  3. 日志应始终同一 wid，name 不是「飞书文档」")
    print("  4. dry_run 会跳过最后一步「确定」")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
