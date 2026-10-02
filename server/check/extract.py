"""What code can read out of a sentence without a model: where sentences start
and end, and the money amounts and dates written in them."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# -- sentences ------------------------------------------------------------------

# Abbreviations whose full stop does not end a sentence.
ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "hon", "esq", "jr", "sr", "inc", "ltd", "llc", "llp", "co", "corp", "no", "nos",
    "vs", "v", "st", "ave", "blvd", "dept", "est", "approx", "e.g", "i.e", "a.m", "p.m", "u.s", "p", "pp", "ex",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}
SENTENCE_END = re.compile(r"[.!?]+[\"')\]]*(?=\s|$)|\n+")
HAS_CONTENT = re.compile(r"[A-Za-z0-9]")
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")


@dataclass(frozen=True)
class Sentence:
    start: int  # Python string indices into the submitted text
    end: int
    text: str
    ended: bool  # False for trailing text with no end punctuation


def split_sentences(text: str) -> list[Sentence]:
    out: list[Sentence] = []
    begin = 0

    def emit(stop: int, ended: bool) -> None:
        chunk = text[begin:stop]
        lead = len(chunk) - len(chunk.lstrip())
        body = chunk.strip()
        if body and HAS_CONTENT.search(body):  # a line of digits alone is still something written: a figure on its own line
            out.append(Sentence(begin + lead, begin + lead + len(body), body, ended))

    for match in SENTENCE_END.finditer(text):
        if match.group().startswith("."):
            before = text[begin : match.start()].split()
            last = before[-1].lower().lstrip("(\"'") if before else ""
            if last in ABBREVIATIONS or (len(last) == 1 and last.isalpha()):
                continue  # "Dr." or an initial
        emit(match.end(), True)
        begin = match.end()
    emit(len(text), False)
    return out


def utf16_offsets(text: str):
    """Maps a Python index to the index a browser uses for the same position."""
    if all(ord(ch) <= 0xFFFF for ch in text):
        return lambda index: index
    table = [0]
    for ch in text:
        table.append(table[-1] + (2 if ord(ch) > 0xFFFF else 1))
    return lambda index: table[index]


# -- money ----------------------------------------------------------------------

MONEY = re.compile(
    r"(?:(?P<sign>\$|USD\s?)\s?(?P<a>\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d+)?)\s?(?P<as>k|m|mm|thousand|million)?(?![A-Za-z]))"
    r"|(?:(?P<b>\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d+)?)\s?(?P<bs>k|thousand|million)?\s?(?:dollars|USD)\b)",
    re.IGNORECASE,
)
SCALE = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "mm": 1_000_000, "million": 1_000_000}


@dataclass(frozen=True)
class Amount:
    start: int
    end: int
    cents: int
    raw: str
    bare: bool = False  # a number with no currency mark: looked up as written, never "corrected"

    @property
    def wrote_cents(self) -> bool:
        return bool(re.search(r"\.\d{2}\b", self.raw))


# A figure can be written without a dollar sign: "64k", or spelled out before "dollars".
BARE_THOUSANDS = re.compile(r"(?<![\w$.,])(\d{1,4}(?:\.\d+)?)\s?k\b", re.IGNORECASE)
UNITS = {
    word: number
    for number, word in enumerate(
        ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
    )
}
TENS = {word: 10 * number for number, word in enumerate(["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"], start=2)}
MULTIPLIERS = {"hundred": 100, "thousand": 1_000, "million": 1_000_000}
_NUMBER_WORD = "|".join([*UNITS, *TENS, *MULTIPLIERS])
SPELLED = re.compile(
    r"\b((?:" + _NUMBER_WORD + r")(?:(?:[\s-]+|\s+and\s+)(?:" + _NUMBER_WORD + r"))*)\s+dollars\b", re.IGNORECASE
)


def words_to_number(words: str) -> int | None:
    total, group = 0, 0
    for word in re.split(r"[\s-]+", words.lower()):
        if word == "and":
            continue
        if word in UNITS:
            group += UNITS[word]
        elif word in TENS:
            group += TENS[word]
        elif word == "hundred":
            group = max(group, 1) * 100
        elif word in MULTIPLIERS:
            total, group = total + max(group, 1) * MULTIPLIERS[word], 0
        else:
            return None
    return total + group


# A plain number that may be money: thousands grouped by commas, or four to seven digits.
# Not a year, and not part of a date, a time, a reference number or a longer number.
BARE_NUMBER = re.compile(r"(?<![\w$.,/:#-])(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d{4,7})(?![\w/:-]|\.\d|,\d)")


def find_amounts(text: str, bare: bool = False) -> list[Amount]:
    """Money written in `text`. With `bare`, plain numbers are included too, marked as such:
    a figure typed without a dollar sign must still meet the leak guard."""
    out = []
    for match in MONEY.finditer(text):
        digits = match.group("a") or match.group("b")
        scale = (match.group("as") or match.group("bs") or "").lower()
        try:
            value = float(digits.replace(",", "")) * SCALE.get(scale, 1)
        except ValueError:
            continue
        raw = match.group().rstrip()
        out.append(Amount(match.start(), match.start() + len(raw), round(value * 100), raw))

    def free(start: int, end: int) -> bool:
        return not any(start < found.end and end > found.start for found in out)

    for match in BARE_THOUSANDS.finditer(text):
        if free(match.start(), match.end()):
            out.append(Amount(match.start(), match.end(), round(float(match.group(1)) * 100_000), match.group()))
    for match in SPELLED.finditer(text):
        value = words_to_number(match.group(1))
        if value and free(match.start(), match.end()):
            out.append(Amount(match.start(), match.end(), value * 100, match.group()))
    if bare:
        dated = [(found.start, found.end) for found in find_dates(text)]
        for match in BARE_NUMBER.finditer(text):
            value = float(match.group(1).replace(",", ""))
            is_year = "," not in match.group(1) and 1900 <= value <= 2100
            in_date = any(match.start() < end and match.end() > start for start, end in dated)
            if not is_year and not in_date and free(match.start(), match.end()):
                out.append(Amount(match.start(), match.end(), round(value * 100), match.group(), bare=True))
    return sorted(out, key=lambda found: found.start)


def cents(amount) -> int | None:
    if amount is None:
        return None
    try:
        return round(float(amount) * 100)
    except (TypeError, ValueError):
        return None


def format_usd(value_cents: int, with_cents: bool) -> str:
    if with_cents or value_cents % 100:
        return f"${value_cents / 100:,.2f}"
    return f"${value_cents // 100:,}"


# -- dates ----------------------------------------------------------------------

MONTHS = {
    name: number
    for number, names in enumerate(
        ["january jan", "february feb", "march mar", "april apr", "may", "june jun", "july jul", "august aug",
         "september sep sept", "october oct", "november nov", "december dec"],
        start=1,
    )
    for name in names.split()
}
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"]
_MONTH = r"(?P<mon>" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"
DATE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("iso", re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})\b")),
    ("us", re.compile(r"\b(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4}|\d{2})\b")),
    ("mdy", re.compile(r"\b" + _MONTH + r"\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y>\d{4})\b", re.IGNORECASE)),
    ("dmy", re.compile(r"\b(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH + r",?\s+(?P<y>\d{4})\b", re.IGNORECASE)),
]


@dataclass(frozen=True)
class DateMention:
    start: int
    end: int
    iso: str
    raw: str
    style: str


def find_dates(text: str) -> list[DateMention]:
    out: list[DateMention] = []
    taken: list[tuple[int, int]] = []
    for style, pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            if any(match.start() < stop and match.end() > start for start, stop in taken):
                continue
            parts = match.groupdict()
            month = MONTHS.get((parts.get("mon") or "").lower()) or int(parts.get("m") or 0)
            year = int(parts["y"])
            if year < 100:
                year += 2000
            try:
                value = date(year, month, int(parts["d"]))
            except ValueError:
                continue
            taken.append((match.start(), match.end()))
            out.append(DateMention(match.start(), match.end(), value.isoformat(), match.group(), style))
    return sorted(out, key=lambda mention: mention.start)


def format_date(iso: str, like: DateMention) -> str:
    """The file's date, written the way the sentence wrote its own."""
    value = date.fromisoformat(iso)
    raw = like.raw
    if like.style == "us":
        padded = bool(re.match(r"0\d/|\d\d/0\d/", raw))
        month, day = (f"{value.month:02d}", f"{value.day:02d}") if padded else (str(value.month), str(value.day))
        year = str(value.year) if re.search(r"/\d{4}$", raw) else f"{value.year % 100:02d}"
        return f"{month}/{day}/{year}"
    if like.style in ("mdy", "dmy"):
        word = re.search(r"[A-Za-z]+", raw)
        short = bool(word) and len(word.group()) <= 4 and value.month != 5
        name = MONTH_NAMES[value.month - 1][:3] if short else MONTH_NAMES[value.month - 1]
        if short and "." in raw:
            name += "."
        if like.style == "mdy":
            return f"{name} {value.day}, {value.year}"
        return f"{value.day} {name} {value.year}"
    return iso


