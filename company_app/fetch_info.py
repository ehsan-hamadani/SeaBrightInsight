"""Fetch company details from the website and Companies House (optional API key)."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, quote_plus, urlparse, urlunparse

import requests

from .ch_urls import public_companies_house_url
from .config import settings
from bs4 import BeautifulSoup

_DEFAULT_CH_BASE = "https://api.company-information.service.gov.uk"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_SESSION = requests.Session()
_SESSION.headers.update(
    {"User-Agent": _USER_AGENT, "Accept": "text/html,application/json"}
)
_log = logging.getLogger(__name__)
_PROPERTY_KEYWORDS = (
    "property",
    "estate",
    "real estate",
    "lettings",
    "landlord",
    "developer",
    "development",
    "construction",
    "housing",
    "residential",
    "commercial property",
    "survey",
    "valuat",
    "planning",
    "mortgage",
)
_PROPERTY_SIC_PREFIXES = ("41", "68", "43")


def _fetch_debug_enabled() -> bool:
    """Return True when fetch debug timing output is enabled by env vars."""
    return settings.fetch_debug_timings


def _dbg(label: str, t0: float, extra: str = "") -> None:
    """Emit a lightweight timing message for fetch operations."""
    if not _fetch_debug_enabled():
        return
    ms = int((time.perf_counter() - t0) * 1000)
    suffix = f" | {extra}" if extra else ""
    _log.debug("FETCH %s: %sms%s", label, ms, suffix)


def _norm_text(s: str | None) -> str:
    """Normalize whitespace in text strings and trim surrounding spaces."""
    return re.sub(r"\s+", " ", (s or "").strip())


def _two_sentence_summary(text: str | None) -> str | None:
    """
    Build a concise two-sentence summary from noisy website description text.
    """
    src = _norm_text(text)
    if not src:
        return None
    # Split by sentence enders while preserving basic punctuation boundaries.
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", src) if p.strip()]
    cleaned: list[str] = []
    for p in parts:
        p = _norm_text(p)
        if not p:
            continue
        if p[-1] not in ".!?":
            p += "."
        cleaned.append(p)
        if len(cleaned) >= 2:
            break
    if len(cleaned) == 2:
        out = f"{cleaned[0]} {cleaned[1]}"
        return out[:1000]
    if len(cleaned) == 1:
        # Fallback: split long single sentence by comma/semicolon into 2 parts.
        chunks = [
            c.strip(" ,;:-")
            for c in re.split(r"[;,]\s+", cleaned[0].rstrip(".!?"))
            if c.strip(" ,;:-")
        ]
        if len(chunks) >= 2:
            s1 = chunks[0]
            s2 = ", ".join(chunks[1:3])
            out = f"{s1}. {s2}."
            return _norm_text(out)[:1000]
        return cleaned[0][:1000]
    return src[:1000]


def _llm_business_summary(
    *,
    company_name: str,
    website_url: str | None,
    raw_description: str | None,
    area: str | None = None,
    address: str | None = None,
) -> str | None:
    """
    Ask local LLM for a concise business summary with unique features.
    Returns at most two sentences of plain text.
    """
    desc = _norm_text(raw_description)
    if not desc or len(desc) < 60:
        return None
    endpoint = (
        os.environ.get("LOCAL_LLM_ADDRESS_EXTRACT_URL")
        or "http://localhost:11434/api/generate"
    ).strip()
    model = (os.environ.get("LOCAL_LLM_MODEL") or "llama3.2:3b").strip()
    timeout = float(
        os.environ.get("LOCAL_LLM_SUMMARY_TIMEOUT_SEC")
        or os.environ.get("LOCAL_LLM_TIMEOUT_SEC")
        or "12"
    )
    prompt = (
        "You are writing a CRM description.\n"
        "Task: Summarize the business and highlight what is unique about it.\n"
        "Output rules:\n"
        "- 2 sentences maximum\n"
        "- plain text only (no markdown, no bullets)\n"
        "- factual, concise, and specific\n"
        "- include distinctive services/specialism/positioning when present\n"
        "- no placeholders, no invented facts\n\n"
        f"Company name: {company_name}\n"
        f"Website: {website_url or ''}\n"
        f"Category/area: {area or ''}\n"
        f"Address hint: {address or ''}\n"
        f"Raw business text: {desc}\n"
    )
    try:
        resp = _SESSION.post(
            endpoint,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 120},
            },
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        out = _norm_text(str(data.get("response") or ""))
        if not out:
            return None
        # If model returns extra labels, strip common prefixes.
        out = re.sub(r"^(summary|description)\s*:\s*", "", out, flags=re.I).strip()
        return _two_sentence_summary(out)
    except Exception:
        return None


def _extract_address_with_local_llm(
    candidates: list[str], website_url: str
) -> str | None:
    """
    Optional local-LLM pass to pick/normalize the real business address.
    Expects an Ollama-compatible endpoint by default.
    """
    cleaned = []
    seen: set[str] = set()
    for c in candidates:
        t = _norm_text(c)
        if len(t) < 10:
            continue
        k = t.casefold()
        if k in seen:
            continue
        seen.add(k)
        cleaned.append(t[:700])
    if not cleaned:
        return None

    endpoint = (
        os.environ.get("LOCAL_LLM_ADDRESS_EXTRACT_URL")
        or "http://localhost:11434/api/generate"
    ).strip()
    model = (os.environ.get("LOCAL_LLM_MODEL") or "llama3.2:3b").strip()
    timeout = float(os.environ.get("LOCAL_LLM_ADDRESS_TIMEOUT_SEC") or "8")

    prompt = (
        "You extract a business postal address from noisy website snippets.\n"
        'Return ONLY JSON in this exact format: {"address": "..."} or {"address": null}.\n'
        "Rules:\n"
        "- Choose the most complete real-world postal address for the business.\n"
        "- Ignore legal text, privacy text, and generic contact labels.\n"
        "- Keep one single-line address.\n"
        f"Website: {website_url}\n"
        "Candidates:\n" + "\n".join(f"- {x}" for x in cleaned[:8])
    )
    llm_request = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }
    _log.debug(
        "LLM address extraction request: %s %s",
        endpoint,
        json.dumps(llm_request, ensure_ascii=False),
    )
    try:
        resp = _SESSION.post(
            endpoint,
            json=llm_request,
            timeout=timeout,
        )
        if resp.status_code != 200:
            _log.debug(
                "LLM address extraction failed status=%s body=%s",
                resp.status_code,
                resp.text[:1200],
            )
            return None
        data = resp.json()
        text_out = str(data.get("response") or "").strip()
        _log.debug("LLM address extraction raw response: %s", text_out[:1200])
        if not text_out:
            return None
        parsed = None
        try:
            parsed = json.loads(text_out)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text_out, re.S)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except json.JSONDecodeError:
                    parsed = None
        if isinstance(parsed, dict):
            addr = _norm_text(str(parsed.get("address") or ""))
            _log.debug("LLM address extraction parsed address: %r", addr)
            return addr[:4000] if addr else None
    except Exception:
        _log.exception("Local LLM address extraction failed")
        return None
    return None


def _officer_display_name(item: dict[str, Any]) -> str | None:
    """Companies House may return name as a string or as a structured object."""
    n = item.get("name")
    if isinstance(n, str) and n.strip():
        return n.strip()
    if isinstance(n, dict):
        fn = (n.get("forename") or "").strip()
        mn = (n.get("middle_name") or n.get("other_forenames") or "").strip()
        sn = (n.get("surname") or "").strip()
        parts = [p for p in (fn, mn, sn) if p]
        if parts:
            return " ".join(parts)
    return None


def normalize_website_url(url: str | None) -> str | None:
    if not url or not str(url).strip():
        return None
    u = str(url).strip()
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    try:
        parts = urlparse(u)
        if not parts.netloc:
            return None
        return urlunparse(
            (
                parts.scheme,
                parts.netloc.lower(),
                parts.path or "/",
                parts.params,
                "",
                "",
            )
        )
    except Exception:
        return u


def _format_ch_address(addr: dict[str, Any] | None) -> str | None:
    if not addr:
        return None
    lines: list[str] = []
    for key in (
        "premises",
        "address_line_1",
        "address_line_2",
        "locality",
        "region",
        "postal_code",
        "country",
    ):
        v = addr.get(key)
        if v and str(v).strip():
            lines.append(str(v).strip())
    return "\n".join(lines) if lines else None


def _ch_area_from_address(addr: dict[str, Any] | None) -> str | None:
    if not addr:
        return None
    return (
        addr.get("locality")
        or addr.get("region")
        or (addr.get("postal_code") or "").split()[0]
        or None
    )


def _pick_search_item(
    items: list[dict[str, Any]], company_name: str
) -> dict[str, Any] | None:
    if not items:
        return None
    name_l = company_name.strip().lower()
    active = [i for i in items if (i.get("company_status") or "").lower() == "active"]
    pool = active or items
    for it in pool:
        if (it.get("title") or "").strip().lower() == name_l:
            return it
    return pool[0]


def _scrape_website(url: str) -> dict[str, Any]:
    t_all = time.perf_counter()
    out: dict[str, Any] = {
        "company_description": None,
        "company_telephone": None,
        "company_email": None,
        "company_address": None,
    }
    try:
        t_req = time.perf_counter()
        r = _SESSION.get(url, timeout=20, allow_redirects=True)
        r.raise_for_status()
        _dbg("website_request", t_req, f"url={url} status={r.status_code}")
    except Exception:
        _dbg("website_request_failed", t_all, f"url={url}")
        return out

    t_parse = time.perf_counter()
    html = r.text or ""
    soup = BeautifulSoup(html, "html.parser")
    address_candidates: list[str] = []
    _dbg("website_parse_html", t_parse, f"chars={len(html)}")

    desc = None
    og = soup.find("meta", property="og:description")
    if og and og.get("content"):
        desc = str(og["content"]).strip()
    if not desc:
        md = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        if md and md.get("content"):
            desc = str(md["content"]).strip()
    if desc:
        out["company_description"] = desc[:4000]

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:") and not out["company_email"]:
            em = href[7:].split("?")[0].strip()
            if em:
                out["company_email"] = em[:500]
        if href.lower().startswith("tel:") and not out["company_telephone"]:
            tel = re.sub(r"[^\d+()\s\-]", "", href[4:])
            if tel:
                out["company_telephone"] = tel.strip()[:200]

    # Common HTML address blocks
    if not out["company_address"]:
        addr_tag = soup.find("address")
        if addr_tag:
            addr_txt = " ".join(addr_tag.stripped_strings)
            addr_txt = re.sub(r"\s+", " ", addr_txt).strip()
            if len(addr_txt) >= 10:
                out["company_address"] = addr_txt[:4000]
                address_candidates.append(addr_txt)

    for script in soup.find_all("script", type=lambda t: t and "ld+json" in t.lower()):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            types = node.get("@type")
            tlist = types if isinstance(types, list) else ([types] if types else [])
            if not any(
                str(t).lower() in ("organization", "localbusiness", "corporation")
                for t in tlist
            ):
                continue
            if not out["company_description"]:
                d = node.get("description")
                if isinstance(d, str) and d.strip():
                    out["company_description"] = d.strip()[:4000]
            if not out["company_telephone"]:
                tel = node.get("telephone")
                if isinstance(tel, str) and tel.strip():
                    out["company_telephone"] = tel.strip()[:200]
            if not out["company_email"]:
                em = node.get("email")
                if isinstance(em, str) and em.strip():
                    out["company_email"] = em.strip()[:500]
            if not out["company_address"]:
                adr = node.get("address")
                if isinstance(adr, str) and adr.strip():
                    out["company_address"] = adr.strip()[:4000]
                    address_candidates.append(adr.strip())
                elif isinstance(adr, dict):
                    parts = []
                    for k in (
                        "streetAddress",
                        "addressLocality",
                        "addressRegion",
                        "postalCode",
                        "addressCountry",
                    ):
                        v = adr.get(k)
                        if isinstance(v, str) and v.strip():
                            parts.append(v.strip())
                    if parts:
                        joined = ", ".join(parts)
                        out["company_address"] = joined[:4000]
                        address_candidates.append(joined)

    # Footer/contact fallback text scan for UK-ish postal patterns.
    if not out["company_address"]:
        candidates: list[str] = []
        for sel in (
            "footer",
            "[id*='contact']",
            "[class*='contact']",
            "[id*='address']",
            "[class*='address']",
        ):
            for n in soup.select(sel):
                txt = " ".join(n.stripped_strings)
                txt = re.sub(r"\s+", " ", txt).strip()
                if txt:
                    candidates.append(txt)
                    address_candidates.append(txt)
        uk_postcode = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.I)
        for txt in candidates:
            if uk_postcode.search(txt):
                out["company_address"] = txt[:4000]
                break

    # Address LLM pass is optional (off by default for speed).
    enable_addr_llm = (
        os.environ.get("ENABLE_LOCAL_LLM_ADDRESS_EXTRACT", "0") or ""
    ).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if enable_addr_llm:
        t_addr_llm = time.perf_counter()
        if out.get("company_address"):
            address_candidates.insert(0, str(out["company_address"]))
        llm_address = _extract_address_with_local_llm(address_candidates, url)
        if llm_address:
            out["company_address"] = llm_address[:4000]
        _dbg(
            "website_address_llm",
            t_addr_llm,
            f"used={'yes' if bool(llm_address) else 'no'}",
        )
    _dbg(
        "website_scrape_done",
        t_all,
        f"desc={bool(out.get('company_description'))} tel={bool(out.get('company_telephone'))} email={bool(out.get('company_email'))} addr={bool(out.get('company_address'))}",
    )

    return out


def _ch_get(path: str, api_key: str) -> dict[str, Any] | None:
    base = os.environ.get("COMPANIES_HOUSE_BASE_URL", _DEFAULT_CH_BASE).rstrip("/")
    t0 = time.perf_counter()
    try:
        r = _SESSION.get(
            f"{base}{path}",
            auth=(api_key, ""),
            headers={"Accept": "application/json"},
            timeout=25,
        )
        if r.status_code != 200:
            _dbg("ch_get_non_200", t0, f"path={path} status={r.status_code}")
            return None
        _dbg("ch_get_ok", t0, f"path={path}")
        return r.json()
    except Exception:
        _dbg("ch_get_error", t0, f"path={path}")
        return None


def _ch_officers(company_number: str, api_key: str) -> list[dict[str, str]]:
    t0 = time.perf_counter()
    path = f"/company/{quote(company_number, safe='')}/officers?items_per_page=100"
    data = _ch_get(path, api_key)
    if not data:
        return []
    items = data.get("items") or []
    directors: list[dict[str, str]] = []
    for it in items:
        if it.get("resigned_on"):
            continue
        role = (it.get("officer_role") or "").lower()
        if "corporate" in role:
            continue
        name = _officer_display_name(it)
        if not name:
            continue
        appointed = it.get("appointed_on") or ""
        directors.append(
            {
                "director_name": name[:500],
                "role": "Director",
                "phone": "",
                "email": "",
                "interaction_notes": "",
                "appointment_date": str(appointed).strip()[:64] if appointed else "",
            }
        )
    _dbg(
        "ch_officers_done",
        t0,
        f"company_number={company_number} count={len(directors)}",
    )
    return directors


@dataclass
class FetchResult:
    """Result container for company enrichment data.

    Fields match the Company model columns plus director and warning details.
    """

    company_description: str | None = None
    company_area: str | None = None
    company_url: str | None = None
    company_address: str | None = None
    company_telephone: str | None = None
    company_email: str | None = None
    company_house_number: str | None = None
    company_house_url: str | None = None
    directors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    property_related: bool = False


def fetch_company_bundle(company_name: str, company_url: str | None) -> FetchResult:
    """Fetch website and Companies House data for a single company.

    Returns a FetchResult containing normalized business metadata and directors.
    """
    t_fetch = time.perf_counter()
    name = (company_name or "").strip()
    url = normalize_website_url(company_url)
    res = FetchResult(company_url=url)
    _dbg("fetch_start", t_fetch, f"name={name} url={url or ''}")

    try:
        api_key = (os.environ.get("COMPANIES_HOUSE_API_KEY") or "").strip()

        if url:
            t_web = time.perf_counter()
            web = _scrape_website(url)
            _dbg("fetch_step_website", t_web)
            if web.get("company_description"):
                res.company_description = web["company_description"]
            if web.get("company_telephone"):
                res.company_telephone = web["company_telephone"]
            if web.get("company_email"):
                res.company_email = web["company_email"]
            if web.get("company_address"):
                res.company_address = web["company_address"]
        else:
            res.warnings.append("No website URL — skipped page scrape.")

        if not api_key:
            _dbg("fetch_step_no_ch_api_key", t_fetch)
            combined_text = " ".join(
                p
                for p in (name, url or "", res.company_description or "")
                if p and str(p).strip()
            ).lower()
            res.property_related = any(k in combined_text for k in _PROPERTY_KEYWORDS)
            res.company_description = _llm_business_summary(
                company_name=name,
                website_url=url,
                raw_description=res.company_description,
                area=res.company_area,
                address=res.company_address,
            ) or _two_sentence_summary(res.company_description)
            res.warnings.append(
                "Companies House API key not set — skipped registry lookup and directors."
            )
            _dbg(
                "fetch_done_no_ch", t_fetch, f"property_related={res.property_related}"
            )
            return res

        t_search = time.perf_counter()
        search = _ch_get(f"/search/companies?q={quote_plus(name)}", api_key)
        _dbg("fetch_step_ch_search", t_search)
        if not search:
            res.warnings.append("Companies House search failed or returned nothing.")
            return res

        t_pick = time.perf_counter()
        items = search.get("items") or []
        hit = _pick_search_item(items, name)
        _dbg("fetch_step_pick_hit", t_pick, f"items={len(items)}")
        if not hit:
            res.warnings.append("No Companies House search results.")
            return res

        num = (hit.get("company_number") or "").strip()
        if not num:
            res.warnings.append("Search hit had no company number.")
            return res

        res.company_house_number = num[:32]
        res.company_house_url = public_companies_house_url(
            res.company_house_number, None
        )

        addr = hit.get("address")
        if isinstance(addr, dict):
            formatted = _format_ch_address(addr)
            if formatted and not res.company_address:
                res.company_address = formatted
            area = _ch_area_from_address(addr)
            if area:
                res.company_area = str(area)[:200]

        t_profile = time.perf_counter()
        profile = _ch_get(f"/company/{quote(num, safe='')}", api_key)
        _dbg("fetch_step_ch_profile", t_profile)
        if profile:
            ro = profile.get("registered_office_address")
            if isinstance(ro, dict):
                formatted = _format_ch_address(ro)
                if formatted and not res.company_address:
                    res.company_address = formatted
                area = _ch_area_from_address(ro)
                if area:
                    res.company_area = str(area)[:200]

        # Property-business gate: allow fetch only when text and/or SIC signals property domain.
        t_prop = time.perf_counter()
        text_blob = " ".join(
            p
            for p in (
                name,
                url or "",
                (hit.get("title") or ""),
                res.company_description or "",
                str(profile.get("type") or "") if isinstance(profile, dict) else "",
            )
            if p and str(p).strip()
        ).lower()
        sic_codes = []
        if isinstance(profile, dict):
            raw_sic = profile.get("sic_codes")
            if isinstance(raw_sic, list):
                sic_codes = [str(x).strip() for x in raw_sic if str(x).strip()]
        property_by_text = any(k in text_blob for k in _PROPERTY_KEYWORDS)
        property_by_sic = any(
            any(s.startswith(p) for p in _PROPERTY_SIC_PREFIXES) for s in sic_codes
        )
        res.property_related = property_by_text or property_by_sic
        _dbg(
            "fetch_step_property_gate",
            t_prop,
            f"by_text={property_by_text} by_sic={property_by_sic} sic_count={len(sic_codes)}",
        )
        if not res.property_related:
            res.warnings.append(
                "Not recognized as a property-related business (keyword/SIC check)."
            )
            res.company_description = _llm_business_summary(
                company_name=name,
                website_url=url,
                raw_description=res.company_description,
                area=res.company_area,
                address=res.company_address,
            ) or _two_sentence_summary(res.company_description)
            _dbg("fetch_done_not_property", t_fetch)
            return res

        t_off = time.perf_counter()
        res.directors = _ch_officers(num, api_key)
        _dbg("fetch_step_officers", t_off, f"count={len(res.directors)}")
        if not res.directors:
            res.warnings.append(
                "No active individual officers returned from Companies House."
            )

        t_sum = time.perf_counter()
        res.company_description = _llm_business_summary(
            company_name=name,
            website_url=url,
            raw_description=res.company_description,
            area=res.company_area,
            address=res.company_address,
        ) or _two_sentence_summary(res.company_description)
        _dbg(
            "fetch_step_summary", t_sum, f"has_summary={bool(res.company_description)}"
        )
        _dbg("fetch_done", t_fetch, f"warnings={len(res.warnings)}")
        return res
    except Exception as exc:
        _log.exception("fetch_company_bundle failed")
        res.warnings.append(
            f"Fetch error: {type(exc).__name__}. Check URL, API key, and network."
        )
        _dbg("fetch_failed", t_fetch, f"error={type(exc).__name__}")
        return res


def result_as_company_dict(fr: FetchResult) -> dict[str, Any]:
    return {
        "company_description": fr.company_description,
        "company_area": fr.company_area,
        "company_url": fr.company_url,
        "company_address": fr.company_address,
        "company_telephone": fr.company_telephone,
        "company_email": fr.company_email,
        "company_house_number": fr.company_house_number,
        "company_house_url": fr.company_house_url,
    }
