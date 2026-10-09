from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response

from app.core.config import get_settings
from app.core.deps import CurrentClaims, PreTenantDb, require_step_up
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.mailer import send_quietly
from app.core.pagination import PaginatedRoute
from app.core.rate_limit import limiter
from app.modules.identity import recovery, repository, service
from app.modules.identity.schemas import (
    AcceptInviteRequest,
    JoinChurchRequest,
    LoginRequest,
    MfaChallengeRequest,
    MfaChallengeResponse,
    MfaSetupResponse,
    MfaVerifyRequest,
    PasswordResetComplete,
    PasswordResetRequest,
    PasswordResetRequested,
    RefreshRequest,
    RegisterRequest,
    SessionResponse,
    StepUpRequest,
    StepUpResponse,
    SwitchTenantRequest,
)

router = APIRouter(route_class=PaginatedRoute, prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _current_user(claims: CurrentClaims, db: PreTenantDb):
    user = await repository.get_user_by_id(db, claims.sub)
    if user is None:
        raise UnauthorizedError("Account no longer exists")
    return user


CurrentUser = Annotated[object, Depends(_current_user)]

BODY_TRANSPORT_HEADER = "x-refresh-token-transport"
CSRF_HEADER = "x-requested-with"


def _wants_body_token(request: Request) -> bool:
    return request.headers.get(BODY_TRANSPORT_HEADER, "").lower() == "body"


def _deliver(request: Request, response: Response, session):
    """Puts the refresh token where it belongs for this client.

    Browsers: an httpOnly, SameSite=Strict cookie scoped to /api/v1/auth — no
    page script can read it, it is never sent cross-site, and it only travels
    to the auth endpoints. Clients that explicitly ask for body transport
    (native apps, the test suite) get it in the JSON instead."""
    if not hasattr(session, "refresh_token"):
        return session  # MFA challenge — no session yet
    settings = get_settings()
    response.headers["Cache-Control"] = "no-store"
    session.expires_in = settings.access_token_ttl_minutes * 60
    if _wants_body_token(request):
        return session
    response.set_cookie(
        settings.refresh_cookie_name,
        session.refresh_token,
        max_age=settings.refresh_token_ttl_days * 86400,
        path=settings.refresh_cookie_path,
        httponly=True,
        secure=settings.is_production,
        samesite="strict",
    )
    session.refresh_token = None
    return session


def _refresh_token_from(request: Request, body_token: str | None) -> str:
    if body_token:
        return body_token
    token = request.cookies.get(get_settings().refresh_cookie_name)
    if not token:
        raise UnauthorizedError("No active session", code="no_session")
    # Cookie-authenticated state change: require a header a cross-site form or
    # <img> cannot send. SameSite=Strict already blocks cross-site sending;
    # this is the second, independent defence.
    if request.headers.get(CSRF_HEADER, "").lower() != "xmlhttprequest":
        raise ForbiddenError("Missing request header", code="csrf")
    return token


def _clear_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.refresh_cookie_name, path=settings.refresh_cookie_path)


@router.post("/register", response_model=SessionResponse)
@limiter.limit("5/hour")
async def register(request: Request, response: Response, payload: RegisterRequest, db: PreTenantDb):
    session = await service.register_church_leader(
        db, payload, user_agent=request.headers.get("user-agent"), ip_address=_client_ip(request)
    )
    return _deliver(request, response, session)


@router.post("/join", response_model=SessionResponse)
@limiter.limit("5/hour")
async def join_church(request: Request, response: Response, payload: JoinChurchRequest, db: PreTenantDb):
    session = await service.join_church(
        db, payload, user_agent=request.headers.get("user-agent"), ip_address=_client_ip(request)
    )
    return _deliver(request, response, session)


@router.post("/login", response_model=SessionResponse | MfaChallengeResponse)
@limiter.limit("10/minute")
async def login(request: Request, response: Response, payload: LoginRequest, db: PreTenantDb):
    result = await service.authenticate(
        db,
        email=payload.email,
        password=payload.password,
        user_agent=request.headers.get("user-agent"),
        ip_address=_client_ip(request),
    )
    return _deliver(request, response, result)


