"""Explicit, versioned data exports from safe application tables only."""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from utils import database

EXPORT_VERSION = 1
EXPORTABLE_SCOPES = {
    "quiz_results", "flashcards", "flashcard_reviews", "planner", "study_activity", "documents"
}


class ExportError(ValueError):
    """Safe error for a selected data export."""


def _decode(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value is not None else default
    except (json.JSONDecodeError, TypeError):
        return default


def _redact(value: Any) -> Any:
    if isinstance(value,str):
        return re.sub(r"AIza[0-9A-Za-z_-]{30,}","[REDACTED API KEY]",value)
    if isinstance(value,list): return [_redact(item) for item in value]
    if isinstance(value,dict): return {key:_redact(item) for key,item in value.items()}
    return value


def _quiz_results(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    attempts=connection.execute("""SELECT a.*,q.title AS quiz_title,q.source_filename,q.difficulty
        FROM quiz_attempts a JOIN quizzes q ON q.id=a.quiz_id ORDER BY a.started_at DESC""").fetchall()
    results=[]
    for attempt in attempts:
        questions=connection.execute("""SELECT aq.position,q.id AS question_id,q.question_text,q.options_json,
            q.correct_option_id,q.explanation,q.topic,q.source_pages_json,
            ans.selected_option_id,ans.is_correct,ans.awarded_marks
            FROM quiz_attempt_questions aq JOIN quiz_questions q ON q.id=aq.question_id
            LEFT JOIN quiz_answers ans ON ans.attempt_id=aq.attempt_id AND ans.question_id=aq.question_id
            WHERE aq.attempt_id=? ORDER BY aq.position""",(attempt["id"],)).fetchall()
        item=dict(attempt); item["attempt_id"]=item.pop("id")
        item["answers"]=[]
        for question in questions:
            answer=dict(question)
            answer["options"]=_decode(answer.pop("options_json"),[])
            answer["source_pages"]=_decode(answer.pop("source_pages_json"),[])
            item["answers"].append(answer)
        results.append(item)
    return results


def _flashcards(connection: sqlite3.Connection, *, include_archived: bool) -> list[dict[str, Any]]:
    where="" if include_archived else "WHERE c.archived_at IS NULL AND d.archived_at IS NULL"
    rows=connection.execute(f"""SELECT c.id AS card_id,c.deck_id,d.title AS deck_title,d.archived_at AS deck_archived_at,
        c.front,c.back,c.explanation,c.topic,c.difficulty,c.source_filename,c.source_pages_json,
        c.created_at,c.updated_at,c.archived_at,s.review_count,s.successful_recall_count,s.lapse_count,
        s.interval_days,s.ease_factor,s.due_at,s.last_reviewed_at,s.learning_state
        FROM flashcards c JOIN flashcard_decks d ON d.id=c.deck_id JOIN flashcard_schedule s ON s.card_id=c.id
        {where} ORDER BY d.title COLLATE NOCASE,c.created_at,c.id""").fetchall()
    result=[]
    for row in rows:
        card=dict(row); card["source_pages"]=_decode(card.pop("source_pages_json"),[])
        card["recall_metric_label"]="Self-rated recall; not an objective score"
        result.append(card)
    return result


def _flashcard_reviews(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows=connection.execute("""SELECT r.id AS review_id,r.session_id,r.card_id,c.front,c.topic,
        d.id AS deck_id,d.title AS deck_title,r.rating,r.reviewed_at,r.previous_due_at,r.new_due_at,
        r.previous_interval_days,r.new_interval_days,r.was_new,r.recall_successful
        FROM flashcard_reviews r JOIN flashcards c ON c.id=r.card_id
        JOIN flashcard_decks d ON d.id=c.deck_id ORDER BY r.reviewed_at DESC,r.id""").fetchall()
    return [{**dict(row),"recall_definition":"Self-reported recall rating"} for row in rows]


def _planner(connection: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    return {
        "subjects":[dict(row) for row in connection.execute("SELECT id,name,description,created_at,updated_at,archived_at FROM planner_subjects ORDER BY created_at")],
        "topics":[dict(row) for row in connection.execute("SELECT id,subject_id,exam_id,name,description,estimated_minutes,priority,source_document_id,source_filename,completed_at,created_at,updated_at,archived_at FROM planner_topics ORDER BY created_at")],
        "exams":[dict(row) for row in connection.execute("SELECT id,name,subject_id,subject_name,exam_date,start_time,location,notes,status,created_at,updated_at FROM planner_exams ORDER BY exam_date")],
        "tasks":[dict(row) for row in connection.execute("SELECT id,title,description,task_type,subject_id,topic_id,exam_id,document_id,document_filename,quiz_id,planned_date,estimated_minutes,priority,status,created_at,updated_at,completed_at,completed_minutes FROM planner_tasks ORDER BY planned_date,created_at")],
        "study_sessions":[dict(row) for row in connection.execute("SELECT id,task_id,subject_id,topic_id,exam_id,duration_minutes,note,started_at,completed_at FROM planner_sessions ORDER BY completed_at DESC")],
        "settings":[dict(row) for row in connection.execute("SELECT daily_goal_minutes,available_minutes,study_days,max_tasks_per_day,updated_at FROM planner_settings ORDER BY id")],
    }


def _activity(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    # Explicit field allowlist; activity descriptions are saved summaries, not prompts.
    return [dict(row) for row in connection.execute("SELECT activity_type,description,score,created_at FROM study_activity ORDER BY created_at DESC,id DESC")]


def _documents(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows=connection.execute("""SELECT d.id,d.original_filename,d.file_size_bytes,d.page_count,d.uploaded_at,
        d.extraction_status,d.content_sha256,s.status AS index_status,s.chunk_count,s.model_name,s.indexed_at,
        (SELECT COUNT(*) FROM document_ocr_pages o WHERE o.document_id=d.id) AS ocr_pages_recorded,
        (SELECT COUNT(*) FROM document_ocr_pages o WHERE o.document_id=d.id AND o.status IN ('complete','empty')) AS ocr_pages_processed
        FROM documents d LEFT JOIN document_index_status s ON s.document_id=d.id
        ORDER BY d.uploaded_at DESC,d.original_filename COLLATE NOCASE""").fetchall()
    return [dict(row) for row in rows]


def build_export(scopes: Iterable[str], *, include_archived: bool=False,
                 database_path=None) -> dict[str, Any]:
    selected=list(dict.fromkeys(scopes))
    if not selected or any(scope not in EXPORTABLE_SCOPES for scope in selected):
        raise ExportError("Select one or more supported data categories to export.")
    try:
        with database.get_connection(database_path) as connection:
            tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            values={}
            for scope in selected:
                if scope not in tables and scope=="study_activity":
                    values[scope]=[]
                elif scope=="quiz_results": values[scope]=_quiz_results(connection)
                elif scope=="flashcards": values[scope]=_flashcards(connection,include_archived=include_archived)
                elif scope=="flashcard_reviews": values[scope]=_flashcard_reviews(connection)
                elif scope=="planner": values[scope]=_planner(connection)
                elif scope=="study_activity": values[scope]=_activity(connection)
                elif scope=="documents": values[scope]=_documents(connection)
            version=connection.execute("PRAGMA user_version").fetchone()[0]
        return _redact({"format":"studymate-data-export","export_schema_version":EXPORT_VERSION,
                "database_schema_version":version,"generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "included_scopes":selected,"records":values})
    except ExportError:
        raise
    except (sqlite3.Error,ValueError,TypeError) as error:
        raise ExportError("The selected data could not be exported from the local database.") from error


def export_json(scopes: Iterable[str], *, include_archived: bool=False, database_path=None) -> bytes:
    content=json.dumps(build_export(scopes,include_archived=include_archived,database_path=database_path),ensure_ascii=False,indent=2)
    return content.encode("utf-8")


def _csv_safe(value: Any) -> Any:
    if isinstance(value,str):
        value=_redact(value)
        candidate=value.lstrip()
        if candidate.startswith(("=","+","-","@")):
            return "'"+value
        return value
    if isinstance(value,(dict,list,tuple)):
        return json.dumps(_redact(value),ensure_ascii=False,separators=(",",":"))
    return value


def export_csv(scope: str, *, include_archived: bool=False, database_path=None) -> bytes:
    """Export one selected category as spreadsheet-compatible UTF-8 CSV."""
    data=build_export([scope],include_archived=include_archived,database_path=database_path)["records"][scope]
    if isinstance(data,dict):
        rows=[]
        for group,items in data.items():
            rows.extend({"record_type":group,**item} for item in items)
    else:
        rows=data
    fields=["record_type","export_schema_version","database_schema_version"]
    for row in rows:
        for key in row:
            if key not in fields: fields.append(key)
    output=io.StringIO(newline="")
    writer=csv.DictWriter(output,fieldnames=fields,extrasaction="ignore",lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        normalized={key:_csv_safe(value) for key,value in row.items()}
        normalized["export_schema_version"]=EXPORT_VERSION
        normalized["database_schema_version"]=database.DATABASE_SCHEMA_VERSION
        writer.writerow(normalized)
    return output.getvalue().encode("utf-8-sig")
