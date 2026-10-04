"""Human-readable item properties (durability, enchantments, lore...) from a stored payload.

Pure JSON in, text out: works for listings of offline sellers and never touches game objects."""

from __future__ import annotations

import json

from ..i18n import tr
from .serializer import prettify_identifier, strip_colors

# Bedrock numeric enchantment ids (the "ench" list in an item's NBT).
_ENCH_IDS = {
    0: "protection", 1: "fire_protection", 2: "feather_falling", 3: "blast_protection",
    4: "projectile_protection", 5: "thorns", 6: "respiration", 7: "depth_strider", 8: "aqua_affinity",
    9: "sharpness", 10: "smite", 11: "bane_of_arthropods", 12: "knockback", 13: "fire_aspect",
    14: "looting", 15: "efficiency", 16: "silk_touch", 17: "unbreaking", 18: "fortune", 19: "power",
    20: "punch", 21: "flame", 22: "infinity", 23: "luck_of_the_sea", 24: "lure", 25: "frost_walker",
    26: "mending", 27: "binding", 28: "vanishing", 29: "impaling", 30: "riptide", 31: "loyalty",
    32: "channeling", 33: "multishot", 34: "piercing", 35: "quick_charge", 36: "soul_speed",
    37: "swift_sneak",
}
_ROMAN = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
MAX_LORE_LINES = 4
MAX_CONTENT_LINES = 12


def roman(n: int) -> str:
    if n <= 0 or n > 39:
        return str(n)
    out = ""
    for value, sym in _ROMAN:
        while n >= value:
            out += sym
            n -= value
    return out


def enchant_name(ident: str) -> str:
    return ident.split(":", 1)[-1].replace("_", " ").title()


def _nbt_value(node, *path):
    for key in path:
        if not isinstance(node, dict) or node.get("t") != "compound":
            return None
        node = node["v"].get(key)
    return node


def _enchants(doc: dict) -> list[tuple[str, int]]:
    found = dict((doc.get("meta") or {}).get("enchants") or {})
    if not found:  # fall back to the NBT "ench" list
        lst = _nbt_value(doc.get("nbt"), "ench")
        for entry in (lst or {}).get("v", []) if isinstance(lst, dict) else []:
            ident = _nbt_value(entry, "id")
            lvl = _nbt_value(entry, "lvl")
            if ident and lvl and ident["v"] in _ENCH_IDS:
                found[f"minecraft:{_ENCH_IDS[ident['v']]}"] = int(lvl["v"])
    return sorted(found.items())


def _damage(doc: dict) -> int | None:
    meta = doc.get("meta") or {}
    if "damage" in meta:
        return int(meta["damage"])
    node = _nbt_value(doc.get("nbt"), "Damage")
    if isinstance(node, dict) and isinstance(node.get("v"), int):
        return int(node["v"])
    return None


def container_contents(doc: dict) -> list[tuple[str, int]]:
    """Items stored inside a container item (shulker box...) as (name, total count), biggest first.
    Reads the 'Items' list of the item's own NBT; returns [] if there is none."""
    node = _nbt_value(doc.get("nbt"), "Items")
    if not isinstance(node, dict) or node.get("t") != "list":
        return []
    totals: dict[str, int] = {}
    for entry in node.get("v", []):
        name = _nbt_value(entry, "Name")
        count = _nbt_value(entry, "Count")
        if not isinstance(name, dict) or not isinstance(name.get("v"), str) or not name["v"]:
            continue  # empty slot
        n = int(count["v"]) if isinstance(count, dict) and isinstance(count.get("v"), int) else 1
        if n > 0:
            totals[name["v"]] = totals.get(name["v"], 0) + n
    return sorted(((prettify_identifier(k), v) for k, v in totals.items()), key=lambda x: (-x[1], x[0]))


def describe_item(payload: str) -> list[str]:
    """Colour-coded property lines, e.g. ['§7Durability: §a120/250 (48%)', '§7Enchantments:', ...]."""
    try:
        doc = json.loads(payload)
    except (TypeError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    lines: list[str] = []
    # Renamed item: also show what it really is (the name without the custom name).
    custom = strip_colors(str((doc.get("meta") or {}).get("display_name") or "")).strip()
    base = prettify_identifier(str(doc.get("id") or ""))
    if custom and base and custom.lower() != base.lower():
        lines.append(tr("§7Item: §f{name}", name=base))
    max_dur = int(doc.get("max_durability") or 0)
    if max_dur > 0:
        used = min(max(_damage(doc) or 0, 0), max_dur)
        left = max_dur - used
        pct = round(left * 100 / max_dur)
        colour = "§a" if pct >= 60 else "§e" if pct >= 25 else "§c"
        lines.append(tr("§7Durability: {colour}{left}/{max} ({pct}%)", colour=colour, left=left, max=max_dur, pct=pct))
    ench = _enchants(doc)
    if ench:
        lines.append(tr("§7Enchantments:"))
        lines.extend(f"  §d{enchant_name(i)} {roman(l)}" for i, l in ench)
    meta = doc.get("meta") or {}
    if meta.get("unbreakable"):
        lines.append(tr("§7Unbreakable"))
    lore = [str(x) for x in meta.get("lore") or []]
    if lore:
        lines.append(tr("§7Lore:"))
        lines.extend("  §8" + x[:48] for x in lore[:MAX_LORE_LINES])
        if len(lore) > MAX_LORE_LINES:
            lines.append(tr("  §8... +{n} more", n=len(lore) - MAX_LORE_LINES))
    contents = container_contents(doc)
    if contents:
        lines.append(tr("§7Contents:"))
        lines.extend(f"  §f{n}x §7{name}" for name, n in contents[:MAX_CONTENT_LINES])
        if len(contents) > MAX_CONTENT_LINES:
            lines.append(tr("  §8... +{n} more", n=len(contents) - MAX_CONTENT_LINES))
    return lines


def short_tags(payload: str) -> str:
    """Tiny suffix for list buttons: '§d✦' enchanted, plus durability percent when damaged."""
    try:
        doc = json.loads(payload)
    except (TypeError, ValueError):
        return ""
    out = ""
    max_dur = int(doc.get("max_durability") or 0)
    used = _damage(doc) or 0
    if max_dur > 0 and used > 0:
        out += f" §8{round((max_dur - min(used, max_dur)) * 100 / max_dur)}%"
    if _enchants(doc):
        out += " §d✦"
    return out
