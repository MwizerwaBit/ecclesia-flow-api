"""Org data access. Runs in the caller's tenant session (RLS-bound) except
where a function's docstring says it takes the platform session."""

from prisma import Prisma
from prisma.models import Organization, OrgDocument

from app.modules.rbac.models import SYSTEM_ROLE_BOARD_ID, SYSTEM_ROLE_LEADER_ID


async def get_org(db: Prisma, org_id: str) -> Organization | None:
    return await db.organization.find_unique(where={"id": org_id})


async def update_org(db: Prisma, org_id: str, data: dict) -> Organization:
    return await db.organization.update(where={"id": org_id}, data=data)


async def list_module_overrides(db: Prisma, tenant_id: str) -> list:
    return await db.orgmoduleoverride.find_many(where={"tenant_id": tenant_id})


async def upsert_module_override(
    db: Prisma, *, tenant_id: str, module_id: str, enabled: bool, user_id: str, by_platform: bool
) -> None:
    await db.execute_raw(
        """
        insert into org_module_overrides (tenant_id, module_id, enabled, set_by_user_id, set_by_platform, updated_at)
        values ($1::uuid, $2, $3, $4::uuid, $5, now())
        on conflict (tenant_id, module_id) do update
          set enabled = excluded.enabled, set_by_user_id = excluded.set_by_user_id,
              set_by_platform = excluded.set_by_platform, updated_at = now()
        """,
        tenant_id,
        module_id,
        enabled,
        user_id,
        by_platform,
    )


async def delete_module_override(db: Prisma, tenant_id: str, module_id: str) -> None:
    await db.execute_raw(
        "delete from org_module_overrides where tenant_id = $1::uuid and module_id = $2", tenant_id, module_id
    )


async def people_in_charge(db: Prisma, tenant_id: str) -> list[dict]:
    """Leader and administrators, active or still invited."""
    return await db.query_raw(
        """
        select tm.id, tm.status, tm.is_leader, tm.role_id, u.email, u.first_name, u.last_name
        from tenant_memberships tm join users u on u.id = tm.user_id
        where tm.tenant_id = $1::uuid
          and (tm.is_leader or tm.role_id in ($2::uuid, $3::uuid))
          and tm.status in ('active', 'invited')
        """,
        tenant_id,
        SYSTEM_ROLE_BOARD_ID,
        SYSTEM_ROLE_LEADER_ID,
    )


async def count_branches(db: Prisma) -> int:
    rows = await db.query_raw("select count(*)::int as n from hierarchy_units where parent_id is not null")
    return rows[0]["n"]


async def latest_document(db: Prisma, tenant_id: str, kind: str) -> OrgDocument | None:
    return await db.orgdocument.find_first(where={"tenant_id": tenant_id, "kind": kind}, order={"uploaded_at": "desc"})


async def list_documents(db: Prisma, tenant_id: str) -> list[dict]:
    return await db.query_raw(
        """
        select d.*, u.first_name || ' ' || u.last_name as uploaded_by_name
        from org_documents d join users u on u.id = d.uploaded_by_user_id
        where d.tenant_id = $1::uuid
        order by d.uploaded_at desc
        """,
        tenant_id,
    )


async def get_document(db: Prisma, tenant_id: str, document_id: str) -> OrgDocument | None:
    return await db.orgdocument.find_first(where={"id": document_id, "tenant_id": tenant_id})


async def supersede_documents(db: Prisma, tenant_id: str, kind: str) -> None:
    await db.orgdocument.update_many(
        where={"tenant_id": tenant_id, "kind": kind, "status": "pending_review"},
        data={"status": "superseded"},
    )


async def create_document(db: Prisma, data: dict) -> OrgDocument:
    return await db.orgdocument.create(data=data)


async def get_subscription(db: Prisma, tenant_id: str):
    return await db.billingsubscription.find_unique(where={"tenant_id": tenant_id})
