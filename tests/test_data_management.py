"""Phase 10 backup, restore, export, and report tests using temporary local data."""
import csv
import hashlib
import io
import json
import shutil
import sqlite3
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from utils import database
from utils import backup_service
from utils.backup_service import BackupError, create_complete_backup, create_database_backup, restore_backup, validate_backup
from utils.data_export_service import ExportError, export_csv, export_json
from utils.flashcard_repository import create_card, create_deck
from utils.planner_service import create_task, record_session, save_exam, save_subject
from utils.study_report_service import ReportError, build_study_report, create_study_report


@pytest.fixture
def data_db(tmp_path,monkeypatch):
    db_path=tmp_path/"source.sqlite3"; docs=tmp_path/"external-documents"; docs.mkdir()
    monkeypatch.setattr(database,"DATABASE_PATH",db_path)
    monkeypatch.setattr(backup_service,"DOCUMENTS_DIR",docs)
    monkeypatch.setattr(backup_service,"APP_DATA_DIR",tmp_path/"appdata")
    database.initialize_database()
    return db_path,docs


def add_document(db_path: Path,docs: Path, *, name="stored.pdf",content=b"%PDF-fake-phase10"):
    path=docs/name; path.write_bytes(content)
    document_id="document-"+name.replace(".","-")
    with database.get_connection() as db:
        db.execute("INSERT INTO documents(id,original_filename,stored_filename,file_size_bytes,page_count,uploaded_at,extraction_status,extracted_text,content_sha256) VALUES(?,?,?,?,?,?,?,?,?)",
            (document_id,"notes, 世界.pdf",name,len(content),2,"2026-10-21T10:00:00+05:30","ready","secret PDF text",hashlib.sha256(content).hexdigest()))
        db.execute("INSERT INTO document_ocr_pages(document_id,page_number,status,recognized_text) VALUES(?,1,'complete','scanned page')",(document_id,))
        db.execute("INSERT INTO document_index_status(document_id,status,chunk_count) VALUES(?,'indexed',3)",(document_id,))
    return document_id,path


def test_online_sqlite_backup_integrity_preserves_source_and_wal_records(data_db):
    db_path,_=data_db
    with database.get_connection() as db: db.execute("INSERT INTO study_activity(activity_type,description,created_at) VALUES('test','保留 this','2026-10-21')")
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("INSERT INTO study_activity(activity_type,description,created_at) VALUES('wal','WAL row','2026-10-22')")
    before=hashlib.sha256(db_path.read_bytes()).hexdigest()
    artifact=create_database_backup(database_path=db_path)
    assert artifact.filename.endswith(".sqlite3") and artifact.kind=="database"
    assert validate_backup(artifact.content)["valid"] is True
    assert hashlib.sha256(db_path.read_bytes()).hexdigest()==before
    with sqlite3.connect(":memory:") as destination:
        source_file=data_db[0].parent/"from-download.sqlite3"; source_file.write_bytes(artifact.content)
        with sqlite3.connect(source_file) as source: source.backup(destination)
        assert destination.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert destination.execute("SELECT COUNT(*) FROM study_activity").fetchone()[0]==2


def test_complete_backup_contains_relative_pdf_and_manifest_but_no_cache(data_db,tmp_path):
    db_path,docs=data_db; doc_id,_=add_document(db_path,docs)
    artifact=create_complete_backup(database_path=db_path,documents_dir=docs)
    assert artifact.filename.endswith(".zip")
    with zipfile.ZipFile(io.BytesIO(artifact.content)) as archive:
        assert set(archive.namelist())=={"manifest.json","database.sqlite3","documents/stored.pdf"}
        manifest=json.loads(archive.read("manifest.json"))
        assert manifest["format_version"]==1 and manifest["documents"][0]["id"]==doc_id
        assert manifest["documents"][0]["archive_path"]=="documents/stored.pdf"
        assert "faiss" in manifest["cache_policy"].lower()
    validated=validate_backup(artifact.content)
    assert validated["kind"]=="complete" and validated["document_files"]==1


def test_complete_backup_missing_pdf_fails_without_claiming_success(data_db):
    db_path,docs=data_db; add_document(db_path,docs); (docs/"stored.pdf").unlink()
    with pytest.raises(BackupError,match="missing"):
        create_complete_backup(database_path=db_path,documents_dir=docs)
    assert create_database_backup(database_path=db_path).kind=="database"


