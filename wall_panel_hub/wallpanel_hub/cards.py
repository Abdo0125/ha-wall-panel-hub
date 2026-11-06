"""Card registry (schema/cards.json): which cards exist and what each one accepts.

The editor receives the registry as is and builds its card picker and property fields
from it. The app mirrors the kinds in CardKind.kt.
"""

import json
import re
from pathlib import Path

REGISTRY: dict = json.loads((Path(__file__).parent / "schema" / "cards.json").read_text(encoding="utf-8"))
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
    """The default card for an entity."""
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


FREE_SIZE = re.compile(r"^([1-9]|1[0-2])x[1-8]$")


def size_allowed(spec: dict, size) -> bool:
    """One of the card type's sizes; for a free-size type (free_size) any WxH from its min_size
    up to the 12x8 grid."""
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


def offered_options(card: dict, features_of) -> dict:
    """What a card's settings offer, for every card type: the panel and the designer draw this and
    decide nothing. {key: {"values": [...], "enabled": [...]}}, the values only for an option that
    has some, "enabled" only for an enum drawn from a set ("of": the values chosen there).
    [features_of] gives an entity's features (catalog.features).

    An option is left out when it means nothing for this card: "requires" a feature of its device,
    of any of its devices with "any_device", of all of them with "all_devices"; "not_empty": the set
    it depends on has values chosen.
    "or_empty" offers it anyway when that set was emptied: a dimmer made a plain switch takes a
    plain switch's options. "from_size" and "to_size": the card's size is between them, each way.
    "not_when": a bool option offered and on does what this one would. "only_when": this one is
    about what a bool option offered and on shows. What is stored for an option left out stays
    stored: a device may only be away, and a setting is never lost for that.
    """
    card_spec = CARDS.get(card.get("card"), {})
    spec = card_spec.get("options", {})
    entities = card.get("entities", [])
    stored = card.get("options") or {}
    alone = len(entities) == 1
    size = _cells(card.get("size") or card_spec.get("default_size", "1x1"))

    def can(opt: dict) -> bool:
        feature = opt["requires"]
        if opt.get("any_device"):
            return any(feature in features_of(e) for e in entities)
        if opt.get("all_devices"):
            return bool(entities) and all(feature in features_of(e) for e in entities)
        return alone and feature in features_of(entities[0])

    def fit(opt: dict) -> list:
        needs = opt.get("value_requires", {})
        return [v for v in opt["values"] if v not in needs or not alone or needs[v] in features_of(entities[0])]

    out: dict = {}
    for key, opt in spec.items():
        if opt.get("required"):
            continue
        emptied = "or_empty" in opt and stored.get(opt["or_empty"]) == []
        if "requires" in opt and not emptied and not can(opt):
            continue
        if "not_empty" in opt and stored.get(opt["not_empty"]) == []:
            continue
        if "from_size" in opt and not all(a <= b <= c for a, b, c in zip(_cells(opt["from_size"]), size, _cells(opt["to_size"]))):
            continue
        if opt["type"] == "set":
            values = fit(opt)
            if values:
                out[key] = {"values": values}
        elif "of" in opt:
            values = fit(spec[opt["of"]])
            enabled = [v for v in stored.get(opt["of"], values) if v in values]
            if len(enabled) > 1:
                out[key] = {"values": values, "enabled": enabled}
        elif "values" in opt:
            out[key] = {"values": opt["values"]}
        else:
            out[key] = {}
    def showing(other: str) -> bool:
        return other in out and stored.get(other, spec[other].get("default"))

    for key, opt in spec.items():
        if ("not_when" in opt and showing(opt["not_when"])) or ("only_when" in opt and not showing(opt["only_when"])):
            out.pop(key, None)
    return out


def _cells(size: str) -> tuple[int, int]:
    width, height = size.split("x")
    return int(width), int(height)


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
    if kind == "set":
        # Some of the values, each once (none: e.g. a light card with no pad).
        if not isinstance(value, list) or len(set(map(str, value))) != len(value):
            return "must be a list of distinct values"
        bad = [v for v in value if v not in opt["values"]]
        return f"must be among {', '.join(opt['values'])}" if bad else None
    if kind == "entity":
        if not isinstance(value, str) or not ENTITY.match(value):
            return "must be an entity id"
        return None if domain_of(value) in opt["domains"] else f"must be a {'/'.join(opt['domains'])} entity"
    return f"unknown option type {kind}"
