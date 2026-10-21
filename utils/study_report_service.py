"""Local HTML study reports derived from saved records, never from Gemini."""
from __future__ import annotations

import html
import re
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from utils import database
from utils.quiz_logic import calculate_progress_analytics

PERIODS = {7:"7 days",30:"30 days",90:"90 days",365:"365 days",None:"All time"}


class ReportError(ValueError):
    """Safe study report generation error."""


def build_study_report(*, period_days: int|None=30, today: date|None=None,
                       database_path=None) -> dict[str,Any]:
    if period_days not in PERIODS:
        raise ReportError("Choose a supported report period.")
    local_today=today or date.today()
    start=(local_today-timedelta(days=period_days-1)).isoformat() if period_days else None
    end=(local_today+timedelta(days=1)).isoformat()
    try:
        with database.get_connection(database_path) as db:
            attempt_sql="""SELECT a.id AS attempt_id,a.submitted_at,a.status,a.total_questions,a.correct_answers,
                a.incorrect_answers,a.unanswered_questions,a.percentage,a.duration_seconds,q.title,
                q.source_filename,q.difficulty FROM quiz_attempts a JOIN quizzes q ON q.id=a.quiz_id
                WHERE a.status IN ('completed','timed_out') AND a.submitted_at IS NOT NULL"""
            params=[]
            if start: attempt_sql+=" AND substr(a.submitted_at,1,10)>=?"; params.append(start)
            attempt_sql+=" AND substr(a.submitted_at,1,10)<? ORDER BY a.submitted_at"; params.append(end)
            attempts=[dict(row) for row in db.execute(attempt_sql,params)]
            answer_sql="""SELECT a.selected_option_id,a.is_correct,q.topic FROM quiz_answers a
                JOIN quiz_questions q ON q.id=a.question_id JOIN quiz_attempts t ON t.id=a.attempt_id
                WHERE t.status IN ('completed','timed_out') AND t.submitted_at IS NOT NULL
                AND a.selected_option_id IS NOT NULL"""
            answer_params=[]
            if start: answer_sql+=" AND substr(t.submitted_at,1,10)>=?"; answer_params.append(start)
            answer_sql+=" AND substr(t.submitted_at,1,10)<?"; answer_params.append(end)
            answers=[dict(row) for row in db.execute(answer_sql,answer_params)]
            quiz=calculate_progress_analytics(attempts,answers)
            flash_rows=db.execute("""SELECT rating,COUNT(*) AS count FROM flashcard_reviews
                WHERE substr(reviewed_at,1,10)>=? AND substr(reviewed_at,1,10)<? GROUP BY rating""",(start or "0001-01-01",end)).fetchall()
            ratings={key:0 for key in ("again","hard","good","easy")}
            ratings.update({row["rating"]:row["count"] for row in flash_rows})
            review_total=sum(ratings.values())
            exams=[dict(row) for row in db.execute("SELECT id,name,subject_name,exam_date,start_time,status FROM planner_exams WHERE status='upcoming' AND exam_date>=? ORDER BY exam_date LIMIT 20",(local_today.isoformat(),))]
            task_params=[]; task_sql="SELECT status,COUNT(*) AS count,COALESCE(SUM(estimated_minutes),0) AS planned FROM planner_tasks WHERE planned_date<?"
            task_params.append(end)
            if start: task_sql+=" AND planned_date>=?"; task_params.append(start)
            task_sql+=" GROUP BY status"
            task_rows=[dict(row) for row in db.execute(task_sql,task_params)]
            task_counts={row["status"]:row["count"] for row in task_rows}
            planned_minutes=sum(row["planned"] for row in task_rows if row["status"]!="cancelled")
            scheduled=sum(count for status,count in task_counts.items() if status in {"planned","completed","missed"})
            completed_tasks=task_counts.get("completed",0)
            session_sql="SELECT COALESCE(SUM(duration_minutes),0) FROM planner_sessions WHERE substr(completed_at,1,10)<?"; session_params=[end]
            if start: session_sql+=" AND substr(completed_at,1,10)>=?"; session_params.append(start)
            completed_minutes=db.execute(session_sql,session_params).fetchone()[0]
            recent_sql="""SELECT completed_at,duration_minutes,note,task_id,subject_id,topic_id,exam_id
                FROM planner_sessions WHERE substr(completed_at,1,10)<?"""; recent_params=[end]
            if start: recent_sql+=" AND substr(completed_at,1,10)>=?"; recent_params.append(start)
            recent_sessions=[dict(row) for row in db.execute(recent_sql+" ORDER BY completed_at DESC LIMIT 10",recent_params)]
            subject_rows=[dict(row) for row in db.execute("""SELECT s.id,s.name,
                COUNT(DISTINCT t.id) AS topic_count,
                SUM(CASE WHEN t.completed_at IS NOT NULL THEN 1 ELSE 0 END) AS completed_topics
                FROM planner_subjects s LEFT JOIN planner_topics t ON t.subject_id=s.id AND t.archived_at IS NULL
                WHERE s.archived_at IS NULL GROUP BY s.id ORDER BY s.name COLLATE NOCASE""")]
        return {"generated_at":datetime.now().astimezone().isoformat(timespec="seconds"),"period_days":period_days,
                "period_label":PERIODS[period_days],"period_start":start,"period_end_exclusive":end,
                "quiz":quiz,"flashcards":{"total_reviews":review_total,"ratings":ratings,
                    "recall_label":"Self-rated recall (not a quiz score or objective mastery measure)."},
                "exams":exams,"planner":{"task_counts":task_counts,"scheduled_task_count":scheduled,
                    "completed_task_count":completed_tasks,"completion_rate":round(100*completed_tasks/scheduled,1) if scheduled else None,
                    "planned_minutes":planned_minutes,"recorded_minutes":completed_minutes,
                    "recent_sessions":recent_sessions},"subjects":subject_rows,
                "empty":not attempts and not review_total and not exams and not scheduled and not completed_minutes and not subject_rows}
    except ReportError:
        raise
    except sqlite3.Error as error:
        raise ReportError("The study report could not read the local database.") from error


