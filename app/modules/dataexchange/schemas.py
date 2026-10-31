from typing import Literal

from pydantic import BaseModel


class ColumnRead(BaseModel):
    key: str
    label: str
    required: bool
    help: str
    example: str


class DatasetRead(BaseModel):
    key: str
    label: str
    description: str
    can_import: bool
    can_export: bool
    columns: list[ColumnRead]


class RowResult(BaseModel):
    #: The spreadsheet row (the header is row 1).
    row: int
    status: Literal["ok", "duplicate", "error"]
    name: str
    messages: list[str] = []


class ImportReport(BaseModel):
    dataset: str
    #: True when nothing was saved: a check run, or an import that was refused.
    dry_run: bool
    committed: bool
    total_rows: int
    #: Rows that are fine (saved, or would be saved).
    valid: int
    #: Rows left out because the person is already on file or repeated in the file.
    duplicates: int
    failed: int
    #: File-level notes, e.g. columns that were ignored.
    notes: list[str]
    #: Only the rows that need attention, capped so a bad file can't flood the reply.
    issues: list[RowResult]
    issues_truncated: bool
