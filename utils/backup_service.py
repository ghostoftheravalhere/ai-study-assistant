"""Validated SQLite snapshots and portable database-plus-PDF backups."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from utils import database
from utils.config import APP_DATA_DIR, DOCUMENTS_DIR

FORMAT_NAME = "studymate-complete-backup"
FORMAT_VERSION = 1
MAX_BACKUP_BYTES = 512 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
REQUIRED_TABLES = {
    "study_activity", "documents", "chat_conversations", "chat_messages",
    "document_index_status", "document_chunks", "document_ocr_pages", "quizzes",
    "quiz_questions", "quiz_attempts", "quiz_attempt_questions", "quiz_answers",
    "flashcard_decks", "flashcards", "flashcard_schedule", "flashcard_reviews",
    "planner_settings", "planner_subjects", "planner_exams", "planner_topics",
    "planner_tasks", "planner_sessions",
}
BASE_REQUIRED_TABLES = {
    "study_activity", "documents", "chat_conversations", "chat_messages",
    "document_chunks", "quizzes", "quiz_questions", "quiz_attempts",
    "flashcard_decks", "flashcards", "flashcard_reviews",
}


class BackupError(ValueError):
    """Safe backup, restore, and validation error suitable for the UI."""


@dataclass(frozen=True)
class BackupArtifact:
    filename: str
    content: bytes
    kind: str
    metadata: dict[str, Any]


@contextmanager
def _connection(path: str | Path, **kwargs: Any):
    """SQLite's connection context manager commits but does not close handles."""
    connection = sqlite3.connect(path, **kwargs)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(COPY_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_filename(name: Any) -> str:
    if not isinstance(name, str) or not name or Path(name).name != name or "/" in name or "\\" in name or name in {".", ".."}:
        raise BackupError("The backup contains an unsafe document filename.")
    if not name.lower().endswith(".pdf") or len(name) > 180:
        raise BackupError("The backup contains an unsupported document filename.")
    return name


def _db_tables(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _check_database(path: Path, *, migrate: bool) -> dict[str, Any]:
    try:
        if not path.is_file() or path.stat().st_size < 100:
            raise BackupError("This file is not a readable StudyMate SQLite database.")
        with path.open("rb") as file_stream:
            if file_stream.read(16) != b"SQLite format 3\x00":
                raise BackupError("This file is not a readable StudyMate SQLite database.")
        with _connection(path) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise BackupError("The database failed SQLite's integrity check.")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            tables = _db_tables(connection)
            if version > database.DATABASE_SCHEMA_VERSION:
                raise BackupError("This backup was created by a newer, incompatible StudyMate schema.")
            if not BASE_REQUIRED_TABLES.issubset(tables):
                raise BackupError("This database is missing required StudyMate records.")
        if migrate and version < database.DATABASE_SCHEMA_VERSION:
            database.initialize_database(path)
        with _connection(path) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            tables = _db_tables(connection)
            missing = REQUIRED_TABLES - tables
            if version == database.DATABASE_SCHEMA_VERSION and missing:
                raise BackupError("The database is missing required StudyMate tables.")
            fk_errors = connection.execute("PRAGMA foreign_key_check").fetchone()
            if fk_errors:
                raise BackupError("The database contains inconsistent record references.")
            return {
                "schema_version": version,
                "table_count": len(tables),
                "document_count": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "quiz_attempt_count": connection.execute("SELECT COUNT(*) FROM quiz_attempts").fetchone()[0] if "quiz_attempts" in tables else 0,
            }
    except BackupError:
        raise
    except (OSError, sqlite3.Error, RuntimeError) as error:
        raise BackupError("The SQLite database could not be safely validated.") from error


def _online_backup(source_path: Path, destination_path: Path) -> dict[str, Any]:
    if not source_path.is_file():
        raise BackupError("The current StudyMate database was not found.")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        uri = source_path.resolve().as_uri() + "?mode=ro"
        with _connection(uri, uri=True, timeout=10) as source:
            with _connection(destination_path, timeout=10) as destination:
                source.backup(destination, pages=256, sleep=0.05)
        return _check_database(destination_path, migrate=False)
    except BackupError:
        raise
    except (OSError, sqlite3.Error) as error:
        destination_path.unlink(missing_ok=True)
        raise BackupError("A consistent SQLite snapshot could not be created. Close other database tools and retry.") from error


def create_database_backup(*, database_path: Path | None = None) -> BackupArtifact:
    """Create a validated, database-only point-in-time backup."""
    source = Path(database_path or database.DATABASE_PATH)
    try:
        with tempfile.TemporaryDirectory(prefix="studymate-backup-") as temporary:
            snapshot = Path(temporary) / "studymate.sqlite3"
            metadata = _online_backup(source, snapshot)
            if snapshot.stat().st_size > MAX_BACKUP_BYTES:
                raise BackupError("The database backup is too large to download through this page.")
            payload = snapshot.read_bytes()
        name = f"studymate-database-{_timestamp()}.sqlite3"
        return BackupArtifact(name, payload, "database", metadata)
    except BackupError:
        raise
    except OSError as error:
        raise BackupError("The database backup could not be prepared.") from error


def _document_entries(connection: sqlite3.Connection, root: Path) -> list[dict[str, Any]]:
    if root.is_symlink():
        raise BackupError("The configured document folder is a symbolic link; complete backup was stopped for safety.")
    resolved_root = root.resolve()
    entries = []
    for row in connection.execute("SELECT id,stored_filename,file_size_bytes,content_sha256 FROM documents ORDER BY id"):
        filename = _safe_filename(row["stored_filename"])
        source = root / filename
        if source.is_symlink():
            raise BackupError("A stored PDF is a symbolic link; complete backup was stopped for safety.")
        try:
            resolved = source.resolve(strict=True)
            if resolved.parent != resolved_root or not resolved.is_file():
                raise BackupError("A stored PDF is outside the configured document folder.")
            size = resolved.stat().st_size
            if size != row["file_size_bytes"] or _sha256_file(resolved) != row["content_sha256"]:
                raise BackupError("A stored PDF is missing or differs from its saved document record.")
        except FileNotFoundError as error:
            raise BackupError("A PDF referenced by the document library is missing. Use a database-only backup or restore the PDF first.") from error
        except OSError as error:
            raise BackupError("A stored PDF could not be safely read.") from error
        entries.append({"id": row["id"], "stored_filename": filename,
                        "archive_path": f"documents/{filename}", "size_bytes": size,
                        "sha256": row["content_sha256"]})
    return entries


def create_complete_backup(*, database_path: Path | None = None, documents_dir: Path | None = None) -> BackupArtifact:
    """Create a ZIP archive with a SQLite snapshot and every library PDF."""
    source = Path(database_path or database.DATABASE_PATH)
    document_root = Path(documents_dir or DOCUMENTS_DIR)
    try:
        with tempfile.TemporaryDirectory(prefix="studymate-complete-") as temporary:
            temp = Path(temporary)
            snapshot, archive_path = temp / "database.sqlite3", temp / "complete.zip"
            metadata = _online_backup(source, snapshot)
            with _connection(snapshot) as db:
                db.row_factory = sqlite3.Row
                files = _document_entries(db, document_root)
            manifest = {
                "format": FORMAT_NAME,
                "format_version": FORMAT_VERSION,
                "schema_version": metadata["schema_version"],
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "database": {"path": "database.sqlite3", "size_bytes": snapshot.stat().st_size,
                             "sha256": _sha256_file(snapshot)},
                "documents": files,
                "cache_policy": "FAISS cache excluded; it can be rebuilt from SQLite vectors.",
            }
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.write(snapshot, "database.sqlite3")
                with archive.open("manifest.json", "w") as manifest_stream:
                    manifest_stream.write(json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
                for item in files:
                    path = document_root / item["stored_filename"]
                    if path.is_symlink() or path.resolve(strict=True).parent != document_root.resolve():
                        raise BackupError("A stored PDF changed while the backup was being created.")
                    with path.open("rb") as source_stream, archive.open(item["archive_path"], "w") as target_stream:
                        shutil.copyfileobj(source_stream, target_stream, length=COPY_CHUNK_BYTES)
                if archive_path.stat().st_size > MAX_BACKUP_BYTES:
                    raise BackupError("The complete backup exceeds the 512 MiB download limit.")
            payload = archive_path.read_bytes()
            validate_backup(payload)
        name = f"studymate-complete-{_timestamp()}.zip"
        return BackupArtifact(name, payload, "complete", {**metadata, "document_files": len(files), "archive_bytes": len(payload)})
    except BackupError:
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as error:
        raise BackupError("The complete backup could not be created. Verify the database and document files, then retry.") from error


def _validate_member_name(name: str) -> str:
    pure = PurePosixPath(name)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts) or "\\" in name or ":" in name:
        raise BackupError("The archive contains an unsafe path.")
    return name


def _read_stream_limited(stream: BinaryIO, target: Path, limit: int) -> int:
    total = 0
    with target.open("xb") as output:
        while True:
            chunk = stream.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise BackupError("The backup contains a file that exceeds the supported size limit.")
            output.write(chunk)
    return total


def _inspect_sqlite_payload(payload: bytes, directory: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    database_path = directory / "database.sqlite3"
    if not payload.startswith(b"SQLite format 3\x00"):
        raise BackupError("The selected file is not a supported StudyMate backup.")
    with database_path.open("xb") as stream:
        stream.write(payload)
    metadata = _check_database(database_path, migrate=False)
    return database_path, metadata, {"kind": "database", "format_version": 1, "schema_version": metadata["schema_version"], "document_files": 0}


def _inspect_archive(payload: bytes, directory: Path) -> tuple[Path, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if len(payload) > MAX_BACKUP_BYTES:
        raise BackupError("The selected archive exceeds the 512 MiB restore limit.")
    database_path = directory / "database.sqlite3"
    docs_dir = directory / "documents"
    docs_dir.mkdir()
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise BackupError("The archive contains duplicate entries.")
            for info in infos:
                name = _validate_member_name(info.filename)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or info.is_dir() or info.flag_bits & 1:
                    raise BackupError("The archive contains a link, directory, or encrypted entry that is not supported.")
                if name not in {"manifest.json", "database.sqlite3"} and not name.startswith("documents/"):
                    raise BackupError("The archive contains an unsupported file.")
            if "manifest.json" not in names or "database.sqlite3" not in names:
                raise BackupError("The complete backup is missing its manifest or database.")
            if sum(info.file_size for info in infos) > MAX_BACKUP_BYTES:
                raise BackupError("The archive expands beyond the supported restore limit.")
            try:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as error:
                raise BackupError("The backup manifest is unreadable.") from error
            if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_NAME:
                raise BackupError("This ZIP file is not a StudyMate complete backup.")
            if manifest.get("format_version") != FORMAT_VERSION:
                raise BackupError("This complete backup format version is not supported.")
            schema = manifest.get("schema_version")
            if not isinstance(schema, int) or schema > database.DATABASE_SCHEMA_VERSION:
                raise BackupError("This backup uses a newer, incompatible StudyMate schema.")
            db_info = manifest.get("database")
            db_member = archive.getinfo("database.sqlite3")
            if not isinstance(db_info, dict) or db_info.get("path") != "database.sqlite3" or db_info.get("size_bytes") != db_member.file_size:
                raise BackupError("The database metadata in the backup manifest is invalid.")
            with archive.open("database.sqlite3") as source:
                if _read_stream_limited(source, database_path, MAX_BACKUP_BYTES) != db_info["size_bytes"]:
                    raise BackupError("The database size does not match the backup manifest.")
            if _sha256_file(database_path) != db_info.get("sha256"):
                raise BackupError("The database checksum does not match the backup manifest.")
            db_meta = _check_database(database_path, migrate=False)
            if db_meta["schema_version"] != schema:
                raise BackupError("The database schema version does not match the backup manifest.")
            docs = manifest.get("documents")
            if not isinstance(docs, list):
                raise BackupError("The document list in the backup manifest is invalid.")
            expected_members = {"manifest.json", "database.sqlite3"}
            seen_ids: set[str] = set()
            seen_names: set[str] = set()
            extracted: list[dict[str, Any]] = []
            with _connection(database_path) as db:
                db_docs = {row[0]: row[1:] for row in db.execute("SELECT id,stored_filename,file_size_bytes,content_sha256 FROM documents")}
            for item in docs:
                if not isinstance(item, dict):
                    raise BackupError("The document list contains an invalid record.")
                document_id = item.get("id")
                if not isinstance(document_id,str) or not document_id:
                    raise BackupError("The document manifest contains an invalid document identifier.")
                filename = _safe_filename(item.get("stored_filename"))
                member = _validate_member_name(item.get("archive_path", ""))
                if member != f"documents/{filename}" or document_id in seen_ids or filename in seen_names:
                    raise BackupError("The document manifest contains duplicate or inconsistent references.")
                if document_id not in db_docs or db_docs[document_id][0] != filename or db_docs[document_id][1] != item.get("size_bytes") or db_docs[document_id][2] != item.get("sha256"):
                    raise BackupError("A document does not match its database record.")
                try:
                    info = archive.getinfo(member)
                except KeyError as error:
                    raise BackupError("A PDF listed in the manifest is missing from the archive.") from error
                if info.file_size != item.get("size_bytes") or not isinstance(item.get("size_bytes"), int) or isinstance(item.get("size_bytes"),bool) or item["size_bytes"] < 0:
                    raise BackupError("A PDF size does not match its manifest entry.")
                target = docs_dir / filename
                with archive.open(member) as source:
                    _read_stream_limited(source, target, MAX_BACKUP_BYTES)
                if _sha256_file(target) != item.get("sha256"):
                    raise BackupError("A PDF checksum does not match the backup manifest.")
                expected_members.add(member); seen_ids.add(document_id); seen_names.add(filename)
                extracted.append({**item,"temporary_path":target})
            if seen_ids != set(db_docs):
                raise BackupError("The complete backup does not contain every PDF referenced by its database.")
            if set(names) != expected_members:
                raise BackupError("The archive contains missing or unsupported document files.")
            return database_path, db_meta, {"kind":"complete","format_version":FORMAT_VERSION,"schema_version":schema,"document_files":len(docs)}, extracted
    except BackupError:
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile, RuntimeError) as error:
        raise BackupError("The complete backup archive could not be safely validated.") from error


def validate_backup(payload: bytes) -> dict[str, Any]:
    """Validate either a SQLite backup or a versioned complete-backup ZIP."""
    if not isinstance(payload, bytes) or not payload:
        raise BackupError("Choose a non-empty SQLite backup or StudyMate ZIP archive.")
    if len(payload) > MAX_BACKUP_BYTES:
        raise BackupError("The selected backup exceeds the 512 MiB restore limit.")
    try:
        with tempfile.TemporaryDirectory(prefix="studymate-validate-") as temporary:
            directory=Path(temporary)
            if payload.startswith(b"SQLite format 3\x00"):
                _,db_meta,info=_inspect_sqlite_payload(payload,directory)
            elif payload.startswith(b"PK"):
                _,db_meta,info,_=_inspect_archive(payload,directory)
            else:
                raise BackupError("The selected file is not a supported StudyMate backup.")
        return {**info,**db_meta,"valid":True}
    except BackupError:
        raise
    except OSError as error:
        raise BackupError("The backup could not be validated in the temporary workspace.") from error


def _prepare_document_targets(documents: list[dict[str, Any]], stage_db: Path, root: Path) -> tuple[list[Path], list[Path]]:
    """Install PDFs under safe generated names; return created files and reused files."""
    if root.is_symlink():
        raise BackupError("The configured document folder is a symbolic link; restore was stopped for safety.")
    root.mkdir(parents=True, exist_ok=True)
    safe_root=root.resolve()
    created=[]; reused=[]
    try:
        with _connection(stage_db) as db:
            for item in documents:
                original=_safe_filename(item["stored_filename"])
                target=root / original
                if target.is_symlink():
                    raise BackupError("A target PDF is a symbolic link; restore was stopped for safety.")
                if target.exists():
                    if target.is_file() and target.resolve().parent==safe_root and _sha256_file(target)==item["sha256"]:
                        reused.append(target)
                        continue
                    # Do not overwrite a different existing file. Assign this
                    # restored record a fresh opaque app-generated filename.
                    fresh=f"{uuid.uuid4().hex}.pdf"; target=root/fresh
                    db.execute("UPDATE documents SET stored_filename=? WHERE id=?",(fresh,item["id"]))
                if target.resolve().parent!=safe_root:
                    raise BackupError("A target PDF would be written outside the configured document folder.")
                try:
                    with item["temporary_path"].open("rb") as source, target.open("xb") as output:
                        shutil.copyfileobj(source,output,length=COPY_CHUNK_BYTES)
                except FileExistsError:
                    raise BackupError("A document filename changed during restore; retry after checking the document folder.")
                created.append(target)
        return created,reused
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def _checkpoint_and_clear_sidecars(path: Path) -> None:
    if not path.exists():
        return
    try:
        with _connection(path, timeout=2) as connection:
            result=connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if result and result[0]:
                raise BackupError("The live database is busy. Close other StudyMate windows and retry restore.")
        wal=Path(str(path)+"-wal"); shm=Path(str(path)+"-shm")
        if wal.exists() and wal.stat().st_size:
            raise BackupError("The live database still has active write-ahead-log data; close other database users and retry.")
        wal.unlink(missing_ok=True); shm.unlink(missing_ok=True)
    except BackupError:
        raise
    except (OSError,sqlite3.Error) as error:
        raise BackupError("The live database is locked or unavailable. Close other StudyMate windows and retry.") from error


def _safety_backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup_dir=Path(APP_DATA_DIR)/"backups"
    target=backup_dir/f"studymate-safety-{_timestamp()}-{uuid.uuid4().hex[:8]}.sqlite3"
    try:
        _online_backup(path,target)
        return target
    except BackupError:
        target.unlink(missing_ok=True)
        raise BackupError("The pre-restore safety backup could not be created; restore was not started.")


def restore_backup(payload: bytes, *, confirmed: bool, database_path: Path|None=None,
                   documents_dir: Path|None=None) -> dict[str, Any]:
    """Validate, migrate in staging, safety-backup, then atomically activate restore."""
    if not confirmed:
        raise BackupError("Restore was not confirmed; the current database was not changed.")
    validate_backup(payload)
    live=Path(database_path or database.DATABASE_PATH); document_root=Path(documents_dir or DOCUMENTS_DIR)
    try:
        live.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="studymate-restore-") as temporary:
            directory=Path(temporary)
            if payload.startswith(b"PK"):
                source_db,_,info,documents=_inspect_archive(payload,directory)
            else:
                source_db,_,info=_inspect_sqlite_payload(payload,directory); documents=[]
            stage=live.parent/f".{live.name}.{uuid.uuid4().hex}.restore"
            # Backup API yields a standalone staged database even if input came
            # from a WAL-mode file. Migrate only the staged copy.
            _online_backup(source_db,stage)
            database.initialize_database(stage)
            staged_metadata=_check_database(stage,migrate=False)
            safety=_safety_backup(live)
            created_files=[]
            try:
                if documents:
                    created_files,_=_prepare_document_targets(documents,stage,document_root)
                _checkpoint_and_clear_sidecars(live)
                os.replace(stage,live)
                try:
                    restored=_check_database(live,migrate=False)
                except BackupError:
                    if safety is None:
                        live.unlink(missing_ok=True)
                    else:
                        rollback=live.parent/f".{live.name}.{uuid.uuid4().hex}.rollback"
                        _online_backup(safety,rollback)
                        _checkpoint_and_clear_sidecars(live)
                        os.replace(rollback,live)
                    raise
            except Exception:
                # On failure before activation the live DB remains as it was;
                # after activation, roll back from the retained safety file.
                if safety and live.exists():
                    try:
                        active=_check_database(live,migrate=False)
                        if active["schema_version"]!=staged_metadata["schema_version"] or active["document_count"]!=staged_metadata["document_count"]:
                            rollback=live.parent/f".{live.name}.{uuid.uuid4().hex}.rollback"
                            _online_backup(safety,rollback); _checkpoint_and_clear_sidecars(live); os.replace(rollback,live)
                    except Exception:
                        # The safety snapshot remains recoverable at its path.
                        pass
                for file_path in created_files:
                    try: file_path.unlink(missing_ok=True)
                    except OSError: pass
                raise
            return {"kind":info["kind"],"metadata":restored,"safety_backup":safety.name if safety else None,"document_files_restored":len(documents)}
    except BackupError:
        raise
    except (OSError,sqlite3.Error,RuntimeError) as error:
        raise BackupError("Restore could not be completed. The existing database and any safety backup were preserved.") from error
