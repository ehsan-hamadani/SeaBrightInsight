"""Company directory — FastAPI UI + SQLite."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from urllib.parse import urlencode
from types import SimpleNamespace

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from .ch_urls import jinja_ch_registry_link
from .date_fmt import jinja_uk_date, normalize_appointment_date
from .phone_display import jinja_phone_cell, jinja_phone_display
from .db import Base, engine, get_db, migrate_companies_table
from .enrich import enrich_all_companies_background, enrich_company_merge
from .fetch_info import (
    fetch_company_bundle,
    normalize_website_url,
    result_as_company_dict,
)
from .import_data import import_directors_csv, import_market_research_csv
from .models import Company, Director

_REPO = Path(__file__).resolve().parent.parent
load_dotenv(_REPO / ".env")
_MARKET_CSV = _REPO / "Market Research List.csv"
_DIRECTORS_CSV = _REPO / "market_research_directors.csv"

app = FastAPI(title="Company Directory", description="Market research company records")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["ch_registry_link"] = jinja_ch_registry_link
templates.env.filters["phone_display"] = jinja_phone_display
templates.env.filters["phone_cell"] = jinja_phone_cell
_log = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


def _auto_enrich_background() -> None:
    delay = float(os.environ.get("AUTO_ENRICH_DELAY_SEC", "1.25") or "1.25")
    try:
        n = enrich_all_companies_background(delay_sec=delay)
        _log.info("AUTO_ENRICH: finished, companies updated this run: %s", n)
    except Exception:
        _log.exception("AUTO_ENRICH: background run failed")


@app.on_event("startup")
def _startup() -> None:
    Base.metadata.create_all(bind=engine)
    migrate_companies_table(engine)
    try:
        from .ch_urls import normalize_stored_ch_urls_to_canonical

        n_ch = normalize_stored_ch_urls_to_canonical()
        if n_ch:
            _log.info("Saved canonical Companies House URLs for %s companies", n_ch)
    except Exception:
        _log.exception("Companies House URL normalization failed")
    if os.environ.get("AUTO_ENRICH_COMPANIES", "1").lower() not in (
        "0",
        "false",
        "no",
        "off",
    ):
        threading.Thread(
            target=_auto_enrich_background,
            name="company-auto-enrich",
            daemon=True,
        ).start()


def _ctx(request: Request, **extra):
    """Build the shared Jinja template context for every page."""
    extra.setdefault("nav", "")
    return {"request": request, **extra}


_CATEGORY_CHOICES = [
    "Travel & Tourism",
    "Real Estate",
    "Health & Lifestyle",
]
_PIPELINE_STAGE_FLOW: list[tuple[str, str]] = [
    ("New Lead", "Company added as a potential opportunity."),
    ("Contacted", "Initial outreach has been made."),
    ("Discovery", "Needs and goals are being discussed."),
    ("Proposal Sent", "Proposal or plan has been shared."),
    ("Negotiation", "Terms, scope, or pricing are being finalized."),
    ("Won", "Company is now a customer."),
    ("Onboarding", "Kickoff and implementation are in progress."),
    ("Active", "Work is ongoing with the customer."),
    ("On Hold", "Opportunity or project is paused."),
    ("Closed Lost", "Opportunity did not convert."),
]
_PIPELINE_STAGE_CHOICES = [s for s, _ in _PIPELINE_STAGE_FLOW]
_DEFAULT_PIPELINE_STAGE = _PIPELINE_STAGE_CHOICES[0]


def _normalize_category(value: str | None) -> str | None:
    """Normalize category input to one of the configured category choices."""
    raw = (value or "").strip()
    if not raw:
        return None
    for opt in _CATEGORY_CHOICES:
        if raw.casefold() == opt.casefold():
            return opt
    return None


def _normalize_pipeline_stage(value: str | None) -> str:
    """Normalize a pipeline stage input to a supported stage."""
    raw = (value or "").strip()
    if not raw:
        return _DEFAULT_PIPELINE_STAGE
    for opt in _PIPELINE_STAGE_CHOICES:
        if raw.casefold() == opt.casefold():
            return opt
    return _DEFAULT_PIPELINE_STAGE


# Companies table sorting (query params: sort=…, dir=asc|desc)
_SORT_KEYS = frozenset(
    {
        "id",
        "company",
        "company_number",
        "category",
        "stage",
        "phone",
        "email",
        "directors",
        "include",
        "connected",
        "moreinfo",
    }
)
_COMPANY_SORT_COL = {
    "id": Company.id,
    "company": Company.company_name,
    "company_number": Company.company_house_number,
    "category": Company.company_area,
    "stage": Company.pipeline_stage,
    "phone": Company.company_telephone,
    "email": Company.company_email,
    "include": Company.include_flag,
    "connected": Company.connected,
    "moreinfo": Company.id,
}


def _normalize_sort(sort: str | None) -> str:
    """Normalize the requested sort column to a valid key."""
    s = (sort or "").strip().lower()
    return s if s in _SORT_KEYS else "company"


def _normalize_sort_dir(d: str | None) -> str:
    """Return the normalized sort direction, defaulting to ascending."""
    return "desc" if (d or "").strip().lower() == "desc" else "asc"


def _next_sort_dir(current_sort: str, current_dir: str, target_col: str) -> str:
    """Toggle the sort direction for the target column."""
    if current_sort != target_col:
        return "asc"
    return "desc" if current_dir == "asc" else "asc"


def _sort_query_string(
    target_col: str,
    *,
    search_q: str,
    category: str,
    current_sort: str,
    current_dir: str,
) -> str:
    """Build a URL query string for sorting links."""
    params: dict[str, str] = {
        "sort": target_col,
        "dir": _next_sort_dir(current_sort, current_dir, target_col),
    }
    qs = (search_q or "").strip()
    if qs:
        params["q"] = qs
    cv = (category or "").strip()
    if cv:
        params["category"] = cv
    return urlencode(params)


def _company_list_statement(filters: list, sort_key: str, ascending: bool):
    """Build the SQLAlchemy select statement used for the company list page."""
    stmt = select(Company)
    if filters:
        stmt = stmt.where(and_(*filters))

    if sort_key == "directors":
        return (
            stmt.outerjoin(Director, Director.company_id == Company.id)
            .group_by(Company.id)
            .order_by(
                (
                    func.count(Director.id).asc()
                    if ascending
                    else func.count(Director.id).desc()
                ),
                Company.company_name.asc(),
                Company.id.asc(),
            )
        )

    col = _COMPANY_SORT_COL[sort_key]
    primary = col.asc() if ascending else col.desc()
    return stmt.order_by(primary, Company.id.asc())


@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse("/companies", status_code=302)


@app.get("/companies", response_class=HTMLResponse)
def list_companies(
    request: Request,
    db: Session = Depends(get_db),
    q: str | None = Query(None, description="Search"),
    category: str | None = Query(None, description="Category"),
    sort: str | None = Query("company", description="Sort column"),
    sort_dir: str = Query("asc", alias="dir", description="asc or desc"),
    msg: str | None = None,
    created: int | None = Query(None),
    updated: int | None = Query(None),
    directors_added: int | None = Query(None),
):
    """Render the company list page with optional filtering, sorting, and import status."""
    filters: list = []
    if q and q.strip():
        term = f"%{q.strip()}%"
        filters.append(
            or_(
                Company.company_name.ilike(term),
                Company.company_area.ilike(term),
                Company.company_house_number.ilike(term),
                Company.company_email.ilike(term),
            )
        )
    cat_val = _normalize_category(category) or ""
    if cat_val:
        filters.append(Company.company_area == cat_val)

    sort_key = _normalize_sort(sort)
    ascending = _normalize_sort_dir(sort_dir) == "asc"
    stmt = _company_list_statement(filters, sort_key, ascending)
    companies = list(db.scalars(stmt))

    categories = list(_CATEGORY_CHOICES)
    total_companies = int(db.scalar(select(func.count()).select_from(Company)) or 0)

    rows = db.execute(
        select(Director.company_id, func.count(Director.id)).group_by(
            Director.company_id
        )
    ).all()
    counts = {int(row[0]): int(row[1]) for row in rows}
    sort_col = sort_key
    sort_dir_out = "asc" if ascending else "desc"
    sort_qs = {
        k: _sort_query_string(
            k,
            search_q=q or "",
            category=cat_val,
            current_sort=sort_col,
            current_dir=sort_dir_out,
        )
        for k in sorted(_SORT_KEYS)
    }
    return templates.TemplateResponse(
        request,
        "companies_list.html",
        _ctx(
            request,
            nav="list",
            companies=companies,
            director_counts=counts,
            search_q=q or "",
            selected_category=cat_val,
            categories=categories,
            total_companies=total_companies,
            msg=msg,
            import_created=created,
            import_updated=updated,
            directors_added=directors_added,
            sort_col=sort_col,
            sort_dir=sort_dir_out,
            sort_qs=sort_qs,
        ),
    )


@app.post("/companies/{company_id}/list-flags")
def update_company_list_flags(
    company_id: int,
    db: Session = Depends(get_db),
    include_yes: str | None = Form(None),
    connected_full: str | None = Form(None),
    q: str | None = Form(None),
    category: str | None = Form(None),
    sort: str | None = Form(None),
    sort_dir: str | None = Form(None, alias="dir"),
):
    """Save list filters for a company and redirect back to the list view."""
    c = db.get(Company, company_id)
    if not c:
        return RedirectResponse("/companies?msg=notfound", status_code=302)
    c.include_flag = "yes" if _blank(include_yes) else None
    c.connected = "full" if _blank(connected_full) else None
    db.commit()
    params: dict[str, str] = {"msg": "flags_saved"}
    qs = (q or "").strip()
    if qs:
        params["q"] = qs
    cv = (category or "").strip()
    if cv:
        params["category"] = cv
    sk = _normalize_sort(sort)
    params["sort"] = sk
    params["dir"] = _normalize_sort_dir(sort_dir)
    return RedirectResponse(f"/companies?{urlencode(params)}", status_code=303)


@app.get("/companies/new", response_class=HTMLResponse)
def new_company_form(request: Request, msg: str | None = Query(None)):
    """Render the empty company creation form."""
    return templates.TemplateResponse(
        request,
        "company_edit.html",
        _ctx(
            request,
            nav="new",
            company=None,
            prefill=None,
            prefill_directors=[],
            fetch_warnings=[],
            directors=[],
            is_new=True,
            category_choices=_CATEGORY_CHOICES,
            pipeline_stage_choices=_PIPELINE_STAGE_CHOICES,
            pipeline_stage_flow=_PIPELINE_STAGE_FLOW,
            msg=msg,
        ),
    )


@app.post("/companies/new/fetch")
async def prefetch_new_company(request: Request):
    """Fetch preliminary company data for a new company form before saving."""
    try:
        form = await request.form()
        name = str(form.get("company_name") or "").strip()
        url = str(form.get("company_url") or "").strip()
        if not name:
            return RedirectResponse("/companies/new?msg=fetch_noname", status_code=303)
        if not url:
            return RedirectResponse(
                "/companies/new?msg=fetch_need_url", status_code=303
            )
        fr = fetch_company_bundle(name, url)
        if not fr.property_related:
            return RedirectResponse(
                "/companies/new?msg=fetch_not_property", status_code=303
            )
        patch = result_as_company_dict(fr)
        nu = normalize_website_url(url) or ""
        prefill = SimpleNamespace(
            company_name=name,
            include_flag=None,
            connected=None,
            company_description=(patch.get("company_description") or "") or "",
            company_notes="",
            company_area=_normalize_category((patch.get("company_area") or "") or "")
            or "",
            pipeline_stage=_DEFAULT_PIPELINE_STAGE,
            company_url=(patch.get("company_url") or nu) or "",
            company_address=(patch.get("company_address") or "") or "",
            company_telephone=(patch.get("company_telephone") or "") or "",
            company_email=(patch.get("company_email") or "") or "",
            company_house_number=(patch.get("company_house_number") or "") or "",
            company_house_url=(patch.get("company_house_url") or "") or "",
        )
        return templates.TemplateResponse(
            request,
            "company_edit.html",
            _ctx(
                request,
                nav="new",
                company=None,
                prefill=prefill,
                prefill_directors=_safe_directors_for_template(fr.directors),
                fetch_warnings=fr.warnings,
                directors=[],
                is_new=True,
                category_choices=_CATEGORY_CHOICES,
                pipeline_stage_choices=_PIPELINE_STAGE_CHOICES,
                pipeline_stage_flow=_PIPELINE_STAGE_FLOW,
                msg=None,
            ),
        )
    except Exception:
        _log.exception("prefetch_new_company failed")
        return RedirectResponse("/companies/new?msg=fetch_failed", status_code=303)


@app.post("/companies/new")
def create_company(
    db: Session = Depends(get_db),
    company_name: str = Form(...),
    company_description: str | None = Form(None),
    company_notes: str | None = Form(None),
    company_area: str | None = Form(None),
    pipeline_stage: str | None = Form(None),
    company_url: str | None = Form(None),
    company_address: str | None = Form(None),
    company_telephone: str | None = Form(None),
    company_email: str | None = Form(None),
    company_house_number: str | None = Form(None),
    company_house_url: str | None = Form(None),
    directors_json: str | None = Form(None),
) -> RedirectResponse:
    """Create a new company and optional directors from the submitted form."""
    c = Company(
        company_name=company_name.strip(),
        company_description=_blank(company_description),
        company_notes=_blank(company_notes),
        company_area=_normalize_category(company_area),
        pipeline_stage=_normalize_pipeline_stage(pipeline_stage),
        company_url=_blank(company_url),
        company_address=_blank(company_address),
        company_telephone=_blank(company_telephone),
        company_email=_blank(company_email),
        company_house_number=_blank(company_house_number),
        company_house_url=_blank(company_house_url),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    if directors_json and directors_json.strip():
        try:
            rows = json.loads(directors_json)
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    dn = str(row.get("director_name") or "").strip()
                    if not dn:
                        continue
                    db.add(
                        Director(
                            company_id=c.id,
                            director_name=dn,
                            appointment_date=_blank(
                                str(row.get("appointment_date") or "").strip() or None
                            ),
                        )
                    )
                db.commit()
        except (json.JSONDecodeError, TypeError):
            pass
    return RedirectResponse(f"/companies/{c.id}?msg=created", status_code=303)


def _blank(v: str | None) -> str | None:
    """Normalize an optional form string to either trimmed text or None."""
    if v is None:
        return None
    s = v.strip()
    return s or None


def _safe_directors_for_template(rows: list | None) -> list[dict[str, str]]:
    """Plain dicts only so Jinja |tojson never fails."""
    out: list[dict[str, str]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "director_name": str(row.get("director_name") or "")[:500],
                "role": str(row.get("role") or "")[:128],
                "phone": str(row.get("phone") or "")[:200],
                "email": str(row.get("email") or "")[:500],
                "interaction_notes": str(row.get("interaction_notes") or "")[:4000],
                "appointment_date": str(row.get("appointment_date") or "")[:64],
            }
        )
    return out


@app.get("/companies/{company_id}", response_class=HTMLResponse)
def view_company(
    request: Request,
    company_id: int,
    db: Session = Depends(get_db),
    msg: str | None = None,
):
    """Render the edit page for a single company record."""
    company = db.get(Company, company_id)
    if not company:
        return RedirectResponse("/companies?msg=notfound", status_code=302)
    if os.environ.get("AUTO_ENRICH_COMPANIES", "1").lower() not in (
        "0",
        "false",
        "no",
        "off",
    ):
        try:
            if enrich_company_merge(db, company_id):
                db.refresh(company)
        except Exception:
            _log.exception("auto-enrich on view failed company_id=%s", company_id)
            db.rollback()
    dirs_ = (
        db.execute(
            select(Director)
            .where(Director.company_id == company_id)
            .order_by(
                Director.appointment_date.desc().nulls_last(),
                Director.director_name.asc(),
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "company_edit.html",
        _ctx(
            request,
            nav="edit",
            company=company,
            prefill=None,
            prefill_directors=[],
            fetch_warnings=[],
            directors=dirs_,
            is_new=False,
            category_choices=_CATEGORY_CHOICES,
            pipeline_stage_choices=_PIPELINE_STAGE_CHOICES,
            pipeline_stage_flow=_PIPELINE_STAGE_FLOW,
            msg=msg,
        ),
    )


@app.post("/companies/{company_id}/fetch")
async def refetch_company(
    company_id: int, request: Request, db: Session = Depends(get_db)
):
    """Refetch company metadata from website/Companies House and update the record."""
    try:
        form = await request.form()
        name = str(form.get("company_name") or "").strip()
        url = str(form.get("company_url") or "").strip()
        c = db.get(Company, company_id)
        if not c:
            return RedirectResponse("/companies?msg=notfound", status_code=302)
        if not name:
            return RedirectResponse(
                f"/companies/{company_id}?msg=fetch_noname", status_code=303
            )
        fr = fetch_company_bundle(name, url or None)
        if not fr.property_related:
            return RedirectResponse(
                f"/companies/{company_id}?msg=fetch_not_property", status_code=303
            )
        patch = result_as_company_dict(fr)
        c.company_name = name
        for key, val in patch.items():
            if val is not None:
                setattr(c, key, val)
        db.execute(delete(Director).where(Director.company_id == company_id))
        for row in fr.directors:
            dn = str(row.get("director_name") or "").strip()
            if not dn:
                continue
            raw_ad = row.get("appointment_date")
            ad = str(raw_ad).strip() if raw_ad is not None else ""
            db.add(
                Director(
                    company_id=company_id,
                    director_name=dn,
                    role=_blank(str(row.get("role") or "") or None),
                    phone=_blank(str(row.get("phone") or "") or None),
                    email=_blank(str(row.get("email") or "") or None),
                    interaction_notes=_blank(
                        str(row.get("interaction_notes") or "") or None
                    ),
                    appointment_date=_blank(ad or None),
                )
            )
        db.commit()
        return RedirectResponse(f"/companies/{company_id}?msg=fetched", status_code=303)
    except Exception:
        _log.exception("refetch_company failed company_id=%s", company_id)
        db.rollback()
        return RedirectResponse(
            f"/companies/{company_id}?msg=fetch_failed", status_code=303
        )


@app.post("/companies/{company_id}")
def save_company(
    company_id: int,
    db: Session = Depends(get_db),
    company_name: str = Form(...),
    company_description: str | None = Form(None),
    company_notes: str | None = Form(None),
    company_area: str | None = Form(None),
    pipeline_stage: str | None = Form(None),
    company_url: str | None = Form(None),
    company_address: str | None = Form(None),
    company_telephone: str | None = Form(None),
    company_email: str | None = Form(None),
    company_house_number: str | None = Form(None),
    company_house_url: str | None = Form(None),
) -> RedirectResponse:
    """Save edits to an existing company record."""
    c = db.get(Company, company_id)
    if not c:
        return RedirectResponse("/companies?msg=notfound", status_code=302)
    c.company_name = company_name.strip()
    c.company_description = _blank(company_description)
    c.company_notes = _blank(company_notes)
    c.company_area = _normalize_category(company_area)
    c.pipeline_stage = _normalize_pipeline_stage(pipeline_stage)
    c.company_url = _blank(company_url)
    c.company_address = _blank(company_address)
    c.company_telephone = _blank(company_telephone)
    c.company_email = _blank(company_email)
    c.company_house_number = _blank(company_house_number)
    c.company_house_url = _blank(company_house_url)
    db.commit()
    return RedirectResponse(f"/companies/{company_id}?msg=saved", status_code=303)


@app.post("/companies/{company_id}/delete")
def delete_company(company_id: int, db: Session = Depends(get_db)):
    """Delete a company and its related directors."""
    c = db.get(Company, company_id)
    if c:
        db.delete(c)
        db.commit()
    return RedirectResponse("/companies?msg=deleted", status_code=303)


@app.post("/companies/{company_id}/directors")
def add_director(
    company_id: int,
    db: Session = Depends(get_db),
    director_name: str = Form(...),
    role: str | None = Form(None),
    phone: str | None = Form(None),
    email: str | None = Form(None),
    interaction_notes: str | None = Form(None),
    appointment_date: str | None = Form(None),
) -> RedirectResponse:
    """Add a director or officer to a specific company."""
    if not db.get(Company, company_id):
        return RedirectResponse("/companies?msg=notfound", status_code=302)
    db.add(
        Director(
            company_id=company_id,
            director_name=director_name.strip(),
            role=_blank(role) or "Director",
            phone=_blank(phone),
            email=_blank(email),
            interaction_notes=_blank(interaction_notes),
            appointment_date=_blank(appointment_date),
        )
    )
    db.commit()
    return RedirectResponse(
        f"/companies/{company_id}?msg=key_person_added", status_code=303
    )


@app.post("/companies/{company_id}/directors/{director_id}/delete")
def remove_director(company_id: int, director_id: int, db: Session = Depends(get_db)):
    """Remove a director from the company record."""
    d = db.get(Director, director_id)
    if d and d.company_id == company_id:
        db.delete(d)
        db.commit()
    return RedirectResponse(
        f"/companies/{company_id}?msg=director_removed", status_code=303
    )


@app.post("/admin/import-market-research")
def admin_import_market(db: Session = Depends(get_db)):
    """Import the market research companies CSV into the database."""
    if not _MARKET_CSV.is_file():
        return RedirectResponse("/companies?msg=missing_csv", status_code=303)
    created, updated = import_market_research_csv(db, _MARKET_CSV)
    return RedirectResponse(
        f"/companies?msg=import_done&created={created}&updated={updated}",
        status_code=303,
    )


@app.post("/admin/import-directors")
def admin_import_directors(db: Session = Depends(get_db)):
    """Import director records from the configured directors CSV."""
    path = _DIRECTORS_CSV
    if not path.is_file():
        alt = _REPO / "market_research_directors_test5.csv"
        path = alt if alt.is_file() else path
    if not path.is_file():
        return RedirectResponse("/companies?msg=missing_directors_csv", status_code=303)
    n = import_directors_csv(db, path)
    return RedirectResponse(
        f"/companies?msg=directors_done&directors_added={n}", status_code=303
    )


@app.post("/admin/enrich-companies")
def admin_enrich_companies(background_tasks: BackgroundTasks):
    """Queue a full merge-enrichment pass in the background."""

    def _job() -> None:
        try:
            n = enrich_all_companies_background(
                delay_sec=float(
                    os.environ.get("AUTO_ENRICH_DELAY_SEC", "1.25") or "1.25"
                )
            )
            _log.info("ADMIN_ENRICH: finished, companies updated this run: %s", n)
        except Exception:
            _log.exception("ADMIN_ENRICH: run failed")

    background_tasks.add_task(_job)
    return RedirectResponse("/companies?msg=enrich_started", status_code=303)
