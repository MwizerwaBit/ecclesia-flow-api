import json

from prisma import Prisma

from app.modules.audit import repository


async def write_audit(
    db: Prisma,
    *,
    tenant_id: str | None,
    actor_user_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    metadata: dict | None = None,
    ip_address: str | None = None,
    is_impersonated: bool = False,
    impersonated_by_user_id: str | None = None,
) -> None:
    """Append one audit entry. The database chains it (chain_seq, prev_hash,
    row_hash) and refuses any later change — see migration 20261009120000."""
    await repository.insert_audit_log(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_json=json.dumps(metadata, default=str) if metadata is not None else None,
        ip_address=ip_address,
        is_impersonated=is_impersonated,
        impersonated_by_user_id=impersonated_by_user_id,
    )


async def history_for(db: Prisma, resource_type: str, resource_id: str, limit: int = 100) -> list[dict]:
    """Audit entries for one record. Callers decide who may ask (a route's
    permission and scope checks); this only reads."""
    return await repository.list_for_resource(db, resource_type, resource_id, limit)