# -- words ----------------------------------------------------------------------

STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by", "can", "could", "did", "do", "does", "for", "from", "had", "has", "have", "he", "her", "hers", "him", "his", "i", "if", "in", "into", "is", "it", "its", "me", "my", "no", "not", "of", "on", "or", "our", "ours", "she", "so", "than", "that", "the", "their", "theirs", "them", "then", "there", "these", "they", "this", "those", "to", "us", "was", "we", "were", "what", "when", "where", "which", "who", "will", "with", "would", "you", "your", "yours", "about", "after", "again", "all", "also", "am", "any", "because", "before", "both", "each", "few", "more", "most", "other", "out", "over", "own", "same", "some", "such", "too", "under", "until", "up", "very", "per", "via", "shall", "may", "might", "must", "should", "please", "thanks", "thank", "regards", "dear", "hello", "hi", "sincerely", "re", "said", "says", "say", "stated", "states"]
)
TOKEN = re.compile(r"[a-z][a-z'’-]{1,}")


def stem(word: str) -> str:
    word = word.replace("’", "'").replace("'s", "").strip("'-")
    for suffix, keep in (("ies", "y"), ("sses", "ss"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3 and not (suffix == "s" and word.endswith("ss")):
            return word[: len(word) - len(suffix)] + keep
    return word


def content_tokens(text: str | None) -> set[str]:
    """Lower-cased, lightly stemmed words that carry a subject; numbers and stopwords dropped."""
    out = set()
    for word in TOKEN.findall((text or "").lower()):
        if word in STOPWORDS or word in MONTHS:
            continue
        token = stem(word)
        if len(token) >= 3 and token not in STOPWORDS:
            out.add(token)
    return out


NEGATION = re.compile(r"\b(?:not|no|never|none|nor|without|cannot|denie[sd]|deny|unknown|missing|unconfirmed|lacks?)\b|n[’']t\b", re.IGNORECASE)


def negated(text: str) -> bool:
    return bool(NEGATION.search(text))


def normalise_sentence(text: str) -> str:
    """Cache key form: case, spacing and typographic quotes do not make a new sentence."""
    table = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})
    return " ".join(text.translate(table).lower().split())
