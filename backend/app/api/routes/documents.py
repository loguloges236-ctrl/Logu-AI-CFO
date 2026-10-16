from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.api.deps import get_cipher, get_scanner, get_storage
from app.db.session import get_db
from app.models import Document, DocumentFile, DocumentKind
from app.schemas.auth import MessageResponse
from app.schemas.documents import DocumentListResponse, DocumentResponse
from app.security.crypto import SensitiveDataCipher
from app.services.auth import AuthenticatedSession, get_current_auth, require_csrf
from app.services.documents import (
    get_document_file,
    get_owner_document,
    is_downloadable,
    list_documents,
    read_for_download,
    remove_blobs_after_revoke,
    revoke_document,
    store_document,
)
from app.storage.file_rules import content_disposition
from app.storage.local import LocalFileStorage
from app.storage.scanner import MalwareScanner

router = APIRouter(prefix="/documents", tags=["documents"])


def _serialize(document: Document, files: list[DocumentFile]) -> dict[str, object]:
    return {
        "id": document.id,
        "kind": document.kind,
        "status": document.status,
        "display_name": document.display_name,
        "created_at": document.created_at,
        "files": [
            {
                "id": item.id,
                "original_filename": item.original_filename,
                "content_type": item.content_type,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
                "scan_status": item.scan_status,
                "downloadable": is_downloadable(document, item),
            }
            for item in files
        ],
    }


def _files_for(db: DBSession, document: Document) -> list[DocumentFile]:
    return list(
        db.scalars(
            select(DocumentFile).where(DocumentFile.document_id == document.id).order_by(DocumentFile.created_at)
        )
    )


@router.get("", response_model=DocumentListResponse)
def list_owner_documents(
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict[str, list[dict[str, object]]]:
    return {
        "documents": [_serialize(document, files) for document, files in list_documents(db, auth.owner.id)]
    }


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def upload_document(
    request: Request,
    kind: DocumentKind = Form(...),
    file: UploadFile = File(...),
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
    scanner: MalwareScanner = Depends(get_scanner),
    storage: LocalFileStorage = Depends(get_storage),
    cipher: SensitiveDataCipher = Depends(get_cipher),
) -> dict[str, object]:
    document = store_document(
        db=db,
        request=request,
        owner=auth.owner,
        session_id=auth.session.id,
        kind=kind,
        upload=file,
        scanner=scanner,
        storage=storage,
        cipher=cipher,
    )
    return _serialize(document, _files_for(db, document))


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: UUID,
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
) -> dict[str, object]:
    document = get_owner_document(db, auth.owner.id, document_id)
    return _serialize(document, _files_for(db, document))


@router.get("/{document_id}/files/{file_id}")
def download_document(
    document_id: UUID,
    file_id: UUID,
    request: Request,
    auth: AuthenticatedSession = Depends(get_current_auth),
    db: DBSession = Depends(get_db),
    storage: LocalFileStorage = Depends(get_storage),
    cipher: SensitiveDataCipher = Depends(get_cipher),
) -> Response:
    document = get_owner_document(db, auth.owner.id, document_id)
    file = get_document_file(db, document, file_id)
    plaintext = read_for_download(
        db=db,
        request=request,
        owner_id=auth.owner.id,
        session_id=auth.session.id,
        document=document,
        file=file,
        storage=storage,
        cipher=cipher,
    )
    return Response(
        content=plaintext,
        media_type=file.content_type,
        headers={
            "Content-Disposition": content_disposition(file.original_filename),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{document_id}", response_model=MessageResponse)
def delete_document(
    document_id: UUID,
    request: Request,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: DBSession = Depends(get_db),
    storage: LocalFileStorage = Depends(get_storage),
) -> dict[str, str]:
    document = get_owner_document(db, auth.owner.id, document_id)
    keys = revoke_document(
        db=db, request=request, owner_id=auth.owner.id, session_id=auth.session.id, document=document
    )
    remove_blobs_after_revoke(storage, keys)
    return {"message": "Document removed."}
