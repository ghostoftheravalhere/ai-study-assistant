"""Navigation architecture and theme configuration regression checks."""

from pathlib import Path
import tomllib

from streamlit.testing.v1 import AppTest

from components.sidebar import NAVIGATION
from utils import database


ROOT = Path(__file__).resolve().parents[1]
PAGE_HEADINGS = {
    "Dashboard": "Good to see you",
    "AI Chat": "AI Chat</h1>",
    "My Documents": "My Documents</h1>",
    "Semantic Search": "Semantic Document Search</h1>",
    "AI Summarizer": "AI Summarizer</h1>",
    "Question Generator": "Question Generator</h1>",
    "Practice Quiz": "Practice Quiz</h1>",
    "Flashcards": "Flashcards</h1>",
    "Study Planner": "Study Planner</h1>",
    "Data Management": "Data Management</h1>",
    "Study Progress": "Study Progress</h1>",
    "Settings": "Settings</h1>",
    "System Health": "System Health</h1>",
}


def test_custom_navigation_has_one_renderer_for_each_supported_page() -> None:
    import app

    navigation_labels = [label for label, _ in NAVIGATION]
    assert list(app.PAGE_RENDERERS) == navigation_labels
    assert all(callable(app.get_page_renderer(label)) for label in navigation_labels)
    assert app.get_page_renderer("unknown") is None


def test_page_modules_are_outside_streamlit_discovery_directory() -> None:
    assert not (ROOT / "pages").exists()
    assert (ROOT / "study_pages" / "settings.py").is_file()
    assert not (ROOT / "study_pages" / "_placeholder.py").exists()

    stale_imports = []
    for source in ROOT.rglob("*.py"):
        if ".venv" in source.parts or "__pycache__" in source.parts:
            continue
        contents = source.read_text(encoding="utf-8")
        legacy_imports = (
            "from " + "pages ",
            "from " + "pages.",
            "import " + "pages.",
        )
        if any(pattern in contents for pattern in legacy_imports):
            stale_imports.append(source.relative_to(ROOT).as_posix())
    assert stale_imports == []


def test_default_theme_and_css_share_streamlit_theme_tokens() -> None:
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    theme = config["theme"]
    assert theme["base"] == "light"
    assert theme["primaryColor"] == "#4567e8"
    assert theme["backgroundColor"] == "#f7f8fc"

    css = (ROOT / "assets" / "style.css").read_text(encoding="utf-8")
    for token in ("--background-color", "--secondary-background-color", "--text-color", "--primary-color"):
        assert token in css
    assert ":focus-visible" in css
    assert "[data-testid=\"stSidebar\"]" in css


def test_all_sidebar_routes_render_and_settings_and_health_are_not_blank(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "study.sqlite3")
    database.initialize_database()
    app_test = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    assert not app_test.exception
    assert app_test.button(key="nav_Dashboard")

    for page, _icon in NAVIGATION:
        app_test.button(key=f"nav_{page}").click().run()
        assert not app_test.exception, f"{page} raised during sidebar navigation"
        assert app_test.session_state["active_page"] == page
        assert any(PAGE_HEADINGS[page] in item.value for item in app_test.markdown), page

    app_test.button(key="nav_Settings").click().run()
    assert any("API Settings" in item.value for item in app_test.markdown)
    app_test.button(key="nav_System Health").click().run()
    assert any("System Health" in item.value for item in app_test.markdown)
    assert any("CORE READY" in item.value for item in app_test.success)

    app_test.session_state["active_page"] = "Unknown page"
    app_test.run()
    assert any("This page is unavailable" in item.value for item in app_test.error)
