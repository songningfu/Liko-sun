from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status

from .auth import (
    GUEST_COOKIE,
    CurrentUser,
    create_guest_token,
    get_current_user,
    private_hash,
    request_ip,
    valid_guest_token,
)
from .config import Settings, get_settings
from .database import Database, get_database


CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class QuotaStatus:
    limit: int
    used: int
    remaining: int
    authenticated: bool


def _today() -> str:
    return datetime.now(CHINA_TZ).date().isoformat()


def _set_guest_cookie(response: Response, token: str, config: Settings) -> None:
    response.set_cookie(
        GUEST_COOKIE,
        token,
        max_age=365 * 24 * 60 * 60,
        httponly=True,
        secure=config.secure_cookies,
        samesite="lax",
        path="/",
    )


def quota_subject(
    request: Request,
    response: Response,
    current_user: CurrentUser,
    config: Settings,
) -> tuple[str, bool]:
    if current_user.authenticated and current_user.user_id:
        return f"user:{current_user.user_id}", True
    if not config.session_secret:
        return f"ip:{request_ip(request)}", False
    signed_token = request.cookies.get(GUEST_COOKIE)
    value = valid_guest_token(signed_token, config.session_secret)
    if not value:
        signed_token = create_guest_token(config.session_secret)
        value = valid_guest_token(signed_token, config.session_secret)
        _set_guest_cookie(response, signed_token, config)
    return f"guest:{value}:ip:{request_ip(request)}", False


def read_chat_quota(
    *,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    config: Settings,
    database: Database | None,
) -> QuotaStatus:
    limit = config.user_daily_limit if current_user.authenticated else config.guest_daily_limit
    if not database or not config.session_secret:
        return QuotaStatus(limit=limit, used=0, remaining=limit, authenticated=current_user.authenticated)
    subject, authenticated = quota_subject(request, response, current_user, config)
    subject_hash = private_hash(subject, config.session_secret)
    used = database.counter_value(kind="chat", subject_hash=subject_hash, period_key=_today())
    return QuotaStatus(limit=limit, used=used, remaining=max(0, limit - used), authenticated=authenticated)


def consume_chat_quota(
    request: Request,
    response: Response,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    config: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database | None, Depends(get_database)],
) -> QuotaStatus:
    status_before = read_chat_quota(
        request=request,
        response=response,
        current_user=current_user,
        config=config,
        database=database,
    )
    if not database or not config.session_secret:
        return status_before
    subject, authenticated = quota_subject(request, response, current_user, config)
    consumed, used = database.consume_counter(
        kind="chat",
        subject_hash=private_hash(subject, config.session_secret),
        period_key=_today(),
        limit=status_before.limit,
    )
    if not consumed:
        tomorrow = datetime.now(CHINA_TZ).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        retry_after = max(1, int((tomorrow - datetime.now(CHINA_TZ)).total_seconds()))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="今天的问答次数已经用完，登录后可获得更多次数" if not authenticated else "今天的问答次数已经用完，明天会自动恢复",
            headers={"Retry-After": str(retry_after)},
        )
    return QuotaStatus(
        limit=status_before.limit,
        used=used,
        remaining=max(0, status_before.limit - used),
        authenticated=authenticated,
    )
