"""General chat and retrieval-grounded study conversations."""

import sqlite3
import uuid

import streamlit as st

from components.document_picker import render_document_picker
from utils.config import EMBEDDING_MODEL_NAME
from utils.database import (
    create_conversation,
    delete_conversation,
    get_chat_messages,
    list_conversations,
    save_chat_exchange,
)
from utils.embedding_service import EmbeddingModelError
from utils.gemini_service import (
    GeminiConfigurationError,
    GeminiServiceError,
    GEMINI_API_MODE_STATE_KEY,
    get_gemini_service_for_session,
    is_gemini_available_for_session,
    is_gemini_configured,
    load_gemini_model,
)
from utils.indexing_service import IndexingError, get_document_index_statuses, index_document
from utils.retrieval_service import RetrievalError, RetrievalService, sanitize_source_citations


def _render_setup_help() -> None:
    if st.session_state.get(GEMINI_API_MODE_STATE_KEY, "application") == "personal":
        if is_gemini_configured():
            message = (
                "Personal-key mode is selected, but no personal key is active. Activate one in Settings, "
                "or switch to the application's configured keys."
            )
        else:
            message = "No Gemini key is active. Add and activate a personal key in Settings or configure application keys."
    else:
        message = (
            "Gemini is not configured yet. Open Settings to activate a personal API key, or configure "
            "`GEMINI_API_KEY_1` (or the legacy `GEMINI_API_KEY`) in your environment or Streamlit secrets. "
            "Your local search results remain available without a Gemini key."
        )
    st.info(message)


def _source_payload(passages) -> list[dict]:
    return [
        {
            "source_id": passage.source_id,
            "document_id": passage.document_id,
            "filename": passage.filename,
            "page_number": passage.page_number,
            "snippet": passage.text,
            "similarity": passage.similarity,
        }
        for passage in passages
    ]


def _render_sources(sources: list[dict], *, key: str) -> None:
    if not sources:
        return
    with st.expander(f"Sources used · {len(sources)} passage{'s' if len(sources) != 1 else ''}"):
        for source in sources:
            st.markdown(
                f"**{source['source_id']} · {source['filename']} · page {source['page_number']}**"
            )
            if isinstance(source.get("similarity"), (int, float)):
                st.caption(f"Cosine similarity: {source['similarity']:.3f} · Similarity is a ranking score, not a probability.")
            st.markdown(source.get("snippet", ""))
            st.divider()


def _index_selected_document(document_id: str, filename: str) -> None:
    progress = st.progress(0.0, text=f"Preparing {filename} for local search…")
    try:
        with st.spinner("The embedding model may download the first time you index a PDF. This runs locally on CPU."):
            result = index_document(document_id, progress_callback=lambda value: progress.progress(value))
        st.success(f"Indexed {result.chunk_count} passages from {filename}.")
        st.rerun()
    except IndexingError as error:
        st.error(str(error))
    finally:
        progress.empty()


