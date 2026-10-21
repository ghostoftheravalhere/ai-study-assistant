"""Study planner, exam calendar, and local study-session workflows."""
from datetime import date, timedelta

import streamlit as st

from utils.planner_service import (
    PlannerError, archive_subject, archive_topic, create_task, generate_plan, get_due_flashcard_summary,
    get_planner_metrics, get_settings, list_exams, list_sessions, list_subjects,
    list_tasks, list_topics, list_linkable_resources, reschedule_preview, save_exam,
    save_plan, save_settings, save_subject, save_topic, set_exam_status,
    set_task_status, set_topic_completed, record_task_session, update_task,
)
from utils.quiz_logic import calculate_progress_analytics
from utils.quiz_repository import get_progress_records
from utils.safe_logging import safe_error_message

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _show_planner_failure(error: Exception, action: str) -> None:
    message = str(error) if isinstance(error, PlannerError) else safe_error_message(error, operation="planner", action=action)
    st.error(message)


def render_planner() -> None:
    st.markdown('<div class="eyebrow">PLAN WITH PURPOSE</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">Study Planner</h1>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">A practical revision plan that stays on this device and adapts to your study days.</p>', unsafe_allow_html=True)
    today=date.today(); settings=get_settings(); metrics=get_planner_metrics(today=today)
    c1,c2,c3=st.columns(3)
    c1.metric("Planned today",f"{metrics['planned_minutes']} min",help="Estimated minutes from planned tasks.")
    c2.metric("Study time recorded today",f"{metrics['completed_minutes']} min",help="Actual durations from recorded study sessions.")
    c3.metric("Upcoming exams",metrics["upcoming_exams"])
    st.progress(min(1.0,metrics["completed_minutes"]/settings["daily_goal_minutes"]) if settings["daily_goal_minutes"] else 0.0,
                text=f"Daily goal: {metrics['completed_minutes']} of {settings['daily_goal_minutes']} minutes recorded")
    st.caption("Exam dates and planned dates use the calendar date on this device. Activity timestamps retain the local offset recorded by the app.")
    tabs=st.tabs(["Today", "Upcoming", "Exam calendar", "Subjects & topics", "Plan settings", "Study history"])
    with tabs[0]:
        st.subheader("Today's plan")
        tasks=list_tasks(start_date=today,end_date=today)
        if not tasks: st.info("Nothing planned today. Add a task or build a revision plan for an upcoming exam.")
        for task in tasks: _task_card(task)
        overdue=[t for t in list_tasks(end_date=today-timedelta(days=1)) if t["status"] in {"planned","missed"}]
        if overdue:
            st.subheader("Overdue tasks")
            for task in overdue:
                _task_card(task)
                if task["status"]=="planned" and st.button("Mark missed",key=f"missed_{task['id']}"):
                    try: set_task_status(task["id"],"missed"); st.rerun()
                    except PlannerError as error: st.error(str(error))
        try:
            due=get_due_flashcard_summary()
            from utils.flashcard_repository import get_flashcard_stats
            card_stats=get_flashcard_stats()
            if due["total"]: st.info(f"Flashcards: {due['total']} card(s) due or overdue · {card_stats['reviewed_today']} review(s) recorded today. This recommendation uses the saved review schedule and does not change intervals.")
            else: st.caption(f"No flashcards due · {card_stats['reviewed_today']} review(s) recorded today.")
        except Exception as error: st.warning(safe_error_message(error, operation="planner_flashcard_summary", action="Flashcard due status"))
        try:
            attempts,answers=get_progress_records(); insights=calculate_progress_analytics(attempts,answers)["topics"]
            review=[item for item in insights if item["insight"]=="Needs review"]
            if review:
                st.subheader("Quiz topics to revisit")
                for item in review: st.write(f"{item['topic']} · {item['correct']}/{item['answered']} correct ({item['accuracy_percentage']:.1f}%) · based on saved quiz responses")
        except Exception as error: st.warning(safe_error_message(error, operation="planner_quiz_suggestions", action="Quiz revision suggestions"))
        with st.expander("Add a task"):
            _task_form()
        with st.expander("Record a study session"):
            subjects=list_subjects(); topics=list_topics(); exams=list_exams()
            with st.form("manual_study_session"):
                minutes=st.number_input("Actual duration (minutes)",1,1440,25,key="manual_session_duration")
                subject=st.selectbox("Subject (optional)",[None]+subjects,format_func=lambda s:"None" if s is None else s["name"],key="manual_session_subject")
                topic=st.selectbox("Topic (optional)",[None]+[t for t in topics if subject is None or t["subject_id"]==subject["id"]],format_func=lambda t:"None" if t is None else t["name"],key="manual_session_topic")
                exam=st.selectbox("Exam (optional)",[None]+exams,format_func=lambda e:"None" if e is None else e["name"],key="manual_session_exam")
                note=st.text_input("Note (optional)",key="manual_session_note")
                if st.form_submit_button("Save study session"):
                    try: record_session(int(minutes),subject_id=subject["id"] if subject else None,topic_id=topic["id"] if topic else None,exam_id=exam["id"] if exam else None,note=note); st.success("Session recorded."); st.rerun()
                    except Exception as error: _show_planner_failure(error, "Saving the study session")
        if overdue:
            st.subheader("Reschedule missed work")
            chosen=st.multiselect("Select overdue or missed tasks",[t["id"] for t in overdue],format_func=lambda i: next((t["title"] for t in overdue if t["id"]==i),i),key="reschedule_ids")
            include_future=st.checkbox("Also rebalance future incomplete tasks",key="rebalance_future_tasks",help="Completed tasks stay fixed. Future planned tasks for other exams are not moved past their exam date.")
            if st.button("Preview reschedule",key="preview_reschedule"):
                st.session_state["planner_reschedule_preview"]=reschedule_preview(chosen,start_date=today,include_future=include_future)
                st.session_state["planner_reschedule_request"]={"ids":list(chosen),"include_future":include_future}
            preview=st.session_state.get("planner_reschedule_preview")
            if preview:
                st.write(preview["changes"])
                if preview["unscheduled"]: st.warning(f"Could not fit {len(preview['unscheduled'])} task(s): {preview['unscheduled']}")
                if st.button("Apply these changes",key="apply_reschedule",type="primary"):
                    request=st.session_state.get("planner_reschedule_request",{"ids":list(chosen),"include_future":include_future})
                    result=reschedule_preview(request["ids"],start_date=today,confirmed=True,include_future=request["include_future"])
                    st.session_state.pop("planner_reschedule_preview",None)
                    st.session_state.pop("planner_reschedule_request",None)
                    st.success(f"Rescheduled {len(result['changes'])} task(s). Unscheduled: {len(result['unscheduled'])}.")
                    st.rerun()
    with tabs[1]:
        st.subheader("Next 7 days")
        for task in list_tasks(start_date=today,end_date=today+timedelta(days=6)):
            st.write(f"**{task['planned_date']}** · {task['title']} · {task['estimated_minutes']} min · {task['status']}")
        exams=[e for e in list_exams() if e["status"]=="upcoming" and e["exam_date"]>=today.isoformat()]
        st.subheader("Coming exams")
        for exam in exams[:5]:
            days=(date.fromisoformat(exam["exam_date"])-today).days
            st.write(f"**{exam['name']}** · {exam['subject_name']} · {exam['exam_date']} · {days} day(s) to go")
    with tabs[2]: _render_exams(today)
    with tabs[3]: _render_subjects()
    with tabs[4]: _render_settings(settings)
    with tabs[5]:
        st.subheader("Study sessions")
        sessions=list_sessions()
        if not sessions: st.info("Your recorded study sessions will appear here.")
        for session in sessions:
            st.write(f"**{session['completed_at']}** · {session['duration_minutes']} min" + (f" · {session['task_title']}" if session["task_title"] else "") + (f" · {session['note']}" if session["note"] else ""))
        st.subheader("Task history")
        history=[t for t in list_tasks() if t["status"] in {"completed","cancelled","missed"}]
        if not history: st.caption("Completed, missed, and canceled tasks will be listed here.")
        for task in history:
            st.write(f"**{task['title']}** · {task['status']} · planned {task['planned_date']}" + (f" · completed {task['completed_at']}" if task["completed_at"] else ""))
            if task["status"]=="completed" and st.button("Reopen",key=f"history_reopen_{task['id']}"):
                try: set_task_status(task["id"],"planned"); st.rerun()
                except PlannerError as error: st.error(str(error))


