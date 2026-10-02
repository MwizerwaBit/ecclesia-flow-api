from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentClaims, PreTenantDb, require_step_up
from app.core.exceptions import UnauthorizedError
from app.core.rate_limit import limiter
from app.modules.identity import repository, service
from app.modules.identity.schemas import (
    LoginRequest,
    MfaChallengeRequest,
    MfaChallengeResponse,
    MfaSetupResponse,
    MfaVerifyRequest,
    RefreshRequest,
    RegisterRequest,
    SessionResponse,
    StepUpRequest,
    StepUpResponse,
    SwitchTenantRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _current_user(claims: CurrentClaims, db: PreTenantDb):
    user = await repository.get_user_by_id(db, claims.sub)
    if user is None:
        raise UnauthorizedError("Account no longer exists")
    return user


CurrentUser = Annotated[object, Depends(_current_user)]


@router.post("/register", response_model=SessionResponse)
@limiter.limit("5/hour")
async def register(request: Request, payload: RegisterRequest, db: PreTenantDb):
    return await service.register_church_leader(
        db, payload, user_agent=request.headers.get("user-agent"), ip_address=_client_ip(request)
    )


@router.post("/login", response_model=SessionResponse | MfaChallengeResponse)
@limiter.limit("10/minute")
async def login(request: Request, payload: LoginRequest, db: PreTenantDb):
    return await service.authenticate(
        db,
        email=payload.email,
        password=payload.password,
        user_agent=request.headers.get("user-agent"),
        ip_address=_client_ip(request),
    )


@router.post("/mfa/challenge", response_model=SessionResponse)
@limiter.limit("10/minute")
async def mfa_challenge(request: Request, payload: MfaChallengeRequest, db: PreTenantDb):
    return await service.complete_mfa_challenge(
        db,
        challenge_token=payload.challenge_token,
        code=payload.code,
        user_agent=request.headers.get("user-agent"),
        ip_address=_client_ip(request),
    )


@router.post("/refresh", response_model=SessionResponse)
@limiter.limit("30/minute")
async def refresh(request: Request, payload: RefreshRequest, db: PreTenantDb):
    return await service.refresh_session(
        db,
        raw_refresh_token=payload.refresh_token,
        user_agent=request.headers.get("user-agent"),
        ip_address=_client_ip(request),
    )


@router.post("/switch-tenant", response_model=SessionResponse)
async def switch_tenant(request: Request, payload: SwitchTenantRequest, claims: CurrentClaims, db: PreTenantDb):
    return await service.switch_tenant(
        db,
        user_id=claims.sub,
        membership_id=payload.membership_id,
        user_agent=request.headers.get("user-agent"),
        ip_address=_client_ip(request),
    )


@router.post("/logout", status_code=204)
async def logout(payload: RefreshRequest, db: PreTenantDb):
    await service.revoke_refresh_token(db, payload.refresh_token)


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
