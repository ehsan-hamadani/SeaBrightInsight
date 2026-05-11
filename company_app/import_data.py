"""CSV import helpers for company and director data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session


def _clean(val: Any) -> str | None:
    """Normalize raw CSV values into a stripped string or None."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return None
    return s


def import_market_research_csv(session: Session, csv_path: Path) -> tuple[int, int]:
    """Upsert companies from a market research CSV.

    Returns a tuple of (created, updated) company rows.
    """
    from .models import Company

    df = pd.read_csv(csv_path)
    col_map = {c.lower().strip(): c for c in df.columns}

    def col(*candidates: str) -> str | None:
        for candidate in candidates:
            key = candidate.lower().strip()
            if key in col_map:
                return col_map[key]
        return None

    c_name = col("company name")
    if not c_name:
        raise ValueError("CSV must contain a Company Name column.")

    c_desc = col("company description")
    c_area = col("company area", "category")
    c_url = col("company url")
    c_addr = col("company address")
    c_tel = col("company telephone")
    c_email = col("company email")
    c_num = col("company house number")
    c_ch_url = col("company house url")
    c_inc = col("include?")
    c_conn = col("connected")

    created = 0
    updated = 0
    for _, row in df.iterrows():
        name = _clean(row.get(c_name))
        if not name:
            continue

        house_num = _clean(row.get(c_num)) if c_num else None
        query = session.query(Company)
        if house_num:
            company = query.filter(Company.company_house_number == house_num).first()
        else:
            company = query.filter(Company.company_name == name).first()

        fields = {
            "company_name": name,
            "company_description": _clean(row.get(c_desc)) if c_desc else None,
            "company_area": _clean(row.get(c_area)) if c_area else None,
            "company_url": _clean(row.get(c_url)) if c_url else None,
            "company_address": _clean(row.get(c_addr)) if c_addr else None,
            "company_telephone": _clean(row.get(c_tel)) if c_tel else None,
            "company_email": _clean(row.get(c_email)) if c_email else None,
            "company_house_number": house_num,
            "company_house_url": _clean(row.get(c_ch_url)) if c_ch_url else None,
            "include_flag": _clean(row.get(c_inc)) if c_inc else None,
            "connected": _clean(row.get(c_conn)) if c_conn else None,
        }

        if company:
            for key, value in fields.items():
                setattr(company, key, value)
            updated += 1
        else:
            session.add(Company(**fields))
            created += 1

    session.commit()
    return created, updated


def import_directors_csv(session: Session, csv_path: Path) -> int:
    """Import director rows matched to existing companies.

    Directors are matched by company house number first, then by company name.
    """
    from .models import Company, Director

    df = pd.read_csv(csv_path)
    lower_columns = {c.lower().strip(): c for c in df.columns}

    def get_column(*names: str) -> str | None:
        for name in names:
            key = name.lower().strip()
            if key in lower_columns:
                return lower_columns[key]
        return None

    c_name = get_column("company name")
    c_num = get_column("company house number")
    d_disp = get_column("director name (display)", "director name")
    d_ch = get_column("director name (as ch)")
    d_app = get_column("appointed on", "appointment date")

    if not c_name or (not d_disp and not d_ch):
        return 0

    added = 0
    for _, row in df.iterrows():
        name = _clean(row.get(c_name))
        if not name:
            continue

        num = _clean(row.get(c_num)) if c_num else None
        director_name = _clean(row.get(d_disp)) if d_disp else None
        if not director_name and d_ch:
            director_name = _clean(row.get(d_ch))
        if not director_name:
            continue

        appointment_date = _clean(row.get(d_app)) if d_app else None

        query = session.query(Company)
        if num:
            company = query.filter(Company.company_house_number == num).first()
        else:
            company = query.filter(Company.company_name == name).first()
        if not company:
            continue

        exists = (
            session.query(Director)
            .filter(
                Director.company_id == company.id,
                Director.director_name == director_name,
                Director.appointment_date == (appointment_date or ""),
            )
            .first()
        )
        if exists:
            continue

        session.add(
            Director(
                company_id=company.id,
                director_name=director_name,
                appointment_date=appointment_date,
            )
        )
        added += 1

    session.commit()
    return added
