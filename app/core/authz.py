"""Authorization policy — the one place that decides who may do what to which record.

Two layers, applied in this order:

RBAC — "may this role do X at all?"
    Checked by ``require_permission``/``require_any_permission`` in
    ``app.core.deps`` against the permission list embedded in the access
    token (resolved from the membership's role at sign-in, and re-validated
    against the database on every request — see ``get_tenant_db``).
    A handful of permissions additionally require an MFA-verified session
    (``MFA_REQUIRED_PERMISSIONS``).

ABAC — "...to THIS record?"
    Attributes of the subject (the membership's ``unit_scope_id``, the user
    id) are compared with attributes of the resource (its ``unit_id``, its
    owner). A session scoped to a hierarchy unit sees that unit and all of
    its descendants (one closure-table query) and nothing else:

        members  visible iff their home unit, or a unit they are an active
                 associate of, is in scope (unassigned members are
                 whole-church records, so out of scope); writable only from
                 their home unit
        groups,  readable if church-wide (no unit) or in scope;
        events   writable only if in scope
        self     a member-role session reaches only the member record
                 linked to its own user id

Out-of-scope records answer 404, not 403: a scoped user learns nothing
about whether a record they can't see exists.

Postgres enforces the same unit rules for people data independently (the
``unit_scope*`` RLS policies, keyed on ``app.unit_scope``), so a query that
forgets these helpers still can't return another branch's people. These
helpers stay because they give the right status code and message, and
because two layers is the point.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from prisma import Prisma

from app.core.deps import CurrentClaims, TenantDb
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.permissions import MFA_REQUIRED_PERMISSIONS, descendant_unit_ids  # noqa: F401 (re-exported)


@dataclass(frozen=True)
class Scope:
    """The subject's ABAC attributes for one request."""

    user_id: str
    #: None = whole organisation. Otherwise the scoped unit plus its descendants.
    unit_ids: frozenset[str] | None
    #: The membership's own scope root, used as the default unit for new records.
    root_unit_id: str | None
    permissions: frozenset[str]
    is_platform_admin: bool

    @property
    def is_scoped(self) -> bool:
        return self.unit_ids is not None

    def has(self, permission: str) -> bool:
        return self.is_platform_admin or permission in self.permissions

    # ── Unit-attribute rules ────────────────────────────────────────────
    def unit_visible(self, unit_id: str | None) -> bool:
        """Strict: for records that always belong to a unit (members)."""
        return self.unit_ids is None or (unit_id is not None and unit_id in self.unit_ids)

    def member_visible(self, row: dict) -> bool:
        """A person: their home unit, or any unit they are an active associate
        of. Rows without ``associate_unit_ids`` are judged on home unit alone,
        which can only hide more, never show more."""
        if self.unit_ids is None or self.unit_visible(row.get("unit_id")):
            return True
        return any(u in self.unit_ids for u in (row.get("associate_unit_ids") or []))

    def shared_readable(self, unit_id: str | None) -> bool:
        """For records that may be church-wide (groups, events): church-wide
        ones are readable by everyone with the read permission."""
        return unit_id is None or self.unit_visible(unit_id)

    def unit_writable(self, unit_id: str | None) -> bool:
        """Writes need the record inside scope; a scoped session never edits
        church-wide records."""
        return self.unit_ids is None or (unit_id is not None and unit_id in self.unit_ids)

    def assignable_unit(self, requested_unit_id: str | None) -> str | None:
        """The unit a new or moved record may be given. A scoped session can
        only place records inside its own branch, and defaults to its root."""
        if self.unit_ids is None:
            return requested_unit_id
        if requested_unit_id is None:
            return self.root_unit_id
        if requested_unit_id not in self.unit_ids:
            raise ForbiddenError("That unit is outside your assigned branch", code="out_of_scope")
        return requested_unit_id

    # ── Filtering helpers ───────────────────────────────────────────────
    def filter_visible(self, rows: list[dict], key: str = "unit_id") -> list[dict]:
        return rows if self.unit_ids is None else [r for r in rows if self.unit_visible(r.get(key))]

    def filter_members(self, rows: list[dict]) -> list[dict]:
        return rows if self.unit_ids is None else [r for r in rows if self.member_visible(r)]

    def filter_shared(self, rows: list[dict], key: str = "unit_id") -> list[dict]:
        return rows if self.unit_ids is None else [r for r in rows if self.shared_readable(r.get(key))]


