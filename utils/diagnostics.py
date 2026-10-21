"""Safe local readiness checks that avoid network, model, and OCR work."""
from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping

from utils import config, database

OK = "OK"
WARNING = "WARNING"
FAILED = "FAILED"
NOT_CHECKED = "NOT CHECKED"

CORE_DISTRIBUTIONS = {
    "Streamlit": "streamlit",
    "PyMuPDF": "PyMuPDF",
    "Google Gen AI SDK": "google-genai",
    "NumPy": "numpy",
}
OPTIONAL_DISTRIBUTIONS = {
    "Sentence Transformers": "sentence-transformers",
    "FAISS CPU": "faiss-cpu",
    "pytesseract": "pytesseract",
    "Pillow (OCR rendering)": "Pillow",
}
REQUIRED_TABLES = {
    "study_activity", "documents", "chat_conversations", "chat_messages",
    "document_index_status", "document_chunks", "document_ocr_pages", "quizzes",
    "quiz_questions", "quiz_attempts", "quiz_attempt_questions", "quiz_answers",
    "flashcard_decks", "flashcards", "flashcard_schedule", "flashcard_reviews",
    "planner_settings", "planner_subjects", "planner_exams", "planner_topics",
    "planner_tasks", "planner_sessions",
}


def _check(name: str, status: str, message: str, next_action: str | None = None,
           details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "status": status, "message": message,
            "next_action": next_action, "details": dict(details or {})}


def _probe_directory(path: Path) -> dict[str, Any]:
    """Create the configured folder if missing and prove write access safely."""
    try:
        was_missing = not path.exists()
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".studymate-check-", dir=path, delete=True):
            pass
        free = shutil.disk_usage(path).free
        message = "Folder was created and is writable." if was_missing else "Folder is available and writable."
        return _check(path.name or str(path), OK, message,
                      details={"path": str(path), "free_bytes": free, "created_by_check": was_missing})
    except OSError as error:
        return _check(path.name or str(path), FAILED,
                      "Folder is missing or cannot be written to.",
                      "Check the configured folder and Windows permissions, then restart StudyMate.",
                      {"path": str(path), "error_category": type(error).__name__})


def _database_check(path: Path, *, integrity: bool = False) -> dict[str, Any]:
    if not path.is_file():
        return _check("Local database", FAILED, "The StudyMate database file is not available.",
                      "Check the database setting and permissions. Restore a verified backup if the file was moved.",
                      {"path": str(path)})
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = sorted(REQUIRED_TABLES - tables)
            sqlite_version = sqlite3.sqlite_version
            if version > database.DATABASE_SCHEMA_VERSION:
                return _check("Local database", FAILED,
                              "This database was created by a newer StudyMate schema.",
                              "Open it with a compatible StudyMate version and create a backup before upgrading.",
                              {"schema_version": version, "supported_schema_version": database.DATABASE_SCHEMA_VERSION,
                               "sqlite_version": sqlite_version})
            if missing:
                return _check("Local database", FAILED,
                              "The database is missing required StudyMate tables.",
                              "Do not delete the database. Restore a verified backup from Data Management.",
                              {"schema_version": version, "missing_table_count": len(missing),
                               "sqlite_version": sqlite_version})
            if integrity:
                result = connection.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    return _check("Database integrity", FAILED,
                                  "SQLite reported a database integrity problem.",
                                  "Stop other StudyMate processes, preserve this file, and restore a verified backup from Data Management.",
                                  {"integrity_result": str(result[0])[:160] if result else "no result"})
                fk_error = connection.execute("PRAGMA foreign_key_check").fetchone()
                if fk_error:
                    return _check("Database integrity", FAILED,
                                  "Some saved records have inconsistent references.",
                                  "Create a backup before attempting recovery; restore a verified backup if needed.")
                return _check("Database integrity", OK, "SQLite integrity and saved record references passed.",
                              details={"sqlite_version": sqlite_version, "schema_version": version})
        return _check("Local database", OK, "Database is readable and the expected StudyMate tables are present.",
                      details={"path": str(path), "schema_version": version, "sqlite_version": sqlite_version,
                               "table_count": len(tables), "migration_status": "current" if version == database.DATABASE_SCHEMA_VERSION else "older supported schema"})
    except sqlite3.OperationalError as error:
        message = str(error).casefold()
        if "locked" in message or "busy" in message:
            return _check("Local database", FAILED, "The database is busy or locked by another process.",
                          "Close other StudyMate windows and database tools, then retry.",
                          {"error_category": type(error).__name__})
        if "malformed" in message or "not a database" in message:
            return _check("Local database", FAILED, "The database appears damaged or is not a StudyMate database.",
                          "Do not delete it. Preserve the file and restore a verified backup from Data Management.",
                          {"error_category": type(error).__name__})
        return _check("Local database", FAILED, "The database could not be read.",
                      "Check permissions and available storage. If corruption is suspected, restore a verified backup.",
                      {"error_category": type(error).__name__})
    except (OSError, sqlite3.Error, ValueError) as error:
        message = str(error).casefold()
        if isinstance(error, sqlite3.DatabaseError) and ("malformed" in message or "not a database" in message):
            return _check("Local database", FAILED, "The database appears damaged or is not a StudyMate database.",
                          "Do not delete it. Preserve the file and restore a verified backup from Data Management.",
                          {"error_category": type(error).__name__})
        return _check("Local database", FAILED, "The database could not be checked safely.",
                      "Check its configured location and permissions; preserve a copy before recovery.",
                      {"error_category": type(error).__name__})


