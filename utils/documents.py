"""Document storage and persistence operations."""

import hashlib
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.config import DOCUMENTS_DIR, MAX_UPLOAD_SIZE_BYTES, MAX_UPLOAD_SIZE_MB
from utils.database import get_connection
from utils.pdf_processing import PDFProcessingError, extract_pdf_text


class DocumentError(ValueError):
    """Raised for expected document upload and storage errors."""


class DuplicateDocumentError(DocumentError):
    """Raised when the exact PDF contents are already in the library."""


def _stored_path(stored_filename: str) -> Path:
    """Resolve only a plain generated filename inside the document directory."""
    if Path(stored_filename).name != stored_filename or not stored_filename.endswith(".pdf"):
        raise DocumentError("The stored document reference is invalid.")
    path = DOCUMENTS_DIR / stored_filename
    if path.parent.resolve() != DOCUMENTS_DIR.resolve():
        raise DocumentError("The stored document reference is invalid.")
    return path


def resolve_stored_document_path(stored_filename: str) -> Path:
    """Return a validated path for an app-generated stored PDF filename."""
    return _stored_path(stored_filename)


def _discard_partial_file(path: Path) -> None:
    """Best-effort cleanup that does not hide the original save failure."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def list_documents() -> list[dict[str, Any]]:
    """List document metadata, newest first, without exposing storage paths."""
    try:
        with get_connection() as connection:
            rows = connection.execute(
                """SELECT id, original_filename, file_size_bytes, page_count, uploaded_at,
                          extraction_status
                   FROM documents ORDER BY uploaded_at DESC, original_filename COLLATE NOCASE"""
            ).fetchall()
    except sqlite3.Error as error:
        raise DocumentError("The document library could not be loaded. Check the local database and try again.") from error
    return [dict(row) for row in rows]


def get_document(document_id: str) -> dict[str, Any] | None:
    """Fetch one document and its extracted text by its opaque ID."""
    try:
        with get_connection() as connection:
            row = connection.execute(
                """SELECT id, original_filename, stored_filename, file_size_bytes, page_count,
                          uploaded_at, extraction_status, extracted_text
                   FROM documents WHERE id = ?""",
                (document_id,),
            ).fetchone()
    except sqlite3.Error as error:
        raise DocumentError("The selected document could not be loaded. Check the local database and try again.") from error
    return dict(row) if row else None


def save_uploaded_pdf(original_filename: str, file_bytes: bytes) -> dict[str, Any]:
    """Validate, extract, store, and persist an uploaded PDF."""
    if not original_filename.lower().endswith(".pdf"):
        raise DocumentError("Only PDF files can be uploaded.")
    if not file_bytes:
        raise DocumentError("The selected file is empty.")
    if len(file_bytes) > MAX_UPLOAD_SIZE_BYTES:
        raise DocumentError(f"This file exceeds the {MAX_UPLOAD_SIZE_MB} MB upload limit.")

    try:
        extraction = extract_pdf_text(file_bytes)
    except PDFProcessingError as error:
        raise DocumentError(str(error)) from error

    content_hash = hashlib.sha256(file_bytes).hexdigest()
    try:
        with get_connection() as connection:
            duplicate = connection.execute(
                "SELECT original_filename FROM documents WHERE content_sha256 = ?", (content_hash,)
            ).fetchone()
    except sqlite3.Error as error:
        raise DocumentError("The document library is unavailable. Please try again later.") from error
    if duplicate:
        raise DuplicateDocumentError(f"This PDF is already stored as “{duplicate['original_filename']}”.")

    document_id = uuid.uuid4().hex
    stored_filename = f"{uuid.uuid4().hex}.pdf"
    stored_path = _stored_path(stored_filename)
    try:
        DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
        with stored_path.open("xb") as stored_file:
            stored_file.write(file_bytes)
        with get_connection() as connection:
            connection.execute(
                """INSERT INTO documents (
                    id, original_filename, stored_filename, file_size_bytes, page_count,
                    uploaded_at, extraction_status, extracted_text, content_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    document_id,
                    Path(original_filename.replace("\\", "/")).name,
                    stored_filename,
                    len(file_bytes),
                    extraction.page_count,
                    datetime.now().astimezone().isoformat(timespec="minutes"),
                    extraction.extraction_status,
                    extraction.extracted_text,
                    content_hash,
                ),
            )
    except sqlite3.IntegrityError as error:
        _discard_partial_file(stored_path)
        if "content_sha256" in str(error) or "UNIQUE constraint failed: documents.content_sha256" in str(error):
            raise DuplicateDocumentError("This PDF is already in your library.") from error
        raise DocumentError("The document could not be added to the library.") from error
    except (OSError, sqlite3.Error) as error:
        _discard_partial_file(stored_path)
        raise DocumentError("The document could not be saved. Check the available storage and try again.") from error

    return get_document(document_id) or {}


def delete_document(document_id: str) -> bool:
    """Delete one document and its file, restoring the file if DB removal fails."""
    document = get_document(document_id)
    if document is None:
        return False

    stored_path = _stored_path(document["stored_filename"])
    tombstone = stored_path.with_name(f".{stored_path.stem}.{uuid.uuid4().hex}.deleting")
    moved_file = False
    try:
        if stored_path.exists():
            os.replace(stored_path, tombstone)
            moved_file = True
        with get_connection() as connection:
            cursor = connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            if cursor.rowcount != 1:
                raise DocumentError("The selected document could not be found.")
    except (OSError, sqlite3.Error, DocumentError) as error:
        if moved_file:
            try:
                os.replace(tombstone, stored_path)
            except OSError as restore_error:
                raise DocumentError("The document could not be deleted or restored. Contact support before retrying.") from restore_error
        if isinstance(error, DocumentError):
            raise
        raise DocumentError("The document could not be deleted. Please try again.") from error

    if moved_file:
        try:
            tombstone.unlink(missing_ok=True)
        except OSError:
            # The record and its public storage path are gone; this rare cleanup
            # failure does not risk deleting another user's document.
            pass
    return True
