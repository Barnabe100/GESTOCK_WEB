from fastapi import APIRouter, Request, Response, status

from app.core.config import Settings
from app.platform.context import (
    CurrentUser,
    DbSession,
    MetaDep,
    NowDep,
    SettingsDep,
)
from app.platform.identity.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    MembershipSummary,
    MeResponse,
    RefreshRequest,
    SessionResponse,
    UserOut,
)
from app.platform.identity.service import AuthService, IssuedSession, MembershipInfo

router = APIRouter(tags=["auth"])


def _summaries(memberships: list[MembershipInfo]) -> list[MembershipSummary]:
    return [
        MembershipSummary(
            tenant_id=info.tenant.id,
            tenant_name=info.tenant.name,
            tenant_slug=info.tenant.slug,
            is_owner=info.membership.is_owner,
        )
        for info in memberships
    ]


def _cookie_path(settings: Settings) -> str:
    return f"{settings.api_v1_prefix}/auth"


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        settings.refresh_cookie_name,
        token,
        max_age=settings.refresh_token_ttl_days * 86400,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="strict",
        path=_cookie_path(settings),
    )


def _session_response(
    issued: IssuedSession, response: Response, settings: Settings
) -> SessionResponse:
    if issued.refresh_token is not None:
        _set_refresh_cookie(response, issued.refresh_token, settings)
    response.headers["Cache-Control"] = "no-store"
    return SessionResponse(
        access_token=issued.access_token,
        expires_in=settings.access_token_ttl_seconds,
        tenant_id=issued.tenant_id,
        user=UserOut.model_validate(issued.user),
        memberships=_summaries(issued.memberships),
    )


@router.post("/auth/login", response_model=SessionResponse)
def login(
    body: LoginRequest,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    now: NowDep,
    meta: MetaDep,
) -> SessionResponse:
    issued = AuthService(db, settings, now).login(body.email, body.password, body.tenant_id, meta)
    db.commit()
    return _session_response(issued, response, settings)


@router.post("/auth/refresh", response_model=SessionResponse)
def refresh(
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    now: NowDep,
    meta: MetaDep,
    request: Request,
    body: RefreshRequest | None = None,
) -> SessionResponse:
    issued = AuthService(db, settings, now).refresh(
        request.cookies.get(settings.refresh_cookie_name), body.tenant_id if body else None, meta
    )
    db.commit()
    return _session_response(issued, response, settings)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    now: NowDep,
    meta: MetaDep,
    request: Request,
) -> Response:
    AuthService(db, settings, now).logout(request.cookies.get(settings.refresh_cookie_name), meta)
    db.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    response.delete_cookie(
        settings.refresh_cookie_name,
        path=_cookie_path(settings),
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return response


@router.get("/me", response_model=MeResponse)
def me(auth: CurrentUser, db: DbSession, settings: SettingsDep, now: NowDep) -> MeResponse:
    memberships = AuthService(db, settings, now).memberships_of(auth.user)
    return MeResponse(
        user=UserOut.model_validate(auth.user),
        tenant_id=auth.claims.tenant_id,
        memberships=_summaries(memberships),
    )


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: ChangePasswordRequest,
    auth: CurrentUser,
    db: DbSession,
    settings: SettingsDep,
    now: NowDep,
    meta: MetaDep,
) -> Response:
    AuthService(db, settings, now).change_password(
        auth.user, auth.claims.session_id, body.current_password, body.new_password, meta
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