def _task_card(task: dict) -> None:
    with st.container(border=True):
        st.write(f"**{task['title']}** · {task['estimated_minutes']} min · priority {task['priority']}/5")
        context=" · ".join(v for v in (task.get("subject_name"),task.get("topic_name"),task.get("exam_name"),task.get("document_filename"),task.get("quiz_title")) if v)
        if context: st.caption(context)
        if task["description"]: st.write(task["description"])
        if task["status"]=="planned":
            a,b=st.columns(2)
            duration=a.number_input("Actual minutes",min_value=1,max_value=1440,value=task["estimated_minutes"],key=f"session_minutes_{task['id']}")
            note=b.text_input("Session note (optional)",key=f"session_note_{task['id']}")
            done,cancel=st.columns(2)
            if done.button("Record session & complete",key=f"complete_{task['id']}"):
                try:
                    record_task_session(task["id"],int(duration),note=note)
                    st.success("Study session recorded."); st.rerun()
                except Exception as error: _show_planner_failure(error, "Recording the study session")
            if cancel.button("Cancel task",key=f"cancel_{task['id']}"):
                try: set_task_status(task["id"],"cancelled"); st.rerun()
                except PlannerError as error: st.error(str(error))
            with st.expander("Edit or reschedule"):
                with st.form(f"edit_task_{task['id']}"):
                    title=st.text_input("Title",task["title"]); day=st.date_input("Planned date",date.fromisoformat(task["planned_date"]),key=f"task_date_{task['id']}")
                    estimate=st.number_input("Estimated minutes",1,1440,task["estimated_minutes"],key=f"task_estimate_{task['id']}")
                    description=st.text_area("Description",task["description"],key=f"task_description_{task['id']}")
                    priority=st.slider("Priority",1,5,task["priority"],key=f"task_priority_{task['id']}")
                    if st.form_submit_button("Save task changes"):
                        try: update_task(task["id"],title=title,planned_date=day,estimated_minutes=int(estimate),description=description,priority=priority); st.success("Task updated."); st.rerun()
                        except PlannerError as error: st.error(str(error))
        elif task["status"]=="completed":
            st.caption(f"Completed {task['completed_at'] or ''} · actual {task['completed_minutes'] or 'unrecorded'} min")
            if st.button("Reopen task",key=f"reopen_{task['id']}"):
                try: set_task_status(task["id"],"planned"); st.rerun()
                except PlannerError as error: st.error(str(error))


