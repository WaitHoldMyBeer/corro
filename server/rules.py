"""Rules that need no model: vocabulary and thresholds, in one place.

Nothing here refers to a particular matter. The keyword lists are the general
vocabulary of personal-injury files and of Clio's own field types; every rule's
output is marked `derivation = computed` so the UI can show it was a rule, and
the digest may replace it with something sourced.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

# Role of a related contact, read from the Clio relationship description.
# Checked in this order: "adverse party, self-insured" is adverse, not an insurer.
# A provider is someone the description says is treating the client. A medical
# specialty alone is not enough: an examiner or expert retained by another party
# is a doctor too, and must never be offered as someone to share the case with.
# Anything ambiguous is "other"; the attorney can still act on it from Clio.
NOT_TREATING = ("expert", "independent medical", "ime", "examin", "defense", "defence", "retained", "custodian")
ROLE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("adverse", ("adverse", "defendant", "opposing", "tortfeasor")),
    ("insurer", ("carrier", "insurer", "insurance", "adjuster", "claims", "no-fault", "underwriter")),
    ("provider", ("treating", "medical provider", "treatment provider", "hospital", "clinic", "emergency care")),
)

# Which of the firm's custom fields fills which slot of the header. Matched on
# the field's name as the firm defined it in Clio, plus Clio's field type.
FIELD_SLOTS: dict[str, dict[str, Any]] = {
    "case_value": {"types": ("currency", "numeric"), "any": ("case value", "estimated value", "valuation")},
    "coverage_confirmed": {"types": ("checkbox",), "any": ("limits confirmed", "coverage confirmed")},
    "coverage": {"types": ("text_area", "text_line", "currency"), "any": ("policy limit", "coverage", "limits")},
    "specials": {"types": ("currency", "numeric"), "any": ("specials", "medical expenses", "medical bills")},
    "incident_date": {"types": ("date",), "any": ("date of incident", "date of loss", "date of accident", "incident date")},
    "lien": {"types": ("text_area", "text_line", "currency"), "any": ("lien",)},
    "wage_loss": {"types": ("text_area", "text_line", "currency", "numeric"),
                  "any": ("wage", "lost income", "loss of income", "lost earnings", "loss of earnings")},
}

# Legal-entity suffixes dropped when looking for a company's name inside free text.
ENTITY_SUFFIX = re.compile(
    r",?\s+(p\.?l\.?l\.?c\.?|l\.?l\.?c\.?|p\.?c\.?|inc\.?|l\.?l\.?p\.?|corp\.?|co\.?|ltd\.?|pa|p\.a\.)$", re.IGNORECASE
)

MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k|m|million|thousand)?\b", re.IGNORECASE)


def role_from_text(text: str | None) -> str:
    lowered = (text or "").lower()
    for role, words in ROLE_KEYWORDS:
        if any(word in lowered for word in words):
            if role == "provider" and any(re.search(rf"\b{re.escape(word)}", lowered) for word in NOT_TREATING):
                return "other"
            return role
    return "other"


def field_slot(name: str, field_type: str, taken: set[str]) -> str | None:
    lowered = name.lower()
    for slot, rule in FIELD_SLOTS.items():
        if slot not in taken and field_type in rule["types"] and any(key in lowered for key in rule["any"]):
            return slot
    return None


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        return None


def money_amounts(text: str | None) -> list[Decimal]:
    """Every dollar amount written in a piece of text, in order of appearance."""
    out: list[Decimal] = []
    for digits, scale in MONEY.findall(text or ""):
        amount = Decimal(digits.replace(",", ""))
        scale = scale.lower()
        if scale in ("k", "thousand"):
            amount *= 1000
        elif scale in ("m", "million"):
            amount *= 1_000_000
        out.append(amount)
    return out


def usd(amount: Decimal | float | None) -> str:
    """One money format for everything the server writes: whole dollars when the cents are zero."""
    if amount is None:
        return ""
    value = Decimal(str(amount)).quantize(Decimal("0.01"))
    sign, value = ("-" if value < 0 else ""), abs(value)  # the sign goes before the currency mark
    return f"{sign}${value:,.0f}" if value == value.to_integral_value() else f"{sign}${value:,.2f}"


def truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in ("true", "1", "yes", "t")


def parse_instant(value: str | None) -> datetime | None:
    """A Clio date or timestamp as an aware-or-naive datetime; None if unreadable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def local_date(value: str | None) -> date | None:
    """The calendar day a Clio value falls on. Clio returns instants in the firm
    account's own offset, so the day is read in that offset, not this machine's."""
    moment = parse_instant(value)
    return moment.date() if moment else None


def firm_today() -> date:
    """Today in the firm's time zone (FIRM_TIMEZONE, an IANA name); this machine's zone if unset."""
    name = os.environ.get("FIRM_TIMEZONE", "").strip()
    if name:
        try:
            return datetime.now(ZoneInfo(name)).date()
        except Exception:
            pass
    return date.today()


def name_patterns(contact: dict[str, Any]) -> list[re.Pattern[str]]:
    """Ways a contact may be written in a task or calendar title."""
    names: list[str] = []
    full = (contact.get("name") or "").strip()
    if full:
        names.append(full)
        stripped = ENTITY_SUFFIX.sub("", full).strip(" ,")
        if stripped and stripped != full:
            names.append(stripped)
    last = (contact.get("last_name") or "").strip()
    if contact.get("type") == "Person" and len(last) >= 4:
        names.append(last)
    return [re.compile(r"(?<!\w)" + re.escape(n) + r"(?!\w)", re.IGNORECASE) for n in names]


def mentions(text: str | None, patterns: list[re.Pattern[str]]) -> bool:
    return bool(text) and any(p.search(text) for p in patterns)
