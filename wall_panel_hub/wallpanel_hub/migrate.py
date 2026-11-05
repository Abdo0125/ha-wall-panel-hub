"""The layout format.

v5 (add-on 0.54.0) is the first format a released add-on keeps: a layout holds what the installer
chose and nothing of what a device can do, which comes from Home Assistant through the hub. v4,
the last format before it, differs only by a card's old "dimming" option, which turned the
default of a much older format into a ban with no setting to lift it: it goes on the way up.

An older format is not kept up to date. A format newer than this add-on's is never read: it could
be anything, and saving what was understood of it would lose the rest.
"""

import copy

SCHEMA = 5
# The format the app before 0.71.0 reads: it gets this add-on's layouts as they are, under its number.
OLDEST = 4


class LayoutTooNew(ValueError):
    """Made by a newer app than this add-on knows."""


class LayoutTooOld(ValueError):
    """From before the oldest format this add-on reads."""


def is_current(layout: dict) -> bool:
    return layout.get("schema") == SCHEMA


def upgrade(layout: dict) -> dict:
    """[layout] in the current format; [LayoutTooNew] or [LayoutTooOld] when it cannot be."""
    schema = layout.get("schema")
    if schema == SCHEMA:
        return layout
    if isinstance(schema, int) and schema > SCHEMA:
        raise LayoutTooNew(f"layout format {schema} is newer than this add-on, which reads format {SCHEMA}")
    if schema != OLDEST:
        raise LayoutTooOld(f"layout format {schema} is too old for this add-on")
    out = copy.deepcopy(layout)
    out["schema"] = SCHEMA
    for page in out.get("pages", []):
        _without_dimming(page.get("cards", []))
    _without_dimming((out.get("dashboard") or {}).get("cards", []))
    return out


def _without_dimming(cards: list[dict]) -> None:
    for card in cards:
        options = card.get("options")
        if options is None:
            continue
        options.pop("dimming", None)
        if not options:
            del card["options"]


def for_app(layout: dict, schema: int) -> dict:
    """[layout] for an app that reads format [schema]."""
    return {**layout, "schema": OLDEST} if schema == OLDEST else layout
