"""Readiness, first-run, and recovery checks use isolated local state."""
import json
import logging
import builtins
import sqlite3
from collections import namedtuple
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from utils import database, diagnostics
from utils.safe_logging import log_failure, safe_error_message


@pytest.fixture
def diagnostic_environment(tmp_path, monkeypatch):
    db_path = tmp_path / "local" / "study.sqlite3"
    data_dir = tmp_path / "data"
    docs_dir = data_dir / "documents"
    index_dir = data_dir / "indexes"
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    monkeypatch.setattr(diagnostics.config, "DATABASE_PATH", db_path)
    monkeypatch.setattr(diagnostics.config, "APP_DATA_DIR", data_dir)
    monkeypatch.setattr(diagnostics.config, "DOCUMENTS_DIR", docs_dir)
    monkeypatch.setattr(diagnostics.config, "INDEX_DIR", index_dir)
    monkeypatch.setattr(diagnostics.config, "TESSERACT_CMD", "")
    monkeypatch.setattr(diagnostics.config, "OCR_LANGUAGE", "eng")
    database.initialize_database()
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)
    monkeypatch.setattr(diagnostics, "_model_cache_exists", lambda _: False)
    return db_path, data_dir, docs_dir, index_dir


def _all_checks(report):
    return json.dumps(report, ensure_ascii=False)


def test_supported_python_and_database_are_ready(diagnostic_environment):
    report = diagnostics.collect_diagnostics(session_state={})
    assert report["readiness"] == "CORE READY"
    assert report["checks"]["runtime"]["status"] == diagnostics.OK
    assert report["checks"]["database"]["status"] == diagnostics.OK
    assert report["checks"]["integrity"]["status"] == diagnostics.NOT_CHECKED
    assert report["network_requested"] is False and report["ocr_run"] is False


def test_unsupported_python_has_actionable_guidance(diagnostic_environment, monkeypatch):
    Version = namedtuple("Version", "major minor micro")
    monkeypatch.setattr(diagnostics.sys, "version_info", Version(3, 9, 18))
    report = diagnostics.collect_diagnostics(session_state={})
    assert report["checks"]["runtime"]["status"] == diagnostics.FAILED
    assert "3.10" in report["checks"]["runtime"]["next_action"]
    assert report["readiness"] == "CORE NOT READY"


def test_missing_optional_packages_does_not_fail_core_readiness(diagnostic_environment, monkeypatch):
    original = diagnostics.importlib.metadata.version

    def version(name):
        if name in {"sentence-transformers", "faiss-cpu", "pytesseract"}:
            raise diagnostics.importlib.metadata.PackageNotFoundError(name)
        return original(name)

    monkeypatch.setattr(diagnostics.importlib.metadata, "version", version)
    report = diagnostics.collect_diagnostics(session_state={})
    assert report["readiness"] == "CORE READY"
    assert report["checks"]["indexing"]["packages"]["status"] == diagnostics.WARNING
    assert report["checks"]["ocr"]["integration"]["status"] == diagnostics.WARNING


def test_missing_gemini_key_and_tesseract_are_optional(diagnostic_environment):
    report = diagnostics.collect_diagnostics(session_state={})
    assert report["readiness"] == "CORE READY"
    assert "local features remain available" in report["checks"]["gemini"]["message"]
    assert report["checks"]["ocr"]["engine"]["status"] == diagnostics.WARNING
    assert "Tesseract is not installed" in report["checks"]["ocr"]["engine"]["message"]


def test_missing_database_is_reported_without_recreating_it(diagnostic_environment, monkeypatch, tmp_path):
    missing = tmp_path / "gone" / "study.sqlite3"
    monkeypatch.setattr(diagnostics.config, "DATABASE_PATH", missing)
    before = missing.exists()
    report = diagnostics.collect_diagnostics(session_state={})
    assert report["checks"]["database"]["status"] == diagnostics.FAILED
    assert missing.exists() is before is False


def test_missing_storage_directories_are_created_and_probed_safely(diagnostic_environment):
    _, data_dir, docs_dir, index_dir = diagnostic_environment
    assert all(not path.exists() for path in (data_dir, docs_dir, index_dir))
    report = diagnostics.collect_diagnostics(session_state={})
    assert all(item["status"] == diagnostics.OK for item in report["checks"]["storage"])
    assert all(path.is_dir() for path in (data_dir, docs_dir, index_dir))
    assert not list(data_dir.rglob(".studymate-check-*"))


def test_personal_key_value_is_not_returned_in_diagnostics(diagnostic_environment):
    key = "AIza" + "Z" * 36
    report = diagnostics.collect_diagnostics(session_state={
        "gemini_api_mode": "personal", "personal_gemini_api_key": key,
    })
    assert "Personal session key are configured" in report["checks"]["gemini"]["message"]
    assert key not in _all_checks(report)
    assert report["checks"]["gemini"]["details"]["key_mode"] == "personal session key"


