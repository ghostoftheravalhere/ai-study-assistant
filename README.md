# AI Study Assistant

**Your Personal AI-Powered Learning Companion**

AI Study Assistant is a diploma engineering project that brings document-based question answering, AI chat, summaries, question generation, quizzes, and study progress into one learning workspace.

## Implemented features

**Phases 6–10 are complete:** quizzes, flashcards, spaced repetition, progress analytics, local OCR, the local-first study planner, and local data management are available below.

Phase 11 adds deployment diagnostics, safer startup and migration handling, and automated CI. System Health reports optional-feature status separately from core readiness.

- Responsive Streamlit dashboard with custom CSS and working sidebar navigation.
- Four dashboard metrics based only on recorded local activity. New installations show zero counts and no average score until real activity exists.
- Quick actions and feature cards for the study tools.
- SQLite records for study activity, documents, and saved AI chat conversations.
- Runs without a Gemini API key.
- Multiple PDF uploads with extension, content, and file-size validation.
- Page-by-page text extraction using PyMuPDF, with page boundaries retained.
- Local document library with persisted metadata, extracted text, duplicate detection, text preview, and confirmed deletion.
- Dashboard document count based on PDFs actually stored in the library.
- Scanned and mixed PDFs are assessed page by page; local OCR is available for likely image-based pages.
- General and document-grounded Gemini study chat with locally saved conversations and messages.
- Local semantic document search over page-aware PDF chunks using Sentence Transformers and FAISS.
- Rebuildable FAISS cache with vectors and index status persisted in the local SQLite database.
- Document chat retrieves only relevant indexed passages and displays page citations and source snippets.
- Gemini summaries in short, medium, and detailed formats.
- Gemini question generation for multiple-choice, short-answer, and descriptive questions, with validated structured responses.
- Interactive 5–30 question multiple-choice quizzes generated from a selected text-ready PDF, with optional timers, difficulty selection, answer saving, deterministic local scoring, explanations, page citations, full or missed-question retries, and attempt history.
- Local study analytics for submitted quiz scores, score trends, answered-question accuracy, and topic-level review suggestions that require at least three responses.
- Manual and Gemini-proposed flashcards organized in persistent decks, with editable review-before-save proposals and PDF source references.
- Active recall sessions with AGAIN, HARD, GOOD, and EASY ratings, deterministic spaced repetition, daily new-card limits, and append-only local review history.
- Successful AI operations, including flashcard proposal generation, appear in recent activity without storing prompts, document contents, or generated results there.
- Local Tesseract OCR for selected scanned PDF pages, with page-aware text, retryable status, and native-text preservation.
- Local study planner with subjects, topics, exams, daily availability, deterministic exam revision proposals, editable tasks, missed-task recovery, and actual study-session history.
- Validated database-only and complete database-plus-PDF backups, confirmation-gated restore with retained safety backups, selected CSV/JSON exports, and downloadable local HTML study reports.
- Local System Health checks for Python, dependencies, configuration, SQLite, storage, OCR, semantic indexing, and Gemini readiness without network calls, OCR, or model loading.

## Technology

- Python 3.10 or newer
- Streamlit
- PyMuPDF (PDF validation and text extraction)
- pytesseract (Python bridge to a separately installed local Tesseract executable)
- `google-genai` (official Google Gen AI Python SDK)
- SQLite (Python standard library)
- Custom CSS

Sentence Transformers provides local CPU embeddings; FAISS CPU provides cosine-similarity retrieval over normalized vectors. The default model is `sentence-transformers/all-MiniLM-L6-v2` (Apache-2.0); change it with `STUDY_ASSISTANT_EMBEDDING_MODEL`. The model is loaded lazily and downloaded the first time a document is indexed or searched. No model is loaded when opening the dashboard or library.

## Project structure