def _task_form() -> None:
    subjects=list_subjects(); exams=list_exams(); topics=list_topics(); resources=list_linkable_resources()
    with st.form("planner_manual_task"):
        title=st.text_input("Task title"); planned=st.date_input("Planned date",value=date.today()); minutes=st.number_input("Estimated duration (minutes)",1,1440,30)
        task_type=st.selectbox("Type",["general","read_review","practice_quiz","revise_flashcards","review_mistakes","revise_topic"])
        subject=st.selectbox("Subject (optional)",[None]+subjects,format_func=lambda s:"No subject" if s is None else s["name"])
        topic=st.selectbox("Topic (optional)",[None]+[t for t in topics if subject is None or t["subject_id"]==subject["id"]],format_func=lambda t:"No topic" if t is None else t["name"])
        exam=st.selectbox("Exam (optional)",[None]+[e for e in exams if e["status"]=="upcoming"],format_func=lambda e:"No exam" if e is None else e["name"])
        document=st.selectbox("PDF (optional)",[None]+resources["documents"],format_func=lambda d:"No PDF" if d is None else d["title"])
        quiz=st.selectbox("Existing quiz (optional)",[None]+resources["quizzes"],format_func=lambda q:"No quiz" if q is None else q["title"])
        description=st.text_area("Notes"); priority=st.slider("Priority",1,5,3)
        if st.form_submit_button("Create task"):
            try: create_task(title,planned,int(minutes),description=description,task_type=task_type,subject_id=subject["id"] if subject else None,topic_id=topic["id"] if topic else None,exam_id=exam["id"] if exam else None,document_id=document["id"] if document else None,quiz_id=quiz["id"] if quiz else None,priority=priority); st.success("Task created."); st.rerun()
            except Exception as error: _show_planner_failure(error, "Creating the task")


