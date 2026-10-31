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

from app.core.deps import require_active_org, require_module
from app.modules.activity.router import public_router as events_public_router
from app.modules.activity.router import router as activity_router
from app.modules.affiliations.router import router as affiliations_router
from app.modules.billing.router import router as billing_router
from app.modules.certificates.router import public_router as certificates_public_router
from app.modules.certificates.router import router as certificates_router
from app.modules.dataexchange.router import router as dataexchange_router
from app.modules.family.router import router as family_router
from app.modules.finance.router import router as finance_router
from app.modules.groups.router import router as groups_router
from app.modules.hierarchy.router import router as hierarchy_router
from app.modules.identity.router import router as identity_router
from app.modules.leadership.router import router as leadership_router
from app.modules.media.router import router as media_router
from app.modules.membership.router import router as membership_router
from app.modules.messaging.router import router as messaging_router
from app.modules.notifications.router import router as notifications_router
from app.modules.notifications.router import send_router as notifications_send_router
from app.modules.org.catalogue import MODULE_BY_ID, tier_at_least
from app.modules.org.router import router as org_router
from app.modules.payment_approvals.router import router as billing_platform_router
from app.modules.people.router import households_router
from app.modules.people.router import router as people_router
from app.modules.platform_admin.router import router as platform_admin_router
from app.modules.portal.router import router as portal_router
from app.modules.rbac.router import router as rbac_router
from app.modules.reports.router import router as reports_router
from app.modules.team.router import router as team_router
from app.modules.tenant.router import router as tenant_router

PUBLIC = [identity_router, tenant_router, certificates_public_router, events_public_router]
ALWAYS_ON = [
    org_router,
    billing_router,
    billing_platform_router,
    team_router,
    rbac_router,
    hierarchy_router,
    leadership_router,
    platform_admin_router,
    # A person's own notifications: a pending or suspended church's people still
    # need "your payment was rejected", and the bell is not a paid module.
    notifications_router,
]
#: Each business router with the module it belongs to (None: not a paid module).
#: While a church isn't paid up, only the free modules answer (require_module).
BUSINESS = [
    (people_router, "people"),
    (households_router, "people"),
    (membership_router, "people"),
    (family_router, "people"),
    (groups_router, "people"),
    (portal_router, None),
    (activity_router, "gatherings"),
    (finance_router, "giving"),
    (messaging_router, "communications"),
    (notifications_send_router, "communications"),
    (media_router, "media"),
    (certificates_router, "certificates"),
    (affiliations_router, "hierarchy"),
    # Import / export of the church's own data. Core to onboarding, so not a paid module;
    # rights come from members:create / members:export, and basics-state churches may use it.
    (dataexchange_router, None),
    (reports_router, "reports"),
]

#: Business modules whose router is also shared plumbing for core screens, so the
#: plan check can't apply to it: /media stores member photos, portal avatars and
#: the church logo, which every plan has. Paid state still applies (402).
PLAN_CHECK_EXEMPT = {"media"}

api_router = APIRouter(prefix="/api/v1")
for router in PUBLIC + ALWAYS_ON:
    api_router.include_router(router)
for router, module_id in BUSINESS:
    guards = [Depends(require_active_org)]
    if module_id:
        module = MODULE_BY_ID[module_id]
        guards.append(
            Depends(
                require_module(
                    module.name,
                    free=tier_at_least("free", module.min_tier),
                    module_id=None if module.id in PLAN_CHECK_EXEMPT else module.id,
                    plan_includes=lambda tier, required=module.min_tier: tier_at_least(tier, required),
                )
            )
        )
    api_router.include_router(router, dependencies=guards)
