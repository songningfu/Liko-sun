from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from .auth import (
    SESSION_COOKIE,
    AuthService,
    CurrentUser,
    UsernameTakenError,
    get_auth_service,
    get_current_user,
    private_hash,
    request_ip,
)
from .config import Settings, get_settings
from .database import Database, get_database, utc_now
from .liko import derive_palette, format_liko_number
from .quota import read_chat_quota
from .schemas import (
    LoginRequest,
    LikoColorChoiceRequest,
    MemoryConsentRequest,
    PasswordChangeRequest,
    PasswordRecoveryRequest,
    RegisterRequest,
)


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _require_service(service: AuthService | None) -> AuthService:
    if not service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="账号服务尚未配置完成",
        )
    return service


def _set_session_cookie(response: Response, token: str, config: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=config.session_days * 24 * 60 * 60,
        httponly=True,
        secure=config.secure_cookies,
        samesite="lax",
        path="/",
    )


def _rate_limit(
    *,
    database: Database,
    config: Settings,
    kind: str,
    subject: str,
    period_key: str,
    limit: int,
) -> None:
    allowed, _ = database.consume_counter(
        kind=kind,
        subject_hash=private_hash(subject, config.session_secret or ""),
        period_key=period_key,
        limit=limit,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="尝试次数过多，请稍后再试",
        )


def companion_payload(current_user: CurrentUser) -> dict | None:
    if not current_user.authenticated or current_user.liko_number is None:
        return None
    created_at = current_user.created_at
    age_days = 1
    if isinstance(created_at, datetime):
        age_days = max(1, (utc_now().date() - created_at.date()).days + 1)
    return {
        "number": current_user.liko_number,
        "displayNumber": format_liko_number(current_user.liko_number),
        "ageDays": age_days,
        "colorChoiceAvailable": current_user.color_choice_available,
        "palette": derive_palette(current_user.shell_color or "#E95F68", current_user.skin_color or "#FFF0C7"),
    }


def auth_payload(
    current_user: CurrentUser,
    *,
    quota: object | None = None,
    recovery_code: str | None = None,
) -> dict:
    result = {
        "authenticated": current_user.authenticated,
        "user": (
            {
                "username": current_user.username,
                "memoryConsent": current_user.memory_consent,
            }
            if current_user.authenticated
            else None
        ),
        "companion": companion_payload(current_user),
    }
    if quota is not None:
        result["quota"] = {
            "limit": quota.limit,
            "used": quota.used,
            "remaining": quota.remaining,
        }
    if recovery_code:
        result["recoveryCode"] = recovery_code
    return result


@router.get("/me")
def me(
    request: Request,
    response: Response,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    config: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database | None, Depends(get_database)],
) -> dict:
    quota = read_chat_quota(
        request=request,
        response=response,
        current_user=current_user,
        config=config,
        database=database,
    )
    return auth_payload(current_user, quota=quota)


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    service_optional: Annotated[AuthService | None, Depends(get_auth_service)],
    config: Annotated[Settings, Depends(get_settings)],
) -> dict:
    service = _require_service(service_optional)
    _rate_limit(
        database=service.database,
        config=config,
        kind="register",
        subject=request_ip(request),
        period_key=datetime.now().date().isoformat(),
        limit=5,
    )
    try:
        user, recovery_code = service.register(payload.username, payload.password)
    except UsernameTakenError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    token, _ = service.create_session(user.id)
    _set_session_cookie(response, token, config)
    current = service.resolve_session(token)
    return auth_payload(current, recovery_code=recovery_code)


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    service_optional: Annotated[AuthService | None, Depends(get_auth_service)],
    config: Annotated[Settings, Depends(get_settings)],
) -> dict:
    service = _require_service(service_optional)
    period = datetime.now().strftime("%Y-%m-%dT%H:%M")[:-1]
    _rate_limit(
        database=service.database,
        config=config,
        kind="login",
        subject=f"{request_ip(request)}:{payload.username.lower()}",
        period_key=period,
        limit=10,
    )
    user = service.authenticate(payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码不正确")
    token, _ = service.create_session(user.id)
    _set_session_cookie(response, token, config)
    return auth_payload(service.resolve_session(token))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    service_optional: Annotated[AuthService | None, Depends(get_auth_service)],
) -> Response:
    if service_optional:
        service_optional.logout(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/password")
def change_password(
    payload: PasswordChangeRequest,
    response: Response,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service_optional: Annotated[AuthService | None, Depends(get_auth_service)],
    config: Annotated[Settings, Depends(get_settings)],
) -> dict:
    service = _require_service(service_optional)
    if not current_user.authenticated or not current_user.user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    try:
        service.change_password(current_user.user_id, payload.current_password, payload.new_password)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    token, _ = service.create_session(current_user.user_id)
    _set_session_cookie(response, token, config)
    return auth_payload(service.resolve_session(token))


@router.post("/recover")
def recover_password(
    payload: PasswordRecoveryRequest,
    request: Request,
    response: Response,
    service_optional: Annotated[AuthService | None, Depends(get_auth_service)],
    config: Annotated[Settings, Depends(get_settings)],
) -> dict:
    service = _require_service(service_optional)
    period = datetime.now().strftime("%Y-%m-%dT%H:%M")[:-1]
    _rate_limit(
        database=service.database,
        config=config,
        kind="recover",
        subject=f"{request_ip(request)}:{payload.username.lower()}",
        period_key=period,
        limit=5,
    )
    try:
        user, replacement_code = service.recover(
            payload.username,
            payload.recovery_code,
            payload.new_password,
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    token, _ = service.create_session(user.id)
    _set_session_cookie(response, token, config)
    return auth_payload(service.resolve_session(token), recovery_code=replacement_code)


@router.patch("/memory-consent")
def update_memory_consent(
    payload: MemoryConsentRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service_optional: Annotated[AuthService | None, Depends(get_auth_service)],
) -> dict:
    service = _require_service(service_optional)
    if not current_user.authenticated or not current_user.user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    service.set_memory_consent(current_user.user_id, payload.enabled)
    refreshed = service.resolve_session(current_user.session_token or "")
    return auth_payload(refreshed)


@router.patch("/liko-colors")
def choose_liko_colors(
    payload: LikoColorChoiceRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service_optional: Annotated[AuthService | None, Depends(get_auth_service)],
) -> dict:
    service = _require_service(service_optional)
    if not current_user.authenticated or not current_user.user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    try:
        service.choose_liko_colors(current_user.user_id, payload.shell, payload.skin)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    refreshed = service.resolve_session(current_user.session_token or "")
    return auth_payload(refreshed)
