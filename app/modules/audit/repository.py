from prisma import Prisma


async def insert_audit_log(
    db: Prisma,
    *,
    tenant_id: str | None,
    actor_user_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    metadata_json: str | None,
    ip_address: str | None,
    is_impersonated: bool,
    impersonated_by_user_id: str | None,
) -> None:
    """A plain INSERT with nothing read back: account-level entries (tenant_id
    NULL) may be appended from a tenant session but never read by one, and an
    INSERT ... RETURNING would need read access too."""
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
        metadata_json,
        ip_address,
        is_impersonated,
        impersonated_by_user_id,
    )


async def chain_breaks(db: Prisma, tenant_id: str | None) -> list[dict]:
    """[] when the chain is intact, else the first break."""
    return await db.query_raw("select * from verify_audit_chain($1::uuid)", tenant_id)


async def chain_heads(db: Prisma) -> list[dict]:
    return await db.query_raw("select tenant_id, chain_seq, row_hash from audit_chain_heads()")


async def get_row_hash(db: Prisma, tenant_id: str | None, chain_seq: int) -> list[dict]:
    return await db.query_raw(
        "select row_hash from audit_logs where tenant_id is not distinct from $1::uuid and chain_seq = $2",
        tenant_id,
        chain_seq,
    )
