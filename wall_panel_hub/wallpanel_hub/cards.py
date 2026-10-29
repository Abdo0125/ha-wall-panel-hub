"""Card registry (schema/cards.json): which cards exist and what each one accepts.

The editor receives the registry as is and builds its card picker and property fields
from it. The app mirrors the kinds in CardKind.kt.
"""

import json
import re
from pathlib import Path

REGISTRY: dict = json.loads((Path(__file__).parent / "schema" / "cards.json").read_text(encoding="utf-8"))
# The registry as apps reading layout format 2 know it (a card type per shape): their editor's.
REGISTRY_V2: dict = json.loads((Path(__file__).parent / "schema" / "cards.v2.json").read_text(encoding="utf-8"))
KINDS: dict = REGISTRY["kinds"]
CARDS: dict = REGISTRY["cards"]

DOMAIN_KIND = {d: kind for kind, spec in KINDS.items() for d in spec["domains"]}
ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def kind_of(entity_id: str) -> str | None:
    """The kind of card an entity gets, or None if the panel has no card for its domain."""
    return DOMAIN_KIND.get(domain_of(entity_id))


def kind_of_card(card_type: str) -> str:
    return card_type.split(".", 1)[0]


def default_size(card_type: str) -> str:
    return CARDS[card_type]["default_size"]


def new_card(card_id: str, entity_id: str) -> dict:
    """The default card for an entity (e.g. light.toggle for a light)."""
    card_type = KINDS[kind_of(entity_id)]["default"]
    return {"id": card_id, "card": card_type, "entities": [entity_id], "size": default_size(card_type)}


def next_card_id(taken: set[str]) -> str:
    n = 1
    while f"c{n}" in taken:
        n += 1
    return f"c{n}"


def card_ids(layout: dict) -> set[str]:
    """Every card id in use: the pages' and my dashboard's (unique across both)."""
    cards = [c for p in layout.get("pages", []) for c in p.get("cards", [])]
    cards += (layout.get("dashboard") or {}).get("cards", [])
    return {c.get("id") for c in cards}


def card_entities(card: dict) -> list[str]:
    """Every entity the card shows: its own and those named in options (e.g. power_entity)."""
    spec = CARDS.get(card.get("card"), {}).get("options", {})
    extra = [v for k, v in (card.get("options") or {}).items()
             if spec.get(k, {}).get("type") == "entity" and isinstance(v, str)]
    return list(card.get("entities", [])) + extra


def _domains(card_type: str) -> list[str]:
    spec = CARDS[card_type]
    return spec["entities"].get("domains") or KINDS[kind_of_card(card_type)]["domains"]


FREE_SIZE = re.compile(r"^[1-6]x[1-4]$")


def size_allowed(spec: dict, size) -> bool:
    """One of the card type's sizes; for a free-size type (free_size) any WxH from its min_size
    up to the 6x4 grid."""
    if size in spec["sizes"]:
        return True
    if not (spec.get("free_size", False) and isinstance(size, str) and FREE_SIZE.match(size)):
        return False
    w, h = (int(n) for n in size.split("x"))
    mw, mh = (int(n) for n in spec.get("min_size", "1x1").split("x"))
    return w >= mw and h >= mh


def check_card(card: dict) -> str | None:
    """What makes the card invalid against the registry, or None."""
    card_type = card.get("card")
    spec = CARDS.get(card_type)
    if spec is None:
        return f"unknown card type {card_type}"
    ents = card.get("entities", [])
    lo, hi = spec["entities"]["min"], spec["entities"]["max"]
    if not lo <= len(ents) <= hi:
        return f"{card_type} takes {lo}..{hi} entities, not {len(ents)}"
    domains = _domains(card_type)
    for e in ents:
        if "*" not in domains and domain_of(e) not in domains:
            return f"{card_type} cannot show {e}"
    if "size" in card and not size_allowed(spec, card["size"]):
        return f"{card_type} has no size {card['size']}"

    options = card.get("options") or {}
    known = spec.get("options", {})
    for key in options:
        if key not in known:
            return f"{card_type} has no option {key}"
    for key, opt in known.items():
        if key not in options:
            if opt.get("required"):
                return f"{card_type} needs option {key}"
            continue
        problem = _check_option(opt, options[key])
        if problem:
            return f"{card_type} option {key}: {problem}"
    return None


def _check_option(opt: dict, value) -> str | None:
    kind = opt["type"]
    if kind == "bool":
        return None if isinstance(value, bool) else "must be true or false"
    if kind == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            return "must be a whole number"
        if value < opt.get("min", value) or value > opt.get("max", value):
            return f"must be {opt.get('min')}..{opt.get('max')}"
        return None
    if kind == "enum":
        return None if value in opt["values"] else f"must be one of {', '.join(opt['values'])}"
    if kind == "string":
        if not isinstance(value, str):
            return "must be text"
        return None if re.fullmatch(opt.get("pattern", ".*"), value) else "has the wrong format"
    if kind == "entity":
        if not isinstance(value, str) or not ENTITY.match(value):
            return "must be an entity id"
        return None if domain_of(value) in opt["domains"] else f"must be a {'/'.join(opt['domains'])} entity"
    return f"unknown option type {kind}"
