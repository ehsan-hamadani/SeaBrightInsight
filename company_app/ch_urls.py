"""Canonical Companies House (public register) URLs."""

from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlparse

_CH_PUBLIC_HOST = "find-and-update.company-information.service.gov.uk"
_CH_PUBLIC_PREFIX = f"https://{_CH_PUBLIC_HOST}/company/"


def normalize_company_number(raw: str | None) -> str | None:
    """Normalize a Companies House company number string.

    Removes whitespace and dashes, and converts to upper case.
    """
    if not raw:
        return None
    s = re.sub(r"[\s\-]+", "", str(raw).strip())
    if not s:
        return None
    return s.upper()


def public_companies_house_url(
    company_number: str | None,
    stored_url: str | None = None,
) -> str | None:
    """
    Return the public register URL for a company profile.
    Always uses find-and-update.company-information.service.gov.uk (not the API host).
    """
    n = normalize_company_number(company_number)
    if n:
        return f"{_CH_PUBLIC_PREFIX}{quote(n, safe='')}"

    su = (stored_url or "").strip()
    if not su:
        return None
    if not su.startswith(("http://", "https://")):
        su = "https://" + su.lstrip("/")

    try:
        p = urlparse(su)
        host = (p.hostname or "").lower()
        path = p.path or ""
        if _CH_PUBLIC_HOST in host:
            m = re.search(r"/company/([^/]+)", path, re.I)
            if m:
                n2 = normalize_company_number(unquote(m.group(1)))
                if n2:
                    return f"{_CH_PUBLIC_PREFIX}{quote(n2, safe='')}"
            return su.split("?")[0].rstrip("/")
        if "api.company-information" in host or "api.companieshouse" in host:
            m = re.search(r"/company/([^/]+)", path, re.I)
            if m:
                n2 = normalize_company_number(unquote(m.group(1)))
                if n2:
                    return f"{_CH_PUBLIC_PREFIX}{quote(n2, safe='')}"
    except Exception:
        pass
    return None


def jinja_ch_registry_link(company) -> str:
    """Jinja filter: Company model -> href string or empty."""
    return (
        public_companies_house_url(
            getattr(company, "company_house_number", None),
            getattr(company, "company_house_url", None),
        )
        or ""
    )


def normalize_stored_ch_urls_to_canonical() -> int:
    """
    Persist canonical find-and-update.company-information.service.gov.uk/company/{number}
    URLs (and backfill company_house_number when missing but derivable from stored URL).
    Returns the number of company rows updated.
    """
    from sqlalchemy import select

    from .db import SessionLocal
    from .models import Company

    db = SessionLocal()
    updated = 0
    try:
        for c in db.scalars(select(Company)):
            canon = public_companies_house_url(
                c.company_house_number, c.company_house_url
            )
            if not canon:
                continue
            changed = False
            if not (c.company_house_number or "").strip():
                m = re.search(r"/company/([^/?#]+)/?$", canon)
                if m:
                    n2 = normalize_company_number(unquote(m.group(1)))
                    if n2:
                        c.company_house_number = n2[:32]
                        changed = True
            if (c.company_house_url or "").strip() != canon:
                c.company_house_url = canon
                changed = True
            if changed:
                updated += 1
        if updated:
            db.commit()
        return updated
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
