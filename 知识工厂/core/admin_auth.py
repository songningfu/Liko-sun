from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass


PBKDF2_ITERATIONS = 310_000
SESSION_SECONDS = 8 * 60 * 60


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if len(password) < 12:
        raise ValueError("管理员密码至少需要 12 个字符")
    actual_salt = salt or secrets.token_bytes(18)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        actual_salt,
        PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64encode(actual_salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _b64decode(salt),
            int(iterations),
        )
        return hmac.compare_digest(actual, _b64decode(expected))
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True, slots=True)
class AdminConfig:
    username: str
    password_hash: str
    session_secret: str
    cookie_secure: bool
    cookie_path: str
    liko_api_base_url: str
    liko_admin_token: str
    fastgpt_admin_url: str

    @classmethod
    def from_env(cls) -> "AdminConfig":
        return cls(
            username=os.getenv("FACTORY_ADMIN_USERNAME", "admin").strip(),
            password_hash=os.getenv("FACTORY_ADMIN_PASSWORD_HASH", "").strip(),
            session_secret=os.getenv("FACTORY_SESSION_SECRET", "").strip(),
            cookie_secure=os.getenv("FACTORY_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"},
            cookie_path=os.getenv("FACTORY_COOKIE_PATH", "/admin").strip() or "/admin",
            liko_api_base_url=os.getenv("LIKO_API_BASE_URL", "http://127.0.0.1:8001").rstrip("/"),
            liko_admin_token=os.getenv("LIKO_ADMIN_API_TOKEN", "").strip(),
            fastgpt_admin_url=os.getenv("FASTGPT_ADMIN_URL", "").strip(),
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.username
            and self.password_hash
            and len(self.session_secret) >= 32
        )


class AdminSessionManager:
    cookie_name = "liko_admin_session"

    def __init__(self, config: AdminConfig):
        self.config = config

    def authenticate(self, username: str, password: str) -> bool:
        return bool(
            self.config.configured
            and secrets.compare_digest(username, self.config.username)
            and verify_password(password, self.config.password_hash)
        )

    def issue(self) -> tuple[str, str]:
        payload = {
            "sub": self.config.username,
            "exp": int(time.time()) + SESSION_SECONDS,
            "csrf": secrets.token_urlsafe(24),
        }
        encoded = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = hmac.new(
            self.config.session_secret.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{encoded}.{_b64encode(signature)}", payload["csrf"]

    def read(self, token: str | None) -> dict | None:
        if not token or not self.config.configured:
            return None
        try:
            encoded, supplied_signature = token.split(".", 1)
            expected_signature = hmac.new(
                self.config.session_secret.encode("utf-8"),
                encoded.encode("ascii"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(expected_signature, _b64decode(supplied_signature)):
                return None
            payload = json.loads(_b64decode(encoded))
            if payload.get("sub") != self.config.username or int(payload.get("exp", 0)) < int(time.time()):
                return None
            if not isinstance(payload.get("csrf"), str):
                return None
            return payload
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            return None


if __name__ == "__main__":
    import getpass

    first = getpass.getpass("管理员密码：")
    second = getpass.getpass("再次输入：")
    if first != second:
        raise SystemExit("两次密码不一致")
    print(hash_password(first))
