import os
from urllib.parse import quote_plus
from dotenv import load_dotenv

load_dotenv()


class Config:
    PROJECT_NAME = "DEVAA"
    SECRET_KEY = os.getenv("SECRET_KEY", "default-secret-key-change-me!!")

    # ── Database ────────────────────────────────────────────────
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "3306")
    DB_NAME = os.getenv("DB_NAME", "devaa_db")

    _safe_password = quote_plus(DB_PASSWORD) if DB_PASSWORD else ""
    _default_db_url = f"mysql+pymysql://{DB_USER}:{_safe_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", _default_db_url)
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ── LLM Provider ────────────────────────────────────────────
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    LLM_API_URL = os.getenv("LLM_API_URL")
    LLM_MODEL = os.getenv("LLM_MODEL")
    LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", 300))

    # ── Jira Integration ────────────────────────────────────────
    JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "")
    JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "DEVAA")
    JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
    JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
    JIRA_STATUS_TODO = os.getenv("JIRA_STATUS_TODO", "To Do")
    JIRA_STATUS_IN_PROGRESS = os.getenv("JIRA_STATUS_IN_PROGRESS", "In Progress")
    JIRA_STATUS_QA_TESTING = os.getenv("JIRA_STATUS_QA_TESTING", "QA Testing")
    JIRA_STATUS_DONE = os.getenv("JIRA_STATUS_DONE", "Done")

    # ── GitHub Integration ──────────────────────────────────────
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
    GITHUB_BASE_URL = os.getenv("GITHUB_BASE_URL", "https://api.github.com")
    GITHUB_ORG = os.getenv("GITHUB_ORG", "")
    GITHUB_DEFAULT_BASE_BRANCH = os.getenv("GITHUB_DEFAULT_BASE_BRANCH", "main")

    GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", "")
    GITLAB_BASE_URL = os.getenv("GITLAB_BASE_URL", "https://gitlab.com")

    # ── Guardrails ──────────────────────────────────────────────
    AGENT_MAX_LOOP_ITERATIONS = int(os.getenv("AGENT_MAX_LOOP_ITERATIONS", 3))
    ALLOWED_REPO_PREFIXES = [
        p.strip() for p in os.getenv("ALLOWED_REPO_PREFIXES", "").split(",") if p.strip()
    ]
    ENABLE_OUTPUT_SCANNING = os.getenv("ENABLE_OUTPUT_SCANNING", "true").lower() == "true"

    # ── CORS ────────────────────────────────────────────────────
    ALLOWED_ORIGINS = [
        o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    ]
