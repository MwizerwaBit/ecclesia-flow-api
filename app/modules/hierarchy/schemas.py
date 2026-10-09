from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

_KEY = r"^[a-z][a-z0-9_]{0,39}$"


class HierarchyUnitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    name: str
    code: str | None
    type: str
    unit_type_id: str | None = None
    unit_type_key: str | None = None
    parent_id: str | None
    parent_name: str | None
    depth: int
    member_count: int
    sub_unit_count: int
    address: str | None
    created_at: datetime


class HierarchyUnitCreate(BaseModel):
    name: str
    #: Free-text type; replaced by the type's label when unit_type_id is given.
    type: str | None = None
    unit_type_id: str | None = None
    code: str | None = None
    address: str | None = None
    parent_id: str | None = None

    @model_validator(mode="after")
    def _type_given(self):
        if not self.unit_type_id and not (self.type and self.type.strip()):
            raise ValueError("Give the unit a type")
        return self


class HierarchyUnitUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    unit_type_id: str | None = None
    code: str | None = None
    address: str | None = None
    parent_id: str | None = None


# ── Unit types ──────────────────────────────────────────────────────────


class UnitTypeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    label: str
    plural_label: str
    allowed_parent_keys: list[str]
    can_be_root: bool
    sort_order: int


class UnitTypeCreate(BaseModel):
    key: str = Field(pattern=_KEY)
    label: str = Field(min_length=1, max_length=60)
    plural_label: str = Field(min_length=1, max_length=60)
    #: Keys of the types a unit of this type may sit under. Empty = any.
    allowed_parent_keys: list[str] = Field(default_factory=list, max_length=20)
    can_be_root: bool = False
    sort_order: int = 0


class UnitTypeUpdate(BaseModel):
    """The key is the type's identity and can't change; everything else can."""

    label: str | None = Field(default=None, min_length=1, max_length=60)
    plural_label: str | None = Field(default=None, min_length=1, max_length=60)
    allowed_parent_keys: list[str] | None = Field(default=None, max_length=20)
    can_be_root: bool | None = None
    sort_order: int | None = None


class UnitTypePreset(BaseModel):
    key: str
    name: str
    description: str
    types: list[UnitTypeCreate]


class ApplyPresetRequest(BaseModel):
    preset: str
