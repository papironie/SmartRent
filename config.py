"""SmartRent configuration. Values come from environment variables / .env."""
import os
import tempfile
from dateutil import parser as dateparser
from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _frozen_today():
    raw = os.getenv("TODAY", "").strip()
    return dateparser.parse(raw).date() if raw else None


class Config:
    APP_NAME = "SmartRent"
    TIMEZONE = "Asia/Manila"
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", "postgresql+psycopg2://smartrent:smartrent@localhost:5432/smartrent"
    )
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE")  # set true when served over HTTPS
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # Excel imports and photo uploads: 5 MB cap per request
    UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "storage", "uploads"))

    # Business rules (write these same numbers in your proposal, Scope section)
    REMINDER_DAYS = 3      # remind tenant 3 days before due date
    LEASE_WARN_DAYS = 30   # warn when a lease ends within 30 days
    NEW_TENANT_DAYS = 30   # lease started within 30 days = "New"
    NEARBY_RADIUS_M = 2000 # "nearby properties" radius (PostGIS ST_DWithin)

    # Freeze "today" for a demo. None = real date.
    TODAY = _frozen_today()

    # Email. enabled=False writes emails to storage/logs/mail.log.
    MAIL_ENABLED = _flag("MAIL_ENABLED")
    MAIL_HOST = os.getenv("MAIL_HOST", "sandbox.smtp.mailtrap.io")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "2525"))
    MAIL_USER = os.getenv("MAIL_USER", "")
    MAIL_PASS = os.getenv("MAIL_PASS", "")
    MAIL_FROM = os.getenv("MAIL_FROM", "no-reply@smartrent.test")
    MAIL_LOG = os.path.join(os.path.dirname(__file__), "storage", "logs", "mail.log")

    # External APIs (integration points). Not libraries: plain HTTPS calls.
    OPENTOPODATA_URL = "https://api.opentopodata.org/v1/srtm30m"
    OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"
    API_TIMEOUT = 6  # seconds. A slow third party must never hang our page.


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test"
    WTF_CSRF_ENABLED = True
    TODAY = None
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "TEST_DATABASE_URL", "postgresql+psycopg2://smartrent:smartrent@localhost:5433/smartrent_test"
    )
    MAIL_ENABLED = False
    UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "smartrent-test-uploads")  # never touch the real uploads
