from prisma import Json, Prisma


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
    data = {
        "tenant_id": tenant_id,
        "actor_user_id": actor_user_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "ip_address": ip_address,
        "is_impersonated": is_impersonated,
        "impersonated_by_user_id": impersonated_by_user_id,
    }
    # Prisma's nullable-Json input type wants the key OMITTED for "no value,"
    # not present-and-None — passing `"metadata": None` explicitly raises
    # MissingRequiredValueError even though the column itself is nullable.
    if metadata is not None:
        data["metadata"] = Json(metadata)
    await db.auditlog.create(data=data)