def test_complete_backup_rejects_source_symlink(data_db,tmp_path):
    db_path,docs=data_db
    target=tmp_path/"outside.pdf"; target.write_bytes(b"outside")
    link=docs/"stored.pdf"
    try: link.symlink_to(target)
    except (OSError,NotImplementedError): pytest.skip("Symlink creation is unavailable on this Windows setup.")
    with database.get_connection() as db:
        db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?)",("doc","outside.pdf","stored.pdf",7,1,"now","ready","text",hashlib.sha256(b"outside").hexdigest()))
    with pytest.raises(BackupError,match="symbolic link"):
        create_complete_backup(database_path=db_path,documents_dir=docs)


def test_restore_is_confirmation_gated_and_retains_safety_backup(data_db):
    db_path,_=data_db
    with database.get_connection() as db: db.execute("INSERT INTO study_activity(activity_type,description,created_at) VALUES('old','from backup','2026-01-01')")
    backup=create_database_backup(database_path=db_path)
    with database.get_connection() as db: db.execute("INSERT INTO study_activity(activity_type,description,created_at) VALUES('new','discard on restore','2026-01-02')")
    with pytest.raises(BackupError,match="not confirmed"): restore_backup(backup.content,confirmed=False,database_path=db_path)
    with database.get_connection() as db: assert db.execute("SELECT COUNT(*) FROM study_activity WHERE activity_type='new'").fetchone()[0]==1
    result=restore_backup(backup.content,confirmed=True,database_path=db_path)
    assert result["safety_backup"] and (backup_service.APP_DATA_DIR/"backups"/result["safety_backup"]).is_file()
    with database.get_connection() as db:
        assert db.execute("SELECT COUNT(*) FROM study_activity WHERE activity_type='new'").fetchone()[0]==0
        assert db.execute("SELECT description FROM study_activity WHERE activity_type='old'").fetchone()[0]=="from backup"
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"


def test_complete_restore_recovers_pdf_and_safely_renames_conflict(data_db):
    db_path,docs=data_db; doc_id,pdf=add_document(db_path,docs)
    artifact=create_complete_backup(database_path=db_path,documents_dir=docs)
    pdf.write_bytes(b"different local file")
    result=restore_backup(artifact.content,confirmed=True,database_path=db_path,documents_dir=docs)
    assert result["document_files_restored"]==1
    with database.get_connection() as db:
        row=db.execute("SELECT stored_filename,original_filename FROM documents WHERE id=?",(doc_id,)).fetchone()
    assert row["original_filename"]=="notes, 世界.pdf" and row["stored_filename"]!="stored.pdf"
    assert (docs/row["stored_filename"]).read_bytes()==b"%PDF-fake-phase10"
    assert pdf.read_bytes()==b"different local file"


def test_restore_failure_before_activation_keeps_live_database_and_safety(data_db,tmp_path):
    db_path,docs=data_db; add_document(db_path,docs)
    archive=create_complete_backup(database_path=db_path,documents_dir=docs)
    with database.get_connection() as db: db.execute("INSERT INTO study_activity(activity_type,description,created_at) VALUES('live','keep','today')")
    invalid_root=tmp_path/"not-a-folder"; invalid_root.write_text("preserve",encoding="utf-8")
    with pytest.raises(BackupError): restore_backup(archive.content,confirmed=True,database_path=db_path,documents_dir=invalid_root)
    with database.get_connection() as db: assert db.execute("SELECT COUNT(*) FROM study_activity WHERE activity_type='live'").fetchone()[0]==1
    safety=list((backup_service.APP_DATA_DIR/"backups").glob("studymate-safety-*.sqlite3"))
    assert safety


@pytest.mark.parametrize("payload",[b"",b"not sqlite",b"SQLite format 3\x00broken"])
def test_invalid_backup_payloads_rejected(payload):
    with pytest.raises(BackupError): validate_backup(payload)