def _model_cache_exists(model_name: str) -> bool:
    model_path = Path(model_name).expanduser()
    if model_path.is_dir():
        return True
    cache = Path(os.environ.get("HF_HUB_CACHE", Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub")).expanduser()
    repo = "models--" + model_name.replace("/", "--")
    snapshots = cache / repo / "snapshots"
    try:
        return snapshots.is_dir() and any(snapshots.iterdir())
    except OSError:
        return False


def _tesseract_check(language: str, command: str = "") -> dict[str, Any]:
    executable = command or shutil.which("tesseract") or ""
    if not executable:
        return _check("Tesseract OCR", WARNING,
                      "Tesseract is not installed or is not on PATH; scanned-page OCR is unavailable.",
                      "Install Tesseract with the required language data, or set STUDY_ASSISTANT_TESSERACT_CMD.")
    if command and not Path(command).expanduser().is_file():
        return _check("Tesseract OCR", WARNING,
                      "The configured Tesseract executable was not found; scanned-page OCR is unavailable.",
                      "Correct STUDY_ASSISTANT_TESSERACT_CMD or install Tesseract on PATH.")
    try:
        result = subprocess.run([executable, "--list-langs"], capture_output=True, text=True,
                                timeout=5, check=False, shell=False)
        if result.returncode != 0:
            return _check("Tesseract OCR", WARNING,
                          "Tesseract did not return its installed language list.",
                          "Check the Tesseract installation and tessdata folder.")
        available = {line.strip() for line in result.stdout.splitlines() if line.strip() and not line.lower().startswith("list of available")}
        requested = {part.strip() for part in language.split("+") if part.strip()}
        missing = sorted(requested - available)
        if missing:
            return _check("Tesseract OCR", WARNING,
                          "Tesseract is installed, but the requested OCR language data is missing.",
                          f"Install language data for: {', '.join(missing)}.", {"available_language_count": len(available)})
        return _check("Tesseract OCR", OK,
                      "Tesseract and the requested OCR language data are available. No document was processed.",
                      details={"requested_languages": sorted(requested)})
    except (OSError, subprocess.SubprocessError):
        return _check("Tesseract OCR", WARNING,
                      "Tesseract could not be checked; scanned-page OCR may be unavailable.",
                      "Verify the configured executable and its permissions.")


def collect_diagnostics(*, session_state: Mapping[str, Any] | None = None,
                        check_integrity: bool = False) -> dict[str, Any]:
    """Collect local readiness without Gemini calls, OCR, or model initialization."""
    runtime_ok = sys.version_info >= (3, 10)
    runtime = _check("Python runtime", OK if runtime_ok else FAILED,
                     f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} is supported."
                     if runtime_ok else f"Python {sys.version_info.major}.{sys.version_info.minor} is not supported; StudyMate requires Python 3.10 or newer.",
                     None if runtime_ok else "Install Python 3.10+ and recreate the project virtual environment.",
                     {"version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}", "minimum": "3.10"})
    dependencies = []
    for label, distribution in CORE_DISTRIBUTIONS.items():
        try:
            version = importlib.metadata.version(distribution)
            dependencies.append(_check(label, OK, "Required application package is installed.", details={"version": version, "role": "startup"}))
        except importlib.metadata.PackageNotFoundError:
            dependencies.append(_check(label, FAILED, "A required application package is not installed.",
                                       "Install runtime packages with `python -m pip install -r requirements.txt`.", {"role": "startup"}))
    optional_by_label = {}
    for label, distribution in OPTIONAL_DISTRIBUTIONS.items():
        try:
            version = importlib.metadata.version(distribution)
            optional_by_label[label] = _check(label, OK, "Optional feature package is installed.", details={"version": version, "role": "optional"})
        except importlib.metadata.PackageNotFoundError:
            optional_by_label[label] = _check(label, WARNING, "This optional feature package is not installed.",
                                              "Install runtime packages with `python -m pip install -r requirements.txt` if you need this feature.", {"role": "optional"})
    dependencies.extend(optional_by_label.values())

    database_path = Path(config.DATABASE_PATH)
    database_check = _database_check(database_path, integrity=False)
    integrity_check = (_database_check(database_path, integrity=True) if check_integrity else _check(
        "Database integrity", NOT_CHECKED, "A deep SQLite integrity check has not been run.",
        "Use Run database integrity check if you need a deeper verification."))
    data_dir = _probe_directory(Path(config.APP_DATA_DIR))
    docs_dir = _probe_directory(Path(config.DOCUMENTS_DIR))
    index_dir = _probe_directory(Path(config.INDEX_DIR))
    db_parent = _probe_directory(Path(config.DATABASE_PATH).expanduser().parent)
    storage = [data_dir, docs_dir, index_dir, db_parent]

    requested_languages = config.OCR_LANGUAGE.split("+")
    valid_language_setting = bool(requested_languages) and all(
        re.fullmatch(r"[A-Za-z0-9_]+", code) for code in requested_languages
    )
    ocr_package = optional_by_label["pytesseract"]
    tesseract = _tesseract_check(config.OCR_LANGUAGE, config.TESSERACT_CMD)
    ocr = {"integration": ocr_package, "engine": tesseract,
           "configuration": _check("OCR configuration", OK if valid_language_setting else WARNING,
                                    f"Requested language codes: {', '.join(requested_languages)}." if valid_language_setting else "The OCR language setting is malformed.",
                                    None if valid_language_setting else "Set STUDY_ASSISTANT_OCR_LANG to codes such as eng or eng+fra.")}

    model_name = config.EMBEDDING_MODEL_NAME
    model_cached = _model_cache_exists(model_name)
    from utils.embedding_service import is_embedding_model_loaded
    model_loaded = is_embedding_model_loaded(model_name)
    embedding_packages_ok = all(optional_by_label[label]["status"] == OK for label in ("Sentence Transformers", "FAISS CPU"))
    indexing = {
        "packages": _check("Semantic indexing packages", OK if embedding_packages_ok else WARNING,
                           "Sentence Transformers and FAISS are available." if embedding_packages_ok else "Semantic indexing packages are incomplete; local indexing is unavailable.",
                           None if embedding_packages_ok else "Install runtime packages with `python -m pip install -r requirements.txt`."),
        "model": _check("Embedding model", OK if model_cached else WARNING,
                        f"Configured model: {model_name}. A local model cache was found." if model_cached else f"Configured model: {model_name}. The model has not been found in the local cache; the first indexing action may need a download.",
                        None if model_cached else "Use Index documents when ready; it may download the model. Health checks never download it.",
                        {"cached": model_cached, "load_state": "LOADED" if model_loaded else "NOT LOADED"}),
        "cache": index_dir,
    }

    state = session_state or {}
    mode = state.get("gemini_api_mode", "application")
    try:
        if mode == "personal":
            configured = bool(str(state.get("personal_gemini_api_key", "") or "").strip())
            key_mode = "personal session key" if configured else "personal key selected, none active"
        else:
            from utils.gemini_service import is_gemini_configured
            configured = is_gemini_configured()
            key_mode = "application keys"
        gemini = _check("Gemini configuration", OK if configured else WARNING,
                        f"{key_mode.capitalize()} are configured; credential validity was not tested." if configured else "No Gemini API key is configured; local features remain available.",
                        None if configured else "Configure an application key or activate a personal key in Settings when using Gemini features.",
                        {"key_mode": key_mode, "configured": configured, "connectivity": NOT_CHECKED})
    except Exception as error:
        gemini = _check("Gemini configuration", WARNING,
                        "Gemini configuration could not be inspected; no network request was made.",
                        "Review Settings and local Streamlit secret configuration.", {"error_category": type(error).__name__})

    core_checks = [runtime, *dependencies[:len(CORE_DISTRIBUTIONS)], database_check, db_parent]
    core_ready = all(item["status"] == OK for item in core_checks)
    configuration = {
        "database_path": str(Path(config.DATABASE_PATH).expanduser()),
        "data_directory": str(Path(config.APP_DATA_DIR).expanduser()),
        "document_directory": str(Path(config.DOCUMENTS_DIR).expanduser()),
        "embedding_model": model_name,
        "ocr_languages": config.OCR_LANGUAGE,
        "ocr_dpi": config.OCR_DPI,
        "tesseract_path_configured": bool(config.TESSERACT_CMD),
        "maximum_pdf_upload_mb": config.MAX_UPLOAD_SIZE_MB,
    }
    all_checks = [runtime, *dependencies, database_check, integrity_check, *storage, ocr_package, tesseract, gemini,
                  indexing["packages"], indexing["model"]]
    return {
        "application_version": "Phase 11",
        "schema_version": database.DATABASE_SCHEMA_VERSION,
        "readiness": "CORE READY" if core_ready else "CORE NOT READY",
        "readiness_message": "The app can start and use its local core features." if core_ready else "A startup requirement needs attention before core StudyMate features are ready.",
        "checks": {"runtime": runtime, "dependencies": dependencies, "database": database_check,
                   "integrity": integrity_check, "storage": storage, "configuration": configuration,
                   "ocr": ocr, "indexing": indexing, "gemini": gemini},
        "all_checks": all_checks,
        "integrity_checked": check_integrity,
        "network_requested": False,
        "ocr_run": False,
        "model_load_state": "LOADED" if model_loaded else "NOT LOADED",
    }
