"""Local application settings page."""

import streamlit as st

from utils.config import EMBEDDING_MODEL_NAME, INDEX_DIR
from utils.gemini_service import (
    GEMINI_API_MODE_STATE_KEY,
    PERSONAL_GEMINI_API_KEY_STATE_KEY,
    GeminiServiceError,
    get_gemini_service_for_session,
    is_gemini_available_for_session,
    is_gemini_configured,
    load_gemini_model,
)


_PERSONAL_KEY_INPUT = "personal_gemini_api_key_input"
_PERSONAL_FEEDBACK = "personal_gemini_key_feedback"
_TEST_STATUS_PREFIX = "gemini_connection_test_"
_TEST_ERROR_PREFIX = "gemini_connection_test_error_"


def _activate_personal_key() -> None:
    candidate = str(st.session_state.get(_PERSONAL_KEY_INPUT, "") or "").strip()
    st.session_state[_PERSONAL_KEY_INPUT] = ""
    if not candidate:
        st.session_state[_PERSONAL_FEEDBACK] = "Enter a Gemini API key before activating personal-key mode."
        return
    st.session_state[PERSONAL_GEMINI_API_KEY_STATE_KEY] = candidate
    st.session_state[GEMINI_API_MODE_STATE_KEY] = "personal"
    st.session_state[f"{_TEST_STATUS_PREFIX}personal"] = "untested"
    st.session_state.pop(f"{_TEST_ERROR_PREFIX}personal", None)
    st.session_state[_PERSONAL_FEEDBACK] = "Your key is active for this session. Test the connection to verify it."


def _clear_personal_key() -> None:
    st.session_state.pop(PERSONAL_GEMINI_API_KEY_STATE_KEY, None)
    st.session_state.pop(_PERSONAL_KEY_INPUT, None)
    st.session_state[GEMINI_API_MODE_STATE_KEY] = "application"
    st.session_state.pop(f"{_TEST_STATUS_PREFIX}personal", None)
    st.session_state.pop(f"{_TEST_ERROR_PREFIX}personal", None)
    st.session_state[_PERSONAL_FEEDBACK] = "The personal key was cleared from this session."


