"""Friendly local deployment readiness page."""
from __future__ import annotations

import streamlit as st

from utils.config import EMBEDDING_MODEL_NAME, OCR_LANGUAGE
from utils.diagnostics import collect_diagnostics


def _show_check(item: dict) -> None:
    status = item["status"]
    label = f"{status} · {item['name']}"
    body = item["message"]
    if item.get("next_action"):
        body += f"\n\nNext step: {item['next_action']}"
    details = item.get("details", {})
    if details:
        safe_details = "\n".join(f"{key.replace('_', ' ').title()}: {value}" for key, value in details.items())
        body += f"\n\n{safe_details}"
    st.markdown(f"**{label}**")
    st.caption(body)


def render_system_health() -> None:
    st.markdown('<div class="eyebrow">LOCAL READINESS</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">System Health</h1>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">Check StudyMate setup and local storage without sending data or loading optional models.</p>', unsafe_allow_html=True)
    checked = bool(st.session_state.get("system_health_deep_check", False))
    report = collect_diagnostics(session_state=st.session_state, check_integrity=checked)
    if report["readiness"] == "CORE READY":
        st.success(f"{report['readiness']} · {report['readiness_message']}")
    else:
        st.error(f"{report['readiness']} · {report['readiness_message']}")
    st.caption(f"StudyMate {report['application_version']} · Database schema {report['schema_version']}. Gemini connectivity, OCR processing, and model loading are not tested here.")

    if not checked:
        if st.button("Run database integrity check", key="run_health_integrity"):
            st.session_state["system_health_deep_check"] = True
            st.rerun()
    else:
        st.info("Deep database integrity check was requested. It is read-only. Refresh to return to lightweight checks.")
        if st.button("Return to lightweight checks", key="clear_health_integrity"):
            st.session_state["system_health_deep_check"] = False
            st.rerun()

    checks = report["checks"]
    with st.expander("Core setup", expanded=True):
        _show_check(checks["runtime"])
        for item in checks["dependencies"]:
            if item["details"].get("role") == "startup":
                _show_check(item)
        _show_check(checks["database"])
        _show_check(checks["integrity"])
    with st.expander("Local storage", expanded=True):
        st.caption(f"Database file: {checks['database']['details'].get('path', 'Configured database location')}")
        for item in checks["storage"]:
            _show_check(item)
    with st.expander("Effective configuration"):
        for key, value in checks["configuration"].items():
            st.caption(f"{key.replace('_', ' ').title()}: {value}")
    with st.expander("Optional features", expanded=True):
        _show_check(checks["gemini"])
        _show_check(checks["ocr"]["integration"])
        _show_check(checks["ocr"]["engine"])
        _show_check(checks["ocr"]["configuration"])
        _show_check(checks["indexing"]["packages"])
        _show_check(checks["indexing"]["model"])
        _show_check(checks["indexing"]["cache"])
        st.caption(f"Embedding model setting: {EMBEDDING_MODEL_NAME} · OCR language setting: {OCR_LANGUAGE}")
    with st.expander("All dependency checks"):
        for item in checks["dependencies"]:
            _show_check(item)
    st.caption("Checks are local and do not run OCR, contact Gemini, or download/load the embedding model. A configured Gemini key has not been validated by a live request.")
