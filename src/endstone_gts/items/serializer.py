"""ItemStack <-> JSON payload.

Strategy (only documented Endstone API is used):

1. ``ItemStack.nbt`` (documented: "Gets or sets the NBT compound tag of this item stack") is
   encoded tag-by-tag with type information (see ``nbt_codec``). This carries custom names,
   lore, enchantments, durability, custom components and any other item data the game keeps.
2. ``ItemStack.type.id``, ``amount`` and ``data`` are stored explicitly.
3. ``ItemMeta`` (display name, lore, damage, enchantments, unbreakable, repair cost) is stored
   as a *fallback* and for display/search. It is only used to rebuild an item when NBT could
   not be read; such a payload is flagged ``lossy``.

Safety net: ``verify_roundtrip`` rebuilds the item from the payload, serializes it again and
requires an identical payload. The sell flow refuses to take an item from a player unless
this passes, so a serializer limitation can never silently destroy an item.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from . import nbt_codec

PAYLOAD_VERSION = 1
_ID_RE = re.compile(r"^[a-z0-9_.\-]+:[a-z0-9_./\-]+$")
_COLOR_RE = re.compile(r"§.")
# Bedrock stores the stack size inside the item NBT as "Count"; the amount is kept in its own
# field so it is stripped from the NBT (otherwise a partial-stack listing would restore wrongly).
_NBT_AMOUNT_KEYS = ("Count",)


class SerializationError(Exception):
    pass


@dataclass(frozen=True)
class SerializedItem:
    payload: str  # canonical JSON, what goes into listings.item_data
    identifier: str
    display_name: str
    amount: int
    search_text: str
    lossy: bool  # True if NBT could not be read and only metadata was captured


def prettify_identifier(identifier: str) -> str:
    name = identifier.split(":", 1)[-1].replace("_", " ").replace("/", " ")
    return name.title()


def strip_colors(text: str) -> str:
    return _COLOR_RE.sub("", text)


def _default_stack_factory() -> Callable[..., Any]:
    from endstone.inventory import ItemStack  # needs the Endstone runtime

    return ItemStack


def _canonical(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class ItemSerializer:
    def __init__(self, stack_factory: Callable[..., Any] | None = None):
        self._factory = stack_factory

    @property
    def _make(self) -> Callable[..., Any]:
        if self._factory is None:
            self._factory = _default_stack_factory()
        return self._factory

    # -- serialize -----------------------------------------------------------------
    def serialize(self, stack: Any, amount: int | None = None) -> SerializedItem:
        if stack is None:
            raise SerializationError("No item")
        try:
            identifier = str(stack.type.id)
            real_amount = int(stack.amount)
            data = int(stack.data)
        except Exception as exc:
            raise SerializationError(f"Cannot read item: {exc}") from exc
        amount = real_amount if amount is None else int(amount)
        if amount < 1:
            raise SerializationError("Amount must be at least 1")
        if not _ID_RE.match(identifier):
            raise SerializationError(f"Unsupported item identifier {identifier!r}")

        lossy = False
        nbt_doc = None
        try:
            tag = stack.nbt
            if tag is not None:
                for key in _NBT_AMOUNT_KEYS:
                    if key in tag:
                        tag = _without(tag, key)
                nbt_doc = nbt_codec.encode(tag)
        except Exception:
            lossy = True
            nbt_doc = None

        meta, meta_complete = self._read_meta(stack)
        if nbt_doc is None and not meta_complete:
            lossy = True
        display_name = meta.get("display_name") or prettify_identifier(identifier)
        try:
            max_durability = int(stack.type.max_durability)
        except Exception:
            max_durability = 0

        doc = {
            "v": PAYLOAD_VERSION,
            "id": identifier,
            "amount": amount,
            "data": data,
            "nbt": nbt_doc,
            "meta": meta,
            "max_durability": max_durability,
            "lossy": lossy,
        }
        search_text = " ".join(
            [identifier, strip_colors(display_name), prettify_identifier(identifier)]
        ).lower()
        return SerializedItem(_canonical(doc), identifier, display_name, amount, search_text, lossy)

    @staticmethod
    def _read_meta(stack: Any) -> tuple[dict, bool]:
        meta: dict[str, Any] = {}
        complete = True
        try:
            m = stack.item_meta
        except Exception:
            return meta, False
        if m is None:
            return meta, True
        try:
            if m.has_display_name:
                meta["display_name"] = str(m.display_name)
            if m.has_lore:
                meta["lore"] = [str(x) for x in m.lore]
            if m.has_damage:
                meta["damage"] = int(m.damage)
            if m.has_repair_cost:
                meta["repair_cost"] = int(m.repair_cost)
            if m.is_unbreakable:
                meta["unbreakable"] = True
            if m.has_enchants:
                enchants: dict[str, int] = {}
                for ench, level in m.enchants.items():
                    ench_id = getattr(ench, "id", None)
                    if ench_id is None:
                        complete = False
                        continue
                    enchants[str(ench_id)] = int(level)
                meta["enchants"] = dict(sorted(enchants.items()))
        except Exception:
            complete = False
        return meta, complete

    # -- deserialize ---------------------------------------------------------------
    def parse(self, payload: str) -> dict:
        try:
            doc = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise SerializationError("Corrupt item payload") from exc
        if not isinstance(doc, dict) or doc.get("v") != PAYLOAD_VERSION:
            raise SerializationError("Unsupported item payload version")
        identifier = doc.get("id")
        amount = doc.get("amount")
        if not isinstance(identifier, str) or not _ID_RE.match(identifier):
            raise SerializationError("Corrupt item identifier")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
            raise SerializationError("Corrupt item amount")
        return doc

    def deserialize(self, payload: str, amount: int | None = None) -> Any:
        doc = self.parse(payload)
        amount = doc["amount"] if amount is None else int(amount)
        if amount < 1:
            raise SerializationError("Amount must be at least 1")
        try:
            stack = self._make(doc["id"], amount, int(doc.get("data", 0)))
            if doc.get("nbt") is not None:
                stack.nbt = nbt_codec.decode(doc["nbt"])
            # NBT is only the item's user-data tag; durability, enchantments, names etc. are managed
            # through ItemMeta. Whatever the NBT did not restore is applied explicitly from the payload.
            self._reconcile_meta(stack, doc.get("meta") or {})
            stack.amount = amount  # NBT may have carried its own count; the amount field wins
        except SerializationError:
            raise
        except Exception as exc:
            raise SerializationError(f"Cannot rebuild item: {exc}") from exc
        return stack

    def _reconcile_meta(self, stack: Any, wanted: dict) -> None:
        if not wanted:
            return
        current, _ = self._read_meta(stack)
        diff = {k: v for k, v in wanted.items() if current.get(k) != v}
        if diff:
            self._apply_meta(stack, diff)

    @staticmethod
    def _apply_meta(stack: Any, meta: dict) -> None:
        if not meta:
            return
        m = stack.item_meta
        if "display_name" in meta:
            m.display_name = meta["display_name"]
        if "lore" in meta:
            m.lore = list(meta["lore"])
        if "damage" in meta:
            m.damage = int(meta["damage"])
        if "repair_cost" in meta:
            m.repair_cost = int(meta["repair_cost"])
        if meta.get("unbreakable"):
            m.is_unbreakable = True
        for ench_id, level in (meta.get("enchants") or {}).items():
            m.add_enchant(ench_id, int(level), True)
        if not stack.set_item_meta(m):
            raise SerializationError("Item meta rejected")

    # -- verification --------------------------------------------------------------
    def verify_roundtrip(self, item: SerializedItem) -> bool:
        """True only if payload -> ItemStack -> payload reproduces the identical payload."""
        try:
            rebuilt = self.deserialize(item.payload)
            again = self.serialize(rebuilt, amount=item.amount)
        except Exception:
            return False
        return again.payload == item.payload and not again.lossy

    def verify_delivered(self, placed: Any, payload: str) -> bool:
        """Did the game really keep the item? ``placed`` is read back from the inventory.

        Compares identity, amount, ItemMeta (durability, enchantments, name, lore...) exactly and the
        stored NBT as a subset (the game may add its own tags, but must not drop or change ours)."""
        try:
            want = self.parse(payload)
            got = json.loads(self.serialize(placed, amount=want["amount"]).payload)
        except Exception:
            return False
        for key in ("id", "data", "amount", "meta"):
            if got.get(key) != want.get(key):
                return False
        return _nbt_subset(want.get("nbt"), got.get("nbt"))

    def is_special(self, payload: str) -> bool:
        """True if the item carries data (NBT/meta) that must be verified after delivery."""
        doc = self.parse(payload)
        nbt = doc.get("nbt")
        has_nbt = isinstance(nbt, dict) and bool(nbt.get("v"))
        return has_nbt or bool(doc.get("meta"))

    def describe(self, payload: str) -> tuple[str, str, int]:
        """(identifier, display name, amount) straight from a stored payload, no game objects."""
        doc = self.parse(payload)
        name = (doc.get("meta") or {}).get("display_name") or prettify_identifier(doc["id"])
        return doc["id"], name, doc["amount"]


def _nbt_subset(want: Any, got: Any) -> bool:
    if want is None:
        return True
    if not isinstance(want, dict) or not isinstance(got, dict) or want.get("t") != got.get("t"):
        return False
    if want["t"] == "compound":
        return all(k in got["v"] and _nbt_subset(v, got["v"][k]) for k, v in want["v"].items())
    return want.get("v") == got.get("v")


def _without(tag: Any, key: str) -> Any:
    """Copy of a CompoundTag minus one key (never mutates the live item's tag)."""
    from endstone import nbt

    copy = nbt.CompoundTag()
    for k in tag.keys():
        if k != key:
            copy[k] = tag[k]
    return copy