def test_future_schema_and_missing_required_tables_rejected(data_db,tmp_path):
    db_path,_=data_db
    future=tmp_path/"future.sqlite3"; shutil.copyfile(db_path,future)
    with sqlite3.connect(future) as db: db.execute("PRAGMA user_version=11")
    with pytest.raises(BackupError,match="newer"): validate_backup(future.read_bytes())
    minimal=tmp_path/"minimal.sqlite3"
    with sqlite3.connect(minimal) as db:
        db.execute("CREATE TABLE study_activity(id INTEGER)"); db.execute("CREATE TABLE documents(id TEXT)")
    with pytest.raises(BackupError,match="required"): validate_backup(minimal.read_bytes())


def test_unsupported_archive_version_and_traversal_are_rejected(data_db):
    db_path,docs=data_db; add_document(db_path,docs)
    artifact=create_complete_backup(database_path=db_path,documents_dir=docs)
    with zipfile.ZipFile(io.BytesIO(artifact.content)) as source:
        entries={name:source.read(name) for name in source.namelist()}
    manifest=json.loads(entries["manifest.json"]); manifest["format_version"]=99
    entries["manifest.json"]=json.dumps(manifest).encode()
    out=io.BytesIO()
    with zipfile.ZipFile(out,"w") as archive:
        for name,content in entries.items(): archive.writestr(name,content)
    with pytest.raises(BackupError,match="version"): validate_backup(out.getvalue())
    out=io.BytesIO()
    with zipfile.ZipFile(out,"w") as archive:
        archive.writestr("../escape.txt",b"bad")
    with pytest.raises(BackupError,match="unsafe path"): validate_backup(out.getvalue())


def test_older_schema_is_migrated_in_staging_before_restore(data_db,tmp_path):
    db_path,_=data_db; older=tmp_path/"older.sqlite3"; shutil.copyfile(db_path,older)
    with sqlite3.connect(older) as db:
        db.execute("PRAGMA user_version=9")
        for table in ("planner_sessions","planner_tasks","planner_topics","planner_exams","planner_subjects","planner_settings"):
            db.execute(f"DROP TABLE {table}")
    metadata=validate_backup(older.read_bytes())
    assert metadata["schema_version"]==9
    result=restore_backup(older.read_bytes(),confirmed=True,database_path=db_path)
    assert result["metadata"]["schema_version"]==database.DATABASE_SCHEMA_VERSION
    with sqlite3.connect(db_path) as db: assert db.execute("SELECT 1 FROM sqlite_master WHERE name='planner_tasks'").fetchone()


def test_json_csv_exports_handle_unicode_formula_cells_and_exclude_chat(data_db):
    db_path,_=data_db
    with database.get_connection() as db:
        db.execute("INSERT INTO study_activity(activity_type,description,created_at) VALUES(?,?,?)",("note",'=HYPERLINK("x")\n學習, "quoted"','2026-10-21'))
        db.execute("INSERT INTO study_activity(activity_type,description,created_at) VALUES(?,?,?)",("secret","AIza"+"A"*36,"2026-10-21"))
        db.execute("INSERT INTO chat_conversations(id,title,created_at,updated_at) VALUES('chat','private conversation','now','now')")
        db.execute("INSERT INTO chat_messages(id,conversation_id,role,content,created_at) VALUES('m','chat','user','transcript','now')")
    exported=json.loads(export_json(["study_activity"],database_path=db_path).decode("utf-8"))
    assert exported["format"]=="studymate-data-export" and exported["export_schema_version"]==1
    serialized=json.dumps(exported,ensure_ascii=False)
    assert "學習" in serialized and "[REDACTED API KEY]" in serialized and "transcript" not in serialized
    csv_text=export_csv("study_activity",database_path=db_path).decode("utf-8-sig")
    rows=list(csv.DictReader(io.StringIO(csv_text)))
    formula_row=next(row for row in rows if "學習" in row["description"])
    assert formula_row["description"].startswith("'") and "\r\n" in csv_text


def test_export_scopes_preserve_flashcard_and_planner_relationships(data_db):
    db_path,_=data_db; deck=create_deck("Science, 世界"); card=create_card(deck,front="Question, ‘x’",back="Answer",explanation="why",topic="Cell",source_pages=[2])
    subject=save_subject("Biology"); exam=save_exam("Final","2026-12-01",subject_id=subject); task=create_task("Review","2026-11-30",25,exam_id=exam,subject_id=subject); record_session(22,task_id=task)
    exported=json.loads(export_json(["flashcards","planner","flashcard_reviews"],database_path=db_path))
    item=exported["records"]["flashcards"][0]
    assert item["deck_id"]==deck and item["card_id"]==card and item["source_pages"]==[2]
    assert "Self-rated recall" in item["recall_metric_label"]
    assert exported["records"]["planner"]["tasks"][0]["exam_id"]==exam
    assert exported["records"]["planner"]["study_sessions"][0]["task_id"]==task


