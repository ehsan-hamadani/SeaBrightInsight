"""Runtime configuration for the company directory app."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Environment-driven settings used at import and startup time."""

    app_env: str = os.environ.get("APP_ENV", "development").strip().lower()
    database_url: str | None = os.environ.get("DATABASE_URL")
    sqlite_path: Path = Path(
        os.environ.get("SQLITE_PATH", str(REPO_ROOT / "data" / "companies.sqlite3"))
    )
    auto_create_tables: bool = _bool_env("AUTO_CREATE_TABLES", True)
    auto_migrate: bool = _bool_env("AUTO_MIGRATE", True)
    auto_enrich_companies: bool = _bool_env(
        "AUTO_ENRICH_COMPANIES",
        os.environ.get("APP_ENV", "development").strip().lower() != "production",
    )
    auto_enrich_delay_sec: float = _float_env("AUTO_ENRICH_DELAY_SEC", 1.25)
    fetch_debug_timings: bool = _bool_env(
        "FETCH_DEBUG_TIMINGS",
        os.environ.get("APP_ENV", "development").strip().lower() != "production",
    )

    @property
    def resolved_database_url(self) -> str:
        if self.database_url and self.database_url.strip():
            return self.database_url.strip()
        return f"sqlite:///{self.sqlite_path.resolve().as_posix()}"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()


def sqlite_file_path(database_url: str) -> Path | None:
    """Return the local SQLite file path for a database URL, if applicable."""
    try:
        url = make_url(database_url)
    except Exception:
        return None
    if url.drivername != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return Path(url.database)
