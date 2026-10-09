from datetime import datetime

from pydantic import BaseModel


class CertificateTemplateToken(BaseModel):
    key: str
    label: str
    x: float
    y: float
    font_size: float
    font_family: str
    color: str


class CertificateTemplateRead(BaseModel):
    id: str
    tenant_id: str
    name: str
    category: str
    status: str
    background_image_url: str | None
    tokens: list[dict]
    qr_code_position: dict | None
    created_at: datetime
    updated_at: datetime


class CertificateTemplateSave(BaseModel):
    id: str | None = None
    name: str
    category: str
    status: str = "draft"
    background_image_url: str | None = None
    tokens: list[CertificateTemplateToken] = []
    qr_code_position: dict | None = None


class CertificateRead(BaseModel):
    id: str
    tenant_id: str
    template_id: str
    template_name: str | None = None
    member_id: str
    member_name: str | None = None
    issued_by_user_id: str
    issued_by_name: str | None = None
    issued_at: datetime
    serial_number: str
    qr_hash: str
    custom_values: dict
    is_revoked: bool
    revoked_at: datetime | None
    revoked_reason: str | None
    pdf_url: str | None


class IssueCertificateInput(BaseModel):
    template_id: str
    member_id: str
    custom_values: dict = {}


class BulkIssueInput(BaseModel):
    template_id: str
    member_ids: list[str]
    custom_values: dict = {}


class RevokeCertificateInput(BaseModel):
    reason: str


class PublicCertificateVerification(BaseModel):
    """Deliberately minimal — this is a public, unauthenticated endpoint
    behind a printed QR code. No tenant_id, custom_values, or pdf_url; just
    enough to answer "is this certificate real" (screen-inventory.md's
    Certificate Verification Page: status, issuing church, recipient name,
    date)."""

    id: str
    serial_number: str
    issued_at: datetime
    is_revoked: bool
    revoked_at: datetime | None
    template_name: str
    category: str
    member_name: str
    org_name: str
