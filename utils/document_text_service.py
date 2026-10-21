"""Shared page-aware effective text combining native PDF text with local OCR."""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from utils.database import get_connection
from utils.config import OCR_MIN_NATIVE_CHARS
from utils.documents import get_document


@dataclass(frozen=True)
class EffectivePage:
    page_number: int
    text: str
    source: str
    status: str


def get_effective_pages(document_id: str) -> list[EffectivePage]:
    document = get_document(document_id)
    if document is None:
        return []
    # Native extraction is already persisted in SQLite, so reading study text
    # never reopens or renders PDFs during ordinary Streamlit reruns.
    pages = [EffectivePage(int(number), text.strip(), "native", "native_text")
             for number, text in re.findall(r"---\s*Page\s+(\d+)\s*---\s*([\s\S]*?)(?=\n\n---\s*Page\s+\d+\s*---|\Z)", document["extracted_text"])]
    if not pages and document["extracted_text"].strip():
        pages = [EffectivePage(1, document["extracted_text"].strip(), "native", "native_text")]
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT page_number, status, recognized_text FROM document_ocr_pages WHERE document_id = ?",
            (document_id,),
        ).fetchall()
    ocr = {int(row["page_number"]): dict(row) for row in rows}
    effective = []
    max_page = max([int(document["page_count"]), *(page.page_number for page in pages)], default=0)
    native_by_number = {page.page_number: page for page in pages}
    for number in range(1, max_page + 1):
        native = native_by_number.get(number, EffectivePage(number, "", "none", "blank"))
        result = ocr.get(number)
        native_adequate = len(native.text.strip()) >= OCR_MIN_NATIVE_CHARS
        if native_adequate:
            effective.append(EffectivePage(number, native.text, "native", "native_text"))
        elif result and result["status"] == "complete" and result["recognized_text"].strip():
            effective.append(EffectivePage(number, result["recognized_text"], "ocr", "complete"))
        elif native.text.strip():
            effective.append(EffectivePage(number, native.text, "native", "not_needed"))
        else:
            effective.append(EffectivePage(number, "", "none", result["status"] if result else native.status))
    return effective


def format_effective_text(pages: list[EffectivePage]) -> str:
    return "\n\n".join(f"--- Page {page.page_number} ---\n{page.text.strip()}" for page in pages if page.text.strip())


def get_effective_document_text(document_id: str) -> str:
    return format_effective_text(get_effective_pages(document_id))


def get_effective_document(document_id: str) -> dict[str, Any] | None:
    document = get_document(document_id)
    if document is None:
        return None
    pages = get_effective_pages(document_id)
    effective_text = format_effective_text(pages)
    document["effective_pages"] = pages
    document["effective_text"] = effective_text
    document["text_ready"] = bool(effective_text.strip())
    document["effective_text_hash"] = hashlib.sha256(json.dumps(
        [(page.page_number, page.source, page.text) for page in pages], ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    with get_connection() as connection:
        statuses = connection.execute("SELECT status, COUNT(*) AS count FROM document_ocr_pages WHERE document_id=? GROUP BY status", (document_id,)).fetchall()
        index = connection.execute("SELECT status FROM document_index_status WHERE document_id=?", (document_id,)).fetchone()
    document["ocr_status_counts"] = {row["status"]: row["count"] for row in statuses}
    document["ocr_complete"] = bool(document["ocr_status_counts"].get("complete")) and not any(
        document["ocr_status_counts"].get(status, 0) for status in ("pending", "failed", "empty")
    )
    document["index_stale"] = bool(index and index["status"] in {"out_of_date", "failed"})
    return document
