import uuid

from pydantic import BaseModel, ConfigDict


class PublicChurchSummary(BaseModel):
    """Backs the frontend's PublicChurchSummary exactly — only what a
    not-yet-authenticated visitor should ever see about a church."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    display_name: str
    country: str
    logo_url: str | None
    primary_color: str | None
    city: str | None = None
    #: Government registration certificate reviewed and accepted.
    verified: bool = False


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    legal_name: str
    display_name: str
    slug: str
    country: str
    currency: str
    timezone: str
    language: str
    status: str
    tier: str
    logo_url: str | None
    primary_color: str | None
