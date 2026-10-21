"""Phase 9 planner persistence, scheduling, and integration tests."""
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from utils import database
from utils.flashcard_repository import create_card, create_deck
from utils.planner_service import (
    PlannerError, archive_subject, create_task, generate_plan, get_due_flashcard_summary,
    get_planner_metrics, get_settings, list_exams, list_sessions, list_tasks,
    list_topics, record_session, record_task_session, reschedule_preview,
    save_exam, save_plan, save_settings, save_subject, save_topic,
    set_exam_status, set_task_status, set_topic_completed,
    archive_topic, update_task,
)


@pytest.fixture
def planner_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database,"DATABASE_PATH",tmp_path/"planner.sqlite3")
    database.initialize_database()
    return database.DATABASE_PATH


def test_additive_schema_preserves_old_activity_and_reinitializes(planner_db):
    with database.get_connection() as db:
        db.execute("INSERT INTO study_activity(activity_type,description,created_at) VALUES('legacy','keep','2026-01-01')")
    database.initialize_database()
    with database.get_connection() as db:
        assert db.execute("SELECT description FROM study_activity").fetchone()[0]=="keep"
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='planner_sessions'").fetchone()


def test_subject_topic_exam_crud_archive_and_document_deletion(planner_db):
    subject=save_subject("Physics"); exam=save_exam("Final",date.today()+timedelta(days=10),subject_id=subject)
    save_exam("Physics final",date.today()+timedelta(days=11),exam_id=exam,subject_id=subject)
    assert list_exams()[0]["name"]=="Physics final"
    doc="doc1"
    with database.get_connection() as db:
        db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?)",(doc,"notes.pdf","stored.pdf",12,1,"now","ready","text","hash"))
    topic=save_topic(subject,"Mechanics",exam_id=exam,estimated_minutes=90,priority=5,source_document_id=doc)
    save_topic(subject,"Mechanics updated",topic_id=topic,exam_id=exam,estimated_minutes=75,priority=4,source_document_id=doc)
    assert list_topics(subject_id=subject)[0]["name"]=="Mechanics updated"
    assert list_topics(subject_id=subject)[0]["source_filename"]=="notes.pdf"
    with database.get_connection() as db: db.execute("DELETE FROM documents WHERE id=?",(doc,))
    assert list_topics()[0]["source_document_id"] is None
    assert list_topics()[0]["source_filename"]=="notes.pdf"
    set_topic_completed(topic); assert list_topics()[0]["completed_at"]
    archive_subject(subject); assert list_topics()==[]
    assert list_exams()[0]["subject_name"]=="Physics"


@pytest.mark.parametrize("kwargs",[
    {"name":" ","exam_date":"2026-11-01","subject_name":"Chem"},
    {"name":"Exam","exam_date":"not a date","subject_name":"Chem"},
    {"name":"Exam","exam_date":"2026-11-01","subject_name":"Chem","start_time":"25:01"},
])
def test_exam_validation(kwargs,planner_db):
    with pytest.raises(PlannerError): save_exam(**kwargs)


def test_exam_today_and_passed_dates_are_calendar_dates(planner_db):
    today=date(2026,10,21)
    subject=save_subject("Chemistry")
    today_exam=save_exam("Today",today,subject_id=subject)
    past=save_exam("Past",today-timedelta(days=1),subject_id=subject)
    rows={r["id"]:r for r in list_exams()}
    assert rows[today_exam]["exam_date"]==today.isoformat()
    assert (today-date.fromisoformat(rows[past]["exam_date"])).days==1
    set_exam_status(past,"completed"); assert next(e for e in list_exams() if e["id"]==past)["status"]=="completed"
    archive_exam=save_exam("Archive me",today+timedelta(days=3),subject_id=save_subject("Biology"))
    related=create_task("Keep related work",today+timedelta(days=1),20,exam_id=archive_exam)
    set_exam_status(archive_exam,"archived")
    assert list_tasks()[0]["id"]==related


def test_settings_validate_days_and_preserve_defaults(planner_db):
    defaults=get_settings(); assert defaults["daily_goal_minutes"]==60 and defaults["study_days"]==[0,1,2,3,4]
    with pytest.raises(PlannerError): save_settings(daily_goal_minutes=30,available_minutes=40,study_days=[],max_tasks_per_day=2)
    save_settings(daily_goal_minutes=45,available_minutes=90,study_days=[0,2,4],max_tasks_per_day=3)
    assert get_settings()["study_days"]==[0,2,4]


def test_topic_archive_preserves_existing_tasks(planner_db):
    subject=save_subject("Art"); topic=save_topic(subject,"Color theory")
    task=create_task("Review color","2026-10-23",20,topic_id=topic)
    archive_topic(topic)
    assert list_topics()==[]
    assert list_tasks()[0]["topic_id"]==topic


