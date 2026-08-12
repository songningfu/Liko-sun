from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from .config import Settings, get_settings
from .database import Database, LoginSession, User, get_database, utc_now
from .liko import create_liko_colors, validate_liko_colors


SESSION_COOKIE = "liko_session"
GUEST_COOKIE = "liko_guest"
USERNAME_PATTERN = re.compile(r"^[a-z0-9_]{4,20}$")
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("not-a-real-user-password")


class UsernameTakenError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Identity derived only from a server-side session cookie."""

    user_id: str | None = None
    authenticated: bool = False
    memory_consent: bool = False
    username: str | None = None
    liko_number: int | None = None
    shell_color: str | None = None
    skin_color: str | None = None
    color_choice_available: bool = False
    created_at: object | None = None
    session_token: str | None = None

    @property
    def can_use_long_term_memory(self) -> bool:
        return bool(self.authenticated and self.user_id and self.memory_consent)


def normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_username(username: str) -> str:
    normalized = normalize_username(username)
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise ValueError("账号只能包含小写字母、数字和下划线，长度为 4–20 位")
    return normalized


def validate_password(password: str) -> str:
    if len(password) < 8 or len(password) > 128:
        raise ValueError("密码长度需为 8–128 位")
    return password


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def private_hash(value: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_recovery_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(20))
    return "-".join(raw[index : index + 5] for index in range(0, 20, 5))


def normalize_recovery_code(code: str) -> str:
    return code.replace("-", "").replace(" ", "").upper()


def create_guest_token(secret: str) -> str:
    value = secrets.token_urlsafe(18)
    signature = private_hash(f"guest:{value}", secret)
    return f"{value}.{signature}"


def valid_guest_token(token: str | None, secret: str) -> str | None:
    if not token or "." not in token:
        return None
    value, signature = token.rsplit(".", 1)
    if not value or not hmac.compare_digest(signature, private_hash(f"guest:{value}", secret)):
        return None
    return value


def request_ip(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    if host in {"127.0.0.1", "::1"}:
        forwarded = request.headers.get("x-real-ip", "").strip()
        if forwarded:
            return forwarded
    return host


class AuthService:
    def __init__(self, database: Database, config: Settings):
        if not config.session_secret:
            raise RuntimeError("SESSION_SECRET is required")
        self.database = database
        self.config = config
        self.secret = config.session_secret

    def register(self, username: str, password: str) -> tuple[User, str]:
        username = validate_username(username)
        password = validate_password(password)
        shell, skin = create_liko_colors()
        recovery_code = generate_recovery_code()
        user = User(
            id=str(uuid4()),
            username=username,
            password_hash=PASSWORD_HASHER.hash(password),
            recovery_code_hash=private_hash(normalize_recovery_code(recovery_code), self.secret),
            shell_color=shell,
            skin_color=skin,
            memory_consent=True,
        )
        try:
            with self.database.session() as db, db.begin():
                db.add(user)
        except IntegrityError as error:
            raise UsernameTakenError("这个账号已被使用") from error
        return user, recovery_code

    def authenticate(self, username: str, password: str) -> User | None:
        username = normalize_username(username)
        with self.database.session() as db:
            user = db.scalar(select(User).where(User.username == username))
            if not user:
                try:
                    PASSWORD_HASHER.verify(DUMMY_PASSWORD_HASH, password)
                except (VerifyMismatchError, VerificationError, InvalidHashError):
                    pass
                return None
            try:
                PASSWORD_HASHER.verify(user.password_hash, password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                return None
            if PASSWORD_HASHER.check_needs_rehash(user.password_hash):
                user.password_hash = PASSWORD_HASHER.hash(password)
                db.commit()
            db.expunge(user)
            return user

    def create_session(self, user_id: str) -> tuple[str, object]:
        raw_token = secrets.token_urlsafe(32)
        expires_at = utc_now() + timedelta(days=self.config.session_days)
        with self.database.session() as db, db.begin():
            db.add(
                LoginSession(
                    token_hash=token_hash(raw_token),
                    user_id=user_id,
                    expires_at=expires_at,
                )
            )
        return raw_token, expires_at

    def resolve_session(self, raw_token: str) -> CurrentUser:
        now = utc_now()
        with self.database.session() as db, db.begin():
            row = db.execute(
                select(LoginSession, User)
                .join(User, User.id == LoginSession.user_id)
                .where(LoginSession.token_hash == token_hash(raw_token))
            ).first()
            if not row:
                return CurrentUser()
            session, user = row
            if session.expires_at <= now:
                db.delete(session)
                return CurrentUser()
            current = CurrentUser(
                user_id=user.id,
                authenticated=True,
                memory_consent=user.memory_consent,
                username=user.username,
                liko_number=user.liko_number,
                shell_color=user.shell_color,
                skin_color=user.skin_color,
                color_choice_available=user.color_choice_available,
                created_at=user.created_at,
                session_token=raw_token,
            )
            return current

    def logout(self, raw_token: str | None) -> None:
        if not raw_token:
            return
        with self.database.session() as db, db.begin():
            db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(raw_token)))

    def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        validate_password(new_password)
        with self.database.session() as db, db.begin():
            user = db.scalar(select(User).where(User.id == user_id))
            if not user:
                raise ValueError("账号不存在")
            try:
                PASSWORD_HASHER.verify(user.password_hash, current_password)
            except (VerifyMismatchError, VerificationError, InvalidHashError) as error:
                raise ValueError("当前密码不正确") from error
            user.password_hash = PASSWORD_HASHER.hash(new_password)
            db.execute(delete(LoginSession).where(LoginSession.user_id == user_id))

    def recover(self, username: str, recovery_code: str, new_password: str) -> tuple[User, str]:
        username = normalize_username(username)
        validate_password(new_password)
        supplied_hash = private_hash(normalize_recovery_code(recovery_code), self.secret)
        replacement_code = generate_recovery_code()
        with self.database.session() as db, db.begin():
            user = db.scalar(select(User).where(User.username == username))
            if not user or not user.recovery_code_hash or not hmac.compare_digest(user.recovery_code_hash, supplied_hash):
                raise ValueError("账号或恢复码不正确")
            user.password_hash = PASSWORD_HASHER.hash(new_password)
            user.recovery_code_hash = private_hash(normalize_recovery_code(replacement_code), self.secret)
            db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
            db.flush()
            db.expunge(user)
            return user, replacement_code

    def set_memory_consent(self, user_id: str, enabled: bool) -> User:
        with self.database.session() as db, db.begin():
            user = db.scalar(select(User).where(User.id == user_id))
            if not user:
                raise ValueError("账号不存在")
            user.memory_consent = enabled
            db.flush()
            db.expunge(user)
            return user

    def choose_liko_colors(self, user_id: str, shell: str, skin: str) -> User:
        shell, skin = validate_liko_colors(shell, skin)
        with self.database.session() as db, db.begin():
            user = db.scalar(select(User).where(User.id == user_id).with_for_update())
            if not user:
                raise ValueError("账号不存在")
            if not user.color_choice_available:
                raise PermissionError("首次选色机会已经使用；以后可通过换色道具再次修改")
            user.shell_color = shell
            user.skin_color = skin
            user.color_choice_available = False
            db.flush()
            db.expunge(user)
            return user


def get_auth_service(
    config: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database | None, Depends(get_database)],
) -> AuthService | None:
    if not database or not config.auth_configured:
        return None
    return AuthService(database, config)


def get_current_user(
    request: Request,
    service: Annotated[AuthService | None, Depends(get_auth_service)],
) -> CurrentUser:
    if not service:
        return CurrentUser()
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token:
        return CurrentUser()
    return service.resolve_session(raw_token)
