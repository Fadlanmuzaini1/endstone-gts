"""Moving items between a player's inventory and GTS storage, with explicit outcomes.

Every operation reports one of three things so callers can stay dupe-safe:
  * definite success,
  * definite failure (nothing changed),
  * ambiguous (something may have changed): the caller must NOT retry or refund blindly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .serializer import ItemSerializer, SerializationError

MAIN_SLOTS = 36  # hotbar + main inventory; armor/offhand are never used for delivery


def _is_empty(item: Any) -> bool:
    if item is None:
        return True
    try:
        return int(item.amount) <= 0 or str(item.type.id) == "minecraft:air"
    except Exception:
        return True


def slot_range(inv: Any) -> range:
    return range(min(int(inv.size), MAIN_SLOTS))


def _first_empty(inv: Any) -> int | None:
    for i in slot_range(inv):
        if _is_empty(inv.get_item(i)):
            return i
    return None


def free_capacity(inv: Any, stack: Any) -> int:
    """How many items of ``stack``'s kind fit into the main inventory (read-only)."""
    max_size = max(1, int(stack.max_stack_size))
    capacity = 0
    for i in slot_range(inv):
        cur = inv.get_item(i)
        if _is_empty(cur):
            capacity += max_size
        elif cur.is_similar(stack):
            capacity += max(0, max_size - int(cur.amount))
    return capacity


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool = False
    ambiguous: bool = False  # items may have been (partially) given: do not retry/refund
    reason: str = ""


@dataclass(frozen=True)
class RemovalResult:
    removed: bool = False
    ambiguous: bool = False
    reason: str = ""


class ItemDelivery:
    def __init__(self, serializer: ItemSerializer):
        self.serializer = serializer

    def can_deliver(self, player: Any, payload: str) -> bool:
        try:
            if self.serializer.is_special(payload):  # verified items are placed in an empty slot
                return _first_empty(player.inventory) is not None
            stack = self.serializer.deserialize(payload)
            return free_capacity(player.inventory, stack) >= int(stack.amount)
        except (SerializationError, Exception):
            return False

    def deliver(self, player: Any, payload: str) -> DeliveryResult:
        """Give the stored item to ``player``. Never leaves a partial delivery behind."""
        try:
            stack = self.serializer.deserialize(payload)
        except SerializationError as exc:
            return DeliveryResult(reason=f"item cannot be rebuilt: {exc}")
        inv = player.inventory
        amount = int(stack.amount)
        try:
            if self.serializer.is_special(payload):
                return self._place_verified(inv, stack, payload)
        except Exception as exc:
            return DeliveryResult(ambiguous=True, reason=f"inventory error: {exc}")
        try:
            if free_capacity(inv, stack) < amount:
                return DeliveryResult(reason="inventory full")
            leftover = inv.add_item(stack)
        except Exception as exc:
            return DeliveryResult(ambiguous=True, reason=f"inventory error: {exc}")

        left_amount = sum(int(s.amount) for s in leftover.values()) if leftover else 0
        if left_amount == 0:
            return DeliveryResult(delivered=True)

        # The capacity pre-check was wrong (should not happen). Take back what was added.
        added = amount - left_amount
        if added <= 0:
            return DeliveryResult(reason="inventory full")
        try:
            undo = self.serializer.deserialize(payload, amount=added)
            not_removed = inv.remove_item(undo)
        except Exception as exc:
            return DeliveryResult(ambiguous=True, reason=f"rollback failed: {exc}")
        if not_removed:
            return DeliveryResult(ambiguous=True, reason="partial delivery could not be rolled back")
        return DeliveryResult(reason="inventory full")

    def _place_verified(self, inv: Any, stack: Any, payload: str) -> DeliveryResult:
        """Items with durability/enchantments/NBT: put into an empty slot, read it back and compare
        with what was stored. If the game did not keep it exactly, take it out again and fail safely."""
        slot = _first_empty(inv)
        if slot is None:
            return DeliveryResult(reason="inventory full")
        try:
            inv.set_item(slot, stack)
            placed = inv.get_item(slot)
            exact = (not _is_empty(placed)) and self.serializer.verify_delivered(placed, payload)
        except Exception as exc:
            self._clear(inv, slot)
            return DeliveryResult(ambiguous=True, reason=f"inventory error: {exc}")
        if exact:
            return DeliveryResult(delivered=True)
        if not self._clear(inv, slot):
            return DeliveryResult(ambiguous=True, reason="inexact item could not be removed again")
        return DeliveryResult(reason="the game could not restore this item exactly")

    @staticmethod
    def _clear(inv: Any, slot: int) -> bool:
        try:
            inv.set_item(slot, None)
            return _is_empty(inv.get_item(slot))
        except Exception:
            return False

    def remove_from_slot(self, player: Any, slot: int, expected_payload: str, amount: int) -> RemovalResult:
        """Remove exactly ``amount`` of the item in ``slot`` iff it still matches ``expected_payload``."""
        inv = player.inventory
        try:
            if not (0 <= slot < min(int(inv.size), MAIN_SLOTS)):
                return RemovalResult(reason="invalid slot")
            current = inv.get_item(slot)
            if _is_empty(current):
                return RemovalResult(reason="the item is no longer in that slot")
            if int(current.amount) < amount:
                return RemovalResult(reason="the stack changed")
            before = self.serializer.serialize(current)
            if self.serializer.serialize(current, amount=amount).payload != expected_payload:
                return RemovalResult(reason="the item changed")
        except Exception as exc:
            return RemovalResult(reason=f"cannot read the slot: {exc}")

        remaining = int(current.amount) - amount
        try:
            if remaining == 0:
                inv.set_item(slot, None)
            else:
                current.amount = remaining
                inv.set_item(slot, current)
            after = inv.get_item(slot)
            ok = _is_empty(after) if remaining == 0 else (
                not _is_empty(after) and int(after.amount) == remaining
            )
        except Exception as exc:
            return self._restore(player, slot, before.payload, f"inventory error: {exc}")
        if ok:
            return RemovalResult(removed=True)
        return self._restore(player, slot, before.payload, "slot did not change as expected")

    def _restore(self, player: Any, slot: int, original_payload: str, reason: str) -> RemovalResult:
        """Put the slot back exactly as it was. Ambiguous if even that fails."""
        try:
            player.inventory.set_item(slot, self.serializer.deserialize(original_payload))
        except Exception as exc:
            return RemovalResult(ambiguous=True, reason=f"{reason}; restore failed: {exc}")
        return RemovalResult(reason=reason)