def _esc(value: Any) -> str:
    text="—" if value is None else str(value)
    text=re.sub(r"AIza[0-9A-Za-z_-]{30,}","[REDACTED API KEY]",text)
    return html.escape(text,quote=True)


def _table(headers: list[str], rows: list[list[Any]], empty: str) -> str:
    if not rows:
        return f"<p class='empty'>{_esc(empty)}</p>"
    head="".join(f"<th>{_esc(header)}</th>" for header in headers)
    body="".join("<tr>"+"".join(f"<td>{_esc(cell)}</td>" for cell in row)+"</tr>" for row in rows)
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def render_report_html(report: dict[str,Any]) -> bytes:
    """Render a downloadable, self-contained, escaped HTML report."""
    quiz=report["quiz"]; planner=report["planner"]; flash=report["flashcards"]
    trend=quiz["trend"][-10:]
    score_rows=[[row["submitted_at"],row["title"],f"{row['percentage']:.1f}%",f"{row['correct_answers']}/{row['total_questions']}",row["status"]] for row in trend]
    exam_rows=[[item["exam_date"],item["name"],item["subject_name"],item["start_time"] or ""] for item in report["exams"]]
    session_rows=[[item["completed_at"],item["duration_minutes"],item["note"]] for item in planner["recent_sessions"]]
    subject_rows=[[item["name"],item["completed_topics"] or 0,item["topic_count"] or 0] for item in report["subjects"]]
    accuracy=quiz["overall_accuracy"]
    trend_text=" · ".join(f"{row['submitted_at'][:10]} {row['percentage']:.1f}%" for row in trend) or "No submitted quiz attempts in this period."
    html_doc=f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>StudyMate study report</title><style>