def test_standard_checks_do_not_run_ocr_load_model_or_call_gemini(diagnostic_environment, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A diagnostic must not start optional expensive work")

    monkeypatch.setattr(diagnostics.subprocess, "run", forbidden)
    monkeypatch.setattr(diagnostics, "_model_cache_exists", lambda _: False)
    monkeypatch.setattr("utils.gemini_service.is_gemini_configured", forbidden)
    monkeypatch.setattr("utils.gemini_service.genai.Client", forbidden)
    report = diagnostics.collect_diagnostics(session_state={"gemini_api_mode": "personal"})
    assert report["model_load_state"] == "NOT LOADED"
    assert report["ocr_run"] is False
    assert report["checks"]["indexing"]["model"]["details"]["load_state"] == "NOT LOADED"


def test_database_integrity_check_is_explicit_and_read_only(diagnostic_environment):
    db_path, *_ = diagnostic_environment
    before = db_path.read_bytes()
    report = diagnostics.collect_diagnostics(session_state={}, check_integrity=True)
    assert report["integrity_checked"] is True
    assert report["checks"]["integrity"]["status"] == diagnostics.OK
    assert db_path.read_bytes() == before


def test_failed_additive_migration_rolls_back_schema_and_preserves_records(tmp_path):
    path = tmp_path / "partial.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE documents(id TEXT)")
        connection.execute("CREATE TABLE study_activity(id INTEGER PRIMARY KEY, description TEXT)")
        connection.execute("INSERT INTO study_activity(description) VALUES('keep this record')")
    with pytest.raises(RuntimeError):
        database.initialize_database(path)
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        record = connection.execute("SELECT description FROM study_activity").fetchone()[0]
    assert tables == {"documents", "study_activity"}
    assert record == "keep this record"


def test_corrupt_database_is_reported_and_never_recreated(tmp_path, monkeypatch):
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"preserve these bytes")
    monkeypatch.setattr(diagnostics.config, "DATABASE_PATH", corrupt)
    original = corrupt.read_bytes()
    report = diagnostics.collect_diagnostics(session_state={})
    assert report["checks"]["database"]["status"] == diagnostics.FAILED
    assert "damaged" in report["checks"]["database"]["message"]
    assert corrupt.read_bytes() == original


def test_locked_database_has_actionable_health_status(diagnostic_environment):
    db_path, *_ = diagnostic_environment
    locker = sqlite3.connect(db_path, timeout=0)
    try:
        locker.execute("BEGIN EXCLUSIVE")
        check = diagnostics._database_check(db_path)
    finally:
        locker.rollback()
        locker.close()
    assert check["status"] == diagnostics.FAILED
    assert "locked" in check["message"]
    assert "Close other StudyMate" in check["next_action"]


def test_clean_app_starts_and_onboarding_has_no_fabricated_activity(diagnostic_environment):
    db_path, *_ = diagnostic_environment
    db_path.unlink()
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    assert not app.exception
    assert db_path.is_file()
    assert app.session_state["active_page"] == "Dashboard"
    assert any("Getting started" in str(item.value) for item in app.markdown)
    assert any("A fresh start" in str(item.value) for item in app.markdown)


def test_missing_optional_engine_imports_do_not_break_dashboard(diagnostic_environment, monkeypatch):
    db_path, *_ = diagnostic_environment
    db_path.unlink()
    original_import = builtins.__import__

    def without_optional_engines(name, *args, **kwargs):
        if name == "pytesseract" or name.startswith("sentence_transformers") or name == "faiss":
            raise ImportError("optional engine unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_optional_engines)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    assert not app.exception
    assert app.session_state["active_page"] == "Dashboard"


def test_dashboard_escapes_user_controlled_activity_html(diagnostic_environment):
    with database.get_connection() as connection:
        connection.execute("INSERT INTO study_activity(activity_type,description,created_at) VALUES(?,?,?)",
                           ("test", "<script>alert('x')</script>", "today"))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    markup = "\n".join(str(item.value) for item in app.markdown)
    assert "&lt;script&gt;" in markup
    assert "<script>alert('x')</script>" not in markup


def test_system_health_page_renders_readiness(diagnostic_environment):
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20)
    app.session_state["active_page"] = "System Health"
    app.run()
    assert not app.exception
    assert any("System Health" in str(item.value) for item in app.markdown)
    assert any("CORE READY" in str(item.value) for item in app.success)


def test_structured_failure_log_omits_secret_values(caplog):
    secret = "AIza" + "Y" * 36
    with caplog.at_level(logging.ERROR, logger="studymate"):
        log_failure("gemini_request", RuntimeError(secret))
    assert "operation=gemini_request" in caplog.text
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text


def test_safe_error_message_classifies_locked_storage_without_raw_details(caplog):
    secret = "AIza" + "Q" * 36
    error = sqlite3.OperationalError(f"database is locked: {secret}")
    message = safe_error_message(error, operation="planner_test", action="Saving plan")
    assert "database is busy" in message
    assert secret not in message and secret not in caplog.text

\n