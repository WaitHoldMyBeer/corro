"""The checker on text nobody would type on purpose.

Run through the route on the synthetic matter with no model, so only the part
that is code is exercised: the split into sentences, the offsets, and the lock
on firm-only figures for a provider. Whatever the text, the answer is a 200,
every span is a true slice of what was sent (offsets are UTF-16 code units, as
a browser indexes a string), and a figure the provider is never shown is locked
however it is written.
"""

from __future__ import annotations

import time

import pytest
import test_provider_routes as routes
from test_provider_routes import A_CHARGES, B_CHARGES, CASE_VALUE, LIMIT, MATTER, A, firm

client = routes.client  # the signed-in firm client on the synthetic matter (a fixture)

ODD = "‮\u0000\U0001f600 中文 عربي été ﻿"
TEXTS = {
    "empty": "",
    "one space": " ",
    "new lines": "\n\n\n",
    "one stop": ".",
    "only punctuation": "...!!!???;;;---",
    "one letter": "x",
    "no stop at the end": "no stop at the end",
    "one very long sentence": "word " * 10_000,
    "fifty thousand characters": "A" * 50_000,
    "fifty thousand in sentences": "Synthetic sentence number one. " * 1_600,
    "unicode": f"First part {ODD}. Second \U0001f600\U0001f600 part costs $12. Third é.",
    "abbreviations": "Dr. A. B. Placeholder, M.D., Ph.D. vs. e.g. i.e. 3.5 U.S.C. No. 5. Next one.",
    "amounts": "It was $1,234.56 and $1234.56 and 1,234.56 USD and USD 1234 and $1.2k and $1.2 million and ($500) and -$500 and $0 and $.50 and 1 234,56 and €5 and $ 7.",
    "dates": "On 01/02/2031, 2031-01-02, Jan 2, 2031, 2 January 2031, 1/2/31, 31/12/2031, 13/13/2031, 02-30-2031, 0000-00-00, 9999-12-31, January 32, 2031 and Feb 29 2031.",
    "digits only": "1" * 400,
    "currency signs only": "$" * 5_000,
    "brackets": "((((" * 2_000,
    "backslashes": "\\" * 3_000,
    "format codes": "%s %d {0} {{}} ${x} \\n",
    "markup": "<script>alert(1)</script> & <b>bold</b>.",
    "windows line ends": "First line.\r\nSecond line.\r\n\r\nThird line.",
    "tabs and stops": "\t.\t.\t.",
}
AUDIENCES = ({}, {"audience": "provider", "audience_contact_id": A})


def units(text: str) -> bytes:
    return text.encode("utf-16-le", "surrogatepass")


def slice16(text: str, start: int, end: int) -> str:
    return units(text)[2 * start : 2 * end].decode("utf-16-le", "surrogatepass")


def check(client, text: str, **extra):
    return client.post(firm("/check"), json={"text": text, **extra})


@pytest.mark.parametrize("label", TEXTS)
def test_every_span_is_a_true_slice_of_what_was_sent(client, label: str) -> None:
    text = TEXTS[label]
    length = len(units(text)) // 2
    for extra in AUDIENCES:
        response = check(client, text, **extra)
        assert response.status_code == 200, f"{label}: {response.status_code} {response.text[:200]}"
        spans = response.json()["spans"]
        last = 0
        for span in spans:
            assert 0 <= span["start"] < span["end"] <= length, f"{label}: span {span['start']}..{span['end']} of {length}"
            assert slice16(text, span["start"], span["end"]) == span["text"], f"{label}: the span's text is not the slice at its offsets"
            assert span["start"] >= last, f"{label}: spans overlap or are out of order"
            last = span["end"]
            assert span["text"].strip(), f"{label}: a span of white space"
        if not text.strip():
            assert spans == []


def test_text_with_nothing_to_say_is_never_called_clean_or_locked(client) -> None:
    for label in ("empty", "one space", "new lines", "one stop", "only punctuation", "tabs and stops"):
        for extra in AUDIENCES:
            body = check(client, TEXTS[label], **extra).json()
            assert not [span for span in body["spans"] if span.get("verdict")], f"{label}: punctuation was given a verdict"
            assert body.get("blocked") in (False, None), f"{label}: nothing to send is reported as blocked"


@pytest.mark.parametrize("figure", [CASE_VALUE, LIMIT, B_CHARGES])
def test_a_figure_a_provider_is_never_shown_is_locked_however_it_is_written(client, figure: float) -> None:
    whole = int(figure)
    written = [f"${whole:,}", f"${whole}", f"{whole}", f"{whole:,}", f"${whole:,}.00", f"{whole:,} dollars", f"USD {whole:,}", f"US${whole}", f"$ {whole:,}", f"({whole:,})", f"${whole:,}."]
    missed = []
    for form in written:
        body = check(client, f"We hold the amount at {form} for now.", audience="provider", audience_contact_id=A).json()
        if not any(span.get("verdict") == "dont_send" for span in body["spans"]):
            missed.append(form)
    assert not missed, f"written as {missed}, the figure is not locked for a provider"


def test_the_lock_is_on_the_figure_not_on_every_sentence(client) -> None:
    for sentence in ("We hold the amount for now.", f"We hold the amount at ${int(A_CHARGES):,} for now.", "We hold 3 pages for now."):
        body = check(client, sentence, audience="provider", audience_contact_id=A).json()
        assert not any(span.get("verdict") == "dont_send" for span in body["spans"]), f"locked for the provider it is about: {sentence}"


def test_fifty_thousand_characters_are_answered_promptly(client) -> None:
    for label in ("fifty thousand characters", "fifty thousand in sentences", "one very long sentence"):
        started = time.perf_counter()
        response = check(client, TEXTS[label], audience="provider", audience_contact_id=A)
        elapsed = time.perf_counter() - started
        assert response.status_code == 200
        assert elapsed < 10, f"{label}: {elapsed:.1f} s"


def test_a_request_that_is_not_a_check_is_refused(client) -> None:
    for body in ({}, {"text": None}, {"text": 5}, {"text": ["x"]}, {"text": "x", "mode": "nope"}, {"text": "x", "audience": "nope"}):
        assert check_raw(client, body).status_code == 422, body
    for extra in ({"audience": "provider"}, {"audience": "provider", "audience_contact_id": 999_999}, {"written_on": "garbage"}, {"author_contact_id": 999_999}):
        assert check(client, "Synthetic words.", **extra).status_code in (200, 400, 404, 422), extra
    assert check(client, "Synthetic words.").json()["matter_id"] == MATTER


def check_raw(client, body: dict):
    return client.post(firm("/check"), json=body)
