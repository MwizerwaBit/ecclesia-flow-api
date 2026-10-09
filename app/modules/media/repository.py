from prisma import Prisma
from prisma.models import MediaAsset


async def list_assets(db: Prisma, *, kind: str | None) -> list[dict]:
    where_sql = "where ma.kind = $1" if kind else ""
    params = [kind] if kind else []
    return await db.query_raw(
        f"""
        select ma.id, ma.tenant_id, ma.kind, ma.url, ma.mime_type, ma.size_bytes,
               ma.storage_key as name, ma.alt_text, ma.created_at, ma.uploaded_by_user_id,
               (u.first_name || ' ' || u.last_name) as uploaded_by_name
        from media_assets ma join users u on u.id = ma.uploaded_by_user_id
        {where_sql}
        order by ma.created_at desc
        """,
        *params,
    )


async def get_asset(db: Prisma, asset_id: str) -> MediaAsset | None:
    return await db.mediaasset.find_unique(where={"id": asset_id})


async def create_asset(db: Prisma, data: dict) -> MediaAsset:
    return await db.mediaasset.create(data=data)


async def delete_asset(db: Prisma, asset_id: str) -> None:
    await db.mediaasset.delete(where={"id": asset_id})