@router.post("/mfa/challenge", response_model=SessionResponse)
@limiter.limit("10/minute")
async def mfa_challenge(request: Request, response: Response, payload: MfaChallengeRequest, db: PreTenantDb):
    session = await service.complete_mfa_challenge(
        db,
        challenge_token=payload.challenge_token,
        code=payload.code,
        user_agent=request.headers.get("user-agent"),
        ip_address=_client_ip(request),
    )
    return _deliver(request, response, session)


@router.post("/refresh", response_model=SessionResponse)
@limiter.limit("30/minute")
async def refresh(request: Request, response: Response, db: PreTenantDb, payload: RefreshRequest | None = None):
    raw = _refresh_token_from(request, payload.refresh_token if payload else None)
    try:
        session = await service.refresh_session(
            db,
            raw_refresh_token=raw,
            user_agent=request.headers.get("user-agent"),
            ip_address=_client_ip(request),
        )
    except UnauthorizedError:
        _clear_cookie(response)
        raise
    return _deliver(request, response, session)


@router.post("/switch-tenant", response_model=SessionResponse)
async def switch_tenant(
    request: Request, response: Response, payload: SwitchTenantRequest, claims: CurrentClaims, db: PreTenantDb
):
    session = await service.switch_tenant(
        db,
        user_id=claims.sub,
        membership_id=str(payload.membership_id),
        mfa_verified=claims.mfa_verified,
        user_agent=request.headers.get("user-agent"),
        ip_address=_client_ip(request),
    )
    return _deliver(request, response, session)


@router.post("/logout", status_code=204)
async def logout(request: Request, db: PreTenantDb, payload: RefreshRequest | None = None):
    try:
        raw = _refresh_token_from(request, payload.refresh_token if payload else None)
    except UnauthorizedError:
        raw = None  # already signed out — still clear the cookie
    if raw:
        await service.revoke_refresh_token(db, raw)
    response = Response(status_code=204)
    _clear_cookie(response)
    return response


@router.post("/accept-invite", response_model=SessionResponse)
@limiter.limit("10/hour")
async def accept_invite(request: Request, response: Response, payload: AcceptInviteRequest, db: PreTenantDb):
    session = await service.accept_invite(
        db,
        raw_token=payload.token,
        password=payload.password,
        first_name=payload.first_name,
        last_name=payload.last_name,
        user_agent=request.headers.get("user-agent"),
        ip_address=_client_ip(request),
    )
    return _deliver(request, response, session)


@router.post("/password-reset/request", response_model=PasswordResetRequested, status_code=202)
@limiter.limit("5/hour")
async def request_password_reset(
    request: Request, payload: PasswordResetRequest, background: BackgroundTasks, db: PreTenantDb
):
    """Same answer whether or not the account exists; the email (if any) is
    sent after the response so its timing reveals nothing either."""
    email = await recovery.request_reset(db, email=str(payload.email), ip_address=_client_ip(request))
    if email is not None:
        background.add_task(send_quietly, email)
    return PasswordResetRequested()


@router.post("/password-reset/complete", status_code=204)
@limiter.limit("10/hour")
async def complete_password_reset(request: Request, payload: PasswordResetComplete, db: PreTenantDb):
    await recovery.complete_reset(
        db, raw_token=payload.token, new_password=payload.password, ip_address=_client_ip(request)
    )
    response = Response(status_code=204)
    _clear_cookie(response)
    return response


@router.post("/mfa/setup", response_model=MfaSetupResponse)
async def mfa_setup(user: CurrentUser, db: PreTenantDb):
    return await service.start_mfa_setup(db, user)


@router.post("/mfa/verify", status_code=204)
async def mfa_verify(payload: MfaVerifyRequest, user: CurrentUser, db: PreTenantDb):
    await service.verify_mfa_setup(db, user, payload.code)


@router.post("/mfa/disable", status_code=204)
async def mfa_disable(user: CurrentUser, db: PreTenantDb, _step_up=Depends(require_step_up)):
    await service.disable_mfa(db, user)


@router.post("/step-up", response_model=StepUpResponse)
@limiter.limit("10/minute")
async def step_up(request: Request, payload: StepUpRequest, claims: CurrentClaims, user: CurrentUser, db: PreTenantDb):
    tenant_id = claims.tenant_id if claims.tenant_id else None
    return await service.issue_step_up_token(db, user, payload.code, tenant_id)
