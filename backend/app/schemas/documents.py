from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DocumentFileResponse(BaseModel):
    id: UUID
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    scan_status: str
    downloadable: bool


class DocumentResponse(BaseModel):
    id: UUID
    kind: str
    status: str
    display_name: str
    created_at: datetime
    files: list[DocumentFileResponse]


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
