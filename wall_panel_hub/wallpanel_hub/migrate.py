"""Layout format versions.

v1 (up to 0.4.0): pages of tiles, one entity per tile.
v2: pages of cards (schema/cards.json): a card type, one or more entities, a size, options.
v3 (app 0.48.0): one card type per function (light, cover...): what v2 chose by type (no
dimming, live, speeds) is an option, what the device can do comes from Home Assistant.
v4 (add-on 0.50.0): the fine grid. Sizes and places in cells a quarter of v3's (12x8 on a
10" panel, not 6x4): v3's are doubled, so a layout looks the same until a card is resized.

Stored older layouts are upgraded on read. Panels running an older app still get the
format they read (see Gateway): the downgrade keeps every card it can express.
"""

import copy

from .cards import CARDS, KINDS, default_size, kind_of_card, next_card_id

SCHEMA = 4

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
    """To the current format, [previous] being the stored layout (current format).
    v3: sizes and places doubled; a card an older app left as it was keeps its own.
    v2: each card as its v3 type with the options its type stood for.
    v1: each tile becomes its kind's default card; when [previous] has a single-entity card
    of the same kind for an entity, that card is kept, with its id, type, size and options:
    an older app saving its layout loses nothing."""
    if is_current(layout):
        return layout
    if layout.get("schema") == 3:
        return _fine(layout, previous)
    old = downgrade(previous, 3) if previous and is_current(previous) else previous
    return _fine(_to_v3(layout, old), previous)


def _to_v3(layout: dict, previous: dict | None) -> dict:
    if layout.get("schema") == 2:
        return {**_cards(copy.deepcopy(layout), _card_up), "schema": 3}
    known: dict[str, dict] = {}
    for page in (previous or {}).get("pages", []):
        for card in page.get("cards", []):
            if len(card.get("entities", [])) == 1:
                known.setdefault(card["entities"][0], card)

    out = {k: copy.deepcopy(v) for k, v in layout.items() if k != "pages"}
    out["schema"] = 3
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
                        "entities": [entity], "size": _half(default_size(card_type))}
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
    """The current format as an older app reads it ([schema] 3, 2 or 1)."""
    if not is_current(layout) or schema >= SCHEMA:
        return layout
    v3 = _coarse(layout)
    if schema == 3:
        return v3
    v2 = {**_cards(v3, _card_down), "schema": 2}
    return v2 if schema == 2 else _v1(v2)


# ---- v3 <-> v4: the 6x4 grid and the fine one ----

def _half(size: str) -> str:
    """A fine size as the 6x4 grid's cells that hold it (rounded up)."""
    w, h = (int(n) for n in size.split("x"))
    return f"{(w + 1) // 2}x{(h + 1) // 2}"


def _double(size: str) -> str:
    w, h = (int(n) for n in size.split("x"))
    return f"{w * 2}x{h * 2}"


def _cell(pos: dict) -> dict:
    return {"screen": pos["screen"], "col": pos["col"] // 2, "row": pos["row"] // 2}


def _tiles(page: dict) -> list[tuple[str | None, dict]]:
    """The room's cards in the rooms menu, each with the floor it shows on (None: its own)."""
    out = [(page.get("floor"), page["tile"])] if page.get("tile") else []
    return out + [(a["floor"], a["tile"]) for a in page.get("also", []) if a.get("tile")]


def _all_cards(layout: dict) -> list[dict]:
    cards = [c for p in layout.get("pages", []) for c in p.get("cards", [])]
    return cards + (layout.get("dashboard") or {}).get("cards", [])


def _default(card: dict) -> str:
    return CARDS.get(card.get("card"), {}).get("default_size", "2x2")


def _fine(layout: dict, previous: dict | None) -> dict:
    """v3 -> v4: sizes and places doubled. A card (or room card) [previous] holds whose size or
    place the older app showed as it was keeps its fine one: saving from an older app changes
    only what was changed there."""
    out = copy.deepcopy(layout)
    out["schema"] = SCHEMA
    before = {c["id"]: c for c in _all_cards(previous or {}) if "id" in c}
    for card in _all_cards(out):
        _refine(card, before.get(card.get("id")), _half(_default(card)))
    rooms = {p["id"]: p for p in (previous or {}).get("pages", [])}
    for page in out.get("pages", []):
        old = dict(_tiles(rooms[page["id"]])) if page.get("id") in rooms else {}
        for floor, tile in _tiles(page):
            _refine(tile, old.get(floor), "2x1")
    return out


def _refine(item: dict, old: dict | None, coarse_default: str) -> None:
    """[item] (v3) in fine cells; [old] (v4) kept where the v3 item is what it showed."""
    shown = item.get("size", coarse_default)
    if old and _half(old.get("size", _double(coarse_default))) == shown:
        if "size" in old:
            item["size"] = old["size"]
        else:
            item.pop("size", None)
    elif "size" in item:
        item["size"] = _double(item["size"])
    if "pos" in item:
        if old and "pos" in old and _cell(old["pos"]) == item["pos"]:
            item["pos"] = old["pos"]
        else:
            item["pos"] = {**item["pos"], "col": item["pos"]["col"] * 2, "row": item["pos"]["row"] * 2}
    elif old and "pos" in old:
        # Left unplaced going down, its cells there taken by another card: still where it was.
        item["pos"] = old["pos"]


def _coarse(layout: dict) -> dict:
    """v4 -> v3: sizes halved (rounded up), places halved (rounded down). A card whose cells
    there are taken by one placed before it is left unplaced: the older app lays it out after
    the placed ones rather than over another."""
    out = copy.deepcopy(layout)
    out["schema"] = 3
    for cards in [p.get("cards", []) for p in out.get("pages", [])] + [(out.get("dashboard") or {}).get("cards", [])]:
        _coarsen(cards, _default)
    floors: dict = {}
    for page in out.get("pages", []):
        for floor, tile in _tiles(page):
            floors.setdefault(floor, []).append(tile)
    for tiles in floors.values():
        _coarsen(tiles, lambda t: "4x2")
    return out


def _coarsen(items: list[dict], default) -> None:
    taken: set[tuple[int, int, int]] = set()
    for item in items:
        w, h = (int(n) for n in _half(item.get("size") or default(item)).split("x"))
        if "size" in item:
            item["size"] = _half(item["size"])
        if "pos" not in item:
            continue
        pos = item["pos"] = _cell(item["pos"])
        cells = {(pos["screen"], pos["col"] + x, pos["row"] + y) for x in range(w) for y in range(h)}
        if cells & taken:
            del item["pos"]
        else:
            taken |= cells


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
