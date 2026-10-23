"""凭证加载与校验。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv


@dataclass
class AuthConfig:
    cookie: str = ""
    authorization: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)

    def get_headers(self) -> dict[str, str]:
        headers: dict[str, str] = dict(self.extra_headers)
        if self.authorization:
            headers["Authorization"] = self.authorization
        return headers

    def get_cookies(self) -> dict[str, str]:
        if not self.cookie:
            return {}
        cookies: dict[str, str] = {}
        for part in self.cookie.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            key, _, value = part.partition("=")
            cookies[key.strip()] = value.strip()
        return cookies

    def is_configured(self) -> bool:
        return bool(self.cookie or self.authorization)


class AuthError(Exception):
    """鉴权相关错误。"""


def load_auth(env_path: str | None = None) -> AuthConfig:
    load_dotenv(env_path)
    extra_raw = os.getenv("EXTRA_HEADERS", "")
    extra_headers: dict[str, str] = {}
    if extra_raw:
        try:
            extra_headers = json.loads(extra_raw)
        except json.JSONDecodeError as exc:
            raise AuthError("EXTRA_HEADERS 必须是合法 JSON") from exc

    return AuthConfig(
        cookie=os.getenv("COOKIE", "").strip(),
        authorization=os.getenv("AUTHORIZATION", "").strip(),
        extra_headers=extra_headers,
    )


def validate_auth(auth: AuthConfig, *, dry_run: bool = False) -> None:
    if dry_run:
        return
    if not auth.is_configured():
        raise AuthError(
            "未配置 COOKIE 或 AUTHORIZATION，请从抓包复制到 .env 文件"
        )
