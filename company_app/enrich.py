"""Background merge-enrichment: fetch website + Companies House when data is still missing."""

from __future__ import annotations

import logging
import time
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .fetch_info import fetch_company_bundle, result_as_company_dict
from .models import Company, Director

log = logging.getLogger(__name__)


def _strip(v: str | None) -> str:
    """Trim a string and normalize None to an empty string."""
    return (v or "").strip()


def company_needs_enrichment(company: Company, director_count: int) -> bool:
    """Return True when the company record should be enriched from external sources."""
    if not _strip(company.company_name):
        return False
    if not _strip(company.company_url):
        return False
    if director_count == 0:
        return True
    if not _strip(company.company_house_number):
        return True
    return False


def enrich_company_merge(session: Session, company_id: int) -> bool:
    """Merge fetched enrichment data into an existing company record.

    Only empty fields are updated, and directors are added only when none exist.
    """
    try:
        c = session.get(Company, company_id)
        if not c:
            return False
        n = int(
            session.scalar(
                select(func.count(Director.id)).where(Director.company_id == company_id)
            )
            or 0
        )
        if not company_needs_enrichment(c, n):
            return False

        fr = fetch_company_bundle(c.company_name.strip(), c.company_url)
        patch = result_as_company_dict(fr)
        for key, val in patch.items():
            if val is None:
                continue
            if isinstance(val, str) and not val.strip():
                continue
            cur = getattr(c, key, None)
            empty = cur is None or (isinstance(cur, str) and not str(cur).strip())
            if empty:
                setattr(c, key, val)

        if n == 0 and fr.directors:
            for row in fr.directors:
                if not isinstance(row, dict):
                    continue
                dn = str(row.get("director_name") or "").strip()
                if not dn:
                    continue
                raw_ad = row.get("appointment_date")
                ad = str(raw_ad).strip() if raw_ad is not None else ""
                session.add(
                    Director(
                        company_id=company_id,
                        director_name=dn[:500],
                        appointment_date=ad or None,
                    )
                )
        session.commit()
        return True
    except Exception:
        log.exception("enrich_company_merge failed company_id=%s", company_id)
        session.rollback()
        return False


def enrich_all_companies_background(delay_sec: float = 1.25) -> int:
    """Enrich all companies sequentially in the background.

    A fresh database session is used for each company to keep transactions isolated.
    Returns the number of companies enriched.
    """
    from .db import SessionLocal

    db = SessionLocal()
    try:
        ids = list(db.scalars(select(Company.id).order_by(Company.id)).all())
    finally:
        db.close()

    enriched = 0
    for cid in ids:
        db = SessionLocal()
        try:
            if enrich_company_merge(db, cid):
                enriched += 1
        except Exception:
            log.exception("enrich failed for company_id=%s", cid)
        finally:
            db.close()
        time.sleep(max(0.0, delay_sec))
    return enriched