```text
ai-study-assistant/
├── app.py
├── assets/style.css
├── components/
│   ├── dashboard.py
│   ├── document_picker.py
│   └── sidebar.py
├── pages/
│   ├── ai_chat.py
│   ├── flashcards.py
│   ├── documents.py
│   ├── data_management.py
│   ├── system_health.py
│   ├── document_search.py
│   ├── progress.py
│   ├── planner.py
│   ├── question_generator.py
│   ├── quiz.py
│   ├── settings.py
│   └── summarizer.py
├── tests/
│   ├── test_ai_tools.py
│   ├── test_flashcards.py
│   ├── test_ocr.py
│   ├── test_quiz.py
│   ├── test_documents.py
│   ├── test_data_management.py
│   └── test_diagnostics.py
├── utils/
│   ├── ai_prompts.py
│   ├── flashcard_repository.py
│   ├── flashcard_scheduler.py
│   ├── flashcard_service.py
│   ├── chunking.py
│   ├── config.py
│   ├── database.py
│   ├── documents.py
│   ├── document_text_service.py
│   ├── gemini_service.py
│   ├── embedding_service.py
│   ├── indexing_service.py
│   ├── ocr_service.py
│   ├── retrieval_service.py
│   ├── vector_store.py
│   ├── pdf_processing.py
│   ├── planner_service.py
│   ├── backup_service.py
│   ├── data_export_service.py
│   ├── study_report_service.py
│   ├── diagnostics.py
│   └── safe_logging.py
├── .github/workflows/tests.yml
├── .env.example
├── .gitignore
├── requirements.txt
└── requirements-dev.txt
```

## Configure Gemini

The app uses Google's supported `google-genai` Python SDK. The default model is `gemini-3.8-flash`; change it with `GEMINI_MODEL` if another model available to your API project is needed. The model name is configured once and used by chat, summaries, and question generation.

