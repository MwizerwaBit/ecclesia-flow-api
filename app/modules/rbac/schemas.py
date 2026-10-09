from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PermissionCatalogueItem(BaseModel):
    key: str
    label: str
    sensitive: bool = False


class PermissionCatalogueModule(BaseModel):
    module: str
    description: str
    permissions: list[PermissionCatalogueItem]


class CustomRoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str | None
    name: str
    color: str | None
    permissions: list[str]
    is_system: bool
    member_count: int
    created_at: datetime


class CustomRoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str | None = None
    permissions: list[str] = Field(default_factory=list)