def render_settings() -> None:
    st.markdown('<div class="eyebrow">PREFERENCES</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">Settings</h1>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">A few details about this StudyMate workspace.</p>', unsafe_allow_html=True)
    st.markdown("### Application")
    st.write("**Name:** AI Study Assistant")
    st.write("**Storage:** Local SQLite database (`study_assistant.db`)")
    st.write(f"**Gemini model:** `{load_gemini_model()}`")
    st.write(f"**Gemini API key:** {'Configured' if is_gemini_configured() else 'Not configured'}")
    st.write(f"**Local embedding model:** `{EMBEDDING_MODEL_NAME}`")
    st.write(f"**FAISS cache:** `{INDEX_DIR}` (rebuildable; vectors are stored in SQLite)")
    st.caption("Set GEMINI_API_KEY_1 through GEMINI_API_KEY_4 and optionally GEMINI_MODEL in the environment or Streamlit secrets. The legacy GEMINI_API_KEY remains supported. Keys are never shown on this page.")

    st.markdown("### API Settings")
    st.write("**Provider:** Google Gemini")
    st.caption(
        "Use keys configured by this application, or activate your own Gemini API key for this session. "
        "Your personal key is sent to this application's server to authenticate requests to Google Gemini. "
        "Only enter a key in deployments you trust."
    )
    if GEMINI_API_MODE_STATE_KEY not in st.session_state:
        st.session_state[GEMINI_API_MODE_STATE_KEY] = "application"
    st.radio(
        "API usage mode",
        options=("application", "personal"),
        format_func=lambda value: (
            "Use the application's configured Gemini API keys" if value == "application"
            else "Use my own Gemini API key"
        ),
        key=GEMINI_API_MODE_STATE_KEY,
    )
    mode = st.session_state[GEMINI_API_MODE_STATE_KEY]
    if mode == "personal":
        st.text_input(
            "Your Gemini API Key",
            type="password",
            key=_PERSONAL_KEY_INPUT,
            help="This key stays in this active Streamlit session and is not saved in SQLite or local configuration files.",
        )
        st.button("Activate personal key", on_click=_activate_personal_key, type="primary", key="activate_personal_gemini_key")
    else:
        st.caption("Application mode preserves the configured environment/Streamlit secrets keys and their existing failover order.")

    feedback = st.session_state.pop(_PERSONAL_FEEDBACK, None)
    if feedback:
        st.info(feedback)

    personal_active = bool(str(st.session_state.get(PERSONAL_GEMINI_API_KEY_STATE_KEY, "") or "").strip())
    application_configured = is_gemini_configured()
    selected_key_available = is_gemini_available_for_session()
    mode_name = "personal" if mode == "personal" else "application"
    test_status = st.session_state.get(f"{_TEST_STATUS_PREFIX}{mode_name}")
    if mode == "personal" and personal_active:
        if test_status == "success":
            st.success("Personal API key connection tested successfully for this session.")
        elif test_status == "failed":
            st.error("Connection test failed; check your personal key or model access, then test again or clear the key.")
            safe_error = st.session_state.get(f"{_TEST_ERROR_PREFIX}{mode_name}")
            if safe_error:
                st.caption(safe_error)
        else:
            st.info("Personal API key active for this session. Its connection has not been tested yet.")
    elif mode == "personal":
        if not application_configured:
            st.warning("No API key configured. Enter and activate a personal key to use Gemini features.")
        else:
            st.warning("Personal mode is selected, but no personal key is active. Application keys will not be used in this mode.")
    elif application_configured:
        if test_status == "success":
            st.success("Connection to Gemini tested successfully with the application's configured keys.")
        elif test_status == "failed":
            st.error("Connection test failed; check the application's Gemini key or model configuration.")
            safe_error = st.session_state.get(f"{_TEST_ERROR_PREFIX}{mode_name}")
            if safe_error:
                st.caption(safe_error)
        else:
            st.info("Using the application's configured API keys.")
    else:
        st.warning("No API key configured. Activate a personal key or configure application keys to use Gemini features.")

    test_col, clear_col = st.columns(2)
    with test_col:
        if st.button("Test Connection", disabled=not selected_key_available, key="test_gemini_connection"):
            try:
                get_gemini_service_for_session().test_connection()
                st.session_state[f"{_TEST_STATUS_PREFIX}{mode_name}"] = "success"
                st.session_state.pop(f"{_TEST_ERROR_PREFIX}{mode_name}", None)
                st.rerun()
            except GeminiServiceError as error:
                st.session_state[f"{_TEST_STATUS_PREFIX}{mode_name}"] = "failed"
                st.session_state[f"{_TEST_ERROR_PREFIX}{mode_name}"] = str(error)
                st.rerun()
            except Exception as error:
                from utils.safe_logging import log_failure
                log_failure("gemini_connection_test", error)
                st.session_state[f"{_TEST_STATUS_PREFIX}{mode_name}"] = "failed"
                st.session_state[f"{_TEST_ERROR_PREFIX}{mode_name}"] = "The connection test failed unexpectedly. Check your key and model access, then try again."
                st.rerun()
    with clear_col:
        draft_key = bool(str(st.session_state.get(_PERSONAL_KEY_INPUT, "") or "").strip())
        st.button(
            "Clear personal key",
            on_click=_clear_personal_key,
            disabled=not (personal_active or draft_key),
            key="clear_personal_gemini_key",
        )

    st.markdown(
        "Get a personal key from [Google AI Studio](https://aistudio.google.com/app/apikey). "
        "Google's current terms and your account determine API availability, usage limits, and any charges."
    )
    st.caption("Personal keys are session-only and are not written to SQLite, chat history, files, secrets, environment variables, or logs. Clearing the key removes it from this session and returns to application-key mode.")
    st.caption("The dashboard, local PDF management, and semantic search work without Gemini. In personal-key mode, a failed personal request never falls back to application keys.")
