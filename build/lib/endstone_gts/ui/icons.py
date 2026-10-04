"""Form button icons.

Bedrock forms accept an icon as a resource-pack texture path ("textures/items/diamond"). Vanilla paths
work on every client with no resource pack. Item icons come from ``data/item_icons.json`` (generated from
Mojang's bedrock-samples by ``tools/build_icons.py``); unknown items simply get no icon. Blocks show their
flat face texture because forms cannot render the 3D inventory icon. Disable with ``[display] icons = false``.
"""

from __future__ import annotations

import json
from pathlib import Path

MENU = {
    "browse": "textures/items/compass_item",
    "sell": "textures/items/emerald",
    "listings": "textures/items/book_writable",
    "history": "textures/items/clock_item",
    "close": "textures/ui/cancel",
    "back": "textures/ui/arrow_left",
    "previous": "textures/ui/arrow_left",
    "next": "textures/ui/arrow_right",
    "search": "textures/ui/magnifyingGlass",
    "clear": "textures/ui/refresh",
    "buy": "textures/items/emerald",
    "confirm": "textures/ui/check",
    "cancel": "textures/ui/cancel",
    "ok": "textures/ui/check",
    "cancel_listing": "textures/ui/trash_default",
    "reclaim": "textures/blocks/chest_front",
    "active": "textures/items/gold_ingot",
    "sold": "textures/items/emerald",
    "cancelled": "textures/ui/cancel",
    "expired": "textures/items/clock_item",
}

_enabled = True
_item_icons: dict[str, str] | None = None


def configure(enabled: bool) -> None:
    global _enabled
    _enabled = bool(enabled)


def _load() -> dict[str, str]:
    global _item_icons
    if _item_icons is None:
        try:
            _item_icons = json.loads((Path(__file__).parent.parent / "data" / "item_icons.json").read_text())
        except (OSError, ValueError):
            _item_icons = {}
    return _item_icons


def menu(name: str) -> str | None:
    return MENU.get(name) if _enabled else None


def item(identifier: str) -> str | None:
    """Texture path for an item identifier like 'minecraft:diamond_pickaxe', or None."""
    if not _enabled or not isinstance(identifier, str) or ":" not in identifier:
        return None
    namespace, name = identifier.split(":", 1)
    if namespace != "minecraft":
        return None
    return _load().get(name)
