"""A made-up ledger for building and testing the checker without a case.

Every name is a placeholder and every amount and date is drawn from a seeded
random generator, so nothing here describes a real matter. `build()` returns
the ledger and the values it used, so a test can write sentences that agree or
disagree with it without knowing any number in advance.

    from server.check import synthetic
    ledger, facts = synthetic.build(seed=7)
    result = synthetic.checker(ledger).run(CheckRequest(text=..., audience="provider",
                                                        audience_contact_id=facts["provider_a"]))
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

from . import tier2
from .engine import Checker, MemoryStore
from .ledger import Ledger

MATTER_ID = 0


def build(seed: int = 1, matter_id: int = MATTER_ID) -> tuple[Ledger, dict[str, Any]]:
    rng = random.Random(seed)

    def money(low: int, high: int) -> float:
        return round(rng.randrange(low * 100, high * 100) / 100, 2)

    start = date(2031, 1, 1) + timedelta(days=rng.randrange(0, 200))

    def day(offset: int) -> str:
        return (start + timedelta(days=offset)).isoformat()

    contacts = [
        {"id": 9001, "name": "Casey Placeholder", "type": "Person", "last_name": "Placeholder", "role": "client"},
        {"id": 9002, "name": "Alpha Example Clinic", "type": "Company", "role": "provider"},
        {"id": 9003, "name": "Beta Example Imaging", "type": "Company", "role": "provider"},
        {"id": 9004, "name": "Gamma Example Mutual", "type": "Company", "role": "insurer"},
    ]
    facts: dict[str, Any] = {
        "client": 9001, "provider_a": 9002, "provider_b": 9003, "insurer": 9004,
        "provider_a_name": contacts[1]["name"], "provider_b_name": contacts[2]["name"],
        "balance_a_notes": money(1000, 5000),
        "balance_a_document": money(5001, 9000),
        "charges_b": money(9001, 12000),
        "case_value": float(rng.randrange(60, 120) * 1000),
        "policy_limit": float(rng.randrange(15, 50) * 1000),
        "incident_date": day(0),
        "hearing_old": day(120),
        "hearing_new": day(150),
        "records_requested": day(30),
        "records_received": day(45),
        "note_old": day(60),
        "note_new": day(90),
    }
    # A second figure of the same kind on a different subject (drawn last, so the values above do not move).
    facts["no_fault_limit"] = float(rng.randrange(51, 59) * 1000)

    def text_claim(number: int, index: int, kind: str, topic: str, statement: str, category: str, *, when: str,
                   source_kind: str = "note", date_: str | None = None, amount: float | None = None, party: str | None = None) -> dict[str, Any]:
        return {
            "id": f"{source_kind}:{number}#{index}", "origin": "correspondence" if source_kind == "communication" else "notes",
            "source_kind": source_kind, "clio_id": str(number), "page": None, "record_date": when,
            "label": f"Synthetic {source_kind} {number}", "quote_verified": True, "kind": kind, "topic": topic,
            "statement": statement, "quote": statement, "date": date_, "amount_usd": amount, "party": party, "category": category,
        }

    def page_claim(document: int, page: int, index: int, kind: str, statement: str, *, received: str, issuer: str | None = None,
                   date_: str | None = None, amount: float | None = None, party: str | None = None) -> dict[str, Any]:
        return {
            "id": f"document:{document}:p{page}#{index}", "origin": "document", "source_kind": "document", "clio_id": str(document),
            "page": page, "record_date": received, "label": f"Synthetic document {document}.pdf", "quote_verified": True,
            "scanned": False, "page_type": "other", "issuer": issuer, "document_date": received, "topic": None,
            "category": "internal", "kind": kind, "statement": statement, "quote": statement, "date": date_,
            "amount_usd": amount, "party": party,
        }

    a, b = facts["provider_a_name"], facts["provider_b_name"]
    usd = lambda value: f"${value:,.2f}"
    claims = [
        text_claim(101, 0, "charge_or_balance", "balance owed to a provider", f"The outstanding balance with {a} is {usd(facts['balance_a_notes'])}.",
                   "bills", when=facts["note_old"], amount=facts["balance_a_notes"], party=a),
        page_claim(201, 2, 0, "charge_or_balance", f"The statement shows a balance due of {usd(facts['balance_a_document'])}.",
                   received=facts["note_new"], issuer=a, amount=facts["balance_a_document"], party=a),
        page_claim(202, 1, 0, "charge_or_balance", f"Total charges are {usd(facts['charges_b'])}.",
                   received=facts["note_new"], issuer=b, amount=facts["charges_b"], party=b),
        text_claim(102, 0, "valuation", "case value", f"The firm values the case at {usd(facts['case_value'])}.",
                   "valuation", when=facts["note_new"], amount=facts["case_value"]),
        text_claim(103, 0, "insurance_or_coverage", "policy limits", f"The policy limit is {usd(facts['policy_limit'])}.",
                   "valuation", when=facts["note_old"], amount=facts["policy_limit"], party=contacts[3]["name"]),
        text_claim(104, 0, "liability_fact", "liability", "Liability is disputed and the firm expects a comparative fault argument.",
                   "strategy", when=facts["note_old"]),
        text_claim(105, 0, "incident_fact", "date of incident", f"The incident occurred on {facts['incident_date']}.",
                   "internal", when=facts["note_old"], date_=facts["incident_date"]),
        page_claim(203, 1, 0, "incident_fact", f"The report records the incident on {facts['incident_date']}.",
                   received=facts["note_old"], date_=facts["incident_date"]),
        text_claim(106, 0, "legal_event", "hearing date", f"The hearing is set for {facts['hearing_old']}.",
                   "status", when=facts["note_old"], date_=facts["hearing_old"]),
        text_claim(107, 0, "legal_event", "hearing date", f"The hearing was continued to {facts['hearing_new']}.",
                   "status", when=facts["note_new"], date_=facts["hearing_new"]),
        text_claim(108, 0, "treatment", "records request to a provider", f"Records were requested from {b} on {facts['records_requested']}.",
                   "records", when=facts["records_requested"], source_kind="communication", date_=facts["records_requested"], party=b),
        text_claim(109, 0, "treatment", "records from a provider", f"Records from {a} have not been received.",
                   "records", when=facts["note_old"], party=a),
        page_claim(204, 1, 0, "treatment", f"Treatment records of {a} are in the file, received {facts['records_received']}.",
                   received=facts["records_received"], issuer=a, date_=facts["records_received"], party=a),
        text_claim(110, 0, "insurance_or_coverage", "no-fault benefits", f"The no-fault limit is {usd(facts['no_fault_limit'])}.",
                   "valuation", when=facts["note_old"], amount=facts["no_fault_limit"], party=contacts[0]["name"]),
    ]
    conflicts = [
        {"id": "conflict:synthetic-balance", "topic": "balance owed to a provider",
         "notes_claim_ids": ["note:101#0"], "document_claim_ids": ["document:201:p2#0"]},
        {"id": "conflict:synthetic-records", "topic": "records from a provider",
         "notes_claim_ids": ["note:109#0"], "document_claim_ids": ["document:204:p1#0"]},
    ]
    headline = ["note:102#0", "note:103#0", "note:105#0", "note:107#0"]
    return Ledger(matter_id, claims, contacts=contacts, conflicts=conflicts, headline_ids=headline), facts


def checker(ledger: Ledger, *, with_model: bool = True, caller: tier2.Caller | None = None, store: MemoryStore | None = None) -> Checker:
    """A checker over a ledger held in memory. Tier 2 uses the lexical stand-in
    unless a caller is given; `with_model=False` gives tier 1 alone."""
    use = caller or (tier2.fake_caller if with_model else None)
    return Checker(ledger, store or MemoryStore(), model="fake-lexical" if use is tier2.fake_caller else "test", caller=use, deadline=None)
