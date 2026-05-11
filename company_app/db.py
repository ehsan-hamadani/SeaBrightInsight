"""Database engine and session factory."""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings, sqlite_file_path

_sqlite_file = sqlite_file_path(settings.resolved_database_url)
if _sqlite_file is not None:
    _sqlite_file.parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL = settings.resolved_database_url
CONNECT_ARGS = (
    {"check_same_thread": False} if DATABASE_URL.startswith("sqlite:///") else {}
)

engine = create_engine(
    DATABASE_URL,
    connect_args=CONNECT_ARGS,
    pool_pre_ping=True,
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
_MIGRATED_ONCE = False


class Base(DeclarativeBase):
    pass


def migrate_companies_table(engine) -> None:
    """Add missing columns and normalize empty strings to NULL for existing SQLite DBs."""
    try:
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        if "companies" not in tables:
            return
        cols = {c["name"] for c in insp.get_columns("companies")}
        dcols = (
            {c["name"] for c in insp.get_columns("directors")}
            if "directors" in tables
            else set()
        )
    except Exception:
        return
    with engine.begin() as conn:
        if "connected" not in cols:
            conn.execute(text("ALTER TABLE companies ADD COLUMN connected VARCHAR(32)"))
        if "company_notes" not in cols:
            conn.execute(text("ALTER TABLE companies ADD COLUMN company_notes TEXT"))
        if "pipeline_stage" not in cols:
            conn.execute(
                text("ALTER TABLE companies ADD COLUMN pipeline_stage VARCHAR(80)")
            )
        if dcols:
            if "role" not in dcols:
                conn.execute(text("ALTER TABLE directors ADD COLUMN role VARCHAR(128)"))
            if "phone" not in dcols:
                conn.execute(
                    text("ALTER TABLE directors ADD COLUMN phone VARCHAR(200)")
                )
            if "email" not in dcols:
                conn.execute(
                    text("ALTER TABLE directors ADD COLUMN email VARCHAR(500)")
                )
            if "interaction_notes" not in dcols:
                conn.execute(
                    text("ALTER TABLE directors ADD COLUMN interaction_notes TEXT")
                )
        conn.execute(
            text(
                "UPDATE companies SET include_flag = NULL "
                "WHERE include_flag IS NOT NULL AND trim(include_flag) = ''"
            )
        )
        conn.execute(
            text(
                "UPDATE companies SET connected = NULL "
                "WHERE connected IS NOT NULL AND trim(connected) = ''"
            )
        )
        conn.execute(
            text(
                "UPDATE companies SET pipeline_stage = CASE "
                "WHEN pipeline_stage IS NULL OR trim(pipeline_stage) = '' THEN 'New Lead' "
                "WHEN pipeline_stage = 'Market Research Survey' THEN 'New Lead' "
                "WHEN pipeline_stage IN ('Response Analysis', 'Business Opportunity Identified') THEN 'Contacted' "
                "WHEN pipeline_stage IN ('Discovery Call Planned', 'Discovery Completed') THEN 'Discovery' "
                "WHEN pipeline_stage IN ('Solution Design', 'Proposal Submitted', 'Proposal & ROI Case') THEN 'Proposal Sent' "
                "WHEN pipeline_stage IN ('Commercial Negotiation', 'Stakeholder Review', 'Commercial Agreement') THEN 'Negotiation' "
                "WHEN pipeline_stage = 'Contract Signed' THEN 'Won' "
                "WHEN pipeline_stage IN ('Onboarding In Progress', 'Kickoff & Access Setup') THEN 'Onboarding' "
                "WHEN pipeline_stage IN ('Active Customer', 'Data Engineering Delivery', 'Analytics / AI Delivery', 'UAT & Business Signoff', 'Go-Live & Adoption', 'Managed Service / Growth') THEN 'Active' "
                "WHEN pipeline_stage = 'On Hold / Nurture' THEN 'On Hold' "
                "ELSE pipeline_stage END"
            )
        )


def get_db():
    """FastAPI dependency: yield a database session and close it after use."""
    global _MIGRATED_ONCE
    if settings.auto_migrate and not _MIGRATED_ONCE:
        try:
            migrate_companies_table(engine)
        finally:
            _MIGRATED_ONCE = True
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