def _render_exams(today: date) -> None:
    st.subheader("Exam calendar")
    subjects=list_subjects()
    filter_col1,filter_col2,filter_col3=st.columns(3)
    filter_subject=filter_col1.selectbox("Filter by subject",[None]+subjects,format_func=lambda s:"All subjects" if s is None else s["name"],key="exam_filter_subject")
    filter_start=filter_col2.date_input("From date",value=None,key="exam_filter_start")
    filter_end=filter_col3.date_input("To date",value=None,key="exam_filter_end")
    with st.form("new_exam"):
        name=st.text_input("Exam name"); subject=st.selectbox("Subject",[None]+subjects,format_func=lambda s:"Choose a subject" if s is None else s["name"])
        exam_day=st.date_input("Exam date",value=today+timedelta(days=14)); start_time=st.time_input("Start time (optional)",value=None)
        location=st.text_input("Location (optional)"); notes=st.text_area("Notes (optional)")
        if st.form_submit_button("Add exam"):
            try:
                if subject is None: raise PlannerError("Create or choose a subject first.")
                save_exam(name,exam_day,subject_id=subject["id"],start_time=start_time.strftime("%H:%M") if start_time else None,location=location,notes=notes)
                st.success("Exam added."); st.rerun()
            except Exception as error: _show_planner_failure(error, "Saving the exam")
    exam_rows=list_exams(subject_id=filter_subject["id"] if filter_subject else None,start_date=filter_start,end_date=filter_end)
    if not exam_rows: st.info("No exams yet. Add an exam to plan revision.")
    for exam in exam_rows:
        with st.expander(f"{exam['exam_date']} · {exam['name']} · {exam['subject_name']} · {exam['status']}"):
            d=(date.fromisoformat(exam["exam_date"])-today).days
            st.write("Today" if d==0 else f"{d} days until exam" if d>0 else f"Exam date passed by {-d} day(s)")
            if exam["location"]: st.caption(exam["location"])
            related=[t for t in list_tasks() if t["exam_id"]==exam["id"]]
            st.write(f"Related tasks: {len(related)}")
            for task in related: st.caption(f"{task['planned_date']} · {task['title']} · {task['status']}")
            with st.form(f"edit_exam_{exam['id']}"):
                en=st.text_input("Name",exam["name"]); ed=st.date_input("Date",date.fromisoformat(exam["exam_date"]),key=f"exam_date_{exam['id']}")
                es=st.selectbox("Subject",subjects,index=next((i for i,s in enumerate(subjects) if s["id"]==exam["subject_id"]),0) if subjects else 0,format_func=lambda s:s["name"],key=f"exam_subject_{exam['id']}") if subjects else None
                et=st.text_input("Time HH:MM",exam["start_time"] or ""); el=st.text_input("Location",exam["location"]); enotes=st.text_area("Notes",exam["notes"])
                if st.form_submit_button("Save exam changes"):
                    try: save_exam(en,ed,exam_id=exam["id"],subject_id=es["id"] if es else None,subject_name=exam["subject_name"],start_time=et or None,location=el,notes=enotes); st.success("Exam updated."); st.rerun()
                    except Exception as error: _show_planner_failure(error, "Updating the exam")
            ca,cb=st.columns(2)
            if ca.button("Mark completed",key=f"exam_done_{exam['id']}"): set_exam_status(exam["id"],"completed"); st.rerun()
            confirmed=st.checkbox("Confirm archive; keep related study history",key=f"confirm_exam_archive_{exam['id']}")
            if cb.button("Archive exam",key=f"exam_archive_{exam['id']}",disabled=not confirmed): set_exam_status(exam["id"],"archived"); st.rerun()
            topics=list_topics(exam_id=exam["id"])
            if topics and exam["status"]=="upcoming":
                if st.button("Build revision plan preview",key=f"build_{exam['id']}"):
                    proposals=generate_plan(exam_date=exam["exam_date"],topics=topics,study_days=get_settings()["study_days"],available_minutes=get_settings()["available_minutes"],daily_goal_minutes=get_settings()["daily_goal_minutes"],max_tasks_per_day=get_settings()["max_tasks_per_day"],existing_tasks=list_tasks(),today=today)
                    st.session_state[f"plan_preview_{exam['id']}"]=proposals
                preview=st.session_state.get(f"plan_preview_{exam['id']}")
                if preview:
                    st.info(preview["message"]); st.dataframe(preview["proposals"],use_container_width=True,hide_index=True)
                    if preview["unscheduled"]: st.warning(f"Unscheduled effort: {preview['unscheduled']}")
                    if st.button("Save this proposed plan",key=f"saveplan_{exam['id']}",type="primary"):
                        try: save_plan(exam["id"],preview["proposals"]); st.success("Plan saved."); st.session_state.pop(f"plan_preview_{exam['id']}",None); st.rerun()
                        except PlannerError as error: st.error(str(error))


