# AI Study Assistant

**Your Personal AI-Powered Learning Companion**

AI Study Assistant is a diploma engineering project that will bring document-based question answering, AI chat, summaries, question generation, quizzes, and study progress into one learning workspace.

## Implemented features

- Responsive Streamlit dashboard with custom CSS and working sidebar navigation.
- Four dashboard metrics based only on recorded local activity. New installations show zero counts and no average score until real activity exists.
- Quick actions and feature cards for the study tools.
- SQLite records for study activity, documents, and saved AI chat conversations.
- Runs without a Gemini API key.
- Multiple PDF uploads with extension, content, and file-size validation.
- Page-by-page text extraction using PyMuPDF, with page boundaries retained.
- Local document library with persisted metadata, extracted text, duplicate detection, text preview, and confirmed deletion.
- Dashboard document count based on PDFs actually stored in the library.
- Scanned or image-only PDFs are identified when no embedded selectable text is found; OCR is not supported.
- General and document-grounded Gemini study chat with locally saved conversations and messages.
- Local semantic document search over page-aware PDF chunks using Sentence Transformers and FAISS.
- Rebuildable FAISS cache with vectors and index status persisted in the local SQLite database.
- Document chat retrieves only relevant indexed passages and displays page citations and source snippets.
- Gemini summaries in short, medium, and detailed formats.
- Gemini question generation for multiple-choice, short-answer, and descriptive questions, with validated structured responses.
- Successful AI operations appear in recent activity without storing their prompts, document contents, or generated summaries/question sets there.

## Technology

- Python 3.10 or newer
- Streamlit
- PyMuPDF (PDF validation and text extraction)
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
│   ├── documents.py
│   ├── document_search.py
│   ├── progress.py
│   ├── question_generator.py
│   ├── quiz.py
│   ├── settings.py
│   └── summarizer.py
├── tests/
│   ├── test_ai_tools.py
│   └── test_documents.py
├── utils/
│   ├── ai_prompts.py
│   ├── chunking.py
│   ├── config.py
│   ├── database.py
│   ├── documents.py
│   ├── gemini_service.py
│   ├── embedding_service.py
│   ├── indexing_service.py
│   ├── retrieval_service.py
│   ├── vector_store.py
│   └── pdf_processing.py
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

## Use the Gemini study tools

- **AI Chat:** choose General study chat, Ask about one indexed document, or Ask across all indexed documents. Document modes retrieve relevant passages locally first; Gemini receives only those passages, which are displayed below answers with their PDF names, pages, and snippets. Conversations are separated by mode and document scope. Start a new chat or confirm deletion to clear a conversation.
- **Semantic Search:** search all indexed PDFs or one PDF locally, choose the number of passages, and review cosine similarity scores, source filenames, page numbers, and passage text. Search does not call Gemini.
- **My Documents:** choose **Index documents** to process all eligible PDFs or index one PDF from its expander. Indexing re-reads each stored PDF to preserve real page numbers, splits text into overlapping page-specific chunks, and creates local CPU embeddings. The first indexing action downloads the configured model. Use **Rebuild FAISS cache** to recreate the ignored cache from vectors stored in SQLite.
- **AI Summarizer:** select a text-based PDF, choose Short, Medium, or Detailed, and generate or regenerate a summary. Use the Copy summary expander to copy the result.
- **Question Generator:** select a PDF, then choose 1–15 questions, difficulty, type, and whether expected answers and explanations should be included. The response is parsed and checked before it is shown. This does not start an interactive quiz.
- A missing key produces setup guidance and does not prevent the dashboard, settings, progress page, or local document library from opening.

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

- Interactive quizzes and scoring are not implemented yet.
- Scanned PDFs without an embedded text layer cannot be read because OCR is not implemented.
- Very large PDFs may take time to process; each upload is limited to 20 MB by default.
- Embeddings use an English sentence model and need a one-time model download; multilingual retrieval quality may be limited. Indexing runs on CPU and can take time for large libraries.
- Similarity scores rank passages; they are not confidence probabilities. Gemini citations are checked against retrieved source/page pairs, but generated answers can still misinterpret evidence.
- Dashboard activity metrics remain at zero until the related activity is recorded.
- Progress data is stored in a local SQLite file and is not synchronized across devices.
- Document chat, summaries, and generated questions use a bounded leading-text window. Semantic retrieval and retrieval-augmented generation are planned for Phase 4.

## Planned development phases

1. **Phase 1 — Foundation:** complete — dashboard, navigation, placeholder pages, and SQLite setup.
2. **Phase 2 — Documents:** complete — PDF upload, text extraction, and document library.
3. **Phase 3 — AI study tools:** complete — Gemini chat, summaries, and validated question generation grounded in selected study material.
4. **Phase 4 — Retrieval:** complete — local embeddings, FAISS semantic search, and passage-grounded document chat.
5. **Phase 5 — Practice and progress:** interactive quizzes, scoring, and study progress insights.

All phases will continue in this repository.
