# AI Study Assistant

**Your Personal AI-Powered Learning Companion**

AI Study Assistant is a diploma engineering project that will bring document-based question answering, AI chat, summaries, question generation, quizzes, and study progress into one learning workspace.

## Phase 1 features

- Responsive Streamlit dashboard with custom CSS and working sidebar navigation.
- Four dashboard metrics based only on recorded local activity. New installations show zero counts and no average score until real activity exists.
- Quick actions and feature cards for the planned study tools.
- Clear placeholder pages for features that are not implemented yet; the app does not generate pretend AI responses.
- Minimal SQLite initialization for future study activity records.
- Runs without a Gemini API key.

## Technology

- Python 3.10 or newer
- Streamlit
- SQLite (Python standard library)
- Custom CSS

Gemini, PyMuPDF, FAISS, and Sentence Transformers are planned for later phases and are not installed in Phase 1.

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
├── utils/
│   ├── config.py
│   └── database.py
├── .env.example
├── .gitignore
└── requirements.txt
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

The app opens in your browser, usually at `http://localhost:8501`. No `.env` file or API key is needed for Phase 1.

## Current limitations

- AI chat, PDF uploads and processing, summarization, question generation, and interactive quizzes are placeholders.
- The dashboard is empty until completed features record real activity.
- Progress data is stored in a local SQLite file and is not synchronized across devices.
- Gemini integration and retrieval-augmented generation are not part of Phase 1.

## Planned development phases

1. **Phase 1 — Foundation:** dashboard, navigation, placeholder pages, and SQLite setup.
2. **Phase 2 — Documents:** PDF upload, text extraction, and document library.
3. **Phase 3 — AI study tools:** Gemini chat, summaries, and question generation grounded in study material.
4. **Phase 4 — Retrieval:** embeddings, FAISS search, and document-based question answering.
5. **Phase 5 — Practice and progress:** interactive quizzes, scoring, and study progress insights.

All phases will continue in this repository.
