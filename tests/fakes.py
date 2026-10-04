"""Test doubles for the Endstone runtime (which cannot be loaded outside a server)."""
from __future__ import annotations

import itertools
import uuid

from endstone.nbt import CompoundTag, IntTag, StringTag, ByteTag, ListTag

from endstone_gts.common import Log
from endstone_gts.config.config import parse_config
from endstone_gts.database.database import Database
from endstone_gts.database.history import HistoryRepository
from endstone_gts.database.listings import ListingRepository
from endstone_gts.database.transactions import TransactionRepository
from endstone_gts.economy.provider import (
    Account, EconomyAmbiguous, EconomyProvider, EconomyUnavailable,
)
from endstone_gts.items.inventory import ItemDelivery
from endstone_gts.items.serializer import ItemSerializer
from endstone_gts.listings.manager import ListingManager
from endstone_gts.transactions.manager import TransactionManager
from endstone_gts.transactions.recovery import RecoveryService


class FakeType:
    def __init__(self, id, max_durability=0):
        self.id = id
        self.max_durability = max_durability


class FakeEnchant:
    def __init__(self, id): self.id = id
    def __hash__(self): return hash(self.id)
    def __eq__(self, o): return self.id == o.id


class FakeMeta:
    """Mirrors Endstone's ItemMeta: durability/enchants/names live here, NOT in ItemStack.nbt."""
    def __init__(self, damage=None, enchants=None, name=None, lore=None, unbreakable=False):
        self._damage, self._enchants, self._name = damage, dict(enchants or {}), name
        self._lore, self.is_unbreakable = list(lore or []), unbreakable
        self.repair_cost_value = None

    has_repair_cost = False
    has_damage = property(lambda s: s._damage is not None)
    damage = property(lambda s: s._damage or 0, lambda s, v: setattr(s, "_damage", v))
    has_enchants = property(lambda s: bool(s._enchants))
    enchants = property(lambda s: {FakeEnchant(k): v for k, v in s._enchants.items()})
    has_display_name = property(lambda s: s._name is not None)
    display_name = property(lambda s: s._name or "", lambda s, v: setattr(s, "_name", v))
    has_lore = property(lambda s: bool(s._lore))
    lore = property(lambda s: list(s._lore), lambda s, v: setattr(s, "_lore", list(v or [])))

    def add_enchant(self, id, level, force=False):
        self._enchants[id] = level; return True


class FakeStack:
    def __init__(self, type, amount=1, data=0, nbt=None, max_stack=64, damage=None, enchants=None,
                 name=None, lore=None, max_durability=0):
        self.type = FakeType(type, max_durability)
        self.amount = amount
        self.data = data
        self._nbt = nbt if nbt is not None else CompoundTag()
        self.max_stack_size = 1 if max_durability else max_stack
        self._meta = FakeMeta(damage, enchants, name, lore)

    @property
    def nbt(self): return self._nbt

    @nbt.setter
    def nbt(self, v): self._nbt = v          # replaces the user-data tag only

    @property
    def item_meta(self):
        m = FakeMeta(self._meta._damage, self._meta._enchants, self._meta._name, self._meta._lore,
                     self._meta.is_unbreakable)
        return m

    def set_item_meta(self, m):
        self._meta = FakeMeta(m._damage, m._enchants, m._name, m._lore, m.is_unbreakable); return True

    def is_similar(self, o):
        return (self.type.id == o.type.id and self.data == o.data
                and self._nbt.to_dict() == o._nbt.to_dict()
                and self._meta._damage == o._meta._damage and self._meta._enchants == o._meta._enchants
                and self._meta._name == o._meta._name)

    def clone(self):
        c = FakeStack(self.type.id, self.amount, self.data, self._copy_nbt(), 64,
                      self._meta._damage, self._meta._enchants, self._meta._name, self._meta._lore,
                      self.type.max_durability)
        c.max_stack_size = self.max_stack_size
        return c

    def _copy_nbt(self):
        c = CompoundTag()
        for k in self._nbt.keys(): c[k] = self._nbt[k]
        return c


MAX_DURABILITY = {"minecraft:diamond_pickaxe": 1561, "minecraft:iron_sword": 250}


def factory(type, amount=1, data=0):
    return FakeStack(type, amount, data, max_durability=MAX_DURABILITY.get(type, 0))


class FakeInventory:
    def __init__(self, size=36):
        self.size = size
        self.slots = [None] * size
        self.fail_add = False
        self.game_drops_damage = False   # simulate a game/version that does not keep ItemMeta damage

    def get_item(self, i):
        s = self.slots[i]
        return s.clone() if s else None

    def set_item(self, i, item):
        self.slots[i] = item.clone() if item else None
        if item and self.game_drops_damage:
            self.slots[i]._meta._damage = None

    def add_item(self, stack):
        if self.fail_add:
            raise RuntimeError("boom")
        left = stack.amount
        for s in self.slots:
            if s and s.is_similar(stack) and s.amount < s.max_stack_size and left:
                n = min(left, s.max_stack_size - s.amount); s.amount += n; left -= n
        for i, s in enumerate(self.slots):
            if s is None and left:
                n = min(left, stack.max_stack_size)
                c = stack.clone(); c.amount = n; self.slots[i] = c; left -= n
        if left:
            c = stack.clone(); c.amount = left
            return {0: c}
        return {}

    def remove_item(self, stack):
        left = stack.amount
        for i, s in enumerate(self.slots):
            if s and s.is_similar(stack) and left:
                n = min(left, s.amount); s.amount -= n; left -= n
                if s.amount == 0: self.slots[i] = None
        if left:
            c = stack.clone(); c.amount = left
            return {0: c}
        return {}

    def count(self, type):
        return sum(s.amount for s in self.slots if s and s.type.id == type)

    def fill_full(self):
        for i in range(self.size):
            if self.slots[i] is None:
                self.slots[i] = FakeStack("minecraft:dirt", 64)


