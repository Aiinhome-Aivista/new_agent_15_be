import os
from urllib.parse import quote_plus
from dotenv import load_dotenv

load_dotenv()


class Config:
    PROJECT_NAME = "DEVAA"
    SECRET_KEY = os.getenv("SECRET_KEY", "default-secret-key-change-me!!")

    # ── Database Provider Configuration ─────────────────────────
    # Options: DEFAULT, AWS, AZURE
    DB_PROVIDER = os.getenv("DB_PROVIDER", "DEFAULT").strip().upper()

    # 1. Local / Default MySQL Settings
    DB_HOST = os.getenv("DB_HOST") or os.getenv("MYSQL_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT") or os.getenv("MYSQL_PORT", "3306")
    DB_USER = os.getenv("DB_USER") or os.getenv("MYSQL_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD") or os.getenv("MYSQL_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME") or os.getenv("MYSQL_DATABASE", "devaa_db")

    # 2. AWS RDS Settings
    AWS_RDS_HOST = os.getenv("AWS_RDS_HOST", "")
    AWS_RDS_PORT = os.getenv("AWS_RDS_PORT", "3306")
    AWS_RDS_DATABASE = os.getenv("AWS_RDS_DATABASE", "")
    AWS_RDS_USER = os.getenv("AWS_RDS_USER", "")
    AWS_RDS_PASSWORD = os.getenv("AWS_RDS_PASSWORD", "")

    # 3. Azure DB Settings
    AZURE_DB_HOST = os.getenv("AZURE_DB_HOST", "")
    AZURE_DB_PORT = os.getenv("AZURE_DB_PORT", "3306")
    AZURE_DB_DATABASE = os.getenv("AZURE_DB_DATABASE", "acse_db")
    AZURE_DB_USER = os.getenv("AZURE_DB_USER", "")
    AZURE_DB_PASSWORD = os.getenv("AZURE_DB_PASSWORD", "")

    # Resolve active SQLALCHEMY_DATABASE_URI dynamically based on DB_PROVIDER
    if os.getenv("DATABASE_URL"):
        SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    elif DB_PROVIDER in ("AWS", "RDS") and AWS_RDS_HOST:
        _safe_aws_pwd = quote_plus(AWS_RDS_PASSWORD) if AWS_RDS_PASSWORD else ""
        SQLALCHEMY_DATABASE_URI = (
            f"mysql+pymysql://{AWS_RDS_USER}:{_safe_aws_pwd}@{AWS_RDS_HOST}:{AWS_RDS_PORT}/{AWS_RDS_DATABASE}"
        )
    elif DB_PROVIDER in ("AZURE",) and AZURE_DB_HOST:
        _safe_az_pwd = quote_plus(AZURE_DB_PASSWORD) if AZURE_DB_PASSWORD else ""
        SQLALCHEMY_DATABASE_URI = (
            f"mysql+pymysql://{AZURE_DB_USER}:{_safe_az_pwd}@{AZURE_DB_HOST}:{AZURE_DB_PORT}/{AZURE_DB_DATABASE}"
        )
    else:
        _safe_password = quote_plus(DB_PASSWORD) if DB_PASSWORD else ""
        SQLALCHEMY_DATABASE_URI = f"mysql+pymysql://{DB_USER}:{_safe_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_recycle": 280,
        "pool_pre_ping": True,
    }

    # ── Storage Configuration ───────────────────────────────────
    # Options: DEFAULT, AWS, AZURE
    CLOUD_PROVIDER = os.getenv("CLOUD_PROVIDER", "DEFAULT").strip().upper()

    # 1. AWS S3 Settings
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME", "")
    AWS_S3_BASE_FOLDER = os.getenv("AWS_S3_BASE_FOLDER", "")
    AWS_S3_AGENT_FOLDER = os.getenv("AWS_S3_AGENT_FOLDER", "")

    # 2. Azure Blob Storage Settings
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    AZURE_CONTAINER_NAME = os.getenv("AZURE_CONTAINER_NAME", "agent-artifacts")

    # 3. Local Storage Settings
    UPLOAD_PATH = os.getenv(
        "UPLOAD_PATH",
        os.path.join(
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            "data",
            "uploads"
        )
    )

    # ── LLM Provider ──────────────────────────────────────────
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")

    # Primary Gemini key (used as fallback)
    GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL    = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

    # Per-agent dedicated keys (KEY_1..KEY_5)
    # If a key is empty, the rotation pool falls back to GEMINI_API_KEY
    GEMINI_API_KEY_1 = os.getenv("GEMINI_API_KEY_1")  # Intake
    GEMINI_API_KEY_2 = os.getenv("GEMINI_API_KEY_2")  # RepoAnalysis
    GEMINI_API_KEY_3 = os.getenv("GEMINI_API_KEY_3")  # Developer
    GEMINI_API_KEY_4 = os.getenv("GEMINI_API_KEY_4")  # Validator
    GEMINI_API_KEY_5 = os.getenv("GEMINI_API_KEY_5")  # BranchPR + Comment

    # Agent → key index mapping (used by LLMService)
    GEMINI_AGENT_KEY_MAP = {
        "IntakeValidation": "GEMINI_API_KEY_1",
        "RepoAnalysis":     "GEMINI_API_KEY_2",
        "Developer":        "GEMINI_API_KEY_3",
        "Validator":        "GEMINI_API_KEY_4",
        "BranchPR":         "GEMINI_API_KEY_5",
        "Comment":          "GEMINI_API_KEY_5",
        "ReworkHandler":    "GEMINI_API_KEY_1",
    }

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    LLM_API_URL = os.getenv("LLM_API_URL")
    LLM_MODEL = os.getenv("LLM_MODEL")
    LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", 300))

    # ── Jira Integration ────────────────────────────────────────
    ACTIVE_TASK_PROVIDER = os.getenv("ACTIVE_TASK_PROVIDER", "jira")
    MIN_SYNC_PRIORITY = os.getenv("MIN_SYNC_PRIORITY", "all")
    JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "")
    JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "DEVAA")
    JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
    JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
    JIRA_STATUS_TODO = os.getenv("JIRA_STATUS_TODO", "To Do")
    JIRA_STATUS_IN_PROGRESS = os.getenv("JIRA_STATUS_IN_PROGRESS", "In Progress")
    JIRA_STATUS_QA_TESTING = os.getenv("JIRA_STATUS_QA_TESTING", "QA Testing")
    JIRA_STATUS_DONE = os.getenv("JIRA_STATUS_DONE", "Done")
    SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", 10))

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

    # ── RAG & Vector Store ──────────────────────────────────────
    CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "chroma_db")))
    WORKSPACES_DIR = os.getenv("WORKSPACES_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "workspaces")))
    RAG_CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", 800))
    RAG_CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", 120))
    RAG_TOP_K = int(os.getenv("RAG_TOP_K", 6))
    EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
    EMBEDDING_MODEL_VERSION = os.getenv("EMBEDDING_MODEL_VERSION", "v1.0")

    # ── SMTP Email Notifications ───────────────────────────────
    SMTP_HOST = os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    SMTP_USER = os.getenv("SMTP_USER") or os.getenv("SMTP_EMAIL", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL") or os.getenv("SMTP_EMAIL") or os.getenv("SMTP_USER", "noreply@devaa.ai")
    SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"


