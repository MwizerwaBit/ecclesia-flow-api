from pydantic import BaseModel


class FilterRead(BaseModel):
    key: str
    label: str
    kind: str


class ColumnRead(BaseModel):
    key: str
    label: str
    kind: str


class ReportRead(BaseModel):
    key: str
    title: str
    description: str
    columns: list[ColumnRead]
    filters: list[FilterRead]


class ReportResult(BaseModel):
    key: str
    title: str
    columns: list[ColumnRead]
    rows: list[dict]
    #: The filters as they were applied, after defaults.
    filters: dict[str, str]