def test_scheduler_distributes_priority_respects_rest_and_daily_budget():
    result=generate_plan(exam_date="2026-10-27",today="2026-10-21",study_days=[0,1,2,3,4,5,6],available_minutes=60,daily_goal_minutes=60,max_tasks_per_day=2,topics=[{"id":"high","name":"High","estimated_minutes":100,"priority":5},{"id":"low","name":"Low","estimated_minutes":100,"priority":2}])
    assert result["proposals"]
    by_day={}
    for item in result["proposals"]: by_day[item["planned_date"]]=by_day.get(item["planned_date"],0)+item["estimated_minutes"]
    assert max(by_day.values())<=60
    topic_days={}
    for item in result["proposals"]:
        if item["topic_id"]: topic_days.setdefault(item["topic_id"],set()).add(item["planned_date"])
        assert item["planned_date"]<"2026-10-27"
    assert len(topic_days["high"])>1
    rest=generate_plan(exam_date="2026-10-30",today="2026-10-21",study_days=[0],available_minutes=30,daily_goal_minutes=30,topics=[{"id":"a","name":"A","estimated_minutes":100}])
    assert all(date.fromisoformat(p["planned_date"]).weekday()==0 for p in rest["proposals"])


def test_scheduler_is_deterministic_reports_shortfall_and_tomorrow_limit():
    args=dict(exam_date="2026-10-23",today="2026-10-21",study_days=[2],available_minutes=30,daily_goal_minutes=30,topics=[{"id":"x","name":"X","estimated_minutes":300,"priority":5}])
    first=generate_plan(**args); second=generate_plan(**args)
    assert first==second and first["unscheduled"] and "could not be scheduled" in first["message"]
    tomorrow=generate_plan(exam_date="2026-10-22",today="2026-10-21",topics=[])
    assert "tomorrow" in tomorrow["message"]
    with pytest.raises(PlannerError): generate_plan(exam_date="2026-10-20",today="2026-10-21",topics=[])


def test_scheduler_enforces_max_task_count_and_review_respects_daily_capacity():
    result=generate_plan(exam_date="2026-10-25",today="2026-10-21",study_days=range(7),available_minutes=30,daily_goal_minutes=30,max_tasks_per_day=1,topics=[{"id":"a","name":"A","estimated_minutes":60},{"id":"b","name":"B","estimated_minutes":60}])
    count={}
    minutes={}
    for proposal in result["proposals"]:
        count[proposal["planned_date"]]=count.get(proposal["planned_date"],0)+1
        minutes[proposal["planned_date"]]=minutes.get(proposal["planned_date"],0)+proposal["estimated_minutes"]
    assert max(count.values())<=1 and max(minutes.values())<=30


def test_existing_tasks_reduce_capacity_and_plan_save_is_idempotent(planner_db):
    subject=save_subject("Biology"); exam=save_exam("Exam","2026-11-01",subject_id=subject); topic=save_topic(subject,"Cells",exam_id=exam,estimated_minutes=45)
    existing=create_task("Already planned","2026-10-22",40)
    plan=generate_plan(exam_date="2026-10-25",today="2026-10-22",topics=[{"id":topic,"name":"Cells","estimated_minutes":60}],study_days=range(7),available_minutes=60,daily_goal_minutes=60,existing_tasks=list_tasks())
    assert sum(p["estimated_minutes"] for p in plan["proposals"] if p["planned_date"]=="2026-10-22")<=20
    first=save_plan(exam,plan["proposals"]); second=save_plan(exam,plan["proposals"])
    assert first==second
    with pytest.raises(PlannerError): save_plan(exam,[{"title":"Bad","task_type":"general","planned_date":"2026-11-01","estimated_minutes":10}])


def test_scheduler_does_not_duplicate_already_planned_topic_purpose(planner_db):
    sub=save_subject("Computer Science"); exam=save_exam("Exam","2026-11-05",subject_id=sub); topic=save_topic(sub,"Trees",exam_id=exam,estimated_minutes=60)
    create_task("Revise Trees","2026-10-22",60,topic_id=topic,exam_id=exam,task_type="revise_topic")
    result=generate_plan(exam_date="2026-11-05",today="2026-10-21",topics=list_topics(exam_id=exam),existing_tasks=list_tasks())
    assert all(proposal["topic_id"]!=topic for proposal in result["proposals"])


def test_task_transitions_and_atomic_session_history(planner_db):
    subject=save_subject("History"); exam=save_exam("Exam","2026-12-01",subject_id=subject)
    task=create_task("Read chapter","2026-11-01",30,subject_id=subject,exam_id=exam)
    with pytest.raises(PlannerError): create_task("",date.today(),30)
    record_task_session(task,24,note="Reviewed chapter")
    first=list_tasks()[0]; assert first["status"]=="completed" and first["completed_minutes"]==24
    original=first["completed_at"]
    assert list_sessions()[0]["duration_minutes"]==24
    set_task_status(task,"planned"); assert list_tasks()[0]["completed_at"]==original
    update_task(task,title="Updated chapter",planned_date="2026-11-02",estimated_minutes=35,priority=5)
    assert list_tasks()[0]["title"]=="Updated chapter" and list_tasks()[0]["planned_date"]=="2026-11-02"
    set_task_status(task,"completed",completed_minutes=20)
    completed=list_tasks()[0]["completed_at"]
    preview=reschedule_preview([task],start_date="2026-11-02",confirmed=True)
    assert preview["changes"]==[] and list_tasks()[0]["completed_at"]==completed


