"""What a described card may show: derived from the contract, not typed by hand.

Every collection of the case model (a list of items, or a single sourced value
such as a header fact) is a possible source, with the plain fields of its items.
The browser has the last word on which sources it will render, so the list is
narrowed to the ones `web/v2/js/cards.js` accepts when that file is present.
"""

from __future__ import annotations

import re
import types
from enum import Enum
from functools import lru_cache
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from shared import contract as c
from shared.card_spec import CardSource

from ..config import ROOT

SCALARS = (str, int, float, bool)
BROWSER_ALLOWLIST = ROOT / "web" / "v2" / "js" / "cards.js"
TOO_LARGE = {"claims"}  # every claim of the matter: not a card


def _unwrap(annotation: Any) -> tuple[Any, bool]:
    """(inner type, whether it was a list), with Optional removed."""
    many = False
    while True:
        origin = get_origin(annotation)
        if origin in (Union, types.UnionType):
            inner = [arg for arg in get_args(annotation) if arg is not type(None)]
            if len(inner) != 1:
                return annotation, many
            annotation = inner[0]
        elif origin is list:
            annotation, many = get_args(annotation)[0], True
        else:
            return annotation, many


def _is_model(kind: Any) -> bool:
    return isinstance(kind, type) and issubclass(kind, BaseModel)


def _is_scalar(kind: Any) -> bool:
    return kind in SCALARS or (isinstance(kind, type) and issubclass(kind, Enum))


def _label(kind: Any, description: str | None) -> str:
    name = "text"
    if kind in (int, float):
        name = "number"
    elif kind is bool:
        name = "yes/no"
    elif isinstance(kind, type) and issubclass(kind, Enum):
        name = "one of " + ", ".join(str(member.value) for member in kind)
    return f"{name}: {description}" if description else name


def _fields(model: type[BaseModel]) -> dict[str, str]:
    """Plain fields of an item, and the plain fields of the objects nested one level inside it."""
    out: dict[str, str] = {}
    for name, field in model.model_fields.items():
        kind, many = _unwrap(field.annotation)
        if many or name.endswith("href"):
            continue
        if _is_scalar(kind):
            out[name] = _label(kind, field.description)
        elif _is_model(kind):
            for inner_name, inner in kind.model_fields.items():
                inner_kind, inner_many = _unwrap(inner.annotation)
                if not inner_many and _is_scalar(inner_kind) and not inner_name.endswith("href"):
                    out[f"{name}.{inner_name}"] = _label(inner_kind, inner.description)
    return out


def _doc(model: type[BaseModel], description: str | None) -> str:
    text = description or (model.__doc__ or "").strip().split("\n\n")[0]
    return " ".join(text.split())


def _derived() -> dict[str, CardSource]:
    out: dict[str, CardSource] = {}
    for name, field in c.CaseModel.model_fields.items():
        kind, many = _unwrap(field.annotation)
        if not _is_model(kind):
            continue
        if many:
            out[name] = CardSource(path=name, many=True, description=_doc(kind, field.description), fields=_fields(kind))
            continue
        for inner_name, inner in kind.model_fields.items():  # one level down: agenda.overdue, brief.stage, spend.lines
            inner_kind, inner_many = _unwrap(inner.annotation)
            if _is_model(inner_kind):
                path = f"{name}.{inner_name}"
                out[path] = CardSource(path=path, many=inner_many, description=_doc(inner_kind, inner.description), fields=_fields(inner_kind))
    return {path: source for path, source in out.items() if path not in TOO_LARGE and source.fields}


def _browser_accepts() -> set[str] | None:
    try:
        text = BROWSER_ALLOWLIST.read_text()
    except OSError:
        return None
    block = re.search(r"ALLOWED_SOURCES\s*=\s*\[(.*?)\]", text, re.DOTALL)
    return set(re.findall(r"'([a-z_.]+)'", block.group(1))) if block else None


@lru_cache(maxsize=1)
def catalog() -> dict[str, CardSource]:
    derived = _derived()
    accepted = _browser_accepts()
    if accepted:
        derived = {path: source for path, source in derived.items() if path in accepted}
    return dict(sorted(derived.items()))
