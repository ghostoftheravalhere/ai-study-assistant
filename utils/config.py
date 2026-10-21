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


if os.environ.get("STUDY_ASSISTANT_DOCUMENTS_DIR"):
    DOCUMENTS_DIR = Path(os.environ["STUDY_ASSISTANT_DOCUMENTS_DIR"]).expanduser()
else:
    APP_DATA_DIR = Path(
        os.environ.get(
            "STUDY_ASSISTANT_DATA_DIR",
            Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "StudyMate",
        )
    ).expanduser()
    DOCUMENTS_DIR = APP_DATA_DIR / "documents"

MAX_UPLOAD_SIZE_MB = _positive_int_setting("STUDY_ASSISTANT_MAX_UPLOAD_MB", 20)
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
