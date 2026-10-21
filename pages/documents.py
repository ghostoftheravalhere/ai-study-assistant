"""PDF library upload, preview, and deletion interface."""

import streamlit as st

from utils.config import MAX_UPLOAD_SIZE_MB
from utils.documents import DocumentError, delete_document, get_document, list_documents, save_uploaded_pdf


_PREVIEW_CHAR_LIMIT = 24_000


def _format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def render_documents() -> None:
    """Render the user's persistent PDF library."""
    st.markdown('<div class="eyebrow">YOUR STUDY LIBRARY</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">My Documents</h1>', unsafe_allow_html=True)
    st.markdown(
        '<p class="page-subtitle">Upload class PDFs and keep their searchable text ready for future study tools.</p>',
        unsafe_allow_html=True,
    )

    notice = st.session_state.pop("document_notice", None)
    if notice:
        getattr(st, notice["kind"])(notice["message"])

    with st.container():
        st.markdown("### Add study documents")
        st.caption(f"PDF only · Up to {MAX_UPLOAD_SIZE_MB} MB per file · Select multiple files to upload a batch")
        uploaded_files = st.file_uploader(
            "Choose PDF files",
            type=["pdf"],
            accept_multiple_files=True,
            help=f"Each PDF must be no larger than {MAX_UPLOAD_SIZE_MB} MB.",
            key="study_document_uploads",
        )
        if uploaded_files and st.button("Upload selected PDFs", type="primary", key="upload_documents"):
            for uploaded_file in uploaded_files:
                with st.spinner(f"Reading {uploaded_file.name}…"):
                    try:
                        saved = save_uploaded_pdf(uploaded_file.name, uploaded_file.getvalue())
                        if saved["extraction_status"] == "no_text":
                            st.warning(
                                f"{uploaded_file.name} was stored, but no selectable text was found. "
                                "It may be scanned; OCR is not supported yet."
                            )
                        else:
                            st.success(f"Added {uploaded_file.name} ({saved['page_count']} pages).")
                    except DocumentError as error:
                        st.error(f"Could not add {uploaded_file.name}: {error}")

    toolbar_left, toolbar_right = st.columns([5, 1])
    with toolbar_left:
        st.markdown("### Document library")
    with toolbar_right:
        if st.button("↻ Refresh", key="refresh_documents", use_container_width=True):
            st.rerun()

    try:
        documents = list_documents()
    except DocumentError as error:
        st.error(str(error))
        return

    if not documents:
        st.markdown(
            '<div class="empty-state"><div class="empty-icon">▤</div><strong>No documents yet</strong>'
            '<p>Upload a PDF above to start building your personal study library.</p></div>',
            unsafe_allow_html=True,
        )
        return

    st.caption(f"{len(documents)} document{'s' if len(documents) != 1 else ''} in your library")
    for document in documents:
        with st.expander(f"▤  {document['original_filename']}", expanded=False):
            metadata_columns = st.columns(4)
            metadata = [
                ("File size", _format_file_size(document["file_size_bytes"])),
                ("Pages", str(document["page_count"])),
                ("Uploaded", document["uploaded_at"]),
                ("Text extraction", "Ready" if document["extraction_status"] == "ready" else "No selectable text"),
            ]
            for column, (label, value) in zip(metadata_columns, metadata):
                with column:
                    st.caption(label)
                    st.write(value)

            if document["extraction_status"] == "no_text":
                st.info("No selectable text was found. This may be a scanned PDF. OCR is not supported in this phase.")
            else:
                try:
                    full_document = get_document(document["id"])
                except DocumentError as error:
                    st.error(str(error))
                    full_document = None
                if full_document:
                    extracted_text = full_document["extracted_text"]
                    preview_text = extracted_text[:_PREVIEW_CHAR_LIMIT]
                    show_full = st.session_state.get(f"full_text_{document['id']}", False)
                    visible_text = extracted_text if show_full else preview_text
                    st.text_area(
                        "Extracted text preview",
                        value=visible_text,
                        height=260,
                        disabled=True,
                        key=f"text_preview_{document['id']}",
                    )
                    if len(extracted_text) > _PREVIEW_CHAR_LIMIT and not show_full:
                        if st.button("Load full extracted text", key=f"load_full_{document['id']}"):
                            st.session_state[f"full_text_{document['id']}"] = True
                            st.rerun()
                    elif show_full and len(extracted_text) > _PREVIEW_CHAR_LIMIT:
                        st.caption("Showing the full extracted text. Large documents may take longer to render.")

            if st.checkbox("I understand this permanently deletes this document", key=f"confirm_delete_{document['id']}"):
                if st.button("Delete document", key=f"delete_{document['id']}", type="secondary"):
                    try:
                        delete_document(document["id"])
                        st.session_state["document_notice"] = {
                            "kind": "success",
                            "message": f"Deleted {document['original_filename']}.",
                        }
                    except DocumentError as error:
                        st.session_state["document_notice"] = {"kind": "error", "message": str(error)}
                    st.rerun()
