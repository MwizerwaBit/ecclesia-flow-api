from datetime import datetime

from pydantic import BaseModel


class MediaAssetRead(BaseModel):
    id: str
    tenant_id: str
    kind: str
    url: str
    mime_type: str
    size_bytes: int
    name: str
    alt_text: str | None
    uploaded_by_name: str | None = None
    created_at: datetime
