"""Layout format versions.

v1 (up to 0.4.0): pages of tiles, one entity per tile.
v2: pages of cards (schema/cards.json): a card type, one or more entities, a size, options.
v3 (app 0.48.0): one card type per function (light, cover...): what v2 chose by type (no
dimming, live, speeds) is an option, what the device can do comes from Home Assistant.

Stored older layouts are upgraded on read. Panels running an older app still get the
format they read (see Gateway): the downgrade keeps every card it can express.
"""

import copy

from .cards import KINDS, default_size, kind_of_card, next_card_id

SCHEMA = 3

# v1 tile types that are not a v2 kind.
_V1_TO_KIND = {"generic": "sensor"}
_KIND_TO_V1 = {"sensor": "generic"}


def is_current(layout: dict) -> bool:
    return layout.get("schema") == SCHEMA


# v2 card types, each as a v3 type and the options it stood for (None: the option dropped,
# it did nothing on the panels).
_V2_TO_V3 = {
    "light.toggle": ("light", {"dimming": False}),
    "light.dimmer": ("light", {"min_brightness": None, "show_percent": None}),
    "light.color": ("light", {}),
    "light.group": ("light", {"dimmer": None}),
    "switch.toggle": ("switch", {}),
    "switch.outlet": ("switch", {}),
    "cover.buttons": ("cover", {}),
    "cover.position": ("cover", {}),
    "cover.tilt": ("cover", {}),
    "cover.group": ("cover", {}),
    "climate.compact": ("climate", {}),
    "climate.thermostat": ("climate", {"step": None}),
    "fan.toggle": ("fan", {"speeds": False}),
    "fan.speed": ("fan", {}),
    "lock.lock": ("lock", {}),
    "scene.action": ("scene", {}),
    "script.action": ("script", {}),
    "button.action": ("button", {}),
    "sensor.value": ("sensor", {}),
    "sensor.binary": ("sensor", {}),
    "camera.view": ("camera", {}),
    "camera.live": ("camera", {"live": True}),
}
# Options v3 cards have and v2 ones did not: dropped on the way down.
_V3_ONLY = ("dimming", "speeds", "live")


def _card_up(card: dict) -> dict:
    mapped = _V2_TO_V3.get(card.get("card"))
    if mapped is None:
        return card
    card_type, options = mapped
    out = {**card, "card": card_type}
    opts = dict(card.get("options") or {})
    for key, value in options.items():
        if value is None:
            opts.pop(key, None)
        else:
            opts[key] = value
    if opts:
        out["options"] = opts
    else:
        out.pop("options", None)
    return out


def _card_down(card: dict) -> dict:
    """A v3 card as the v2 type nearest to it."""
    card_type = card.get("card")
    opts = card.get("options") or {}
    many = len(card.get("entities", [])) > 1
    old = {
        "light": "light.group" if many else "light.toggle" if opts.get("dimming") is False else "light.dimmer",
        "switch": "switch.outlet" if opts.get("power_entity") else "switch.toggle",
        "cover": "cover.group" if many else "cover.buttons",
        "climate": "climate.thermostat" if card.get("size") == "2x2" else "climate.compact",
        "fan": "fan.toggle" if opts.get("speeds") is False else "fan.speed",
        "lock": "lock.lock",
        "scene": "scene.action",
        "script": "script.action",
        "button": "button.action",
        "sensor": "sensor.value",
        "camera": "camera.live" if opts.get("live") else "camera.view",
    }.get(card_type)
    if old is None:
        return card
    out = {**card, "card": old}
    kept = {k: v for k, v in opts.items() if k not in _V3_ONLY}
    if kept:
        out["options"] = kept
    else:
        out.pop("options", None)
    return out


def _cards(layout: dict, fn) -> dict:
    out = {**layout, "pages": [{**p, "cards": [fn(c) for c in p.get("cards", [])]} for p in layout.get("pages", [])]}
    if layout.get("dashboard"):
        out["dashboard"] = {**layout["dashboard"], "cards": [fn(c) for c in layout["dashboard"].get("cards", [])]}
    return out


def upgrade(layout: dict, previous: dict | None = None) -> dict:
    """To the current format. v2: each card as its v3 type with the options its type stood
    for. v1: each tile becomes its kind's default card; when [previous] (the stored layout)
    has a single-entity card of the same kind for an entity, that card is kept, with its id,
    type, size and options: an older app saving its layout loses nothing."""
    if is_current(layout):
        return layout
    if layout.get("schema") == 2:
        return {**_cards(copy.deepcopy(layout), _card_up), "schema": SCHEMA}
    known: dict[str, dict] = {}
    for page in (previous or {}).get("pages", []):
        for card in page.get("cards", []):
            if len(card.get("entities", [])) == 1:
                known.setdefault(card["entities"][0], card)

    out = {k: copy.deepcopy(v) for k, v in layout.items() if k != "pages"}
    out["schema"] = SCHEMA
    taken: set[str] = set()
    pages = []
    for page in layout.get("pages", []):
        cards = []
        for tile in page.get("tiles", []):
            entity = tile.get("entity", "")
            kind = _V1_TO_KIND.get(tile.get("type"), tile.get("type"))
            if kind not in KINDS:
                kind = "sensor"
            old = known.get(entity)
            if old and kind_of_card(old["card"]) == kind and old["id"] not in taken:
                card = {k: copy.deepcopy(v) for k, v in old.items() if k not in ("label", "icon", "confirm")}
            else:
                card_type = KINDS[kind]["default"]
                card = {"id": next_card_id(taken | _reserved(known, taken)), "card": card_type,
                        "entities": [entity], "size": default_size(card_type)}
            for key in ("label", "icon", "confirm"):
                if key in tile:
                    card[key] = tile[key]
            taken.add(card["id"])
            cards.append(card)
        new_page = {k: copy.deepcopy(v) for k, v in page.items() if k != "tiles"}
        new_page["cards"] = cards
        pages.append(new_page)
    out["pages"] = pages
    return out


def _reserved(known: dict[str, dict], taken: set[str]) -> set[str]:
    # Ids of cards that may still be reused further down must not be handed out now.
    return {c["id"] for c in known.values()} - taken


def downgrade(layout: dict, schema: int = 1) -> dict:
    """The current format as an older app reads it ([schema] 2 or 1)."""
    if not is_current(layout) or schema >= SCHEMA:
        return layout
    v2 = {**_cards(copy.deepcopy(layout), _card_down), "schema": 2}
    return v2 if schema == 2 else _v1(v2)


def _v1(layout: dict) -> dict:
    """v2 -> v1: one tile per entity. App cards and options are dropped."""
    out = {k: copy.deepcopy(v) for k, v in layout.items() if k not in ("schema", "pages", "dashboard")}
    pages = []
    for page in layout["pages"]:
        tiles = []
        for card in page.get("cards", []):
            kind = kind_of_card(card["card"])
            if kind in ("app", "widget") or kind not in KINDS:
                continue
            ents = card.get("entities", [])
            for entity in ents:
                tile = {"type": _KIND_TO_V1.get(kind, kind), "entity": entity}
                if len(ents) == 1 and "label" in card:
                    tile["label"] = card["label"]
                for key in ("icon", "confirm"):
                    if key in card:
                        tile[key] = card[key]
                tiles.append(tile)
        new_page = {k: copy.deepcopy(v) for k, v in page.items() if k != "cards"}
        new_page["tiles"] = tiles
        pages.append(new_page)
    out["pages"] = pages
    return out
