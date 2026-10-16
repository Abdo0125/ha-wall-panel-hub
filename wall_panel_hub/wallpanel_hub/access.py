"""What a panel may change in its own layout, enforced by the hub.

The panel's editor hides what is not allowed; this is the check that counts. On the
panel nothing is ever added or removed (entities, cards, pages): that is done in the
add-on. Depending on the role's capabilities (config.schema.json, access):

- edit_cards: a card's shape (same kind), size, label, icon, confirmation, options;
- reorder: the order of pages, and of cards within their page;
- dashboard: my dashboard, from what the panel already has;
- add_widgets: app and widget cards added to or removed from the rooms.

A widget's entity comes from the project's sources; an app must be allowed on the panel.
"""

from .cards import CARDS, kind_of_card

CARD_FIELDS = ("card", "size", "label", "icon", "confirm", "options")


class EditRefused(Exception):
    pass


def role_caps(effective: dict, role: str) -> set[str]:
    return set(effective["access"].get(role, []))


# Cards a panel may add or remove in its rooms, with add_widgets.
ADDABLE = ("app", "widget")


def check_panel_edit(old: dict, new: dict, caps: set[str], allowed_apps: set[str] | None = None,
                     sources: set[str] | None = None) -> None:
    """Raises EditRefused with the first change the capabilities do not allow."""
    allowed_apps, sources = allowed_apps or set(), sources or set()
    _check_dashboard(old, new, caps, allowed_apps, sources)
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
        added = [c for cid, c in new_cards.items() if cid not in old_cards]
        removed = [c for cid, c in old_cards.items() if cid not in new_cards]
        if any(kind_of_card(c["card"]) not in ADDABLE for c in added + removed):
            raise EditRefused("cards can only be added, removed or moved to another page in the add-on")
        if (added or removed) and "add_widgets" not in caps:
            raise EditRefused("adding apps and widgets is not allowed on this panel")
        for card in added:
            _check_new(card, allowed_apps, sources, "this room")
        kept = [cid for cid in (c["id"] for c in old_page["cards"]) if cid in new_cards]
        if kept != [c["id"] for c in new_page["cards"] if c["id"] in old_cards] and "reorder" not in caps:
            raise EditRefused("reordering cards is not allowed on this panel")
        moved = _moved(old_page["cards"], new_page["cards"], pinning=bool(added or removed))
        if moved and "reorder" not in caps:
            raise EditRefused("moving cards is not allowed on this panel")

        for cid, card in new_cards.items():
            if cid not in old_cards:
                continue
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


def _moved(old_cards: list[dict], new_cards: list[dict], pinning: bool) -> bool:
    """A card took another place. When an app or widget is added or removed, the panel pins
    the other cards where they show so nothing shifts: a card given its first place then is
    not a move."""
    before = {c["id"]: c for c in old_cards}
    for card in new_cards:
        old = before.get(card["id"])
        if old is None or card.get("pos") == old.get("pos"):
            continue
        if pinning and old.get("pos") is None:
            continue
        return True
    return False


def _check_new(card: dict, allowed_apps: set[str], sources: set[str], where: str) -> None:
    kind = kind_of_card(card["card"])
    if kind == "app" and (card.get("options") or {}).get("package") not in allowed_apps:
        raise EditRefused(f"card {card['id']}: this app is not allowed on this panel")
    if kind == "widget" and not set(card.get("entities", [])) <= sources:
        raise EditRefused(f"card {card['id']}: a widget shows the project's sources only")


def _check_dashboard(old: dict, new: dict, caps: set[str], allowed_apps: set[str], sources: set[str]) -> None:
    """My dashboard, changed on the panel: with the dashboard permission, and only from what
    the panel already has (its rooms' entities, the project's sources, allowed apps, the
    panel's own cards)."""
    before = (old.get("dashboard") or {}).get("cards")
    after = (new.get("dashboard") or {}).get("cards")
    if before == after or after is None:
        return
    if "dashboard" not in caps:
        raise EditRefused("changing my dashboard is not allowed on this panel")
    known = {e for p in new["pages"] for c in p["cards"] for e in c.get("entities", [])}
    known |= {e for c in before or [] for e in c.get("entities", [])}
    known |= sources
    for card in after:
        if not set(card.get("entities", [])) <= known:
            raise EditRefused(f"card {card['id']}: my dashboard only takes devices already on this panel")
        if kind_of_card(card["card"]) == "app" and (card.get("options") or {}).get("package") not in allowed_apps:
            raise EditRefused(f"card {card['id']}: this app is not allowed on this panel")


def _entity_options(card: dict) -> dict:
    spec = CARDS.get(card["card"], {}).get("options", {})
    return {k: v for k, v in (card.get("options") or {}).items() if k.endswith("_entity") or spec.get(k, {}).get("type") == "entity"}
