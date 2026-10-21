"""User-controlled local backup, restore, export, and report downloads."""
import hashlib
from datetime import date

import streamlit as st

from utils.backup_service import (
    BackupError, create_complete_backup, create_database_backup,
    restore_backup, validate_backup,
)
from utils.data_export_service import EXPORTABLE_SCOPES, ExportError, export_csv, export_json
from utils.database import get_connection
from utils.study_report_service import PERIODS, ReportError, create_study_report

SCOPE_LABELS={
    "quiz_results":"Quiz results and answer review",
    "flashcards":"Flashcards",
    "flashcard_reviews":"Flashcard review history",
    "planner":"Planner subjects, topics, exams, tasks, and sessions",
    "study_activity":"Study activity summaries",
    "documents":"Document, OCR, and indexing metadata",
}


def render_data_management() -> None:
    st.markdown('<div class="eyebrow">YOUR LOCAL RECORDS</div>',unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">Data Management</h1>',unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">Back up your StudyMate library, restore a verified copy, or export selected records.</p>',unsafe_allow_html=True)
    st.warning("Backups and exports can contain private study records and PDFs. Store downloaded files securely. Backup files are not encrypted.")
    feedback=st.session_state.pop("data_restore_feedback",None)
    if feedback: st.success(feedback)
    tabs=st.tabs(["Create backup","Restore backup","Export data","Study report","Guidance"])
    with tabs[0]: _backup_section()
    with tabs[1]: _restore_section()
    with tabs[2]: _export_section()
    with tabs[3]: _report_section()
    with tabs[4]: _guidance()


def _backup_section() -> None:
    st.subheader("Create a backup")
    try:
        with get_connection() as db:
            documents=db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            attempts=db.execute("SELECT COUNT(*) FROM quiz_attempts").fetchone()[0]
        st.caption(f"Current local records: {documents} document(s) · {attempts} quiz attempt(s).")
    except Exception:
        st.caption("Local database summary is currently unavailable.")
    kind=st.radio("Backup contents",["complete","database"],format_func=lambda value:"Complete backup (database + uploaded PDFs)" if value=="complete" else "Database only (SQLite records; no PDF files)",key="backup_kind")
    if kind=="complete":
        st.info("The complete archive includes a consistent SQLite snapshot and each PDF referenced by your document library. Rebuildable FAISS caches, OCR render images, secrets, and unrelated files are excluded.")
    else:
        st.info("The SQLite snapshot includes extracted text, OCR metadata, quizzes, flashcards, planner records, and activity. It does not include the external uploaded PDF files.")
    if st.button("Create and validate backup",type="primary",key="create_backup"):
        try:
            artifact=create_complete_backup() if kind=="complete" else create_database_backup()
            st.session_state["backup_download"]={"filename":artifact.filename,"content":artifact.content,"kind":artifact.kind,"metadata":artifact.metadata}
            st.success(f"Backup created and validated · {len(artifact.content):,} bytes.")
        except BackupError as error: st.error(str(error))
    artifact=st.session_state.get("backup_download")
    if artifact:
        st.download_button("Download backup",data=artifact["content"],file_name=artifact["filename"],mime="application/zip" if artifact["kind"]=="complete" else "application/vnd.sqlite3",key="download_backup")
        st.caption(f"Verified schema version {artifact['metadata']['schema_version']} · {artifact['metadata']['document_count']} database document record(s).")


def _restore_section() -> None:
    st.subheader("Restore a validated backup")
    uploaded=st.file_uploader("Select a StudyMate SQLite backup or complete ZIP",type=["sqlite3","sqlite","db","zip"],max_upload_size=512,key="restore_upload")
    if uploaded is None:
        st.caption("Choose a database snapshot or a versioned complete-backup archive (maximum 512 MiB).")
        st.session_state.pop("validated_restore",None)
        return
    payload=uploaded.getvalue()
    digest=hashlib.sha256(payload).hexdigest()
    state=st.session_state.get("validated_restore")
    if state and state.get("digest")!=digest:
        st.session_state.pop("validated_restore",None); state=None
    if st.button("Validate selected backup",key="validate_restore"):
        try:
            metadata=validate_backup(payload)
            st.session_state["validated_restore"]={"digest":digest,"payload":payload,"filename":uploaded.name,"metadata":metadata}
            state=st.session_state["validated_restore"]
        except BackupError as error:
            st.session_state.pop("validated_restore",None); st.error(str(error)); state=None
    state=st.session_state.get("validated_restore")
    if state:
        details=state["metadata"]
        st.success(f"Verified {details['kind']} backup · format v{details['format_version']} · database schema v{details['schema_version']} · {details['document_count']} document record(s).")
        if details["kind"]=="complete": st.caption(f"The archive contains {details['document_files']} PDF file(s); restore will reuse identical existing files and safely rename conflicting files.")
        else: st.warning("This database-only backup does not include external PDFs. Existing local PDF files will not be changed; restored document records may refer to files unavailable on this computer.")
        st.error("Restoring replaces current SQLite records with the selected backup. A separate safety database backup is created first when the current database exists. Any newly restored PDF files are placed in the configured document folder without overwriting existing files. Keep the app open until the operation finishes; no restart is required after success.")
        confirm=st.checkbox("I understand that restoration replaces the current database records.",key=f"confirm_restore_{digest[:12]}")
        if st.button("Restore and replace current records",type="primary",disabled=not confirm,key=f"restore_button_{digest[:12]}"):
            try:
                result=restore_backup(state["payload"],confirmed=True)
                safety=f" Safety backup retained as {result['safety_backup']}." if result["safety_backup"] else " There was no existing database to back up."
                st.session_state["data_restore_feedback"]=f"Restore completed and database integrity was verified.{safety}"
                st.session_state.pop("validated_restore",None)
                st.rerun()
            except BackupError as error: st.error(str(error))


def _export_section() -> None:
    st.subheader("Export selected records")
    st.caption("Exports use explicit record categories. Chat transcripts, API keys, environment variables, secrets, raw PDF text, and FAISS vectors are not included.")
    format_choice=st.radio("Format",["JSON","CSV"],horizontal=True,key="export_format")
    scopes=st.multiselect("Categories",options=list(EXPORTABLE_SCOPES),format_func=lambda key:SCOPE_LABELS[key],default=["quiz_results","flashcards","planner"],key="export_scopes")
    archived=st.checkbox("Include archived flashcard decks and cards",value=False,key="export_archived")
    if format_choice=="CSV" and len(scopes)>1:
        st.info("CSV contains one category per file. Choose a single category, or select JSON for a multi-category export.")
    if st.button("Prepare export",key="prepare_export",disabled=not scopes or (format_choice=="CSV" and len(scopes)!=1)):
        try:
            if format_choice=="JSON":
                payload=export_json(scopes,include_archived=archived); name=f"studymate-export-{date.today().strftime('%Y%m%d')}.json"; mime="application/json"
            else:
                payload=export_csv(scopes[0],include_archived=archived); name=f"studymate-{scopes[0]}-{date.today().strftime('%Y%m%d')}.csv"; mime="text/csv"
            st.session_state["prepared_export"]={"content":payload,"filename":name,"mime":mime}
            st.success(f"Export prepared · {len(payload):,} bytes.")
        except ExportError as error: st.error(str(error))
    prepared=st.session_state.get("prepared_export")
    if prepared:
        st.download_button("Download selected export",data=prepared["content"],file_name=prepared["filename"],mime=prepared["mime"],key="download_export")


def _report_section() -> None:
    st.subheader("Generate a study report")
    period=st.selectbox("Reporting period",options=list(PERIODS),format_func=lambda value:PERIODS[value],index=1,key="report_period")
    st.caption("Report calculations use local calendar dates from saved records. Quiz performance, self-rated flashcard recall, planned minutes, and recorded study time are shown separately.")
    if st.button("Generate HTML report",key="generate_report"):
        try:
            filename,content,report=create_study_report(period_days=period)
            st.session_state["prepared_report"]={"filename":filename,"content":content,"summary":report}
        except ReportError as error: st.error(str(error))
    prepared=st.session_state.get("prepared_report")
    if prepared:
        data=prepared["summary"]
        if data["empty"]: st.info("No study records were found for this period; the report will show a genuine empty state.")
        else:
            a,b,c,d=st.columns(4)
            a.metric("Quiz attempts",data["quiz"]["completed_attempts"])
            b.metric("Average quiz score",f"{data['quiz']['average_score']:.1f}%" if data["quiz"]["average_score"] is not None else "—")
            c.metric("Flashcard reviews",data["flashcards"]["total_reviews"])
            d.metric("Study time recorded",f"{data['planner']['recorded_minutes']} min")
        st.download_button("Download HTML study report",data=prepared["content"],file_name=prepared["filename"],mime="text/html",key="download_report")


def _guidance() -> None:
    st.subheader("Backup and export guidance")
    st.markdown("""
- **Database backup (`.sqlite3`):** a point-in-time SQLite online backup with all app tables, indexes, quiz/flashcard/planner history, extracted PDF text, OCR metadata, and stored semantic vectors. It does **not** include uploaded PDF files stored outside SQLite.
- **Complete backup (`.zip`):** a versioned `manifest.json`, the validated SQLite snapshot, and the original PDF files referenced by the document library. The manifest lists relative paths, file sizes, and SHA-256 checksums. Rebuildable FAISS cache, secrets, environment variables, logs, temporary files, and arbitrary folder contents are excluded. A complete archive is not encrypted and is limited to 512 MiB.
- **Restore:** the selected SQLite file or archive is validated before confirmation. Restore creates a persistent safety SQLite backup in the StudyMate app-data `backups` directory, migrates an older supported database in staging, checkpoints the live database, then atomically replaces it. Existing PDFs are never overwritten; identical files are reused and filename conflicts receive a fresh generated filename. A failed restore keeps the safety backup. No app restart is required because database connections are opened and closed per operation and are not cached.
- **Compatibility:** the manifest format is version 1. Databases at or below the current schema version 10 are accepted when the core StudyMate tables exist; older supported databases are upgraded additively before activation. A future schema version is rejected. Do not assume backup compatibility with future app versions.
- **Exports:** JSON can include multiple selected categories; CSV exports one category at a time and uses UTF-8 with spreadsheet-safe formula handling. Quiz exports include saved question/answer review details. Flashcard JSON preserves deck/card relationships and uses schema version 1; archived cards are optional. Review history is labeled as self-reported recall. There is no card importer in this phase. Document exports contain metadata only, never PDF text. Conversation transcripts are not exported.
- **Study reports:** download a standalone HTML file for 7, 30, 90, 365 days, or all time. Metrics come from persisted records; quiz scores and flashcard recall are separate, and estimated planned minutes are separate from actual session durations.
- Backup and export files may contain private study information. Store them securely. The downloaded files are unencrypted. Temporary validation and staging files use the operating system's temporary folder and are removed when the operation finishes; retained pre-restore safety backups are intentional.
""")