def _render_subjects() -> None:
    st.subheader("Subjects")
    with st.form("subject_form"):
        name=st.text_input("New subject"); description=st.text_area("Description")
        if st.form_submit_button("Add subject"):
            try: save_subject(name,description=description); st.success("Subject saved."); st.rerun()
            except Exception as error: _show_planner_failure(error, "Saving the subject")
    subjects=list_subjects(); exams=list_exams()
    if not subjects: st.info("Add a subject to start organizing topics.")
    for subject in subjects:
        with st.expander(f"{subject['name']}"):
            with st.form(f"subject_edit_{subject['id']}"):
                edited=st.text_input("Name",subject["name"]); desc=st.text_area("Description",subject["description"])
                if st.form_submit_button("Save subject"):
                    try: save_subject(edited,subject_id=subject["id"],description=desc); st.success("Subject updated."); st.rerun()
                    except Exception as error: _show_planner_failure(error, "Updating the subject")
            if st.button("Archive subject",key=f"archive_subject_{subject['id']}"):
                try: archive_subject(subject["id"]); st.success("Archived; topic and study history are retained."); st.rerun()
                except PlannerError as error: st.error(str(error))
            with st.form(f"topic_{subject['id']}"):
                topic_name=st.text_input("Add topic or chapter"); effort=st.number_input("Estimated effort (minutes)",1,100000,60,key=f"effort_{subject['id']}")
                exam=st.selectbox("Related exam",[None]+[e for e in exams if e["subject_id"]==subject["id"] and e["status"]=="upcoming"],format_func=lambda e:"None" if e is None else e["name"],key=f"topic_exam_{subject['id']}")
                priority=st.slider("Priority",1,5,3,key=f"topic_priority_{subject['id']}")
                if st.form_submit_button("Add topic"):
                    try: save_topic(subject["id"],topic_name,estimated_minutes=int(effort),exam_id=exam["id"] if exam else None,priority=priority); st.success("Topic added."); st.rerun()
                    except Exception as error: _show_planner_failure(error, "Saving the topic")
            for topic in list_topics(subject_id=subject["id"]):
                state="complete" if topic["completed_at"] else f"{topic['estimated_minutes']} min · priority {topic['priority']}"
                st.write(f"• {topic['name']} — {state}" + (f" · source {topic['source_filename']}" if topic["source_filename"] else ""))
                if not topic["completed_at"] and st.button("Mark topic complete",key=f"topic_done_{topic['id']}"): set_topic_completed(topic["id"]); st.rerun()
                with st.expander(f"Edit {topic['name']}"):
                    docs=list_linkable_resources()["documents"]
                    with st.form(f"edit_topic_{topic['id']}"):
                        topic_name=st.text_input("Topic name",topic["name"]); effort=st.number_input("Estimated minutes",1,100000,topic["estimated_minutes"],key=f"edit_effort_{topic['id']}")
                        exam=st.selectbox("Related exam",[None]+[e for e in exams if e["subject_id"]==subject["id"] and e["status"]=="upcoming"],index=next((i+1 for i,e in enumerate(exams) if e["id"]==topic["exam_id"]),0),format_func=lambda e:"None" if e is None else e["name"],key=f"edit_topic_exam_{topic['id']}")
                        priority=st.slider("Priority",1,5,topic["priority"],key=f"edit_topic_priority_{topic['id']}")
                        doc=st.selectbox("Source PDF",[None]+docs,index=next((i+1 for i,d in enumerate(docs) if d["id"]==topic["source_document_id"]),0),format_func=lambda d:"No PDF" if d is None else d["title"],key=f"edit_topic_doc_{topic['id']}")
                        if st.form_submit_button("Save topic changes"):
                            try: save_topic(subject["id"],topic_name,topic_id=topic["id"],estimated_minutes=int(effort),exam_id=exam["id"] if exam else None,priority=priority,source_document_id=doc["id"] if doc else None); st.success("Topic updated."); st.rerun()
                            except Exception as error: _show_planner_failure(error, "Updating the topic")
                    if st.button("Archive topic",key=f"archive_topic_{topic['id']}"):
                        try: archive_topic(topic["id"]); st.success("Topic archived; related tasks are retained."); st.rerun()
                        except PlannerError as error: st.error(str(error))


def _render_settings(settings: dict) -> None:
    st.subheader("Study goals and availability")
    with st.form("planner_settings"):
        goal=st.number_input("Daily study goal (minutes; 0 disables)",0,1440,settings["daily_goal_minutes"])
        available=st.number_input("Available study minutes on selected days",1,1440,settings["available_minutes"])
        labels=st.multiselect("Preferred study days (unselected days are rest days)",DAY_NAMES,default=[DAY_NAMES[d] for d in settings["study_days"]])
        maximum=st.number_input("Maximum planned tasks per day",1,20,settings["max_tasks_per_day"])
        if st.form_submit_button("Save settings"):
            try: save_settings(daily_goal_minutes=int(goal),available_minutes=int(available),study_days=[DAY_NAMES.index(d) for d in labels],max_tasks_per_day=int(maximum)); st.success("Study settings saved."); st.rerun()
            except PlannerError as error: st.error(str(error))
