import json

from prisma import Prisma


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
    row_hash) and refuses any later change — see migration 20261009120000.

    A plain INSERT with nothing read back: account-level entries (tenant_id
    NULL) may be appended from a tenant session but never read by one, and
    an INSERT ... RETURNING would need read access too."""
    await db.execute_raw(
        """
        insert into audit_logs (id, tenant_id, actor_user_id, action, resource_type, resource_id,
                                metadata, ip_address, is_impersonated, impersonated_by_user_id)
        values (gen_random_uuid(), $1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb, $7::inet, $8, $9::uuid)
        """,
        tenant_id,
        actor_user_id,
        action,
        resource_type,
        resource_id,
        json.dumps(metadata, default=str) if metadata is not None else None,
        ip_address,
        is_impersonated,
        impersonated_by_user_id,
    )