def test_empty_exports_are_valid_utf8_and_do_not_mutate_database(data_db):
    db_path,_=data_db
    before=hashlib.sha256(db_path.read_bytes()).hexdigest()
    obj=json.loads(export_json(["quiz_results","documents"],database_path=db_path))
    assert obj["records"]=={"quiz_results":[],"documents":[]}
    csv_bytes=export_csv("quiz_results",database_path=db_path)
    assert csv_bytes.startswith(b"\xef\xbb\xbf") and b"export_schema_version" in csv_bytes
    assert hashlib.sha256(db_path.read_bytes()).hexdigest()==before
    with pytest.raises(ExportError): export_json([])


def test_study_report_uses_saved_data_periods_and_keeps_metrics_separate(data_db):
    db_path,_=data_db; subject=save_subject("Math"); save_exam("Midterm",date.today()+timedelta(days=5),subject_id=subject)
    task=create_task("Practice","2026-10-21",35,subject_id=subject)
    record_session(24,task_id=task,note="actual")
    with database.get_connection() as db:
        db.execute("INSERT INTO quizzes(id,title,source_filename,difficulty,requested_question_count,created_at) VALUES('q','Quiz','notes.pdf','Easy',5,'2026-10-20')")
        db.execute("INSERT INTO quiz_attempts(id,quiz_id,started_at,submitted_at,status,total_questions,total_possible_marks,correct_answers,incorrect_answers,unanswered_questions,percentage,duration_seconds) VALUES('a','q','2026-10-20','2026-10-21','completed',5,5,4,1,0,80,60)")
        db.execute("INSERT INTO flashcard_decks(id,title,created_at,updated_at) VALUES('d','Deck','now','now')")
        db.execute("INSERT INTO flashcards(id,deck_id,front,back,created_at,updated_at) VALUES('c','d','Q','A','now','now')")
        db.execute("INSERT INTO flashcard_schedule(card_id,due_at) VALUES('c','2026-10-21')")
        db.execute("INSERT INTO flashcard_reviews(id,card_id,session_id,rating,reviewed_at,previous_due_at,new_due_at,previous_interval_days,new_interval_days,recall_successful) VALUES('r','c','s','good','2026-10-21','2026-10-20','2026-10-22',0,1,1)")
    report=build_study_report(period_days=7,today=date(2026,10,21),database_path=db_path)
    assert report["quiz"]["average_score"]==80 and report["quiz"]["overall_accuracy"] is None
    assert report["flashcards"]["total_reviews"]==1 and report["flashcards"]["ratings"]["good"]==1
    assert report["planner"]["planned_minutes"]==35 and report["planner"]["recorded_minutes"]==24
    assert report["exams"][0]["name"]=="Midterm"
    filename,html,again=create_study_report(period_days=7,today=date(2026,10,21),database_path=db_path)
    assert filename.endswith(".html") and b"self-rated" in html.lower() and again["quiz"]["average_score"]==80
    assert build_study_report(period_days=30,today=date(2026,10,21),database_path=db_path)["quiz"]["completed_attempts"]==1


def test_empty_report_period_and_html_escape(data_db):
    db_path,_=data_db
    report=build_study_report(period_days=7,today=date(2026,10,21),database_path=db_path)
    assert report["empty"] and report["quiz"]["average_score"] is None
    _,content,_=create_study_report(period_days=7,today=date(2026,10,21),database_path=db_path)
    assert b"no saved study records" in content.lower()
    with pytest.raises(ReportError): build_study_report(period_days=5,database_path=db_path)


def test_data_management_page_renders_without_gemini_or_external_models(data_db,monkeypatch):
    db_path,_=data_db
    monkeypatch.setattr("utils.config.DATABASE_PATH",db_path)
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/"app.py").run()
    app.session_state["active_page"]="Data Management"
    app.run()
    assert not app.exception
    assert any("uncrypted" in warning.value.lower() or "not encrypted" in warning.value.lower() for warning in app.warning)
