"""What a panel may change in its own layout, enforced by the hub.

The panel's editor hides what is not allowed; this is the check that counts. On the
panel nothing is ever added or removed (entities, cards, pages): that is done in the
add-on. Depending on the role's capabilities (config.schema.json, access):

- edit_cards: a card's shape (same kind), size, label, icon, confirmation, options;
- reorder: the order of pages, and of cards within their page.
"""

from .cards import CARDS, kind_of_card

CARD_FIELDS = ("card", "size", "label", "icon", "confirm", "options")


class EditRefused(Exception):
    pass


def role_caps(effective: dict, role: str) -> set[str]:
    return set(effective["access"].get(role, []))


def check_panel_edit(old: dict, new: dict, caps: set[str]) -> None:
    """Raises EditRefused with the first change the capabilities do not allow."""
    old_pages = {p["id"]: p for p in old["pages"]}
    new_pages = {p["id"]: p for p in new["pages"]}
    if set(old_pages) != set(new_pages):
        raise EditRefused("pages can only be added or removed in the add-on")
    if [p["id"] for p in old["pages"]] != [p["id"] for p in new["pages"]] and "reorder" not in caps:
        raise EditRefused("reordering pages is not allowed on this panel")

    for pid, new_page in new_pages.items():
        old_page = old_pages[pid]
        for key in ("title", "icon"):
            if old_page.get(key) != new_page.get(key):
                raise EditRefused("pages are renamed in the add-on")
        old_cards = {c["id"]: c for c in old_page["cards"]}
        new_cards = {c["id"]: c for c in new_page["cards"]}
        if set(old_cards) != set(new_cards):
            raise EditRefused("cards can only be added, removed or moved to another page in the add-on")
        if [c["id"] for c in old_page["cards"]] != [c["id"] for c in new_page["cards"]] and "reorder" not in caps:
            raise EditRefused("reordering cards is not allowed on this panel")
        moved = any(card.get("pos") != old_cards[cid].get("pos") for cid, card in new_cards.items())
        if moved and "reorder" not in caps:
            raise EditRefused("moving cards is not allowed on this panel")

        for cid, card in new_cards.items():
            before = old_cards[cid]
            if card.get("entities") != before.get("entities"):
                raise EditRefused(f"card {cid}: entities are chosen in the add-on")
            if kind_of_card(card["card"]) != kind_of_card(before["card"]):
                raise EditRefused(f"card {cid}: a card keeps its kind")
            if _entity_options(card) != _entity_options(before):
                raise EditRefused(f"card {cid}: entities are chosen in the add-on")
            changed = any(card.get(k) != before.get(k) for k in CARD_FIELDS)
            if changed and "edit_cards" not in caps:
                raise EditRefused("editing cards is not allowed on this panel")


def _entity_options(card: dict) -> dict:
    spec = CARDS.get(card["card"], {}).get("options", {})
    return {k: v for k, v in (card.get("options") or {}).items() if k.endswith("_entity") or spec.get(k, {}).get("type") == "entity"}