def render_ai_chat() -> None:
    st.markdown('<div class="eyebrow">YOUR STUDY COMPANION</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">AI Chat</h1>', unsafe_allow_html=True)
    st.markdown(
        '<p class="page-subtitle">Ask a general study question or ground your chat in locally indexed PDF passages.</p>',
        unsafe_allow_html=True,
    )

    mode_labels = {
        "General study chat": "general",
        "Ask about one document": "document",
        "Ask across indexed documents": "all_documents",
    }
    chat_label = st.radio("Chat mode", tuple(mode_labels), horizontal=True, key="ai_chat_mode")
    chat_mode = mode_labels[chat_label]
    selected_document = None
    source_document_id = None
    status_by_id = {item["id"]: item for item in get_document_index_statuses()}
    if chat_mode == "document":
        selected_document = render_document_picker("Study document", key="chat")
        if selected_document is None:
            return
        source_document_id = selected_document["id"]
        status = status_by_id.get(source_document_id, {}).get("status", "not_indexed")
        if not selected_document.get("text_ready", selected_document["extraction_status"] == "ready") or status == "no_text":
            st.warning("This PDF has no usable native or OCR text for semantic indexing. Run local OCR from My Documents if scanned pages were detected.")
            return
        if status != "indexed":
            st.warning(f"This PDF is {status.replace('_', ' ')} and is not available for document chat yet.")
            if st.button("Index selected PDF locally", type="primary", key=f"index_chat_{source_document_id}"):
                _index_selected_document(source_document_id, selected_document["original_filename"])
            st.caption("Indexing uses the local CPU embedding model and does not require a Gemini API key.")
            return
    elif chat_mode == "all_documents":
        indexed_count = sum(item["status"] == "indexed" for item in status_by_id.values())
        if indexed_count == 0:
            st.info("No documents are indexed yet. Index PDFs in My Documents before asking across your library.")
            if st.button("Open My Documents", key="open_docs_for_chat"):
                st.session_state.active_page = "My Documents"
                st.rerun()
            return

    context_key = source_document_id or chat_mode
    selector_key = f"chat_conversation_{context_key}"
    try:
        conversations = list_conversations(source_document_id, chat_mode=chat_mode)
    except (OSError, sqlite3.Error):
        st.error("Your saved conversations could not be loaded. Check the local database and try again.")
        return
    conversations_by_id = {item["id"]: item for item in conversations}
    valid_ids = [None, *conversations_by_id.keys()]
    if selector_key in st.session_state and st.session_state[selector_key] not in valid_ids:
        del st.session_state[selector_key]
    pending_selection_key = f"chat_next_selection_{context_key}"
    if pending_selection_key in st.session_state:
        pending_selection = st.session_state.pop(pending_selection_key)
        st.session_state[selector_key] = pending_selection if pending_selection in valid_ids else None
    new_chat_confirmation_key = f"chat_new_confirmation_{context_key}"

    conversation_col, action_col = st.columns([5, 1])
    with action_col:
        if st.button("＋ New chat", key=f"new_chat_{context_key}", use_container_width=True):
            try:
                conversation_id = create_conversation(source_document_id, chat_mode=chat_mode)
                st.session_state[pending_selection_key] = conversation_id
                st.session_state[new_chat_confirmation_key] = conversation_id
                st.rerun()
            except (OSError, sqlite3.Error):
                st.error(
                    "A new conversation could not be created in the local database. "
                    "Check that the database folder is writable, then try again."
                )
    with conversation_col:
        selected_conversation_id = st.selectbox(
            "Conversation",
            options=valid_ids,
            format_func=lambda conversation_id: "New conversation" if conversation_id is None else conversations_by_id[conversation_id]["title"],
            key=selector_key,
        )

    messages = []
    if selected_conversation_id:
        try:
            messages = get_chat_messages(selected_conversation_id)
        except (OSError, sqlite3.Error):
            st.error("This conversation could not be loaded. Check the local database and try again.")
            return

    if not messages:
        st.markdown(
            '<div class="empty-state"><div class="empty-icon">✦</div><strong>What are you studying today?</strong>'
            '<p>Ask a question to start a conversation. Successful chats are saved on this device.</p></div>',
            unsafe_allow_html=True,
        )
        if st.session_state.get(new_chat_confirmation_key) == selected_conversation_id and selected_conversation_id:
            st.success("New chat started. Send a message to begin your conversation.")
    for index, message in enumerate(messages):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant":
                _render_sources(message.get("sources", []), key=f"{selected_conversation_id}_{index}")

    if selected_conversation_id:
        if st.button("Clear this conversation", key=f"clear_chat_{selected_conversation_id}"):
            st.session_state[f"confirm_clear_chat_{selected_conversation_id}"] = True
        if st.session_state.get(f"confirm_clear_chat_{selected_conversation_id}"):
            st.warning("This permanently deletes the selected conversation and its messages from this device.")
            confirm_col, cancel_col = st.columns(2)
            with confirm_col:
                if st.button("Confirm delete conversation", type="primary", key=f"confirm_delete_chat_{selected_conversation_id}"):
                    try:
                        delete_conversation(selected_conversation_id)
                        st.session_state[pending_selection_key] = None
                        st.session_state.pop(f"confirm_clear_chat_{selected_conversation_id}", None)
                        st.rerun()
                    except sqlite3.Error:
                        st.error("The conversation could not be deleted. Please try again.")
            with cancel_col:
                if st.button("Cancel", key=f"cancel_delete_chat_{selected_conversation_id}"):
                    st.session_state.pop(f"confirm_clear_chat_{selected_conversation_id}", None)
                    st.rerun()

    st.caption(
        f"Model: {load_gemini_model()} · Local embedding model: {EMBEDDING_MODEL_NAME} · "
        + ("Only retrieved excerpts are sent to Gemini when you submit a document question." if chat_mode != "general" else "General chat sends no document text.")
    )
    if chat_mode == "general" and not is_gemini_available_for_session():
        _render_setup_help()
        return
    if chat_mode != "general" and not is_gemini_available_for_session():
        st.info("Local retrieval works without a Gemini key. Add a key to receive an AI-generated answer after searching.")
    prompt = st.chat_input("Ask a study question…", max_chars=4_000)
    if not prompt:
        return

    history = [{"role": message["role"], "content": message["content"]} for message in messages]
    request_id = uuid.uuid4().hex
    try:
        retrieved = None
        if chat_mode != "general":
            with st.spinner("Searching the selected local document scope…"):
                retrieved = RetrievalService().search(prompt, document_id=source_document_id, top_k=5)
            if not retrieved.passages:
                st.info("StudyMate could not find relevant indexed passages. Rephrase the question or index/select another document.")
                return
            if not is_gemini_available_for_session():
                st.markdown("### Local search results")
                _render_sources(_source_payload(retrieved.passages), key=f"preview_{request_id}")
                _render_setup_help()
                return

        with st.spinner("Thinking through the retrieved study evidence…" if retrieved else "Thinking through your question…"):
            response = get_gemini_service_for_session().answer_chat(
                history,
                prompt,
                retrieved_context=retrieved.context if retrieved else None,
            )
        answer = sanitize_source_citations(response.text, retrieved.passages) if retrieved else response.text
        conversation_id = selected_conversation_id or create_conversation(source_document_id, chat_mode=chat_mode)
        sources = _source_payload(retrieved.passages) if retrieved else []
        saved = save_chat_exchange(conversation_id, prompt, answer, request_id, sources=sources)
        if not saved:
            st.rerun()
        st.session_state[pending_selection_key] = conversation_id
        st.rerun()
    except (GeminiConfigurationError, EmbeddingModelError) as error:
        if isinstance(error, GeminiConfigurationError):
            _render_setup_help()
        else:
            st.error(str(error))
    except (GeminiServiceError, RetrievalError, IndexingError) as error:
        st.error(str(error))
    except (OSError, sqlite3.Error):
        st.error("The answer could not be saved locally. Please check storage and try again.")
