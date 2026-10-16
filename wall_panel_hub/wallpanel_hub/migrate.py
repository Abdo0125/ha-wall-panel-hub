"""Layout format versions.

v1 (up to 0.4.0): pages of tiles, one entity per tile.
v2: pages of cards (schema/cards.json): a card type, one or more entities, a size, options.

Stored v1 layouts are upgraded on read. Panels running an app older than v2 still get
v1 (see Gateway): the downgrade keeps every entity, and drops what v1 cannot express.
"""

import copy

from .cards import CARDS, KINDS, default_size, kind_of_card, next_card_id

SCHEMA = 2

# v1 tile types that are not a v2 kind.
_V1_TO_KIND = {"generic": "sensor"}
_KIND_TO_V1 = {"sensor": "generic"}


def is_current(layout: dict) -> bool:
    return layout.get("schema") == SCHEMA


def upgrade(layout: dict, previous: dict | None = None) -> dict:
    """v1 -> v2. Each tile becomes its kind's default card. When [previous] (the stored v2
    layout) has a single-entity card of the same kind for an entity, that card is kept,
    with its id, type, size and options: an older app saving its layout loses nothing."""
    if is_current(layout):
        return layout
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


def downgrade(layout: dict) -> dict:
    """v2 -> v1 for an older app: one tile per entity. App cards and options are dropped."""
    if not is_current(layout):
        return layout
    out = {k: copy.deepcopy(v) for k, v in layout.items() if k not in ("schema", "pages", "dashboard")}
    pages = []
    for page in layout["pages"]:
        tiles = []
        for card in page.get("cards", []):
            kind = kind_of_card(card["card"])
            if kind == "app" or card["card"] not in CARDS:
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
