"""Shared selector for choosing one stored study document."""

import streamlit as st

from utils.document_text_service import get_effective_document
from utils.documents import DocumentError, list_documents


def render_document_picker(label: str, *, key: str) -> dict | None:
    """Select a stored document and return its extracted text and metadata."""
    try:
        documents = list_documents()
    except DocumentError as error:
        st.error(str(error))
        return None
    if not documents:
        st.info("Upload a PDF in My Documents before using this document-based tool.")
        if st.button("Open My Documents", key=f"{key}_open_library"):
            st.session_state.active_page = "My Documents"
            st.rerun()
        return None

    document_ids = [item["id"] for item in documents]
    state_key = f"{key}_document_id"
    if state_key in st.session_state and st.session_state[state_key] not in document_ids:
        del st.session_state[state_key]
    metadata_by_id = {item["id"]: item for item in documents}
    selected_id = st.selectbox(
        label,
        options=document_ids,
        format_func=lambda document_id: metadata_by_id[document_id]["original_filename"],
        key=state_key,
    )
    try:
        document = get_effective_document(selected_id)
    except DocumentError as error:
        st.error(str(error))
        return None
    if document is None:
        st.warning("That document is no longer in your library. Refresh and choose another document.")
        return None
    sources = {page.source for page in document["effective_pages"] if page.text.strip()}
    source_label = "OCR and native text" if sources == {"native", "ocr"} else "OCR text" if sources == {"ocr"} else "Native text" if "native" in sources else "No usable text"
    pending_ocr = sum(document["ocr_status_counts"].get(status, 0) for status in ("pending", "failed", "empty"))
    st.caption(f"{document['page_count']} pages · {document['uploaded_at']} · {source_label}" + (f" · {pending_ocr} page(s) still need OCR" if pending_ocr else ""))
    if "ocr" in sources:
        st.caption("Recognized text may contain OCR errors. Verify formulas, tables, handwriting, and low-quality scans against the PDF.")
    if not document["text_ready"]:
        st.warning("No usable native or recognized text is available. Run local OCR from My Documents if scanned pages were detected.")
    document["extracted_text"] = document["effective_text"]
    return document