def test_task_cancellation_overdue_reschedule_preview_and_exam_guard(planner_db):
    sub=save_subject("Math"); exam=save_exam("Soon","2026-10-25",subject_id=sub)
    late=create_task("Late","2026-10-20",25,exam_id=exam)
    future=create_task("Next","2026-10-21",30,exam_id=exam)
    assert get_planner_metrics(today="2026-10-21")["overdue_tasks"]==1
    before={t["id"]:t["planned_date"] for t in list_tasks()}
    preview=reschedule_preview([late,future],start_date="2026-10-21")
    assert preview["applied"] is False and {t["id"]:t["planned_date"] for t in list_tasks()}==before
    applied=reschedule_preview([late,future],start_date="2026-10-21",confirmed=True)
    assert applied["applied"] and all(x["planned_date"]<"2026-10-25" for x in applied["changes"])
    set_task_status(late,"cancelled")
    with pytest.raises(PlannerError): set_task_status(late,"completed")


def test_bulk_rebalance_includes_future_incomplete_but_preserves_completed_and_exams(planner_db):
    subject=save_subject("Science"); exam=save_exam("Revision","2026-11-10",subject_id=subject)
    missed=create_task("Missed","2026-10-20",20,exam_id=exam)
    future=create_task("Future","2026-10-25",30,exam_id=exam)
    done=create_task("Done","2026-10-19",15,exam_id=exam)
    set_task_status(done,"completed",completed_minutes=12)
    completion=list_tasks(status="completed")[0]["completed_at"]
    preview=reschedule_preview([missed],start_date="2026-10-21",include_future=True)
    assert {change["task_id"] for change in preview["changes"]}=={missed,future}
    assert preview["applied"] is False
    applied=reschedule_preview([missed],start_date="2026-10-21",include_future=True,confirmed=True)
    assert applied["applied"]
    records={task["id"]:task for task in list_tasks()}
    assert records[done]["completed_at"]==completion
    assert next(exam_row for exam_row in list_exams() if exam_row["id"]==exam)["exam_date"]=="2026-11-10"
    assert all(records[task_id]["planned_date"]<"2026-11-10" for task_id in (missed,future))


def test_flashcard_due_recommendation_reads_only_and_quiz_data_untouched(planner_db):
    deck=create_deck("Recall"); create_card(deck,front="F",back="B")
    before=get_due_flashcard_summary(now=datetime.now(timezone.utc)+timedelta(days=1))
    assert before["total"]==1
    with database.get_connection() as db:
        schedule_before=db.execute("SELECT due_at,interval_days FROM flashcard_schedule").fetchone()
        db.execute("INSERT INTO quizzes(id,title,source_filename,difficulty,requested_question_count,created_at) VALUES('q','Quiz','f.pdf','Easy',5,'now')")
        db.execute("INSERT INTO quiz_attempts(id,quiz_id,started_at,status,total_questions,total_possible_marks,percentage) VALUES('a','q','now','completed',5,5,80)")
    get_due_flashcard_summary(now=datetime.now(timezone.utc)+timedelta(days=1))
    with database.get_connection() as db:
        assert tuple(db.execute("SELECT due_at,interval_days FROM flashcard_schedule").fetchone())==tuple(schedule_before)
        assert db.execute("SELECT percentage FROM quiz_attempts WHERE id='a'").fetchone()[0]==80


def test_progress_denominators_separate_planned_from_recorded_minutes(planner_db):
    task=create_task("Study",date.today(),estimated_minutes=90)
    assert get_planner_metrics()["planned_minutes"]==90
    assert get_planner_metrics()["completed_minutes"]==0
    record_session(37,task_id=task)
    metrics=get_planner_metrics(); assert metrics["planned_minutes"]==90 and metrics["completed_minutes"]==37


def test_database_failure_rolls_back_task_session_transaction(planner_db):
    task=create_task("Study",date.today(),estimated_minutes=20)
    with database.get_connection() as db:
        db.execute("CREATE TRIGGER abort_session BEFORE INSERT ON planner_sessions BEGIN SELECT RAISE(ABORT,'no session'); END")
    with pytest.raises(sqlite3.IntegrityError): record_task_session(task,20)
    assert list_tasks()[0]["status"]=="planned" and list_sessions()==[]


def test_empty_planner_renders_without_gemini_or_ocr(planner_db,monkeypatch):
    monkeypatch.setattr("utils.config.DATABASE_PATH",database.DATABASE_PATH)
    at=AppTest.from_file(Path(__file__).resolve().parents[1]/"app.py").run()
    at.session_state["active_page"]="Study Planner"
    at.run()
    assert not at.exception
    assert any("Nothing planned today" in item.value for item in at.info)