class FakePlayer:
    def __init__(self, name):
        self.name = name
        self.unique_id = uuid.uuid4()
        self.inventory = FakeInventory()
        self.messages = []

    def send_message(self, m): self.messages.append(m)


class FakeEconomy(EconomyProvider):
    name = "fake"

    def __init__(self):
        self.balances = {}
        self.available = True
        self.fail_withdraw = None   # None | "false" | "unavailable" | "ambiguous"
        self.fail_deposit = None
        self.calls = []

    def is_available(self): return self.available
    def get_balance(self, a):
        if not self.available: raise EconomyUnavailable("down")
        return self.balances.get(a.uuid, 0)

    def withdraw(self, a, amount):
        self.calls.append(("withdraw", a.name, amount))
        if self.fail_withdraw == "unavailable": raise EconomyUnavailable("down")
        if self.fail_withdraw == "false": return False
        if self.fail_withdraw == "ambiguous":
            self.balances[a.uuid] = self.balances.get(a.uuid, 0) - amount  # it DID apply
            raise EconomyAmbiguous("timeout")
        if self.balances.get(a.uuid, 0) < amount: return False
        self.balances[a.uuid] -= amount
        return True

    def deposit(self, a, amount):
        self.calls.append(("deposit", a.name, amount))
        if self.fail_deposit == "unavailable": raise EconomyUnavailable("down")
        if self.fail_deposit == "false": return False
        if self.fail_deposit == "ambiguous": raise EconomyAmbiguous("timeout")
        self.balances[a.uuid] = self.balances.get(a.uuid, 0) + amount
        return True


class QuietLogger:
    def __init__(self): self.lines = []
    def debug(self, m): pass
    def info(self, m): self.lines.append(("info", m))
    def warning(self, m): self.lines.append(("warning", m))
    def error(self, m): self.lines.append(("error", m))
    def critical(self, m): self.lines.append(("critical", m))


class Clock:
    def __init__(self, t=1_000_000): self.t = t
    def __call__(self): return self.t
    def advance(self, s): self.t += s


class World:
    """Everything wired together against an in-memory-ish SQLite file."""

    def __init__(self, tmp_path, **cfg):
        raw = {"listing": {k: v for k, v in cfg.items() if k in (
            "max_active_listings", "minimum_price", "maximum_price", "expiration_hours",
            "listing_fee", "tax_percent", "banned_items", "strict_item_roundtrip")}}
        self.config = parse_config(raw)
        self.clock = Clock()
        self.logger = QuietLogger()
        self.log = Log(self.logger)
        self.path = tmp_path / "gts.db"
        self.db = Database(self.path); self.db.open()
        self.economy = FakeEconomy()
        self.serializer = ItemSerializer(stack_factory=factory)
        self.delivery = ItemDelivery(self.serializer)
        self.listings = ListingRepository(self.db, self.clock)
        self.txs = TransactionRepository(self.db, self.clock)
        self.notified = []
        self.tm = TransactionManager(self.db, self.listings, self.txs, self.economy, self.serializer,
                                     self.delivery, self.config, self.log, self.clock,
                                     notify=lambda u, m: self.notified.append((u, m)))
        self.lm = ListingManager(self.db, self.listings, HistoryRepository(self.db), self.delivery,
                                 self.config, self.log)
        self.recovery = RecoveryService(self.db, self.listings, self.txs, self.tm, self.log)

    def player(self, name, money=0):
        p = FakePlayer(name)
        if hasattr(self.economy, 'balances'): self.economy.balances[str(p.unique_id)] = money
        return p

    def money(self, p): return self.economy.balances[str(p.unique_id)]

    def sell(self, p, slot, amount, price):
        stack = p.inventory.get_item(slot)
        payload = self.serializer.serialize(stack, amount=amount).payload
        return self.tm.sell(p, slot, amount, price, payload)

    def reopen(self):
        """Simulate a restart: new connection to the same file, fresh services."""
        self.db.close()
        w = World.__new__(World)
        w.__dict__.update(self.__dict__)
        w.db = Database(self.path); w.db.open()
        w.listings = ListingRepository(w.db, w.clock); w.txs = TransactionRepository(w.db, w.clock)
        w.tm = TransactionManager(w.db, w.listings, w.txs, w.economy, w.serializer, w.delivery,
                                  w.config, w.log, w.clock, notify=lambda u, m: w.notified.append((u, m)))
        w.lm = ListingManager(w.db, w.listings, HistoryRepository(w.db), w.delivery, w.config, w.log)
        w.recovery = RecoveryService(w.db, w.listings, w.txs, w.tm, w.log)
        return w
