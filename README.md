# AI Study Assistant

**Your Personal AI-Powered Learning Companion**

AI Study Assistant is a diploma engineering project that will bring document-based question answering, AI chat, summaries, question generation, quizzes, and study progress into one learning workspace.

## Phase 1 and Phase 2 features

- Responsive Streamlit dashboard with custom CSS and working sidebar navigation.
- Four dashboard metrics based only on recorded local activity. New installations show zero counts and no average score until real activity exists.
- Quick actions and feature cards for the planned study tools.
- Clear placeholder pages for features that are not implemented yet; the app does not generate pretend AI responses.
- Minimal SQLite initialization for future study activity records.
- Runs without a Gemini API key.
- Multiple PDF uploads with extension, content, and file-size validation.
- Page-by-page text extraction using PyMuPDF, with page boundaries retained.
- Local document library with persisted metadata, extracted text, duplicate detection, text preview, and confirmed deletion.
- Dashboard document count based on PDFs actually stored in the library.
- Scanned or image-only PDFs are identified when no embedded selectable text is found; OCR is not supported.

## Technology

- Python 3.10 or newer
- Streamlit
- PyMuPDF (PDF validation and text extraction)
- SQLite (Python standard library)
- Custom CSS

Gemini, FAISS, and Sentence Transformers are planned for later phases and are not installed in Phase 2.

## Project structure

```text
ai-study-assistant/
├── app.py
├── assets/style.css
├── components/
│   ├── dashboard.py
│   └── sidebar.py
├── pages/
│   ├── ai_chat.py
│   ├── documents.py
│   ├── progress.py
│   ├── question_generator.py
│   ├── quiz.py
│   ├── settings.py
│   └── summarizer.py
├── tests/
│   └── test_documents.py
├── utils/
│   ├── config.py
│   ├── database.py
│   ├── documents.py
│   └── pdf_processing.py
├── .env.example
├── .gitignore
├── requirements.txt
└── requirements-dev.txt
```

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

### Local database and document storage

- The existing SQLite database stays at `study_assistant.db` in the project folder by default so Phase 1 activity data is preserved.
- Uploaded PDFs are stored outside the repository in `%LOCALAPPDATA%\StudyMate\documents` on Windows. On other systems, the default is `~/.local/share/StudyMate/documents`.
- The database contains the document metadata and extracted text. The database and uploaded files are local to this device and are ignored by Git.
- Optional environment variables:
  - `STUDY_ASSISTANT_DB`: full path to the SQLite database file.
  - `STUDY_ASSISTANT_DATA_DIR`: app data directory; PDFs are stored in its `documents` subfolder.
  - `STUDY_ASSISTANT_DOCUMENTS_DIR`: direct override for the PDF storage directory. Takes precedence over `STUDY_ASSISTANT_DATA_DIR`.
  - `STUDY_ASSISTANT_MAX_UPLOAD_MB`: maximum file size in megabytes (default `20`).

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

## Current limitations

- AI chat, summarization, question generation, and interactive quizzes remain placeholders.
- Scanned PDFs without an embedded text layer cannot be read because OCR is not implemented.
- Very large PDFs may take time to process; each upload is limited to 20 MB by default.
- The dashboard is empty until completed features record real activity.
- Progress data is stored in a local SQLite file and is not synchronized across devices.
- Gemini integration and retrieval-augmented generation are not part of Phase 2.

## Planned development phases

1. **Phase 1 — Foundation:** complete — dashboard, navigation, placeholder pages, and SQLite setup.
2. **Phase 2 — Documents:** complete — PDF upload, text extraction, and document library.
3. **Phase 3 — AI study tools:** Gemini chat, summaries, and question generation grounded in study material.
4. **Phase 4 — Retrieval:** embeddings, FAISS search, and document-based question answering.
5. **Phase 5 — Practice and progress:** interactive quizzes, scoring, and study progress insights.

All phases will continue in this repository.
