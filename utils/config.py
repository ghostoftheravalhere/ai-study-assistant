"""Application configuration values."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = Path(os.environ.get("STUDY_ASSISTANT_DB", PROJECT_ROOT / "study_assistant.db"))


def _positive_int_setting(name: str, default: int) -> int:
    """Read a positive integer environment setting, falling back safely."""
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


APP_DATA_DIR = Path(
    os.environ.get(
        "STUDY_ASSISTANT_DATA_DIR",
        Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "StudyMate",
    )
).expanduser()
DOCUMENTS_DIR = (
    Path(os.environ["STUDY_ASSISTANT_DOCUMENTS_DIR"]).expanduser()
    if os.environ.get("STUDY_ASSISTANT_DOCUMENTS_DIR")
    else APP_DATA_DIR / "documents"
)
INDEX_DIR = Path(os.environ.get("STUDY_ASSISTANT_INDEX_DIR", APP_DATA_DIR / "indexes")).expanduser()

MAX_UPLOAD_SIZE_MB = _positive_int_setting("STUDY_ASSISTANT_MAX_UPLOAD_MB", 20)
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
MAX_DOCUMENT_CONTEXT_CHARS = min(
    _positive_int_setting("STUDY_ASSISTANT_MAX_CONTEXT_CHARS", 40_000),
    120_000,
)
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
MAX_CHAT_MESSAGE_CHARS = 4_000
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_MODEL_NAME = os.environ.get("STUDY_ASSISTANT_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip() or DEFAULT_EMBEDDING_MODEL
CHUNK_SIZE_CHARS = _positive_int_setting("STUDY_ASSISTANT_CHUNK_SIZE_CHARS", 1_200)
CHUNK_OVERLAP_CHARS = min(
    _positive_int_setting("STUDY_ASSISTANT_CHUNK_OVERLAP_CHARS", 200),
    CHUNK_SIZE_CHARS - 1,
)
RETRIEVAL_TOP_K = _positive_int_setting("STUDY_ASSISTANT_RETRIEVAL_TOP_K", 5)
MAX_RETRIEVAL_TOP_K = 10
OCR_DPI = max(72, min(_positive_int_setting("STUDY_ASSISTANT_OCR_DPI", 275), 300))
OCR_LANGUAGE = os.environ.get("STUDY_ASSISTANT_OCR_LANG", "eng").strip() or "eng"
TESSERACT_CMD = os.environ.get("STUDY_ASSISTANT_TESSERACT_CMD", "").strip()
OCR_MIN_NATIVE_CHARS = _positive_int_setting("STUDY_ASSISTANT_OCR_MIN_NATIVE_CHARS", 30)