Get an API key from [Google AI Studio](https://aistudio.google.com/apikey) and follow Google's [Gemini API key guidance](https://ai.google.dev/gemini-api/docs/api-key). Use a key restricted for the Gemini API. Never commit the key. The dashboard and local PDF tools do not need it.

### PowerShell environment variables

Set one or more keys for the current PowerShell window before launching the app. Numbered keys are tried in order; use only keys you are authorized to use:

```powershell
$env:GEMINI_API_KEY_1 = "paste-your-first-key-here"
$env:GEMINI_API_KEY_2 = "paste-your-second-key-here" # Optional
$env:GEMINI_API_KEY_3 = "" # Optional
$env:GEMINI_API_KEY_4 = "" # Optional
$env:GEMINI_MODEL = "gemini-3.8-flash"
$env:STUDY_ASSISTANT_MAX_CONTEXT_CHARS = "40000"
streamlit run app.py
```

`.env.example` documents these values, but the app does not automatically load `.env` files. `.env` is Git-ignored.

### Streamlit secrets

Alternatively, create `.streamlit/secrets.toml` in the project directory. It is excluded from Git. Configure one or more numbered keys, leaving unused entries out or empty:

```toml
GEMINI_API_KEY_1 = "paste-your-first-key-here"
GEMINI_API_KEY_2 = "paste-your-second-key-here"
GEMINI_API_KEY_3 = ""
GEMINI_API_KEY_4 = ""
GEMINI_MODEL = "gemini-3.8-flash"
```

The existing `GEMINI_API_KEY` setting remains supported for older setups. For each slot, an environment variable takes precedence over its Streamlit secret. Numbered keys are used in order, empty values are ignored, duplicates are removed, and at most four distinct keys are attempted. The legacy key is considered after numbered keys. Keys are passed directly to the SDK client and are never displayed, stored in SQLite, or logged by StudyMate. See Google's [official Python SDK documentation](https://googleapis.github.io/python-genai/) and [available Gemini models](https://ai.google.dev/gemini-api/docs/models).

### Key failover and quotas

If a request receives an authentication/permission response (HTTP 401 or 403) or a rate-limit/quota response (HTTP 429), StudyMate tries the next configured key, up to four distinct keys for that request. If all attempted keys fail, it reports the attempted key slots and asks you to check the keys and project quota. Other invalid requests do not trigger failover. Network failures and timeouts do not trigger another-key submission because the first request may already have completed; server errors are left to the SDK's bounded retry behavior and then reported.

Key failover is for using separately configured credentials, not for evading provider restrictions. Keys attached to the same Google Cloud project share that project's Gemini quotas, so adding keys from one project does not increase its project-level rate limits. The dashboard, SQLite activity, PDF uploads, and local document library work without any keys; Gemini features need at least one valid key.

## Using Your Own Gemini API Key

BYOK (“bring your own key”) lets a student authenticate Gemini requests with their own Google AI Studio key. Open **Settings** in the sidebar and scroll to **API Settings**. Select **Use my own Gemini API key**, enter the value in **Your Gemini API Key** (the field is masked), then choose **Activate personal key**. Activation only selects the key for the current Streamlit session; it does not verify it. Choose **Test Connection** to send a small request and confirm access. Choose **Clear personal key** to remove it from the session and return to the application's configured-key mode.

In application mode, StudyMate continues to use `GEMINI_API_KEY_1` through `GEMINI_API_KEY_4`, then the supported legacy `GEMINI_API_KEY`, from the environment or `.streamlit/secrets.toml`. Environment values take precedence over secrets per slot, and the existing bounded key rotation/failover behavior is preserved. If no personal key is active and application mode is selected, the configured keys are used when available. If personal mode is selected but no key is active, Gemini requests remain unavailable instead of silently using the application's keys. A failing personal key also never falls back to configured application credentials; correct it or clear it explicitly.

The personal key lives only in the active Streamlit session's memory. StudyMate does not save it in SQLite, chat history, files, secrets, environment variables, or activity records. It is still sent to and processed by the application's server so that server can authenticate requests to Google Gemini. Only enter keys into deployments you trust. Use **Clear personal key** when finished; closing the session also ends its session-scoped state. Google controls API availability, usage limits, billing, and applicable policies under the current terms for your account. Create a key at [Google AI Studio](https://aistudio.google.com/app/apikey).

## Use the Gemini study tools

- **AI Chat:** choose General study chat, Ask about one indexed document, or Ask across all indexed documents. Document modes retrieve relevant passages locally first; Gemini receives only those passages, which are displayed below answers with their PDF names, pages, and snippets. Conversations are separated by mode and document scope. Start a new chat or confirm deletion to clear a conversation.
- **Semantic Search:** search all indexed PDFs or one PDF locally, choose the number of passages, and review cosine similarity scores, source filenames, page numbers, and passage text. Search does not call Gemini.
- **My Documents:** choose **Index documents** to process all eligible PDFs or index one PDF from its expander. Indexing re-reads each stored PDF to preserve real page numbers, splits text into overlapping page-specific chunks, and creates local CPU embeddings. The first indexing action downloads the configured model. Use **Rebuild FAISS cache** to recreate the ignored cache from vectors stored in SQLite.
- **AI Summarizer:** select a text-based PDF, choose Short, Medium, or Detailed, and generate or regenerate a summary. Use the Copy summary expander to copy the result.
- **Question Generator:** select a PDF, then choose 1–15 questions, difficulty, type, and whether expected answers and explanations should be included. The response is parsed and checked before it is shown. This does not start an interactive quiz.
- **Practice Quiz:** select a text-ready PDF, optionally enter a title, choose difficulty and question count, and optionally set a 5–120 minute timer. Quiz questions are generated through the same centralized Gemini service and key selection as the other AI tools. During an attempt, saved answers and the original start time are stored locally; correct answers and explanations are hidden until submission. Scoring is performed locally (one point per correct answer, no negative marks), including when the timer ends. Results include duration, every submitted answer, correct answer, explanation, topic, and validated source pages. You can retry the full quiz or only missed and unanswered questions; both create a new saved attempt without changing prior results.
- **Quiz history:** saved quizzes, question sets, answers, and attempts live in the local SQLite database. History is newest first, can be filtered by quiz or PDF, and lets you resume an unfinished attempt or review a submitted result. If a source PDF is deleted, its filename and saved quiz results remain available.
- **Flashcards:** create decks, add or edit cards manually without an API key, or generate proposals from a text-ready PDF with the existing Gemini service. Select cards to save after reviewing and editing each proposal. Study Due Cards starts a deck-filtered or all-deck session; only the front appears until you choose **Show answer**, then rate recall as Again, Hard, Good, or Easy. Standard sessions include overdue and currently due review cards; optional new cards are limited by the daily allowance (default 10). Decks and cards are archived instead of permanently deleted, preserving review history. If a source PDF is deleted, its filename and page references remain while the unavailable source is identified. A validated quiz question can also be copied into a chosen deck without modifying its quiz result.
- **Flashcard scheduling:** this SM-2-inspired variant schedules Again after 10 minutes; new Hard, Good, and Easy cards after 1, 2, and 4 days. During relearning after Again, these intervals are 1, 3, and 4 days. For later reviews, Hard uses about 1.2× the previous interval, Good uses the current ease factor, and Easy is at least one day longer than the corresponding Good interval. Hard lowers ease by 0.15, Easy raises it by 0.15; intervals are clamped to 1–3650 days. Again lowers ease by 0.2 and records a lapse. Failed cards do not repeat inside the current session. Day intervals keep the local clock time, and “due today” uses local calendar midnight on the running application host.
- **Study Progress:** see completed and timed-out attempts, average/best/latest submitted quiz scores, score trend when at least two attempts exist, and accuracy across answered questions. Average score is the arithmetic mean of attempt percentages; answer accuracy is correct answers divided by answered responses. Blank responses are excluded from answer accuracy while counting as unanswered quiz points. Topic suggestions are marked provisional until at least three responses are recorded for that topic; topics below 60% accuracy after that threshold are marked for review. Quiz review and analytics do not call Gemini.
- Flashcard review events and scheduling state are stored in the same local SQLite database. Flashcard success rate counts Hard, Good, and Easy self-ratings divided by all ratings; it is self-reported recall, not an objective score or a guarantee of mastery. Generated flashcards require the configured or personal Gemini key; manual cards, deck management, due queues, review, and saved-card history work without a Gemini key or embedding model.

## Study Planner and exam revision calendar

Open **Study Planner** from the sidebar or dashboard. Create a subject first, then add topics or chapters with estimated effort (minutes), priority (1–5), and optionally an upcoming exam. Topics can exist without a PDF. The optional PDF source keeps its document ID and filename; if its document is later deleted, the topic and saved filename remain and the foreign-key reference becomes empty. Subject archive hides it from active planning while preserving related exams, topics, tasks, sessions, and quiz/flashcard history.

Add exams with a required subject and date; start time, location, and notes are optional. Exams can be edited, marked completed, or archived. Archiving preserves related work. A date can be today or in the past; the calendar reports the relative day without shifting the saved date. Dates are calendar dates interpreted on the computer running Streamlit (the local host timezone). Saved activity/session timestamps include the host's local UTC offset. There is no external calendar sync or cross-timezone conversion.

In **Plan settings**, configure a daily goal (0 disables the goal), available minutes on selected days, preferred study days, and a per-day task limit. Unselected days are rest days and the planner will not allocate work to them. Defaults are a 60-minute goal, 120 minutes available, Monday through Friday selected, and a four-task daily limit; weekends start as rest days.

For an exam with associated topics, choose **Build revision plan preview**. The deterministic local algorithm sorts topics by priority (high first), allocates chunks of up to 45 minutes across eligible days, observes existing planned workload and task caps, and offers a short final-recall task when capacity permits. It excludes the exam date and later dates. Review the proposed tasks and any reported unscheduled minutes, then explicitly save. Capacity is an estimate, not a guarantee of exam success. If time is insufficient, the planner reports the remaining effort; increase available study time/days, adjust topic estimates, or plan manually. No Gemini key, OCR, FAISS index, or embedding model is needed.

Create tasks manually or from a proposed plan. Tasks can link to a subject/topic, upcoming exam, existing PDF, and existing quiz. Planned tasks can be edited, rescheduled, completed, canceled, or marked missed when overdue. Completing from a task records the actual session and completes the task atomically. Completed tasks retain their completion timestamp if reopened. Canceled tasks remain in history and are not counted as completed. The missed-work preview places selected overdue/missed incomplete tasks on configured study days before their related exam, within availability and task limits; **Apply these changes** is the explicit confirmation. Completed tasks and exam dates are not modified. Work that does not fit is listed.

The daily view shows actual due/overdue flashcard counts as a recommendation, read from the existing flashcard schedule. Merely viewing the planner does not modify due dates or intervals and does not create recurring recommendation tasks. The planner also surfaces only quiz topics that existing progress analytics mark for review based on saved responses; it does not regenerate quizzes or change attempt scores. Use **Revise flashcards** or **Practice quiz** task types to plan sessions and optionally link existing material.

Record a session from a task with actual minutes and an optional note, or add a standalone session with optional subject, topic, and exam links. Study history lists saved sessions. Progress distinguishes estimated planned minutes from actual recorded session minutes, and counts task completion only for completed tasks (not canceled tasks). Session records, subjects, topics, exams, tasks, and planner settings are stored in additive `planner_*` tables in the same local `study_assistant.db` described below. Planner tables do not contain PDF text or API keys. All planner workflows work without Gemini.
- A missing key produces setup guidance and does not prevent the dashboard, settings, progress page, local document library, or manual flashcard workflows from opening.

## Backups, exports, and study reports

Open **Data Management** from the sidebar. Backups and exports are created only when you click their action; ordinary page reruns do not start backup work. These files can contain private study data. Downloaded backups and exports are **not encrypted**; store them securely.

- **Database-only backup (`.sqlite3`):** a point-in-time SQLite online snapshot. It includes SQLite tables, indexes, constraints, extracted PDF text, OCR metadata, quizzes and answers, flashcards and review history, planner data, study activity, and saved semantic vectors. It does not include externally stored PDF files.
- **Complete backup (`.zip`):** a version 1 manifest, a validated SQLite snapshot, and every PDF referenced by the document library. The manifest records relative archive paths, sizes, and SHA-256 checksums. FAISS caches are rebuildable and excluded, as are environment variables, app/API secrets, session-only personal Gemini keys, logs, OCR render temporaries, virtual environments, and unrelated files. Complete archives are limited to 512 MiB and are not encrypted.
- **Restore:** select a `.sqlite3`, `.sqlite`, `.db`, or complete `.zip` file and validate it before confirming replacement. Validation checks SQLite integrity, core tables, foreign keys, format/schema versions, archive paths, and checksums. A persistent safety SQLite snapshot is saved under the configured app-data directory's `backups` folder before replacement. Older additive schemas are migrated in a staging database; a future schema is rejected. The live database is checkpointed and atomically replaced, then checked again. Existing PDFs are never overwritten: identical files are reused, while conflicts receive a new generated filename. A database-only restore leaves external PDF files alone, so some restored document records may refer to unavailable files. Connections are opened and closed per operation, so no restart is required. The app must not be actively writing to the database from another process during restore.
- **Exports:** choose one or more categories for versioned UTF-8 JSON, or one category for UTF-8-with-BOM CSV. Categories are quiz results with answer details, flashcards, flashcard review history, planner records, study activity summaries, and document/OCR/index metadata. Chat transcripts, API keys, environment variables, secrets, and PDF contents are excluded. CSV cells that could execute as spreadsheet formulas are prefixed safely. Flashcard exports preserve deck/card relationships, source filename and page metadata, scheduling state, and archived cards when you opt in; recall labels remain explicitly self-reported. There is no flashcard importer in Phase 10.
- **Study report:** download a standalone HTML report for 7, 30, 90, or 365 days, or all time. It uses saved quiz attempts, flashcard reviews, exams, planner tasks, subjects/topics, and actual study sessions. Quiz scores remain separate from self-rated flashcard recall, and estimated planned minutes remain separate from recorded session minutes. Empty periods are reported without fabricated values.
- **Temporary files:** validation and restore staging use an operating-system temporary directory and are cleaned after each operation. The pre-restore safety snapshot is intentionally retained for recovery. FAISS indexes can be rebuilt from the database after restoring on another machine.

The ZIP manifest format is currently version 1 and the database schema is version 10. Backups from a newer schema are rejected; retain the original app version for recovery if you need to open a backup created by a future release.

When an AI request runs, the current prompt and the data needed for that request are sent to Google's Gemini service. General chat sends no document text. Document chat sends only locally retrieved passages within the configured context budget; it does not send the full PDF or extracted document text. Semantic search embeds queries locally and does not call Gemini. Embedding inference and FAISS search run on the local CPU. Treat local chat history, uploaded documents, and the SQLite database as sensitive; they stay on this device unless you use a Gemini study tool.

The Google SDK handles a bounded number of transient retries. StudyMate does not loop or retry indefinitely; invalid credentials, rate limits, unavailable models, and network failures are reported without displaying the key or returning a fabricated answer.

## Run on Windows

Open PowerShell in the project folder, then create and activate a virtual environment:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

If PowerShell blocks virtual environment activation, run this for the current terminal and activate again:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
```

The app opens in your browser, usually at `http://localhost:8501`. No `.env` file or API key is needed.

### Upload PDFs

Open **My Documents**, select one or more PDF files, then choose **Upload selected PDFs**. Files are limited to 20 MB each by default. Uploaded PDFs are validated, their text is extracted, and the file and text are saved locally for later use. Open a document in the library to review its metadata and extracted text. Select the confirmation checkbox before deleting a document.

### Local OCR for scanned PDFs

Text assessment happens per page during upload. Pages with enough selectable text use that native text; pages with a substantial image and too little native text are marked for OCR. Blank pages and small decorative images are not automatically OCRed. Upload never starts OCR.

Install the separate Tesseract engine on Windows using the [UB Mannheim Windows installer](https://github.com/UB-Mannheim/tesseract/wiki), a distribution recommended by the [Tesseract installation guide](https://tesseract-ocr.github.io/tessdoc/Installation.html). Select English (`eng`) or the desired language data during setup. Additional language `.traineddata` files are available from the [official Tesseract language-data repository](https://github.com/tesseract-ocr/tessdata) and must be installed in Tesseract's `tessdata` directory; StudyMate reports missing language data before processing. Tesseract is not installed by pip, and the Python `pytesseract` package alone is insufficient.

If the executable is not on PATH, set its full path before launching Streamlit:

```powershell
$env:STUDY_ASSISTANT_TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
$env:STUDY_ASSISTANT_OCR_LANG = "eng"
streamlit run app.py
```

For multiple installed languages, use a plus-separated code such as `eng+fra`. In **My Documents**, open a document and choose **Run local OCR**. OCR runs only for pages flagged for scanning, failed pages, or pages explicitly reprocessed after a language or resolution setting changes. The display reports completed, empty, and failed pages; failed and empty attempts can be retried, while successful prior text remains saved if a retry fails. The original PDF is never changed. OCR uses PyMuPDF rendering at 275 DPI by default, capped at 300 DPI and 25 megapixels per rendered page; configure `STUDY_ASSISTANT_OCR_DPI` (72–300) if needed. Temporary images stay in memory.

Recognized text is stored in SQLite per PDF page with language, engine version, status, and timestamp. Native extracted text remains intact. StudyMate prefers usable native page text and substitutes successful OCR text for pages with insufficient native text; page labels retain original PDF page numbers. The preview labels OCR pages. After OCR changes text, the semantic index becomes stale and is excluded from retrieval until you choose **Reindex**. OCR itself does not load Sentence Transformers, FAISS, or Gemini. Once indexed, OCR-derived passages can be used by semantic search and document chat; summaries, generated questions, quizzes, and flashcard proposals use the shared effective text and preserve page markers where citations are supported.

OCR can misread similar characters, equations, tables, handwriting, rotated content, and low-quality scans. It does not provide reliable handwriting recognition or a guaranteed confidence score. Review recognized text against the PDF. OCR is local, but later Gemini study requests may send the relevant effective text or retrieved passages to Google under the existing context limits and API-key selection rules.

### Local database and document storage

- The existing SQLite database stays at `study_assistant.db` in the project folder by default so Phase 1 activity data is preserved.
- Uploaded PDFs are stored outside the repository in `%LOCALAPPDATA%\StudyMate\documents` on Windows. On other systems, the default is `~/.local/share/StudyMate/documents`.
- The database contains the document metadata and extracted text. The database and uploaded files are local to this device and are ignored by Git.
- Optional environment variables:
  - `STUDY_ASSISTANT_DB`: full path to the SQLite database file.
  - `STUDY_ASSISTANT_DATA_DIR`: app data directory; PDFs are stored in its `documents` subfolder.
  - `STUDY_ASSISTANT_DOCUMENTS_DIR`: direct override for the PDF storage directory. Takes precedence over `STUDY_ASSISTANT_DATA_DIR`.
  - `STUDY_ASSISTANT_MAX_UPLOAD_MB`: maximum file size in megabytes (default `20`).
  - `STUDY_ASSISTANT_INDEX_DIR`: FAISS cache directory (default `<app data directory>/indexes`). The cache is disposable and ignored by Git; SQLite vectors are the source of truth.
  - `STUDY_ASSISTANT_EMBEDDING_MODEL`: Sentence Transformers model identifier (default `sentence-transformers/all-MiniLM-L6-v2`). Changing this makes existing indexes stale and requires reindexing.
  - `STUDY_ASSISTANT_CHUNK_SIZE_CHARS` / `STUDY_ASSISTANT_CHUNK_OVERLAP_CHARS`: page-local chunk sizing (defaults `1200` / `200`); changes require reindexing.
  - `STUDY_ASSISTANT_RETRIEVAL_TOP_K`: default passages retrieved for document chat/search (default `5`, maximum `10`).
  - `STUDY_ASSISTANT_TESSERACT_CMD`: optional full path to `tesseract.exe` if it is not on PATH.
  - `STUDY_ASSISTANT_OCR_LANG`: installed Tesseract language codes (default `eng`; combine with `+`).
  - `STUDY_ASSISTANT_OCR_DPI`: local rendering resolution (default `275`, clamped to 72–300 DPI).
  - `STUDY_ASSISTANT_OCR_MIN_NATIVE_CHARS`: per-page minimum native text used during scan assessment (default `30`).

For example, set an option for the current PowerShell session before launching:

```powershell
$env:STUDY_ASSISTANT_MAX_UPLOAD_MB = "30"
streamlit run app.py
```

### Automated tests

Install the optional test dependency and run the suite from the project folder:

```powershell
pip install -r requirements-dev.txt
pytest
```

## Deployment, Diagnostics, and Recovery

### Supported installation

StudyMate supports Python 3.10 or newer. A clean Windows setup uses a local virtual environment and installs runtime packages from `requirements.txt`; `requirements-dev.txt` adds pytest for development. Pillow is listed directly for local OCR image rendering. Tesseract itself is a separate Windows installation and is not supplied by pip.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m streamlit run app.py
```

Gemini is optional for local use. Set `GEMINI_API_KEY_1` (and optionally numbered keys 2–4) or configure Streamlit secrets when you want Gemini features. Personal keys can instead be activated from **Settings** and remain in the current Streamlit session. Health checks show only whether a selected key mode is configured; they do not verify credentials or make a Gemini request.

Install Tesseract only if you need OCR for scanned PDFs. Use the Windows setup above and install the language data you need. **System Health** checks the Python wrapper, executable, and selected language list, but it never OCRs a document. A missing Tesseract executable or language pack disables scanned-page OCR only.

Sentence Transformers and FAISS power local semantic indexing. The embedding model is loaded lazily when indexing/search first needs it; the first use may download it and requires network access unless it is already cached. System Health checks the configured model and local cache without loading or downloading it. Gemini requests also require network access. Local PDFs, quiz scoring, flashcard review, planner, backups, and exports do not require Gemini.

### Readiness and recovery

Open **System Health** from the sidebar. **CORE READY** means the supported Python runtime, startup packages, database, and database-folder access are available. Warnings for Gemini, Tesseract, or the embedding model are optional-feature status, not a failure of local study tools. The page shows effective local storage locations and free space, optional dependency versions, schema compatibility, and actionable next steps. A deep database integrity check runs only after you select **Run database integrity check**; it is read-only. Basic checks do not run OCR, contact Gemini, or load the embedding model. They may create missing configured data, document, and index folders, then remove their temporary write probes.

The default SQLite file is `study_assistant.db` in the repository. PDFs live under `%LOCALAPPDATA%\StudyMate\documents` on Windows. Configuration overrides are listed in the local storage section above. Database schema changes are additive and run inside a transaction. Startup never deletes or resets an existing database; a failed migration rolls back its schema changes and the app reports a safe recovery message.

- **Database locked:** close other StudyMate windows and SQLite/database tools using the same file, then retry. Do not copy a live database while it is being written; use **Data Management → Create backup**.
- **Database corruption or incompatible schema:** keep the original database and its `-wal`/`-shm` sidecars; do not delete them. If StudyMate cannot start, first make a separate filesystem copy of those files. Then set `STUDY_ASSISTANT_DB` to a new recovery path, launch StudyMate there, and use **Data Management → Restore backup** with a previously verified Phase 10 backup. For example, in PowerShell set `$env:STUDY_ASSISTANT_DB = "$env:LOCALAPPDATA\StudyMate\recovery.sqlite3"` before launching. Keep the recovery path configured after the restore. A newer schema requires the matching newer StudyMate version.
- **Model download failure:** check network access and available disk space, then retry indexing from **My Documents**. Health checks do not download a model; they identify whether a local cache appears to exist. Do not delete SQLite vectors or document data as a model troubleshooting step.
- **OCR failure:** open **System Health** to distinguish a missing Python wrapper, executable, or language pack. Correct `STUDY_ASSISTANT_TESSERACT_CMD` or install the missing language data, restart the app, and retry OCR from the document card. OCR errors preserve already completed page results.
- **PDF upload failure:** confirm the PDF is not password protected or damaged, stays within the configured per-file limit, and that the configured document folder is writable. The original upload name is metadata only; stored names are generated locally.

To run CI checks locally, activate the project environment and run:

```powershell
python -m pip check
python -m compileall -q app.py components pages utils tests
python -m pytest -q
git diff --check
```

GitHub Actions runs these checks on pushes to `master` and pull requests targeting `master`, using Python 3.11 and 3.12. The workflow installs runtime and test dependencies, checks dependency consistency and compilation, runs the full suite, and checks changed lines for whitespace errors. Tests run in offline Hugging Face mode, do not require a Gemini key or live Gemini calls, and do not install Tesseract. CI does not publish or deploy the app.

StudyMate is a local, single-user application without authentication, cloud synchronization, encryption for backups, or multi-user isolation. A readiness check is a local configuration/storage check, not a penetration test or a guarantee of production security. Keep backups private and verified.

## Current limitations

- Quiz questions depend on Gemini generation quality; invalid or duplicate generated questions are discarded, and at least five valid questions are needed to save a quiz. Source page references are limited to pages present in the supplied PDF passages.
- Quiz timers are enforced by the local application session and saved start time; they are intended for study practice, not proctored or tamper-proof exams.
- Spaced repetition is a transparent SM-2-inspired heuristic. Flashcard ratings are self-reported recall and do not verify mastery or guarantee retention; due-date day boundaries follow the local timezone of the app host.
- OCR quality depends on scan clarity and installed Tesseract language data. It may misread handwriting, formulas, tables, and distorted pages; manual verification is recommended.
- Very large PDFs may take time to process; each upload is limited to 20 MB by default.
- Embeddings use an English sentence model and need a one-time model download; multilingual retrieval quality may be limited. Indexing runs on CPU and can take time for large libraries.
- Similarity scores rank passages; they are not confidence probabilities. Gemini citations are checked against retrieved source/page pairs, but generated answers can still misinterpret evidence.
- Dashboard activity metrics remain at zero until the related activity is recorded.
- Progress data is stored in a local SQLite file and is not synchronized across devices.
- Summaries and generated questions use a bounded leading-text window; document chat uses the Phase 4 semantic retrieval pipeline.

## Planned development phases

1. **Phase 1 — Foundation:** complete — dashboard, navigation, placeholder pages, and SQLite setup.
2. **Phase 2 — Documents:** complete — PDF upload, text extraction, and document library.
3. **Phase 3 — AI study tools:** complete — Gemini chat, summaries, and validated question generation grounded in selected study material.
4. **Phase 4 — Retrieval:** complete — local embeddings, FAISS semantic search, and passage-grounded document chat.
5. **Phase 5 — BYOK:** complete — session-only personal Gemini keys, explicit connection testing, and application-key selection.
6. **Phase 6 — Practice and progress:** complete — interactive quizzes, scoring, answer review, and local study progress insights.
7. **Phase 7 — Flashcards:** complete — persistent decks and cards, PDF-generated proposals, active recall, local spaced repetition, and review analytics.
8. **Phase 8 — Local OCR:** complete — page-aware Tesseract OCR for scanned and mixed PDFs, safe retries, semantic reindexing, and connected study tools.
9. **Phase 9 — Study planner:** complete — local subjects, exams, revision planning, tasks, and session history.
10. **Phase 10 — Data management:** complete — validated backups and restore, selected data exports, and downloadable study reports.
11. **Phase 11 — Deployment and reliability:** complete — local readiness diagnostics, safe startup guidance, transactional additive schema setup, and GitHub Actions regression checks.

All phases will continue in this repository.
