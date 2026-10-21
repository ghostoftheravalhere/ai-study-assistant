"""Local-first study planner persistence and deterministic exam scheduler."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from utils.database import get_connection

TASK_TYPES = {"read_review", "practice_quiz", "revise_flashcards", "review_mistakes", "revise_topic", "general"}
TASK_STATES = {"planned", "completed", "cancelled", "missed"}


class PlannerError(ValueError):
    """Invalid planner input or an unavailable planner record."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _text(value: str, label: str, *, required: bool = False, limit: int = 500) -> str:
    if not isinstance(value, str):
        raise PlannerError(f"{label} must be text.")
    value = value.strip()
    if required and not value:
        raise PlannerError(f"{label} cannot be empty.")
    if len(value) > limit:
        raise PlannerError(f"{label} must be {limit} characters or fewer.")
    return value


def _date(value: date | str, label: str) -> str:
    try:
        parsed = value if isinstance(value, date) else date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise PlannerError(f"{label} must be a valid calendar date (YYYY-MM-DD).") from error
    return parsed.isoformat()


def _bounded_int(value: int, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PlannerError(f"{label} must be between {minimum} and {maximum}.")
    return value


def list_subjects(*, include_archived: bool = False) -> list[dict[str, Any]]:
    with get_connection() as db:
        rows = db.execute("SELECT * FROM planner_subjects" + ("" if include_archived else " WHERE archived_at IS NULL") + " ORDER BY archived_at IS NOT NULL,name COLLATE NOCASE").fetchall()
    return [dict(row) for row in rows]


def save_subject(name: str, *, subject_id: str | None = None, description: str = "") -> str:
    name = _text(name, "Subject name", required=True, limit=120)
    description = _text(description, "Description", limit=1000)
    now, subject_id = _now(), subject_id or uuid.uuid4().hex
    with get_connection() as db:
        if subject_id and db.execute("SELECT 1 FROM planner_subjects WHERE id=?", (subject_id,)).fetchone():
            db.execute("UPDATE planner_subjects SET name=?,description=?,updated_at=? WHERE id=? AND archived_at IS NULL", (name, description, now, subject_id))
            if db.total_changes == 0:
                raise PlannerError("That subject is archived; restore it before editing.")
        else:
            db.execute("INSERT INTO planner_subjects(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)", (subject_id, name, description, now, now))
    return subject_id


def archive_subject(subject_id: str, *, archived: bool = True) -> None:
    with get_connection() as db:
        row = db.execute("SELECT id FROM planner_subjects WHERE id=?", (subject_id,)).fetchone()
        if row is None: raise PlannerError("That subject could not be found.")
        db.execute("UPDATE planner_subjects SET archived_at=?,updated_at=? WHERE id=?", (_now() if archived else None, _now(), subject_id))


def save_exam(name: str, exam_date: date | str, *, exam_id: str | None = None, subject_id: str | None = None,
              subject_name: str = "", start_time: str | None = None, location: str = "", notes: str = "") -> str:
    name, exam_day = _text(name, "Exam name", required=True, limit=160), _date(exam_date, "Exam date")
    location, notes = _text(location, "Location", limit=250), _text(notes, "Notes", limit=3000)
    if start_time and (len(start_time) != 5 or start_time[2] != ":" or not start_time[:2].isdigit() or not start_time[3:].isdigit() or int(start_time[:2]) > 23 or int(start_time[3:]) > 59):
        raise PlannerError("Start time must use 24-hour HH:MM format.")
    now, exam_id = _now(), exam_id or uuid.uuid4().hex
    with get_connection() as db:
        if subject_id:
            subject = db.execute("SELECT name FROM planner_subjects WHERE id=? AND archived_at IS NULL", (subject_id,)).fetchone()
            if subject is None: raise PlannerError("Choose an active subject.")
            subject_name = subject["name"]
        elif not _text(subject_name, "Subject", required=True, limit=120):
            raise PlannerError("Subject is required.")
        exists = db.execute("SELECT id FROM planner_exams WHERE id=?", (exam_id,)).fetchone()
        if exists:
            db.execute("UPDATE planner_exams SET name=?,subject_id=?,subject_name=?,exam_date=?,start_time=?,location=?,notes=?,updated_at=? WHERE id=?", (name, subject_id, subject_name.strip(), exam_day, start_time or None, location, notes, now, exam_id))
        else:
            db.execute("INSERT INTO planner_exams(id,name,subject_id,subject_name,exam_date,start_time,location,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (exam_id,name,subject_id,subject_name.strip(),exam_day,start_time or None,location,notes,now,now))
    return exam_id


def list_exams(*, include_archived: bool = False, subject_id: str|None=None, start_date: date|str|None=None, end_date: date|str|None=None) -> list[dict[str, Any]]:
    sql="SELECT * FROM planner_exams WHERE 1=1"; params=[]
    if not include_archived: sql+=" AND status!='archived'"
    if subject_id: sql+=" AND subject_id=?"; params.append(subject_id)
    if start_date: sql+=" AND exam_date>=?"; params.append(_date(start_date,"Start date"))
    if end_date: sql+=" AND exam_date<=?"; params.append(_date(end_date,"End date"))
    sql+=" ORDER BY exam_date,start_time,name"
    with get_connection() as db: rows = db.execute(sql,params).fetchall()
    return [dict(row) for row in rows]


def set_exam_status(exam_id: str, status: str) -> None:
    if status not in {"upcoming", "completed", "archived"}: raise PlannerError("Unsupported exam status.")
    with get_connection() as db:
        changed = db.execute("UPDATE planner_exams SET status=?,updated_at=? WHERE id=?", (status,_now(),exam_id)).rowcount
        if changed != 1: raise PlannerError("That exam could not be found.")


def save_topic(subject_id: str, name: str, *, topic_id: str | None = None, description: str = "", exam_id: str | None = None,
               estimated_minutes: int = 60, priority: int = 3, source_document_id: str | None = None) -> str:
    name = _text(name, "Topic name", required=True, limit=160)
    description = _text(description, "Description", limit=2000)
    estimated_minutes = _bounded_int(estimated_minutes, "Estimated minutes", 1, 100000)
    priority = _bounded_int(priority, "Priority", 1, 5)
    now, topic_id = _now(), topic_id or uuid.uuid4().hex
    with get_connection() as db:
        if not db.execute("SELECT 1 FROM planner_subjects WHERE id=? AND archived_at IS NULL", (subject_id,)).fetchone(): raise PlannerError("Choose an active subject.")
        if exam_id:
            exam=db.execute("SELECT subject_id,status FROM planner_exams WHERE id=?",(exam_id,)).fetchone()
            if exam is None or exam["status"]!="upcoming": raise PlannerError("Choose an upcoming exam.")
            if exam["subject_id"] and exam["subject_id"]!=subject_id: raise PlannerError("The topic and exam must belong to the same subject.")
        filename = None
        if source_document_id:
            doc = db.execute("SELECT original_filename FROM documents WHERE id=?", (source_document_id,)).fetchone()
            if doc is None: raise PlannerError("The selected document is unavailable.")
            filename = doc["original_filename"]
        exists = db.execute("SELECT 1 FROM planner_topics WHERE id=?", (topic_id,)).fetchone()
        if exists:
            changed=db.execute("UPDATE planner_topics SET subject_id=?,exam_id=?,name=?,description=?,estimated_minutes=?,priority=?,source_document_id=?,source_filename=?,updated_at=? WHERE id=? AND archived_at IS NULL", (subject_id,exam_id,name,description,estimated_minutes,priority,source_document_id,filename,now,topic_id)).rowcount
            if changed != 1: raise PlannerError("That topic is archived; restore it before editing.")
        else:
            db.execute("INSERT INTO planner_topics(id,subject_id,exam_id,name,description,estimated_minutes,priority,source_document_id,source_filename,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (topic_id,subject_id,exam_id,name,description,estimated_minutes,priority,source_document_id,filename,now,now))
    return topic_id


def list_topics(*, subject_id: str | None = None, exam_id: str | None = None, include_archived: bool = False) -> list[dict[str, Any]]:
    sql="SELECT t.*,s.name AS subject_name FROM planner_topics t JOIN planner_subjects s ON s.id=t.subject_id WHERE 1=1"; params=[]
    if not include_archived: sql += " AND t.archived_at IS NULL AND s.archived_at IS NULL"
    if subject_id: sql += " AND t.subject_id=?"; params.append(subject_id)
    if exam_id: sql += " AND t.exam_id=?"; params.append(exam_id)
    sql += " ORDER BY t.priority DESC,t.name COLLATE NOCASE"
    with get_connection() as db: rows=db.execute(sql,params).fetchall()
    return [dict(row) for row in rows]


def set_topic_completed(topic_id: str, completed: bool = True) -> None:
    with get_connection() as db:
        changed=db.execute("UPDATE planner_topics SET completed_at=?,updated_at=? WHERE id=? AND archived_at IS NULL", (_now() if completed else None,_now(),topic_id)).rowcount
        if changed != 1: raise PlannerError("That active topic could not be found.")


def archive_topic(topic_id: str, *, archived: bool=True) -> None:
    with get_connection() as db:
        changed=db.execute("UPDATE planner_topics SET archived_at=?,updated_at=? WHERE id=?",(_now() if archived else None,_now(),topic_id)).rowcount
        if changed != 1: raise PlannerError("That topic could not be found.")


def get_settings() -> dict[str, Any]:
    with get_connection() as db:
        row=db.execute("SELECT * FROM planner_settings WHERE id=1").fetchone()
        if row is None:
            now=_now(); db.execute("INSERT INTO planner_settings(id,updated_at) VALUES(1,?)",(now,)); row=db.execute("SELECT * FROM planner_settings WHERE id=1").fetchone()
    result=dict(row); result["study_days"]=json.loads(result["study_days"]); return result


def save_settings(*, daily_goal_minutes: int, available_minutes: int, study_days: Iterable[int], max_tasks_per_day: int) -> None:
    goal=_bounded_int(daily_goal_minutes,"Daily goal",0,1440); available=_bounded_int(available_minutes,"Daily availability",1,1440); maximum=_bounded_int(max_tasks_per_day,"Maximum tasks per day",1,20)
    days=sorted(set(study_days))
    if any(isinstance(day,bool) or not isinstance(day,int) or day not in range(7) for day in days): raise PlannerError("Choose valid study days.")
    if not days: raise PlannerError("Select at least one study day; rest days are respected.")
    with get_connection() as db: db.execute("INSERT INTO planner_settings(id,daily_goal_minutes,available_minutes,study_days,max_tasks_per_day,updated_at) VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET daily_goal_minutes=excluded.daily_goal_minutes,available_minutes=excluded.available_minutes,study_days=excluded.study_days,max_tasks_per_day=excluded.max_tasks_per_day,updated_at=excluded.updated_at",(goal,available,json.dumps(days),maximum,_now()))


def generate_plan(*, exam_date: date | str, topics: Iterable[Mapping[str, Any]], study_days: Iterable[int] = range(7),
                  available_minutes: int = 120, daily_goal_minutes: int = 60, max_tasks_per_day: int = 4,
                  existing_tasks: Iterable[Mapping[str, Any]] = (), today: date | str | None = None,
                  review_minutes: int = 30) -> dict[str, Any]:
    """Allocate high-priority topics first, spreading chunks across eligible days.

    Each study day offers at most daily availability, further limited by the
    goal when nonzero. Topics are allocated round-robin by priority in chunks
    no larger than 45 minutes. A final review slot is reserved before the exam
    where capacity permits. Returns proposals and an explicit unallocated list.
    """
    end=date.fromisoformat(_date(exam_date,"Exam date")); start=date.fromisoformat(_date(today or date.today(),"Today"))
    available=_bounded_int(available_minutes,"Daily availability",1,1440); goal=_bounded_int(daily_goal_minutes,"Daily goal",0,1440); cap=min(available,goal) if goal else available
    max_tasks=_bounded_int(max_tasks_per_day,"Maximum tasks per day",1,20)
    days=sorted(set(study_days))
    if not days or any(isinstance(d,bool) or not isinstance(d,int) or d not in range(7) for d in days): raise PlannerError("Choose valid study days.")
    if end < start: raise PlannerError("The exam date has passed; update it before planning.")
    existing_tasks=list(existing_tasks)
    occupied={}
    for task in existing_tasks:
        day=str(task.get("planned_date","")); minutes=int(task.get("estimated_minutes",0) or 0)
        if day and task.get("status","planned")=="planned":
            occupied[day]=occupied.get(day,0)+minutes
    eligible=[]
    cursor=start
    while cursor<end:
        if cursor.weekday() in days:
            key=cursor.isoformat(); room=max(0,cap-occupied.get(key,0))
            tasks_count=sum(1 for task in existing_tasks if task.get("planned_date")==key and task.get("status","planned")=="planned")
            if room and tasks_count<max_tasks: eligible.append([cursor,room,max_tasks-tasks_count])
        cursor+=timedelta(days=1)
    normalized=[]
    for topic in topics:
        effort=_bounded_int(int(topic.get("estimated_minutes",60)),"Topic effort",1,100000)
        already_planned=sum(int(task.get("estimated_minutes",0) or 0) for task in existing_tasks
                            if task.get("status","planned")=="planned" and task.get("topic_id")==topic.get("id")
                            and task.get("task_type")=="revise_topic")
        priority=_bounded_int(int(topic.get("priority",3)),"Priority",1,5)
        normalized.append({"id":topic.get("id"),"title":_text(str(topic.get("name",topic.get("title","Review topic"))),"Topic",required=True,limit=160),"priority":priority,"remaining":effort})
        normalized[-1]["remaining"]=max(0,effort-already_planned)
    normalized.sort(key=lambda item:(-item["priority"],item["title"].casefold(),str(item["id"])))
    proposals=[]
    # Round-robin by topic across available dates: this prevents a single topic
    # from monopolizing early dates while retaining priority ordering.
    used_by_day={}
    count_by_day={}
    for day,room,task_slots in eligible:
        used_topics=set()
        # One chunk per topic per day distributes material over the calendar.
        while room and task_slots:
            active=[topic for topic in normalized if topic["remaining"] and topic["id"] not in used_topics]
            if not active: break
            topic=active[0]
            minutes=min(topic["remaining"],room,45)
            proposals.append({"title":f"Revise {topic['title']}","task_type":"revise_topic","topic_id":topic["id"],"planned_date":day.isoformat(),"estimated_minutes":minutes,"priority":topic["priority"]})
            topic["remaining"]-=minutes; room-=minutes; task_slots-=1; used_topics.add(topic["id"])
        used_by_day[day.isoformat()]=cap-room
        count_by_day[day.isoformat()]=max_tasks-task_slots
    unscheduled=[{"topic_id":t["id"],"title":t["title"],"minutes":t["remaining"]} for t in normalized if t["remaining"]]
    capacity=sum(item[1] for item in eligible)
    message=("All estimated topic effort fits the available study days." if not unscheduled else f"{sum(t['minutes'] for t in unscheduled)} minutes could not be scheduled. Add study days or availability, reduce estimates, or schedule another plan.")
    if end==start: message="The exam is today; no pre-exam study days remain. Schedule only a short review manually if useful."
    elif end-start==timedelta(days=1): message="The exam is tomorrow; only today's available capacity is usable, so prioritize the most important topics."
    if review_minutes>0 and proposals and end>start:
        for day,_,_ in reversed(eligible):
            key=day.isoformat(); room=cap-used_by_day.get(key,0)
            if day<end and room>0 and count_by_day.get(key,0)<max_tasks:
                minutes=min(review_minutes,15,room)
                proposals.append({"title":"Final review and recall","task_type":"review_mistakes","topic_id":None,"planned_date":key,"estimated_minutes":minutes,"priority":5})
                break
    return {"proposals":proposals,"unscheduled":unscheduled,"message":message,"capacity_minutes":capacity}


def save_plan(exam_id: str, proposals: Iterable[Mapping[str, Any]]) -> list[str]:
    """Persist reviewed proposals idempotently by exam/topic/date/purpose."""
    ids=[]
    with get_connection() as db:
        exam=db.execute("SELECT exam_date,status,subject_id FROM planner_exams WHERE id=?",(exam_id,)).fetchone()
        if exam is None or exam["status"]!="upcoming": raise PlannerError("Choose an upcoming exam.")
        for proposal in proposals:
            day=_date(str(proposal["planned_date"]),"Task date")
            if day>=exam["exam_date"]: raise PlannerError("Study tasks must be scheduled before the exam date.")
            if proposal.get("task_type") not in TASK_TYPES: raise PlannerError("Choose a supported task type.")
            key=f"plan:{exam_id}:{proposal.get('topic_id') or 'review'}:{proposal.get('task_type')}:{day}"
            existing=db.execute("SELECT id FROM planner_tasks WHERE unique_key=?",(key,)).fetchone()
            if existing: ids.append(existing["id"]); continue
            task_id=uuid.uuid4().hex; now=_now()
            topic=db.execute("SELECT subject_id FROM planner_topics WHERE id=?",(proposal.get("topic_id"),)).fetchone() if proposal.get("topic_id") else None
            if proposal.get("topic_id") and topic is None: raise PlannerError("The proposed topic is no longer available.")
            if topic and topic["subject_id"]!=exam["subject_id"]: raise PlannerError("The proposed topic does not belong to this exam's subject.")
            db.execute("INSERT INTO planner_tasks(id,title,task_type,subject_id,topic_id,exam_id,planned_date,estimated_minutes,priority,status,created_at,updated_at,unique_key) VALUES(?,?,?,?,?,?,?,?,?,'planned',?,?,?)",(task_id,_text(str(proposal["title"]),"Task title",required=True,limit=200),proposal["task_type"],topic["subject_id"] if topic else None,proposal.get("topic_id"),exam_id,day,_bounded_int(int(proposal["estimated_minutes"]),"Task duration",1,1440),_bounded_int(int(proposal.get("priority",3)),"Priority",1,5),now,now,key))
            ids.append(task_id)
    return ids


def create_task(title: str, planned_date: date | str, estimated_minutes: int, *, description: str="", task_type: str="general", subject_id: str|None=None, topic_id: str|None=None, exam_id: str|None=None, document_id: str|None=None, quiz_id: str|None=None, priority: int=3) -> str:
    title=_text(title,"Task title",required=True,limit=200); description=_text(description,"Description",limit=2000); day=_date(planned_date,"Task date")
    minutes=_bounded_int(estimated_minutes,"Task duration",1,1440); priority=_bounded_int(priority,"Priority",1,5)
    if task_type not in TASK_TYPES: raise PlannerError("Choose a supported task type.")
    task_id,now=uuid.uuid4().hex,_now()
    with get_connection() as db:
        if topic_id:
            topic=db.execute("SELECT subject_id FROM planner_topics WHERE id=? AND archived_at IS NULL",(topic_id,)).fetchone()
            if topic is None: raise PlannerError("Choose an active topic.")
            subject_id=subject_id or topic["subject_id"]
        if exam_id:
            exam=db.execute("SELECT exam_date,status FROM planner_exams WHERE id=?",(exam_id,)).fetchone()
            if exam is None or exam["status"]!="upcoming": raise PlannerError("Choose an upcoming exam.")
            if day>=exam["exam_date"]: raise PlannerError("A related study task must be before its exam date.")
            if subject_id:
                exam_subject=db.execute("SELECT subject_id FROM planner_exams WHERE id=?",(exam_id,)).fetchone()["subject_id"]
                if exam_subject and subject_id!=exam_subject: raise PlannerError("The task and exam must belong to the same subject.")
        filename=None
        if document_id:
            doc=db.execute("SELECT original_filename FROM documents WHERE id=?",(document_id,)).fetchone()
            if doc is None: raise PlannerError("The selected document is unavailable.")
            filename=doc["original_filename"]
        if quiz_id and not db.execute("SELECT 1 FROM quizzes WHERE id=?",(quiz_id,)).fetchone(): raise PlannerError("The selected quiz is unavailable.")
        db.execute("INSERT INTO planner_tasks(id,title,description,task_type,subject_id,topic_id,exam_id,document_id,quiz_id,document_filename,planned_date,estimated_minutes,priority,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'planned',?,?)",(task_id,title,description,task_type,subject_id,topic_id,exam_id,document_id,quiz_id,filename,day,minutes,priority,now,now))
    return task_id


def update_task(task_id: str, *, title: str, planned_date: date | str, estimated_minutes: int,
                description: str="", priority: int=3) -> None:
    title=_text(title,"Task title",required=True,limit=200); description=_text(description,"Description",limit=2000)
    day=_date(planned_date,"Task date"); minutes=_bounded_int(estimated_minutes,"Task duration",1,1440); priority=_bounded_int(priority,"Priority",1,5)
    with get_connection() as db:
        task=db.execute("SELECT exam_id,status FROM planner_tasks WHERE id=?",(task_id,)).fetchone()
        if task is None or task["status"]!="planned": raise PlannerError("Only planned tasks can be edited or rescheduled.")
        if task["exam_id"]:
            exam=db.execute("SELECT exam_date FROM planner_exams WHERE id=?",(task["exam_id"],)).fetchone()
            if exam and day>=exam["exam_date"]: raise PlannerError("A related study task must be before its exam date.")
        db.execute("UPDATE planner_tasks SET title=?,description=?,planned_date=?,estimated_minutes=?,priority=?,updated_at=? WHERE id=? AND status='planned'",(title,description,day,minutes,priority,_now(),task_id))


def list_tasks(*, start_date: date | str | None=None, end_date: date | str | None=None, status: str|None=None) -> list[dict[str,Any]]:
    sql="SELECT t.*,s.name AS subject_name,tp.name AS topic_name,e.name AS exam_name,q.title AS quiz_title FROM planner_tasks t LEFT JOIN planner_subjects s ON s.id=t.subject_id LEFT JOIN planner_topics tp ON tp.id=t.topic_id LEFT JOIN planner_exams e ON e.id=t.exam_id LEFT JOIN quizzes q ON q.id=t.quiz_id WHERE 1=1"; params=[]
    if start_date: sql+=" AND planned_date>=?"; params.append(_date(start_date,"Start date"))
    if end_date: sql+=" AND planned_date<=?"; params.append(_date(end_date,"End date"))
    if status:
        if status not in TASK_STATES: raise PlannerError("Unsupported task status.")
        sql+=" AND t.status=?"; params.append(status)
    sql+=" ORDER BY planned_date,priority DESC,created_at"
    with get_connection() as db: rows=db.execute(sql,params).fetchall()
    return [dict(row) for row in rows]


def list_linkable_resources() -> dict[str, list[dict[str, str]]]:
    with get_connection() as db:
        documents=[dict(row) for row in db.execute("SELECT id,original_filename AS title FROM documents ORDER BY original_filename COLLATE NOCASE")]
        quizzes=[dict(row) for row in db.execute("SELECT id,title FROM quizzes ORDER BY created_at DESC,title COLLATE NOCASE")]
    return {"documents":documents,"quizzes":quizzes}


def set_task_status(task_id: str, status: str, *, completed_minutes: int|None=None) -> None:
    if status not in TASK_STATES: raise PlannerError("Unsupported task status.")
    if completed_minutes is not None: completed_minutes=_bounded_int(completed_minutes,"Completed minutes",1,1440)
    now=_now()
    with get_connection() as db:
        changed=db.execute("UPDATE planner_tasks SET status=?,completed_at=CASE WHEN ?='completed' THEN ? ELSE completed_at END,completed_minutes=CASE WHEN ?='completed' THEN COALESCE(?,completed_minutes) ELSE completed_minutes END,updated_at=? WHERE id=? AND status!='cancelled'",(status,status,now,status,completed_minutes,now,task_id)).rowcount
        if changed != 1: raise PlannerError("That task cannot be updated (it may be missing or cancelled).")


def reschedule_preview(task_ids: Iterable[str], *, start_date: date | str, confirmed: bool=False,
                       include_future: bool=False) -> dict[str,Any]:
    tasks=[t for t in list_tasks() if t["status"] in {"planned","missed"}]
    selected_ids=set(task_ids)
    if include_future:
        start_iso=_date(start_date,"Start date")
        selected_ids.update(t["id"] for t in tasks if t["status"]=="planned" and t["planned_date"]>=start_iso)
    selected=[t for t in tasks if t["id"] in selected_ids]
    start=date.fromisoformat(_date(start_date,"Start date")); settings=get_settings(); occupied={}
    for task in tasks:
        if task not in selected and task["status"]=="planned": occupied[task["planned_date"]]=occupied.get(task["planned_date"],0)+task["estimated_minutes"]
    rows=[]; unscheduled=[]
    exams_by_id={exam["id"]:exam for exam in list_exams(include_archived=True)}
    for task in sorted(selected,key=lambda t:(exams_by_id.get(t["exam_id"],{}).get("exam_date","9999-12-31"),-t["priority"],t["planned_date"],t["created_at"])):
        day=start; placed=False; exam=exams_by_id.get(task["exam_id"])
        while exam is None or day.isoformat()<exam["exam_date"]:
            key=day.isoformat()
            count=sum(1 for r in rows if r["planned_date"]==key)+sum(1 for t in tasks if t not in selected and t["planned_date"]==key and t["status"]=="planned")
            cap=min(settings["available_minutes"],settings["daily_goal_minutes"] or settings["available_minutes"])
            if day.weekday() in settings["study_days"] and occupied.get(key,0)+task["estimated_minutes"]<=cap and count<settings["max_tasks_per_day"]:
                rows.append({"task_id":task["id"],"from":task["planned_date"],"planned_date":key,"title":task["title"]}); occupied[key]=occupied.get(key,0)+task["estimated_minutes"]; placed=True; break
            day+=timedelta(days=1)
            if (day-start).days>366: break
        if not placed: unscheduled.append({"task_id":task["id"],"title":task["title"],"minutes":task["estimated_minutes"]})
    if confirmed:
        with get_connection() as db:
            for row in rows: db.execute("UPDATE planner_tasks SET planned_date=?,status='planned',updated_at=? WHERE id=? AND status IN ('planned','missed')",(row["planned_date"],_now(),row["task_id"]))
    return {"changes":rows,"unscheduled":unscheduled,"applied":bool(confirmed)}


def record_session(duration_minutes: int, *, task_id: str|None=None, subject_id: str|None=None, topic_id: str|None=None, exam_id: str|None=None, note: str="", started_at: str|None=None) -> str:
    duration=_bounded_int(duration_minutes,"Session duration",1,1440); note=_text(note,"Session note",limit=1000); session_id,now=uuid.uuid4().hex,_now()
    with get_connection() as db:
        if task_id:
            task=db.execute("SELECT subject_id,topic_id,exam_id FROM planner_tasks WHERE id=?",(task_id,)).fetchone()
            if task is None: raise PlannerError("That task could not be found.")
            subject_id=subject_id or task["subject_id"]; topic_id=topic_id or task["topic_id"]; exam_id=exam_id or task["exam_id"]
        db.execute("INSERT INTO planner_sessions(id,task_id,subject_id,topic_id,exam_id,duration_minutes,note,started_at,completed_at) VALUES(?,?,?,?,?,?,?,?,?)",(session_id,task_id,subject_id,topic_id,exam_id,duration,note,started_at,now))
    return session_id


def record_task_session(task_id: str, duration_minutes: int, *, note: str="", started_at: str|None=None) -> str:
    """Atomically save a session and complete its task, with one stable session id."""
    duration=_bounded_int(duration_minutes,"Session duration",1,1440); note=_text(note,"Session note",limit=1000)
    session_id,now=uuid.uuid4().hex,_now()
    with get_connection() as db:
        task=db.execute("SELECT * FROM planner_tasks WHERE id=? AND status='planned'",(task_id,)).fetchone()
        if task is None: raise PlannerError("That task is no longer planned.")
        db.execute("INSERT INTO planner_sessions(id,task_id,subject_id,topic_id,exam_id,duration_minutes,note,started_at,completed_at) VALUES(?,?,?,?,?,?,?,?,?)",(session_id,task_id,task["subject_id"],task["topic_id"],task["exam_id"],duration,note,started_at,now))
        changed=db.execute("UPDATE planner_tasks SET status='completed',completed_at=?,completed_minutes=?,updated_at=? WHERE id=? AND status='planned'",(now,duration,now,task_id)).rowcount
        if changed != 1: raise PlannerError("The task changed before the study session could be saved.")
    return session_id


def list_sessions(limit: int=100) -> list[dict[str,Any]]:
    with get_connection() as db: rows=db.execute("SELECT ss.*,s.name AS subject_name,tp.name AS topic_name,e.name AS exam_name,t.title AS task_title FROM planner_sessions ss LEFT JOIN planner_subjects s ON s.id=ss.subject_id LEFT JOIN planner_topics tp ON tp.id=ss.topic_id LEFT JOIN planner_exams e ON e.id=ss.exam_id LEFT JOIN planner_tasks t ON t.id=ss.task_id ORDER BY ss.completed_at DESC LIMIT ?",(max(1,min(limit,500)),)).fetchall()
    return [dict(row) for row in rows]


def get_due_flashcard_summary(*, now: datetime|None=None) -> dict[str,Any]:
    """Read due counts only; flashcard schedule remains the sole due-date authority."""
    # The flashcard repository persists due_at in UTC; compare using that same
    # canonical representation so local offset strings cannot sort incorrectly.
    instant=(now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as db:
        row=db.execute("SELECT COUNT(*) AS total,MIN(s.due_at) AS oldest FROM flashcards c JOIN flashcard_schedule s ON s.card_id=c.id JOIN flashcard_decks d ON d.id=c.deck_id WHERE c.archived_at IS NULL AND d.archived_at IS NULL AND s.due_at<=?",(instant,)).fetchone()
    return dict(row)


def get_planner_metrics(*, today: date|str|None=None) -> dict[str,int|float]:
    day=_date(today or date.today(),"Today"); start=(date.fromisoformat(day)-timedelta(days=date.fromisoformat(day).weekday())).isoformat(); end=(date.fromisoformat(start)+timedelta(days=7)).isoformat()
    with get_connection() as db:
        planned=db.execute("SELECT COALESCE(SUM(estimated_minutes),0) FROM planner_tasks WHERE planned_date=? AND status='planned'",(day,)).fetchone()[0]
        completed=db.execute("SELECT COALESCE(SUM(duration_minutes),0) FROM planner_sessions WHERE completed_at LIKE ?",(day+"%",)).fetchone()[0]
        week=db.execute("SELECT COUNT(*) FROM planner_tasks WHERE status='completed' AND completed_at>=? AND completed_at<?",(start,end)).fetchone()[0]
        overdue=db.execute("SELECT COUNT(*) FROM planner_tasks WHERE status IN ('planned','missed') AND planned_date<?",(day,)).fetchone()[0]
        exams=db.execute("SELECT COUNT(*) FROM planner_exams WHERE status='upcoming' AND exam_date>=?",(day,)).fetchone()[0]
        scheduled=db.execute("SELECT COUNT(*) FROM planner_tasks WHERE planned_date=? AND status IN ('planned','completed','missed')",(day,)).fetchone()[0]
        done=db.execute("SELECT COUNT(*) FROM planner_tasks WHERE planned_date=? AND status='completed'",(day,)).fetchone()[0]
    return {"planned_minutes":planned,"completed_minutes":completed,"tasks_completed_week":week,"overdue_tasks":overdue,"upcoming_exams":exams,"completion_rate":round(done*100/scheduled,1) if scheduled else 0.0}
