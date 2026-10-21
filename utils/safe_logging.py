"""Minimal structured logging that never prints exception messages or payloads."""
from __future__ import annotations

import logging
import re
import sqlite3


LOGGER = logging.getLogger("studymate")
_SAFE_OPERATION = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


def log_failure(operation: str, error: BaseException, *, level: int = logging.ERROR) -> None:
    """Log an operation and exception type, omitting values and traceback locals."""
    safe_operation = operation if _SAFE_OPERATION.fullmatch(operation) else "application_operation"
    diagnostic_error = error.__cause__ or error
    category = type(diagnostic_error).__name__
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", category):
        category = "Exception"
    LOGGER.log(level, "operation=%s outcome=failed error_category=%s", safe_operation, category)


def safe_error_message(error: BaseException, *, operation: str, action: str) -> str:
    """Return useful but non-sensitive UI guidance for unexpected failures."""
    log_failure(operation, error)
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in chain and len(chain) < 6:
        chain.append(current)
        current = current.__cause__ or current.__context__
    sqlite_errors = [item for item in chain if isinstance(item, sqlite3.Error)]
    os_errors = [item for item in chain if isinstance(item, OSError)]
    for item in sqlite_errors:
        detail = str(item).casefold()
        if "locked" in detail or "busy" in detail:
            return f"{action} could not be completed because the local database is busy. Close other StudyMate windows or database tools, then retry."
        if "malformed" in detail or "not a database" in detail:
            return f"{action} could not be completed because the local database may be damaged. Preserve it and follow the verified-backup recovery steps in README.md."
        if "readonly" in detail or "permission" in detail:
            return f"{action} could not be completed because the local database is not writable. Check its folder permissions and available storage."
        if "disk is full" in detail or "database or disk is full" in detail:
            return f"{action} could not be completed because the drive may be full. Free storage space, then retry."
    for item in os_errors:
        if isinstance(item, PermissionError) or getattr(item, "winerror", None) == 5:
            return f"{action} could not be completed because local storage permissions denied access. Check the configured folder and try again."
        if getattr(item, "errno", None) == 28 or getattr(item, "winerror", None) == 112:
            return f"{action} could not be completed because the drive may be full. Free storage space, then retry."
    return f"{action} could not be completed. Check System Health for local database or storage issues, then try again."

\n