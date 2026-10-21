"""Application configuration values."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = Path(os.environ.get("STUDY_ASSISTANT_DB", PROJECT_ROOT / "study_assistant.db"))
