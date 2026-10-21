"""Shared selector for choosing one stored study document."""

import streamlit as st

from utils.documents import DocumentError, get_document, list_documents


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
        document = get_document(selected_id)
    except DocumentError as error:
        st.error(str(error))
        return None
    if document is None:
        st.warning("That document is no longer in your library. Refresh and choose another document.")
        return None
    st.caption(
        f"{document['page_count']} pages · {document['uploaded_at']} · "
        f"{'Text ready' if document['extraction_status'] == 'ready' else 'No selectable text'}"
    )
    return document
