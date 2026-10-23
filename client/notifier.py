"""飞书 Webhook 通知。"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)


def get_webhook_url() -> str:
    return os.getenv("FEISHU_WEBHOOK_URL", "").strip()


def send_feishu_text(title: str, content: str) -> bool:
    webhook_url = get_webhook_url()
    if not webhook_url:
        logger.info("未配置 FEISHU_WEBHOOK_URL，跳过通知: %s - %s", title, content)
        return False

    payload: dict[str, Any] = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "plain_text", "content": content},
                }
            ],
        },
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") not in (0, None) and data.get("StatusCode") not in (0, None):
            logger.warning("飞书通知返回异常: %s", data)
            return False
        return True
    except requests.RequestException as exc:
        logger.error("飞书通知发送失败: %s", exc)
        return False


def notify_success(court: str, time_slot: str, date: str, detail: str = "") -> None:
    content = f"日期: {date}\n场地: {court}\n时段: {time_slot}"
    if detail:
        content += f"\n详情: {detail}"
    send_feishu_text("抢场成功", content)


def notify_failure(reason: str) -> None:
    send_feishu_text("抢场失败", reason)


def notify_token_expired() -> None:
    send_feishu_text(
        "Token 已过期",
        "请重新抓包更新 .env 中的 COOKIE 或 AUTHORIZATION",
    )
