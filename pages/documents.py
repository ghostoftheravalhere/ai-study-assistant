"""PDF library upload, preview, and deletion interface."""

import streamlit as st

from utils.config import MAX_UPLOAD_SIZE_MB
from utils.documents import DocumentError, delete_document, get_document, list_documents, save_uploaded_pdf
from utils.indexing_service import (
    IndexingError,
    get_document_index_statuses,
    index_document,
    index_pending_documents,
    rebuild_faiss_index,
)


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

    index_statuses = get_document_index_statuses()
    index_by_id = {item["id"]: item for item in index_statuses}
    status_counts = {name: sum(item["status"] == name for item in index_statuses) for name in (
        "indexed", "not_indexed", "out_of_date", "failed", "missing_source", "no_text"
    )}
    st.caption(
        f"Semantic index: {status_counts['indexed']} indexed · "
        f"{status_counts['not_indexed']} awaiting indexing · "
        f"{status_counts['out_of_date'] + status_counts['failed']} requiring reindexing"
    )
    st.caption("Indexing uses local CPU embeddings and does not require a Gemini key. The embedding model may download the first time you index.")
    eligible_count = status_counts["not_indexed"] + status_counts["out_of_date"] + status_counts["failed"]
    index_col, rebuild_col = st.columns(2)
    with index_col:
        if st.button("Index documents" if eligible_count else "All eligible documents indexed", disabled=eligible_count == 0, type="primary", key="index_all_documents"):
            progress = st.progress(0.0, text="Preparing local document indexing…")
            filename_by_id = {item["id"]: item["original_filename"] for item in documents}
            try:
                with st.spinner("Embedding PDF passages locally on CPU…"):
                    results = index_pending_documents(
                        progress_callback=lambda document_id, value: progress.progress(
                            value,
                            text=f"Indexing {filename_by_id.get(document_id, 'document')}…",
                        )
                    )
                for result in results:
                    if result.status == "indexed":
                        st.success(f"Indexed {result.filename}: {result.chunk_count} passages.")
                    else:
                        st.error(result.message)
                index_statuses = get_document_index_statuses()
                index_by_id = {item["id"]: item for item in index_statuses}
            except (IndexingError, OSError) as error:
                st.error(str(error))
            finally:
                progress.empty()
    with rebuild_col:
        if st.button("Rebuild FAISS cache", disabled=status_counts["indexed"] == 0, key="rebuild_faiss_cache"):
            try:
                with st.spinner("Rebuilding the local FAISS cache from SQLite vectors…"):
                    count = rebuild_faiss_index()
                st.success(f"Rebuilt the FAISS cache with {count} vectors.")
            except IndexingError as error:
                st.error(str(error))

    st.caption(f"{len(documents)} document{'s' if len(documents) != 1 else ''} in your library")
    for document in documents:
        with st.expander(f"▤  {document['original_filename']}", expanded=False):
            index_status = index_by_id.get(document["id"], {}).get("status", "not_indexed")
            st.write(f"**Semantic index:** {index_status.replace('_', ' ').title()}")
            last_error = index_by_id.get(document["id"], {}).get("error_message")
            if last_error:
                st.caption(f"Last indexing issue: {last_error}")
            if document["extraction_status"] == "ready":
                action = "Reindex" if index_status == "indexed" else "Index / retry"
                if st.button(action, key=f"index_document_{document['id']}"):
                    progress = st.progress(0.0, text=f"Preparing {document['original_filename']}…")
                    try:
                        with st.spinner("The first index may download the embedding model; indexing uses local CPU."):
                            result = index_document(
                                document["id"],
                                force=True,
                                progress_callback=lambda value: progress.progress(value),
                            )
                        st.success(f"Indexed {result.chunk_count} passages.")
                        updated_statuses = get_document_index_statuses()
                        index_by_id[document["id"]] = next(
                            item for item in updated_statuses if item["id"] == document["id"]
                        )
                    except (IndexingError, OSError) as error:
                        st.error(str(error))
                    finally:
                        progress.empty()
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