body{{font:16px/1.55 system-ui,Segoe UI,sans-serif;color:#1d2939;background:#f5f7fb;margin:0;padding:32px}}main{{max-width:1000px;margin:auto;background:white;padding:36px;border-radius:16px;box-shadow:0 8px 32px #1d293912}}h1{{margin-bottom:0}}h2{{margin-top:34px;color:#334155}}.muted,.empty{{color:#667085}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}.metric{{background:#f6f8fc;padding:16px;border-radius:10px}}.metric strong{{display:block;font-size:23px;color:#293c73}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{text-align:left;padding:9px;border-bottom:1px solid #e4e7ec;vertical-align:top}}th{{background:#f7f8fa}}.table-wrap{{overflow-x:auto}}footer{{margin-top:34px;color:#667085;font-size:13px}}@media print{{body{{background:white;padding:0}}main{{box-shadow:none;padding:0}}}}
</style></head><body><main><h1>StudyMate study report</h1>
<p class="muted">Generated {_esc(report['generated_at'])} · Period: {_esc(report['period_label'])}{f" ({_esc(report['period_start'])} through {_esc((date.fromisoformat(report['period_end_exclusive'])-timedelta(days=1)).isoformat())})" if report['period_start'] else ""}. Dates follow the application host's local calendar.</p>
{'<p class="empty">There are no saved study records for this report period yet.</p>' if report['empty'] else ''}
<h2>Quiz performance</h2><div class="grid"><div class="metric">Submitted attempts<strong>{quiz['completed_attempts']}</strong></div><div class="metric">Average quiz score<strong>{_esc(f"{quiz['average_score']:.1f}%" if quiz['average_score'] is not None else '—')}</strong></div><div class="metric">Best quiz score<strong>{_esc(f"{quiz['best_score']:.1f}%" if quiz['best_score'] is not None else '—')}</strong></div><div class="metric">Accuracy on answered questions<strong>{_esc(f"{accuracy:.1f}%" if accuracy is not None else '—')}</strong></div></div>
<p class="muted">Quiz score averages submitted attempt percentages. Answer accuracy is correct answers divided by answered questions; unanswered questions are excluded. Recent score trend: {_esc(trend_text)}</p>
{_table(['Submitted','Quiz','Score','Correct','Status'],score_rows,'No submitted quiz attempts in this period.')}
<h2>Flashcard review activity</h2><p>{flash['total_reviews']} saved review(s). {_esc(flash['recall_label'])}</p>
{_table(['Self-rating','Reviews'],[[key.title(),value] for key,value in flash['ratings'].items()],'No saved flashcard reviews in this period.')}
<h2>Planner activity</h2><div class="grid"><div class="metric">Tasks completed<strong>{planner['completed_task_count']} / {planner['scheduled_task_count']}</strong></div><div class="metric">Task completion rate<strong>{_esc(f"{planner['completion_rate']:.1f}%" if planner['completion_rate'] is not None else '—')}</strong></div><div class="metric">Planned time<strong>{planner['planned_minutes']} min</strong></div><div class="metric">Recorded session time<strong>{planner['recorded_minutes']} min</strong></div></div>
<p class="muted">Planned time sums estimates for non-canceled tasks scheduled in the report period. Recorded time sums study-session durations completed in the period. Canceled tasks are excluded from the task completion denominator.</p>
{_table(['Subject','Completed topics','Active topics'],subject_rows,'No active subjects or topics.')}
<h2>Upcoming exams</h2>{_table(['Date','Exam','Subject','Time'],exam_rows,'No upcoming exams.')}
<h2>Recent study sessions</h2>{_table(['Completed','Actual minutes','Note'],session_rows,'No recorded sessions in this period.')}
<footer>Generated entirely from local StudyMate records. This report does not infer causality, guarantee learning outcomes, include chat transcripts, or include Gemini API keys.</footer></main></body></html>"""
    return html_doc.encode("utf-8")


def create_study_report(*, period_days: int|None=30, today: date|None=None, database_path=None) -> tuple[str,bytes,dict[str,Any]]:
    report=build_study_report(period_days=period_days,today=today,database_path=database_path)
    body=render_report_html(report)
    filename=f"studymate-study-report-{datetime.now().strftime('%Y%m%d')}-{period_days or 'all'}.html"
    return filename,body,report
