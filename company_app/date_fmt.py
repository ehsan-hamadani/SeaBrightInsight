"""Appointment date parsing (storage: ISO YYYY-MM-DD) and UK display (DD/MM/YYYY)."""

from __future__ import annotations

from datetime import datetime


def normalize_appointment_date(s: str | None) -> str | None:
    """Parse common date inputs and return YYYY-MM-DD, or None if empty."""
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    token = t.split()[0][:32]
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(token, fmt).date().strftime("%Y-%m-%d")
        except ValueError:
            continue
    try:
        return datetime.strptime(token[:8], "%d/%m/%y").date().strftime("%Y-%m-%d")
    except ValueError:
        pass
    return token[:64]


def jinja_uk_date(raw: str | None) -> str:
    """Jinja filter: show DD/MM/YYYY, or em dash if empty."""
    if raw is None:
        return "—"
    t = str(raw).strip()
    if not t:
        return "—"
    iso = normalize_appointment_date(t)
    if iso and len(iso) >= 10 and iso[4] == "-":
        try:
            return datetime.strptime(iso[:10], "%Y-%m-%d").date().strftime("%d/%m/%Y")
        except ValueError:
            pass
    return t[:64]