async def get_scope(claims: CurrentClaims, db: TenantDb) -> Scope:
    unit_ids: frozenset[str] | None = None
    if claims.unit_scope_id is not None:
        ids = await descendant_unit_ids(db, claims.unit_scope_id)
        ids.add(claims.unit_scope_id)
        unit_ids = frozenset(ids)
    return Scope(
        user_id=claims.sub,
        unit_ids=unit_ids,
        root_unit_id=claims.unit_scope_id,
        permissions=frozenset(claims.permissions),
        is_platform_admin=claims.is_platform_admin,
    )


CurrentScope = Annotated[Scope, Depends(get_scope)]


# ── Record-level guards (load the attribute, then decide) ───────────────────


async def _unit_of(db: Prisma, table: str, record_id: str) -> tuple[bool, str | None]:
    # `table` is only ever one of the literals below, never caller input.
    rows = await db.query_raw(f"select unit_id from {table} where id = $1::uuid", record_id)
    if not rows:
        return False, None
    return True, rows[0]["unit_id"]


_MEMBER_UNITS_SQL = """
    select m.id, m.unit_id,
           array(select um.unit_id from unit_memberships um
                  where um.member_id = m.id and um.kind = 'associate' and um.status = 'active') as associate_unit_ids
    from members m
"""


async def ensure_member_visible(db: Prisma, scope: Scope, member_id: str) -> None:
    rows = await db.query_raw(_MEMBER_UNITS_SQL + " where m.id = $1::uuid", member_id)
    if not rows or not scope.member_visible(rows[0]):
        raise NotFoundError("No such member")


async def ensure_member_writable(db: Prisma, scope: Scope, member_id: str) -> None:
    """Changing a person's record is for their home unit's staff; an
    associate unit can see them but not edit them."""
    exists, unit_id = await _unit_of(db, "members", member_id)
    if not exists:
        raise NotFoundError("No such member")
    if not scope.unit_writable(unit_id):
        await ensure_member_visible(db, scope, member_id)  # 404 if not even visible
        raise ForbiddenError("Only their home unit can change this person's record", code="out_of_scope")


async def ensure_members_visible(db: Prisma, scope: Scope, member_ids: list[str]) -> list[str]:
    """The subset of ids this session may act on. Others are dropped silently,
    exactly as if they did not exist."""
    if not member_ids:
        return []
    rows = await db.query_raw(_MEMBER_UNITS_SQL + " where m.id = any($1::uuid[])", member_ids)
    return [r["id"] for r in rows if scope.member_visible(r)]


async def ensure_group_readable(db: Prisma, scope: Scope, group_id: str) -> None:
    exists, unit_id = await _unit_of(db, "groups", group_id)
    if not exists or not scope.shared_readable(unit_id):
        raise NotFoundError("No such group")


async def ensure_group_writable(db: Prisma, scope: Scope, group_id: str) -> None:
    await ensure_group_readable(db, scope, group_id)
    _, unit_id = await _unit_of(db, "groups", group_id)
    if not scope.unit_writable(unit_id):
        raise ForbiddenError("Church-wide groups can only be changed by whole-church staff", code="out_of_scope")


async def ensure_event_readable(db: Prisma, scope: Scope, event_id: str) -> None:
    exists, unit_id = await _unit_of(db, "events", event_id)
    if not exists or not scope.shared_readable(unit_id):
        raise NotFoundError("No such event")


async def ensure_event_writable(db: Prisma, scope: Scope, event_id: str) -> None:
    await ensure_event_readable(db, scope, event_id)
    _, unit_id = await _unit_of(db, "events", event_id)
    if not scope.unit_writable(unit_id):
        raise ForbiddenError("Church-wide events can only be changed by whole-church staff", code="out_of_scope")


async def ensure_unit_in_scope(db: Prisma, scope: Scope, unit_id: str | None) -> None:
    """For hierarchy edits and staff assignments: the target unit itself."""
    if unit_id is not None and not scope.unit_writable(unit_id):
        raise ForbiddenError("That unit is outside your assigned branch", code="out_of_scope")
    if unit_id is None and scope.is_scoped:
        raise ForbiddenError("Only whole-church staff can act at church level", code="out_of_scope")


def ensure_self_or_permission(scope: Scope, owner_user_id: str, permission: str) -> None:
    """A member may read their own record; anyone else needs the permission."""
    if owner_user_id != scope.user_id and not scope.has(permission):
        raise NotFoundError("No such record")


def ensure_grantable(scope: Scope, permissions: list[str] | set[str]) -> None:
    """No privilege escalation: nobody can grant (via a custom role or a role
    assignment) a permission they do not hold themselves."""
    if scope.is_platform_admin:
        return
    excess = set(permissions) - scope.permissions
    if excess:
        raise ForbiddenError(
            f"You can't grant permissions you don't have: {', '.join(sorted(excess))}",
            code="privilege_escalation",
        )
