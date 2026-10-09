"""The words a church uses for its own structure and people.

Labels only. Renaming "administrator" to "parish secretary" changes what
screens say, never what anyone may do — permissions belong to roles, which
are keyed by id, not by name.
"""

import json
from datetime import UTC, datetime

from prisma import Json, Prisma
from pydantic import BaseModel, Field, field_validator

from app.modules.audit.service import write_audit

#: Every term a church can rename, with the default wording.
DEFAULT_TERMS: dict[str, str] = {
    "organization": "Church",
    "unit": "Unit",
    "units": "Units",
    "member": "Member",
    "members": "Members",
    "visitor": "Visitor",
    "household": "Household",
    "households": "Households",
    "group": "Group",
    "groups": "Groups",
    "event": "Gathering",
    "events": "Gatherings",
    "leader": "Church leader",
    "administrator": "Administrator",
    "staff": "Staff",
    "giving": "Giving",
}


class TerminologyRead(BaseModel):
    #: Effective wording: defaults with the church's overrides applied.
    terms: dict[str, str]
    #: Only what the church changed.
    overrides: dict[str, str]
    defaults: dict[str, str]


class TerminologyUpdate(BaseModel):
    """The full set of overrides; omit a term (or send it empty) to go back
    to the default."""

    overrides: dict[str, str] = Field(default_factory=dict)

    @field_validator("overrides")
    @classmethod
    def _known_terms(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(value) - set(DEFAULT_TERMS))
        if unknown:
            raise ValueError(f"Unknown terms: {', '.join(unknown)}")
        cleaned = {}
        for key, text in value.items():
            text = " ".join(text.split())
            if len(text) > 40:
                raise ValueError(f"'{key}' is too long (40 characters at most)")
            if text and text != DEFAULT_TERMS[key]:
                cleaned[key] = text
        return cleaned


def _as_dict(raw) -> dict[str, str]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    return {k: v for k, v in (raw or {}).items() if k in DEFAULT_TERMS and isinstance(v, str)}


def _read(overrides: dict[str, str]) -> dict:
    return {"terms": {**DEFAULT_TERMS, **overrides}, "overrides": overrides, "defaults": DEFAULT_TERMS}


async def get_terminology(db: Prisma, tenant_id: str) -> dict:
    row = await db.organizationconfiguration.find_unique(where={"tenant_id": tenant_id})
    return _read(_as_dict(row.terminology) if row else {})


async def update_terminology(db: Prisma, tenant_id: str, user_id: str, payload: TerminologyUpdate) -> dict:
    await db.organizationconfiguration.upsert(
        where={"tenant_id": tenant_id},
        data={
            "create": {"tenant_id": tenant_id, "terminology": Json(payload.overrides), "updated_by_user_id": user_id},
            "update": {
                "terminology": Json(payload.overrides),
                "updated_by_user_id": user_id,
                "updated_at": datetime.now(UTC),
            },
        },
    )
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        action="org.terminology_updated",
        resource_type="organization",
        resource_id=tenant_id,
        metadata={"overrides": payload.overrides},
    )
    return _read(payload.overrides)
