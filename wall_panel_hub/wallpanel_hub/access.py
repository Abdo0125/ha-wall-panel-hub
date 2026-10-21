"""What a panel may change in its own layout, enforced by the hub.

The panel's editor hides what is not allowed; this is the check that counts. Depending on
the role's capabilities (config.schema.json, access; docs/parity.md):

- rooms: rooms added (with their devices), removed, renamed, given another icon or floor;
  the floors shown in the rooms menu (empty ones too);
- devices: device cards added from anywhere in HA, removed or moved to another room, in the
  rooms and on my dashboard;

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
ADDABLE = ("app", "widget", "room")


def check_panel_edit(old: dict, new: dict, caps: set[str], allowed_apps: set[str] | None = None,
                     sources: set[str] | None = None) -> None:
    """Raises EditRefused with the first change the capabilities do not allow."""
    allowed = Allowed(allowed_apps or set(), sources or set(), {p["id"] for p in new["pages"]})
    _check_dashboard(old, new, caps, allowed)
    old_pages = {p["id"]: p for p in old["pages"]}
    new_pages = {p["id"]: p for p in new["pages"]}
    if set(old_pages) != set(new_pages) and "rooms" not in caps:
        raise EditRefused("adding or removing rooms is not allowed on this panel")
    if (old.get("floors") or []) != (new.get("floors") or []) and "rooms" not in caps:
        raise EditRefused("adding or removing floors needs the rooms permission")
    # The rooms kept, in their order: a room added at the end moves no other.
    kept_before = [p["id"] for p in old["pages"] if p["id"] in new_pages]
    kept_after = [p["id"] for p in new["pages"] if p["id"] in old_pages]
    if kept_before != kept_after and "reorder" not in caps:
        raise EditRefused("reordering pages is not allowed on this panel")

    for pid, new_page in new_pages.items():
        old_page = old_pages.get(pid)
        if old_page is None:
            continue  # a new room, with whatever devices it was given
        for key in ("title", "icon", "floor", "tile"):
            if old_page.get(key) != new_page.get(key) and "rooms" not in caps:
                raise EditRefused("renaming rooms is not allowed on this panel")
        old_cards = {c["id"]: c for c in old_page["cards"]}
        new_cards = {c["id"]: c for c in new_page["cards"]}
        added = [c for cid, c in new_cards.items() if cid not in old_cards]
        removed = [c for cid, c in old_cards.items() if cid not in new_cards]
        if any(kind_of_card(c["card"]) not in ADDABLE for c in added + removed) and "devices" not in caps:
            raise EditRefused("adding, removing or moving devices is not allowed on this panel")
        if any(kind_of_card(c["card"]) in ADDABLE for c in added + removed) and "add_widgets" not in caps:
            raise EditRefused("adding apps and widgets is not allowed on this panel")
        for card in added:
            if kind_of_card(card["card"]) in ADDABLE:
                _check_new(card, allowed)
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
            if card.get("entities") != before.get("entities") and "devices" not in caps:
                raise EditRefused(f"card {cid}: changing a card's device is not allowed on this panel")
            if kind_of_card(card["card"]) != kind_of_card(before["card"]):
                raise EditRefused(f"card {cid}: a card keeps its kind")
            if _entity_options(card) != _entity_options(before) and "devices" not in caps:
                raise EditRefused(f"card {cid}: changing a card's device is not allowed on this panel")
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


class Allowed:
    """What the panel may add: its allowed apps (and their widgets), the project's sources,
    room cards of its own rooms."""

    def __init__(self, apps: set[str], sources: set[str], pages: set[str] | None = None):
        self.apps, self.sources, self.pages = apps, sources, pages or set()


def _target(card: dict) -> str | None:
    """What an app or Android widget card opens or shows: its package or provider."""
    options = card.get("options") or {}
    return options.get("provider") if card["card"] == "widget.android" else options.get("package")


def _check_new(card: dict, allowed: Allowed) -> None:
    kind = kind_of_card(card["card"])
    if kind == "app" and _target(card) not in allowed.apps:
        raise EditRefused(f"card {card['id']}: this app is not allowed on this panel")
    if card["card"] == "widget.android" and (_target(card) or "").split("/")[0] not in allowed.apps:
        raise EditRefused(f"card {card['id']}: this widget's app is not allowed on this panel")
    if kind == "room" and (card.get("options") or {}).get("page") not in allowed.pages:
        raise EditRefused(f"card {card['id']}: a room card shows one of this panel's rooms")
    if kind == "widget" and not set(card.get("entities", [])) <= allowed.sources:
        raise EditRefused(f"card {card['id']}: a widget shows the project's sources only")


def _check_dashboard(old: dict, new: dict, caps: set[str], allowed: Allowed) -> None:
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
    known |= allowed.sources
    had = {c["id"]: (c["card"], _target(c)) for c in before or []}
    for card in after:
        if "devices" not in caps and not set(card.get("entities", [])) <= known:
            raise EditRefused(f"card {card['id']}: my dashboard only takes devices already on this panel")
        # An app or Android widget added now must be allowed; one already there stays, even
        # if no longer allowed.
        if had.get(card["id"]) != (card["card"], _target(card)) and kind_of_card(card["card"]) in ("app", "widget", "room"):
            _check_new(card, allowed)


def _entity_options(card: dict) -> dict:
    spec = CARDS.get(card["card"], {}).get("options", {})
    return {k: v for k, v in (card.get("options") or {}).items() if k.endswith("_entity") or spec.get(k, {}).get("type") == "entity"}
