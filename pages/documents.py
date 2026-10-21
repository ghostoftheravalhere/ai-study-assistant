"""PDF library upload, preview, and deletion interface."""

import streamlit as st

from utils.config import MAX_UPLOAD_SIZE_MB, OCR_LANGUAGE
from utils.documents import DocumentError, assess_stored_document_pages, delete_document, get_ocr_page_status, list_documents, save_uploaded_pdf
from utils.document_text_service import get_effective_document
from utils.indexing_service import (
    IndexingError,
    get_document_index_statuses,
    index_document,
    index_pending_documents,
    rebuild_faiss_index,
)
from utils.ocr_service import OCRError, run_document_ocr


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
        '<p class="page-subtitle">Upload PDFs, recognize scanned pages locally, and prepare text for study tools.</p>',
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
                        detected = get_ocr_page_status(saved["id"])
                        if detected.get("pending", 0):
                            st.info(f"Added {uploaded_file.name} ({saved['page_count']} pages). {detected['pending']} page(s) appear image-based; run local OCR from the document card when ready.")
                        elif saved["extraction_status"] == "no_text":
                            st.warning(
                                f"{uploaded_file.name} was stored, but no selectable text was found. "
                                "Scanned pages can be recognized locally from the document card below."
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

    with st.expander("About local OCR and Windows setup"):
        st.write("OCR renders selected pages on this computer using Tesseract. It does not require Gemini or send pages to a cloud OCR service. Later Gemini tools may send relevant study text or retrieved passages to Google.")
        st.markdown("1. Install [Tesseract for Windows from UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki), including English or other desired language data.\n2. If `tesseract.exe` is not on PATH, set `STUDY_ASSISTANT_TESSERACT_CMD` to its full path, for example `C:\\Program Files\\Tesseract-OCR\\tesseract.exe`, then restart StudyMate.\n3. Set `STUDY_ASSISTANT_OCR_LANG` to codes such as `eng` or `eng+fra`. Install every corresponding `.traineddata` language pack. See the [Tesseract installation guide](https://tesseract-ocr.github.io/tessdoc/Installation.html).\n4. Select **Run local OCR** on a document, then choose **Index / retry** or **Reindex** to add recognized pages to semantic search.")
        st.warning("OCR can misread similar characters, formulas, math notation, tables, handwriting, rotated pages, and poor scans. Verify recognized text against the original PDF.")

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
            try:
                ocr_status = assess_stored_document_pages(document["id"])
            except DocumentError as error:
                ocr_status = {}
                st.warning(str(error))
            pending_ocr = ocr_status.get("pending", 0) + ocr_status.get("failed", 0) + ocr_status.get("empty", 0)
            complete_ocr = ocr_status.get("complete", 0)
            if complete_ocr:
                st.write(f"**OCR:** {complete_ocr} page(s) recognized" + (f" · {pending_ocr} page(s) still need attention" if pending_ocr else " · complete"))
            elif pending_ocr:
                st.write(f"**Extraction:** {pending_ocr} page(s) likely need local OCR")
            elif document["extraction_status"] == "ready":
                st.write("**Extraction:** native PDF text ready")
            selected_language = st.text_input(
                "OCR language code(s)", value=OCR_LANGUAGE, key=f"ocr_language_{document['id']}",
                help="Use installed Tesseract codes, for example eng or eng+fra. Changing language or OCR resolution reprocesses completed pages when requested.",
            ) if pending_ocr or complete_ocr else OCR_LANGUAGE
            if pending_ocr or complete_ocr:
                st.caption("OCR runs locally only after you choose this action. Failed pages can be retried; successful page text is preserved.")
                label = f"Run local OCR · {pending_ocr} page(s)" if pending_ocr else "Reprocess OCR if language changed"
                if st.button(label, key=f"run_ocr_{document['id']}", type="secondary"):
                    progress = st.progress(0.0, text=f"Preparing local OCR for {document['original_filename']}…")
                    notice = None
                    try:
                        with st.spinner("Recognizing selected PDF pages locally…"):
                            result = run_document_ocr(
                                document["id"],
                                language=selected_language,
                                progress_callback=lambda done, total: progress.progress(done / max(total, 1), text=f"OCR page {done} of {total}…"),
                            )
                        if result.failed or result.empty:
                            message = f"OCR processed {result.processed} page(s): {result.succeeded} recognized, {result.empty} empty, {result.failed} failed. Completed page text was saved. " + " ".join(result.errors[:3])
                            notice = {"kind": "warning", "message": message}
                        elif result.processed:
                            notice = {"kind": "success", "message": f"OCR recognized text on all {result.succeeded} processed page(s)."}
                        else:
                            notice = {"kind": "info", "message": "No pending pages needed OCR."}
                        if result.remaining:
                            notice["message"] += f" {result.remaining} page(s) still need OCR; retry is available."
                    except OCRError as error:
                        st.error(str(error))
                    finally:
                        progress.empty()
                    if notice:
                        st.session_state[f"ocr_notice_{document['id']}"] = notice
                        st.rerun()

            notice = st.session_state.pop(f"ocr_notice_{document['id']}", None)
            if notice:
                getattr(st, notice["kind"])(notice["message"])

            if document["extraction_status"] == "ready" or complete_ocr:
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
                ("Text extraction", "Native + OCR" if document["extraction_status"] == "ready" and complete_ocr else "OCR ready" if complete_ocr else "Native text" if document["extraction_status"] == "ready" else "OCR needed" if pending_ocr else "No usable text"),
            ]
            for column, (label, value) in zip(metadata_columns, metadata):
                with column:
                    st.caption(label)
                    st.write(value)

            try:
                full_document = get_effective_document(document["id"])
            except (DocumentError, OSError):
                st.error("The recognized document text could not be loaded safely.")
                full_document = None
            if full_document and full_document["text_ready"]:
                blocks = [f"--- Page {page.page_number} · {'OCR' if page.source == 'ocr' else 'native text'} ---\n{page.text}" for page in full_document["effective_pages"] if page.text.strip()]
                extracted_text = "\n\n".join(blocks)
                preview_text = extracted_text[:_PREVIEW_CHAR_LIMIT]
                show_full = st.session_state.get(f"full_text_{document['id']}", False)
                visible_text = extracted_text if show_full else preview_text
                st.text_area("Effective text preview · OCR pages are labeled", value=visible_text, height=260, disabled=True, key=f"text_preview_{document['id']}")
                if len(extracted_text) > _PREVIEW_CHAR_LIMIT and not show_full:
                    if st.button("Load full extracted text", key=f"load_full_{document['id']}"):
                        st.session_state[f"full_text_{document['id']}"] = True
                        st.rerun()
            elif full_document:
                st.info("No usable text is available yet. The PDF may be blank or contain only small images that were not classified as a scan.")

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
