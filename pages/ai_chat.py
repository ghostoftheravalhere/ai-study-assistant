"""General and document-grounded study chat."""

import sqlite3
import uuid

import streamlit as st

from components.document_picker import render_document_picker
from utils.config import MAX_DOCUMENT_CONTEXT_CHARS
from utils.database import create_conversation, delete_conversation, get_chat_messages, list_conversations, save_chat_exchange
from utils.gemini_service import (
    GeminiConfigurationError,
    GeminiService,
    GeminiServiceError,
    is_gemini_configured,
    load_gemini_model,
)


def _render_setup_help() -> None:
    st.info(
        "Gemini is not configured yet. Add `GEMINI_API_KEY_1` (or the legacy `GEMINI_API_KEY`) to your environment or to "
        "`.streamlit/secrets.toml` to send AI requests. The key is never shown here."
    )


def render_ai_chat() -> None:
    st.markdown('<div class="eyebrow">YOUR STUDY COMPANION</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">AI Chat</h1>', unsafe_allow_html=True)
    st.markdown(
        '<p class="page-subtitle">Ask general study questions or focus on one PDF in your library.</p>',
        unsafe_allow_html=True,
    )

    chat_mode = st.radio(
        "Chat mode",
        ("General study chat", "Ask about a document"),
        horizontal=True,
        key="ai_chat_mode",
    )
    selected_document = None
    source_document_id = None
    if chat_mode == "Ask about a document":
        selected_document = render_document_picker("Study document", key="chat")
        if selected_document is None:
            return
        source_document_id = selected_document["id"]
        if selected_document["extraction_status"] != "ready" or not selected_document["extracted_text"].strip():
            st.warning("This PDF has no selectable text. Choose a text-based PDF; OCR is not available yet.")
            return

    context_key = source_document_id or "general"
    selector_key = f"chat_conversation_{context_key}"
    try:
        conversations = list_conversations(source_document_id)
    except (OSError, sqlite3.Error):
        st.error("Your saved conversations could not be loaded. Check the local database and try again.")
        return
    conversations_by_id = {item["id"]: item for item in conversations}
    valid_ids = [None, *conversations_by_id.keys()]
    if selector_key in st.session_state and st.session_state[selector_key] not in valid_ids:
        del st.session_state[selector_key]
    pending_selection_key = f"chat_next_selection_{context_key}"
    if pending_selection_key in st.session_state:
        st.session_state[selector_key] = st.session_state.pop(pending_selection_key)

    conversation_col, action_col = st.columns([5, 1])
    with action_col:
        if st.button("＋ New chat", key=f"new_chat_{context_key}", use_container_width=True):
            st.session_state[selector_key] = None
            st.rerun()
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

    context_notice = st.session_state.get(f"chat_context_notice_{selected_conversation_id}") if selected_conversation_id else None
    if context_notice:
        st.warning(context_notice)
    if not messages:
        st.markdown(
            '<div class="empty-state"><div class="empty-icon">✦</div><strong>What are you studying today?</strong>'
            '<p>Ask a question to start a conversation. Successful chats are saved on this device.</p></div>',
            unsafe_allow_html=True,
        )
    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

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
        f"Model: {load_gemini_model()} · When sent, your message"
        + (" and the selected PDF's extracted text" if selected_document else "")
        + " are sent to Google Gemini for processing."
    )
    if not is_gemini_configured():
        _render_setup_help()
        return
    prompt = st.chat_input("Ask a study question…", max_chars=4_000)
    if not prompt:
        return

    history = [{"role": message["role"], "content": message["content"]} for message in messages]
    document_text = selected_document["extracted_text"] if selected_document else None
    request_id = uuid.uuid4().hex
    try:
        with st.spinner("Thinking through your question…"):
            result = GeminiService().answer_chat(history, prompt, document_text=document_text)
        conversation_id = selected_conversation_id or create_conversation(source_document_id)
        saved = save_chat_exchange(conversation_id, prompt, result.text, request_id)
        if not saved:
            st.rerun()
        st.session_state[pending_selection_key] = conversation_id
        if result.context_truncated:
            st.session_state[f"chat_context_notice_{conversation_id}"] = (
                f"For the latest answer, only the first {min(result.original_context_length, MAX_DOCUMENT_CONTEXT_CHARS):,} "
                f"of {result.original_context_length:,} extracted characters were supplied "
                "due to the context limit. This is direct truncation, not semantic search."
            )
        st.rerun()
    except GeminiConfigurationError:
        _render_setup_help()
    except GeminiServiceError as error:
        st.error(str(error))
    except (OSError, sqlite3.Error):
        st.error("Gemini replied, but the conversation could not be saved locally. Please retry after checking storage.")
