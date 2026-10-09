import secrets

from prisma import Prisma

from app.core.exceptions import NotFoundError
from app.modules.certificates import repository
from app.modules.certificates.schemas import BulkIssueInput, CertificateTemplateSave, IssueCertificateInput

_SERIAL_PREFIX = {"sacramental": "SAC", "membership": "MEM", "recognition": "REC", "education": "EDU"}


async def list_templates(db: Prisma, *, category: str | None) -> list:
    return await repository.list_templates(db, category=category)


async def get_template(db: Prisma, template_id: str):
    template = await repository.get_template(db, template_id)
    if template is None:
        raise NotFoundError("No such certificate template")
    return template


async def save_template(db: Prisma, tenant_id: str, payload: CertificateTemplateSave):
    data = payload.model_dump(exclude={"id"})
    data["tokens"] = [t for t in data["tokens"]]
    if payload.id:
        existing = await repository.get_template(db, payload.id)
        if existing is None:
            raise NotFoundError("No such certificate template")
        return await repository.update_template(db, payload.id, data)
    return await repository.create_template(db, tenant_id, data)


async def list_issued(db: Prisma, *, member_id: str | None) -> list[dict]:
    return await repository.list_issued(db, member_id=member_id)


async def get_issued(db: Prisma, certificate_id: str) -> dict:
    row = await repository.get_issued_row(db, certificate_id)
    if row is None:
        raise NotFoundError("No such certificate")
    return row


async def _issue_one(
    db: Prisma, tenant_id: str, issued_by_user_id: str, template, member_id: str, custom_values: dict
) -> dict:
    prefix = _SERIAL_PREFIX.get(template.category, "CERT")
    serial = await repository.next_serial_number(db, tenant_id, template.category, prefix)
    qr_hash = secrets.token_urlsafe(32)
    cert = await repository.create_certificate(
        db,
        {
            "tenant_id": tenant_id,
            "template_id": template.id,
            "member_id": member_id,
            "issued_by_user_id": issued_by_user_id,
            "serial_number": serial,
            "qr_hash": qr_hash,
            "custom_values": custom_values,
        },
    )
    return await get_issued(db, cert.id)


async def issue(db: Prisma, tenant_id: str, issued_by_user_id: str, payload: IssueCertificateInput) -> dict:
    template = await get_template(db, payload.template_id)
    return await _issue_one(db, tenant_id, issued_by_user_id, template, payload.member_id, payload.custom_values)


async def bulk_issue(db: Prisma, tenant_id: str, issued_by_user_id: str, payload: BulkIssueInput) -> list[dict]:
    template = await get_template(db, payload.template_id)
    results = []
    for member_id in payload.member_ids:
        results.append(await _issue_one(db, tenant_id, issued_by_user_id, template, member_id, payload.custom_values))
    return results


async def revoke(db: Prisma, certificate_id: str, reason: str) -> dict:
    existing = await repository.get_issued_row(db, certificate_id)
    if existing is None:
        raise NotFoundError("No such certificate")
    await repository.revoke_certificate(db, certificate_id, reason=reason)
    return await get_issued(db, certificate_id)


async def verify(db: Prisma, qr_hash: str) -> dict | None:
    return await repository.verify_by_hash(db, qr_hash)
