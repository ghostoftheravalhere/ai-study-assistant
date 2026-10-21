"""Local semantic search page that does not call Gemini."""

import streamlit as st

from utils.indexing_service import get_document_index_statuses
from utils.retrieval_service import RetrievalError, RetrievalService


def render_document_search() -> None:
    st.markdown('<div class="eyebrow">SEARCH YOUR STUDY LIBRARY</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">Semantic Document Search</h1>', unsafe_allow_html=True)
    st.markdown(
        '<p class="page-subtitle">Search indexed PDF passages with a local CPU embedding model and FAISS.</p>',
        unsafe_allow_html=True,
    )
    documents = get_document_index_statuses()
    indexed = [item for item in documents if item["status"] == "indexed"]
    if not indexed:
        st.info("No PDFs are indexed yet. Open My Documents and choose Index documents. The model downloads on the first indexing run.")
        if st.button("Open My Documents", key="search_open_documents"):
            st.session_state.active_page = "My Documents"
            st.rerun()
        return

    document_ids = [item["id"] for item in indexed]
    by_id = {item["id"]: item for item in indexed}
    with st.form("semantic_document_search"):
        question = st.text_input("What would you like to find?", max_chars=2_000)
        scope_label = st.selectbox(
            "Search scope",
            ("All indexed documents", "One document"),
        )
        selected_id = None
        if scope_label == "One document":
            selected_id = st.selectbox(
                "Document",
                options=document_ids,
                format_func=lambda document_id: by_id[document_id]["original_filename"],
            )
        top_k = st.slider("Number of passages", min_value=1, max_value=10, value=5)
        submitted = st.form_submit_button("Search locally", type="primary")

    if submitted:
        try:
            with st.spinner("Embedding your query locally and searching FAISS…"):
                result = RetrievalService().search(question, document_id=selected_id, top_k=top_k)
            st.session_state["document_search_result"] = {
                "question": question.strip(),
                "document_id": selected_id,
                "top_k": top_k,
                "passages": result.passages,
            }
        except (RetrievalError, ValueError) as error:
            st.error(str(error))
            st.session_state.pop("document_search_result", None)

    saved = st.session_state.get("document_search_result")
    if not saved:
        st.caption("Search and embeddings run locally. No Gemini API key is needed.")
        return
    if saved["document_id"] not in (None, *document_ids):
        st.session_state.pop("document_search_result", None)
        return
    st.markdown("### Search results")
    st.caption(f"Query: {saved['question']}")
    if not saved["passages"]:
        st.info("No relevant passages were found in this scope. Try another wording or search scope.")
        return
    for passage in saved["passages"]:
        with st.container():
            st.markdown(f"**{passage.filename} · page {passage.page_number} · {passage.source_id}**")
            st.caption(f"Cosine similarity: {passage.similarity:.3f} · Ranking score, not a probability.")
            st.markdown(passage.text)
            st.divider()
    st.caption("Only these retrieved passages are supplied to Gemini for document-grounded chat.")
