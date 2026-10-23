#!/usr/bin/env python3
"""列出窗口并标注：订场候选 / 主窗禁止 / 文档排除。

用法:
  python3 scripts/list_feishu_windows.py

真正会被点击的通常是标题匹配 config.yaml playwright.desktop.target_window_titles、
约 350x700 的手机形弹窗。
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from client.window_utils import (  # noqa: E402
    DEFAULT_EXCLUDE_TITLES,
    DEFAULT_TARGET_TITLES,
    LIST_EXTRA_KEYWORDS,
    MAX_BOOKING_AREA,
    SEARCH_KEYWORDS,
    get_active_window_id,
    get_window_geometry,
    get_window_name,
    is_booking_title,
    is_excluded_title,
    is_main_feishu_title,
    is_phone_shaped,
    score_booking_window,
    search_window_ids,
)


def classify(name: str, geom=None) -> str:
    if is_excluded_title(name):
        return "排除"
    if is_main_feishu_title(name):
        return "主窗禁止"
    if is_booking_title(name):
        if geom is not None and geom.area > MAX_BOOKING_AREA:
            return "过大跳过"
        return "订场候选"
    return "其他"


def main() -> int:
    # 优先读 config.yaml 里的窗口标题配置
    config_path = PROJECT_DIR / "config.yaml"
    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        cfg = {}
    desktop = ((cfg.get("playwright") or {}).get("desktop") or {})
    search_keywords = list(desktop.get("search_keywords") or SEARCH_KEYWORDS)
    target_titles = list(desktop.get("target_window_titles") or DEFAULT_TARGET_TITLES)
    exclude_titles = list(desktop.get("exclude_window_titles") or DEFAULT_EXCLUDE_TITLES)

    print("=" * 60)
    print("  窗口列表（只有「订场候选」且 score>0 会被点击）")
    print("=" * 60)
    print(f"订场搜索: {', '.join(search_keywords)}")
    print(f"目标标题(严格匹配): {', '.join(target_titles)}")
    print(f"排除: {', '.join(exclude_titles)}")
    print()

    all_keywords = list(dict.fromkeys(search_keywords + LIST_EXTRA_KEYWORDS))
    all_wids = search_window_ids(all_keywords)
    if not all_wids:
        print("未找到相关窗口。请先打开订场小程序弹窗。")
        return 1

    active = get_active_window_id()
    if active:
        print(f"当前前台 wid={active} name={get_window_name(active)!r}")
    print()
    print(f"{'wid':>12}  {'size':>12}  {'score':>5}  {'分类':<8}  标题")
    print("-" * 76)

    best_booking = None
    for wid in all_wids:
        try:
            geom = get_window_geometry(wid)
        except Exception:
            continue
        name = geom.name or get_window_name(wid)
        kind = classify(name, geom)
        score = score_booking_window(geom) if kind == "订场候选" else -1
        phone = " 手机形" if is_phone_shaped(geom) and kind == "订场候选" else ""
        marker = " ← 前台" if wid == active else ""
        print(
            f"{wid:>12}  {geom.w:4d}x{geom.h:<5d}  {score:5d}  {kind:<8}  "
            f"{name!r}{phone}{marker}"
        )
        if kind == "订场候选" and score > 0:
            if best_booking is None or score > best_booking[0]:
                best_booking = (score, geom, name)

    print()
    print("说明:")
    print("  - 真正目标是标题匹配 target_window_titles、约 350x700、带「手机形」标注的窗口")
    print("  - UI 里的大标题 ≠ 系统窗口名；以本列表标题为准")
    print("  - Cursor / Chrome / 企业工作台不会被点击")
    print("  - 请对着订场弹窗重录坐标:")
    print("      python3 scripts/record_desktop_clicks.py --apply")
    print("  - 测试: python3 main.py --mode playwright --now")
    if best_booking:
        _, g, n = best_booking
        print()
        print(f"推荐锁定: wid={g.wid} name={n!r} {g.w}x{g.h} score={best_booking[0]}")
    active_name = get_window_name(active) if active else ""
    if active and classify(active_name) != "订场候选":
        print()
        print("提示: 当前前台不是订场候选；脚本会自动 activate 订场弹窗。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
