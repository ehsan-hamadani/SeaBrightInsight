"""Display helpers for telephone fields in templates."""

from __future__ import annotations

import re

from markupsafe import Markup, escape


def _digits_only(s: str) -> str:
    """Return only digits from a phone string."""
    return re.sub(r"\D", "", s)


def _is_plausible_phone(s: str) -> bool:
    """Return True for strings that look like valid phone numbers."""
    if not (s or "").strip():
        return False
    if re.search(r"\bhttps?://", s, re.I):
        return False
    d = _digits_only(s)
    return 7 <= len(d) <= 15


def _split_phone_candidates(raw: str) -> list[str]:
    """Split combined contact strings into individual phone candidates."""
    s = raw.strip()
    if not s:
        return []
    if re.search(r"[;|\n]", s):
        parts = re.split(r"\s*(?:[;|]|\n+)\s*", s)
        return [p.strip() for p in parts if p.strip()]
    if " / " in s:
        return [p.strip() for p in s.split(" / ") if p.strip()]
    if "/" in s and not s.startswith("http"):
        parts = re.split(r"\s*/\s*", s)
        return [
            p.strip() for p in parts if p.strip() and _is_plausible_phone(p.strip())
        ]
    return [s]


def _tel_href(raw: str) -> str:
    """Build a dialable tel: URI from user-entered text."""
    t = raw.strip()
    d = _digits_only(t)
    if not d:
        return "tel:"
    if t.startswith("+"):
        return "tel:+" + d
    if d.startswith("00") and len(d) >= 10:
        return "tel:+" + d[2:]
    return "tel:" + d


def _pretty_phone_label(raw: str) -> str:
    """Format phone numbers into a more readable display label."""
    orig = raw.strip()
    d = _digits_only(orig)
    if not (7 <= len(d) <= 15):
        return orig

    if orig.startswith("+") or d.startswith("44"):
        if d.startswith("44") and len(d) >= 12:
            rest = d[2:]
            if rest.startswith("7") and len(rest) >= 10:
                return f"+44 {rest[:4]} {rest[4:]}"
            if len(rest) >= 9:
                return f"+44 {rest[:2]} {rest[2:6]} {rest[6:]}"
        return orig

    if len(d) == 11 and d.startswith("0"):
        if d.startswith("07"):
            return f"{d[:5]} {d[5:]}"
        return f"{d[:5]} {d[5:8]} {d[8:]}"

    return orig


def jinja_phone_display(raw: str | None) -> str:
    """Render a phone string for display, hiding invalid values."""
    s = (raw or "").strip()
    if not s:
        return "-"
    if re.search(r"\bhttps?://", s, re.I):
        return "-"
    digits = _digits_only(s)
    if len(digits) < 7 or len(digits) > 15:
        return "-"
    return s


_PHONE_LINK_CLASS = (
    "inline-block max-w-full text-brand-600 underline decoration-brand-500/40 "
    "underline-offset-2 hover:text-brand-700 hover:decoration-brand-600 "
    "tabular-nums tracking-tight leading-snug break-words"
)


def _phone_anchor(href: str, label: str) -> Markup:
    """Trusted `<a>` markup; dynamic parts escaped (MarkupSafe str+Markup rules)."""
    return (
        Markup('<a href="')
        + escape(href)
        + Markup('" class="')
        + escape(_PHONE_LINK_CLASS)
        + Markup('">')
        + escape(label)
        + Markup("</a>")
    )


def jinja_phone_cell(raw: str | None) -> Markup:
    """
    Rich table cell: tel: links, optional multi-line stack, UK-friendly spacing.
    """
    s = (raw or "").strip()
    if not s:
        return Markup('<span class="text-slate-400">—</span>')
    if re.search(r"\bhttps?://", s, re.I):
        return Markup('<span class="text-slate-400">—</span>')

    parts = _split_phone_candidates(s)
    links: list[Markup] = []
    for p in parts:
        if not _is_plausible_phone(p):
            continue
        links.append(_phone_anchor(_tel_href(p), _pretty_phone_label(p)))

    if not links:
        return Markup('<span class="text-slate-400">—</span>')
    if len(links) == 1:
        return links[0]
    out = Markup("")
    for link in links:
        out += Markup('<div class="min-w-0 leading-tight">') + link + Markup("</div>")
    return (
        Markup('<div class="flex flex-col gap-1.5 min-w-0">') + out + Markup("</div>")
    )
