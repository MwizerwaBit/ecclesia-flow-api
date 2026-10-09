"""Aggregates every module's router under one `/api/v1` prefix.

Routers come in three groups, and which group a router is in is a security
decision — keep this file the single place it is made:

  PUBLIC        no session (sign-in, public church pages, pricing, webhooks)
  ALWAYS_ON     a session, but usable while a church is still onboarding or
                suspended — this is how a pending church becomes active
  BUSINESS      the church's day-to-day data; only for active churches
                (see app.core.deps.require_active_org)
"""

from fastapi import APIRouter, Depends

from app.core.deps import require_active_org
from app.modules.activity.router import public_router as events_public_router
from app.modules.activity.router import router as activity_router
from app.modules.affiliations.router import router as affiliations_router
from app.modules.billing.router import router as billing_router
from app.modules.certificates.router import public_router as certificates_public_router
from app.modules.certificates.router import router as certificates_router
from app.modules.finance.router import router as finance_router
from app.modules.groups.router import router as groups_router
from app.modules.hierarchy.router import router as hierarchy_router
from app.modules.identity.router import router as identity_router
from app.modules.leadership.router import router as leadership_router
from app.modules.media.router import router as media_router
from app.modules.membership.router import router as membership_router
from app.modules.messaging.router import router as messaging_router
from app.modules.org.router import router as org_router
from app.modules.people.router import households_router
from app.modules.people.router import router as people_router
from app.modules.platform_admin.router import router as platform_admin_router
from app.modules.portal.router import router as portal_router
from app.modules.rbac.router import router as rbac_router
from app.modules.team.router import router as team_router
from app.modules.tenant.router import router as tenant_router

PUBLIC = [identity_router, tenant_router, certificates_public_router, events_public_router]
ALWAYS_ON = [
    org_router,
    billing_router,
    team_router,
    rbac_router,
    hierarchy_router,
    leadership_router,
    platform_admin_router,
]
BUSINESS = [
    people_router,
    households_router,
    membership_router,
    groups_router,
    portal_router,
    activity_router,
    finance_router,
    messaging_router,
    media_router,
    certificates_router,
    affiliations_router,
]

api_router = APIRouter(prefix="/api/v1")
for router in PUBLIC + ALWAYS_ON:
    api_router.include_router(router)
for router in BUSINESS:
    api_router.include_router(router, dependencies=[Depends(require_active_org)])
