import pytest
from endstone.nbt import CompoundTag, IntTag, StringTag
from fakes import World, FakeStack
from endstone_gts.items.details import describe_item, short_tags


def pick(damage=500, enchants=None, name=None, lore=None, slot_amount=1):
    return FakeStack("minecraft:diamond_pickaxe", slot_amount, damage=damage,
                     enchants=enchants if enchants is not None else {"minecraft:efficiency": 5, "minecraft:unbreaking": 3, "minecraft:mending": 1},
                     name=name, lore=lore, max_durability=1561)


@pytest.fixture
def w(tmp_path): return World(tmp_path)


def test_durability_and_enchants_survive_sale_and_purchase(w):
    s, b = w.player("S"), w.player("B", 1000)
    s.inventory.slots[0] = pick(damage=1500, name="§bOld Faithful", lore=["from the mine"])
    r = w.sell(s, 0, 1, 100); assert r.ok, r.message
    assert w.tm.buy(b, r.listing_id).ok
    got = next(x for x in b.inventory.slots if x)
    assert got._meta._damage == 1500                       # NOT reset to full durability
    assert got._meta._enchants == {"minecraft:efficiency": 5, "minecraft:unbreaking": 3, "minecraft:mending": 1}
    assert got._meta._name == "§bOld Faithful" and got._meta._lore == ["from the mine"]


def test_sell_low_durability_then_cancel_does_not_repair(w):
    """The reported exploit: sell a nearly-broken tool, cancel, get it back at full durability."""
    s = w.player("S"); s.inventory.slots[0] = pick(damage=1550)
    r = w.sell(s, 0, 1, 100); assert r.ok
    assert w.lm.cancel(s, r.listing_id).ok
    back = next(x for x in s.inventory.slots if x)
    assert back._meta._damage == 1550


def test_expiry_reclaim_keeps_durability(w):
    s = w.player("S"); s.inventory.slots[0] = pick(damage=1200)
    r = w.sell(s, 0, 1, 100); w.clock.advance(49 * 3600); w.lm.expire_due()
    assert w.lm.reclaim_all(s).ok
    assert next(x for x in s.inventory.slots if x)._meta._damage == 1200


def test_game_that_drops_damage_is_detected_and_buyer_refunded(w):
    s, b = w.player("S"), w.player("B", 1000)
    s.inventory.slots[0] = pick(damage=900)
    r = w.sell(s, 0, 1, 100); assert r.ok
    b.inventory.game_drops_damage = True
    res = w.tm.buy(b, r.listing_id)
    assert not res.ok and "restore" in res.message
    assert all(x is None for x in b.inventory.slots)        # nothing left behind
    assert w.money(b) == 1000 and w.money(s) == 0           # refunded, seller unpaid
    assert w.listings.get(r.listing_id).status == "ACTIVE"  # still for sale, item intact in GTS


def test_listing_refused_if_item_cannot_be_rebuilt_exactly(w, monkeypatch):
    s = w.player("S"); s.inventory.slots[0] = pick(damage=900)
    from endstone_gts.items.serializer import ItemSerializer
    monkeypatch.setattr(ItemSerializer, "_apply_meta", lambda self, stack, meta: None)  # meta can't be applied
    r = w.sell(s, 0, 1, 100)
    assert not r.ok and s.inventory.slots[0]._meta._damage == 900


def test_plain_stackables_still_merge(w):
    s, b = w.player("S"), w.player("B", 1000)
    s.inventory.slots[0] = FakeStack("minecraft:diamond", 10)
    b.inventory.slots[3] = FakeStack("minecraft:diamond", 50)
    r = w.sell(s, 0, 10, 10); assert w.tm.buy(b, r.listing_id).ok
    assert b.inventory.count("minecraft:diamond") == 60 and b.inventory.slots[3].amount == 60


def test_special_item_needs_free_slot(w):
    s, b = w.player("S"), w.player("B", 1000)
    s.inventory.slots[0] = pick(); r = w.sell(s, 0, 1, 100)
    b.inventory.fill_full()
    res = w.tm.buy(b, r.listing_id)
    assert not res.ok and "full" in res.message.lower() and w.money(b) == 1000


def test_game_added_nbt_tags_are_tolerated(w):
    s, b = w.player("S"), w.player("B", 1000)
    nbt = CompoundTag(); nbt["custom"] = StringTag("x")
    s.inventory.slots[0] = FakeStack("minecraft:stick", 3, nbt=nbt)
    r = w.sell(s, 0, 3, 5)
    orig = b.inventory.set_item
    def adding(i, item):
        orig(i, item)
        if item: b.inventory.slots[i]._nbt["added_by_game"] = IntTag(1)
    b.inventory.set_item = adding
    assert w.tm.buy(b, r.listing_id).ok


def test_description_lines():
    w_ = None
    from fakes import factory, FakeStack as F
    from endstone_gts.items.serializer import ItemSerializer
    ser = ItemSerializer(stack_factory=factory)
    item = ser.serialize(pick(damage=1500, lore=["a", "b", "c", "d", "e"]))
    lines = describe_item(item.payload)
    assert lines[0] == "§7Durability: §c61/1561 (4%)"
    assert "  §dEfficiency V" in lines and "  §dUnbreaking III" in lines and "  §dMending I" in lines
    assert any("+1 more" in l for l in lines)
    assert short_tags(item.payload) == " §84% §d✦"
    full = ser.serialize(pick(damage=None, enchants={}))
    assert describe_item(full.payload) == ["§7Durability: §a1561/1561 (100%)"] and short_tags(full.payload) == ""
    assert describe_item(ser.serialize(F("minecraft:dirt", 5)).payload) == []


def test_enchant_fallback_from_nbt_ench_list():
    from endstone.nbt import ListTag, ShortTag
    from fakes import factory
    from endstone_gts.items.serializer import ItemSerializer
    e = CompoundTag(); e["id"] = ShortTag(9); e["lvl"] = ShortTag(5)
    lst = ListTag(); lst.append(e)
    nbt = CompoundTag(); nbt["ench"] = lst
    item = ItemSerializer(stack_factory=factory).serialize(FakeStack("minecraft:iron_sword", 1, nbt=nbt, max_durability=250))
    assert "  §dSharpness V" in describe_item(item.payload)




def _shulker(entries):
    from endstone.nbt import ListTag, ByteTag
    items = ListTag()
    for name, count in entries:
        c = CompoundTag(); c["Name"] = StringTag(name); c["Count"] = ByteTag(count); items.append(c)
    nbt = CompoundTag(); nbt["Items"] = items
    return FakeStack("minecraft:undyed_shulker_box", 1, nbt=nbt)


def test_shulker_contents_are_listed_and_survive_sale(w):
    s, b = w.player("S"), w.player("B", 1000)
    s.inventory.slots[0] = _shulker([("minecraft:diamond", 10), ("minecraft:diamond", 5), ("minecraft:dirt", 64)])
    r = w.sell(s, 0, 1, 100); assert r.ok, r.message
    lines = describe_item(w.lm.get(r.listing_id).item_data)
    assert "§7Contents:" in lines and "  §f64x §7Dirt" in lines and "  §f15x §7Diamond" in lines
    assert w.tm.buy(b, r.listing_id).ok
