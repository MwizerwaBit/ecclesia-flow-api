"""Aggregates every module's router under one `/api/v1` prefix.

Updated as each module in plan.md's roadmap lands — this file's import list
is the single place that tracks which modules are wired in versus still
module-only code with no routes yet.
"""
from fastapi import APIRouter

from app.modules.identity.router import router as identity_router
from app.modules.tenant.router import router as tenant_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(identity_router)
api_router.include_router(tenant_router)
