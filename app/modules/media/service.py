import uuid
from pathlib import Path

from fastapi import UploadFile
from prisma import Prisma

from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.core.private_storage import FileTooLargeError, UnsupportedFileError, sniff_media
from app.modules.media import repository


def _kind_from_mime(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    return "document"


async def list_assets(db: Prisma, *, kind: str | None) -> list[dict]:
    return await repository.list_assets(db, kind=kind)


async def upload(db: Prisma, tenant_id: str, uploaded_by_user_id: str, file: UploadFile) -> dict:
    settings = get_settings()
    # Read one byte past the cap so an oversized file is detected without
    # holding more than that in memory.
    contents = await file.read(settings.max_document_bytes + 1)
    if len(contents) > settings.max_document_bytes:
        raise FileTooLargeError(f"Files can be at most {settings.max_document_bytes // (1024 * 1024)} MB")
    if not contents:
        raise UnsupportedFileError("The file is empty")
    # The type is decided by the bytes; the client's claim and file name are ignored.
    mime_type, ext = sniff_media(contents)
    kind = _kind_from_mime(mime_type)
    asset_id = str(uuid.uuid4())
    original_name = (file.filename or "upload")[:200]

    tenant_dir = Path(settings.media_local_dir) / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    disk_path = tenant_dir / f"{asset_id}{ext}"
    disk_path.write_bytes(contents)

    url = f"{settings.media_public_url_prefix}/{tenant_id}/{asset_id}{ext}"
    asset = await repository.create_asset(
        db,
        {
            "id": asset_id,
            "tenant_id": tenant_id,
            "uploaded_by_user_id": uploaded_by_user_id,
            "kind": kind,
            "storage_key": original_name,
            "url": url,
            "mime_type": mime_type,
            "size_bytes": len(contents),
        },
    )
    rows = await repository.list_assets(db, kind=None)
    return next(r for r in rows if r["id"] == asset.id)


async def remove(db: Prisma, asset_id: str, *, actor_user_id: str, is_admin: bool) -> None:
    asset = await repository.get_asset(db, asset_id)
    if asset is None or (not is_admin and str(asset.uploaded_by_user_id) != actor_user_id):
        # Someone else's file answers exactly like a missing one.
        raise NotFoundError("No such media asset")
    settings = get_settings()
    ext = Path(asset.storage_key).suffix
    disk_path = Path(settings.media_local_dir) / asset.tenant_id / f"{asset_id}{ext}"
    disk_path.unlink(missing_ok=True)
    await repository.delete_asset(db, asset_id)
