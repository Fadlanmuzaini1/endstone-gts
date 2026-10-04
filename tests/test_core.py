import pytest
from endstone.nbt import CompoundTag, IntTag, ByteTag, ShortTag, StringTag, ListTag, DoubleTag, FloatTag, LongTag, ByteArrayTag, IntArrayTag
from fakes import World, FakeStack
from endstone_gts.items import nbt_codec
from endstone_gts.items.validator import ListingValidator, ValidationError
from endstone_gts.config.config import parse_config


@pytest.fixture
def w(tmp_path): return World(tmp_path)


def give(p, type="minecraft:diamond", amount=64, slot=0, nbt=None):
    s = FakeStack(type, amount, nbt=nbt); p.inventory.slots[slot] = s; return s


# ---------------------------------------------------------------- nbt / serializer
def test_nbt_roundtrip_preserves_tag_types():
    t = CompoundTag()
    t["b"] = ByteTag(1); t["s"] = ShortTag(1); t["i"] = IntTag(1); t["l"] = LongTag(2**40)
    t["f"] = FloatTag(1.5); t["d"] = DoubleTag(2.25); t["str"] = StringTag("§bHi")
    lst = ListTag(); lst.append(IntTag(1)); lst.append(IntTag(2)); t["list"] = lst
    inner = CompoundTag(); inner["x"] = ByteTag(3); t["c"] = inner
    t["ba"] = ByteArrayTag([1, 2, 3]); t["ia"] = IntArrayTag([4, 5])
    back = nbt_codec.decode(nbt_codec.encode(t))
    assert nbt_codec.encode(back) == nbt_codec.encode(t)
    assert type(back["b"]) is ByteTag and type(back["s"]) is ShortTag and type(back["i"]) is IntTag


def test_serializer_roundtrip_and_partial_amount(w):
    nbt = CompoundTag(); nbt["Count"] = ByteTag(64); nbt["custom"] = StringTag("x")
    s = FakeStack("minecraft:diamond", 64, nbt=nbt)
    item = w.serializer.serialize(s, amount=32)
    assert item.amount == 32 and not item.lossy
    assert "Count" not in item.payload
    assert w.serializer.verify_roundtrip(item)
    rebuilt = w.serializer.deserialize(item.payload)
    assert rebuilt.amount == 32 and rebuilt.nbt["custom"].value == "x"
    assert s.nbt["Count"].value == 64  # live item untouched


def test_roundtrip_failure_is_detected(w):
    item = w.serializer.serialize(FakeStack("minecraft:diamond", 4))
    bad = type(item)(item.payload.replace('"data":0', '"data":0,"x":1'), item.identifier,
                     item.display_name, item.amount, item.search_text, False)
    # payload has an unknown key: rebuilt item serializes without it -> mismatch
    assert not w.serializer.verify_roundtrip(bad)


def test_corrupt_payload_rejected(w):
    from endstone_gts.items.serializer import SerializationError
    for bad in ("", "nope", "{}", '{"v":1,"id":"bad id","amount":1}', '{"v":1,"id":"minecraft:a","amount":0}'):
        with pytest.raises(SerializationError):
            w.serializer.deserialize(bad)


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("text", ["-5", "0", "abc", "1.5", "", "   ", "1e9", "99999999999999999999",
                                  "1000000001", "0x10", "١٢٣", "1 000 0000000000000000000"])
def test_bad_prices_rejected(text):
    v = ListingValidator(parse_config({}))
    with pytest.raises(ValidationError):
        v.parse_price(text)


def test_good_prices():
    v = ListingValidator(parse_config({}))
    assert v.parse_price("10,000") == 10000 and v.parse_price(" 1 ") == 1
    assert v.parse_price("1000000000") == 1_000_000_000


def test_config_bad_values_do_not_crash():
    c = parse_config({"listing": {"minimum_price": -4, "maximum_price": "x", "max_active_listings": True},
                      "database": {"file": "../../etc/passwd"}, "display": {"items_per_page": 9999}})
    assert c.minimum_price == 1 and c.database_file == "gts.db" and c.items_per_page == 30
    assert c.max_active_listings == 5 and c.warnings


# ---------------------------------------------------------------- sell
def test_sell_partial_stack_keeps_remainder(w):
    p = w.player("A"); give(p, amount=64)
    r = w.sell(p, 0, 32, 1000)
    assert r.ok, r.message
    assert p.inventory.slots[0].amount == 32
    l = w.listings.get(r.listing_id)
    assert l.status == "ACTIVE" and l.amount == 32 and l.price == 1000


def test_sell_whole_stack_clears_slot(w):
    p = w.player("A"); give(p, amount=5)
    assert w.sell(p, 0, 5, 10).ok and p.inventory.slots[0] is None


def test_sell_rejects_invalid_and_keeps_item(w):
    p = w.player("A"); give(p, amount=5)
    stack = p.inventory.get_item(0)
    payload = w.serializer.serialize(stack, 3).payload
    for amount, price in [(0, 10), (6, 10), (3, 0), (3, -1), (3, 10**12)]:
        assert not w.tm.sell(p, 0, amount, price, payload).ok
    assert p.inventory.slots[0].amount == 5
    assert w.listings.count_active() == 0


def test_sell_detects_item_swapped_after_selection(w):
    p = w.player("A"); give(p, amount=5)
    payload = w.serializer.serialize(p.inventory.get_item(0), 5).payload
    give(p, "minecraft:emerald", 5)  # player swaps the slot while the form is open
    assert not w.tm.sell(p, 0, 5, 10, payload).ok
    assert p.inventory.slots[0].type.id == "minecraft:emerald"


def test_listing_limit_cannot_be_bypassed(tmp_path):
    w = World(tmp_path, max_active_listings=2)
    p = w.player("A")
    for i in range(4): give(p, amount=1, slot=i)
    results = [w.sell(p, i, 1, 10) for i in range(4)]
    assert [r.ok for r in results] == [True, True, False, False]
    assert results[2].message == "Cannot create another listing."
    assert p.inventory.slots[2].amount == 1 and p.inventory.slots[3].amount == 1


def test_banned_item(tmp_path):
    w = World(tmp_path, banned_items=["minecraft:bedrock"])
    p = w.player("A"); give(p, "minecraft:bedrock", 1)
    assert not w.sell(p, 0, 1, 10).ok and p.inventory.slots[0].amount == 1


def test_lossy_item_refused(w):
    p = w.player("A"); s = give(p, amount=1)
    original = FakeStack.__dict__["nbt"]
    FakeStack.nbt = property(lambda self: (_ for _ in ()).throw(RuntimeError("no nbt")))
    try:
        r = w.sell(p, 0, 1, 10)
    finally:
        FakeStack.nbt = original
    assert not r.ok and p.inventory.slots[0] is not None


def test_listing_fee_charged_only_on_success(tmp_path):
    w = World(tmp_path, listing_fee=50)
    p = w.player("A", money=100); give(p, amount=1)
    assert w.sell(p, 0, 1, 10).ok and w.money(p) == 50
    q = w.player("B", money=10); give(q, amount=1)
    assert not w.sell(q, 0, 1, 10).ok and w.money(q) == 10 and q.inventory.slots[0].amount == 1


def test_fee_refunded_when_item_removal_fails(tmp_path):
    w = World(tmp_path, listing_fee=50)
    p = w.player("A", money=100); give(p, amount=2)
    orig = w.delivery.remove_from_slot
    from endstone_gts.items.inventory import RemovalResult
    w.delivery.remove_from_slot = lambda *a, **k: RemovalResult(reason="slot changed")
    r = w.sell(p, 0, 2, 10)
    assert not r.ok and w.money(p) == 100 and p.inventory.slots[0].amount == 2
    assert w.listings.count_open_by_seller(str(p.unique_id)) == 0


def test_fee_ambiguous_parks_and_keeps_item(tmp_path):
    w = World(tmp_path, listing_fee=50)
    p = w.player("A", money=100); give(p, amount=2)
    w.economy.fail_withdraw = "ambiguous"
    r = w.sell(p, 0, 2, 10)
    assert not r.ok and p.inventory.slots[0].amount == 2
    assert [t.status for t in w.txs.needs_review()] == ["NEEDS_REVIEW"]


# ---------------------------------------------------------------- buy
def listed(w, seller_money=0, price=1000, amount=32):
    s = w.player("Seller", seller_money); give(s, amount=64)
    r = w.sell(s, 0, amount, price); assert r.ok, r.message
    return s, r.listing_id


def test_buy_happy_path(w):
    seller, lid = listed(w)
    b = w.player("Buyer", 5000)
    r = w.tm.buy(b, lid)
    assert r.ok, r.message
    assert b.inventory.count("minecraft:diamond") == 32 and w.money(b) == 4000 and w.money(seller) == 1000
    assert w.listings.get(lid).status == "SOLD"
    assert w.txs.get(r.transaction_id).status == "COMPLETED"
    assert w.notified and w.notified[0][0] == str(seller.unique_id)
    assert not w.tm.buy(w.player("C", 5000), lid).ok  # sold once only


def test_buy_with_tax(tmp_path):
    w = World(tmp_path, tax_percent=10)
    seller, lid = listed(w, price=1000)
    b = w.player("B", 1000)
    assert w.tm.buy(b, lid).ok and w.money(seller) == 900 and w.money(b) == 0


def test_cannot_buy_own_listing(w):
    seller, lid = listed(w); w.economy.balances[str(seller.unique_id)] = 9999
    assert not w.tm.buy(seller, lid).ok and w.listings.get(lid).status == "ACTIVE"


def test_insufficient_funds_no_side_effects(w):
    seller, lid = listed(w); b = w.player("B", 10)
    r = w.tm.buy(b, lid)
    assert not r.ok and w.money(b) == 10 and w.listings.get(lid).status == "ACTIVE"
    assert b.inventory.count("minecraft:diamond") == 0


def test_inventory_full_no_charge_no_sold(w):
    seller, lid = listed(w); b = w.player("B", 5000); b.inventory.fill_full()
    r = w.tm.buy(b, lid)
    assert not r.ok and "full" in r.message.lower()
    assert w.money(b) == 5000 and w.money(seller) == 0 and w.listings.get(lid).status == "ACTIVE"


def test_double_click_and_two_buyers_single_sale(w):
    seller, lid = listed(w)
    a, b = w.player("A", 5000), w.player("B", 5000)
    ra, rb, rc = w.tm.buy(a, lid), w.tm.buy(b, lid), w.tm.buy(a, lid)
    assert [ra.ok, rb.ok, rc.ok] == [True, False, False]
    assert w.money(b) == 5000 and b.inventory.count("minecraft:diamond") == 0
    assert w.money(a) == 4000 and a.inventory.count("minecraft:diamond") == 32
    assert w.money(seller) == 1000


def test_claim_is_atomic_at_db_level(w):
    seller, lid = listed(w)
    with w.db.transaction():
        assert w.listings.claim_for_purchase(lid, "buyer1")
    with w.db.transaction():
        assert not w.listings.claim_for_purchase(lid, "buyer2")


def test_economy_refuses_withdraw_listing_released(w):
    seller, lid = listed(w); b = w.player("B", 5000)
    w.economy.fail_withdraw = "false"
    assert not w.tm.buy(b, lid).ok
    assert w.listings.get(lid).status == "ACTIVE" and b.inventory.count("minecraft:diamond") == 0


def test_economy_down_releases_listing(w):
    seller, lid = listed(w); b = w.player("B", 5000)
    w.economy.fail_withdraw = "unavailable"
    assert not w.tm.buy(b, lid).ok and w.listings.get(lid).status == "ACTIVE"


def test_ambiguous_withdraw_parks_listing_no_item_given(w):
    seller, lid = listed(w); b = w.player("B", 5000)
    w.economy.fail_withdraw = "ambiguous"
    r = w.tm.buy(b, lid)
    assert not r.ok and b.inventory.count("minecraft:diamond") == 0
    assert w.listings.get(lid).status == "FAILED"          # held, not buyable again, not cancellable
    assert not w.tm.buy(w.player("C", 5000), lid).ok
    assert not w.lm.cancel(seller, lid).ok


def test_delivery_failure_refunds_buyer_and_relists(w):
    seller, lid = listed(w); b = w.player("B", 5000)
    b.inventory.fail_add = True   # add_item raises -> ambiguous
    r = w.tm.buy(b, lid)
    assert not r.ok
    assert w.listings.get(lid).status == "FAILED" and len(w.txs.needs_review()) == 1
    assert w.money(seller) == 0


def test_definite_delivery_failure_refunds(w):
    seller, lid = listed(w); b = w.player("B", 5000)
    from endstone_gts.items.inventory import DeliveryResult
    w.delivery.deliver = lambda *a, **k: DeliveryResult(reason="inventory full")
    r = w.tm.buy(b, lid)
    assert not r.ok and w.money(b) == 5000 and w.listings.get(lid).status == "ACTIVE"
    assert w.money(seller) == 0


def test_refund_deferred_when_economy_down_then_retried(w):
    seller, lid = listed(w); b = w.player("B", 5000)
    from endstone_gts.items.inventory import DeliveryResult
    w.delivery.deliver = lambda *a, **k: DeliveryResult(reason="inventory full")
    orig = w.economy.deposit
    def dep(a, amt):
        w.economy.fail_deposit = None
        raise __import__("endstone_gts.economy.provider", fromlist=["x"]).EconomyUnavailable("down")
    w.economy.deposit = dep
    r = w.tm.buy(b, lid)
    assert not r.ok and w.money(b) == 4000 and w.listings.get(lid).status == "PROCESSING"
    w.economy.deposit = orig
    w.recovery.retry_pending()
    assert w.money(b) == 5000 and w.listings.get(lid).status == "ACTIVE"
    w.recovery.retry_pending()
    assert w.money(b) == 5000   # idempotent


def test_seller_payout_deferred_then_paid_once(w):
    seller, lid = listed(w); b = w.player("B", 5000)
    w.economy.fail_deposit = "unavailable"
    r = w.tm.buy(b, lid)
    assert r.ok and w.money(seller) == 0 and w.txs.get(r.transaction_id).payout_state == "PENDING"
    w.economy.fail_deposit = None
    w.recovery.retry_pending(); w.recovery.retry_pending()
    assert w.money(seller) == 1000 and w.txs.get(r.transaction_id).payout_state == "PAID"


def test_ambiguous_payout_never_retried(w):
    seller, lid = listed(w); b = w.player("B", 5000)
    w.economy.fail_deposit = "ambiguous"
    r = w.tm.buy(b, lid)
    assert r.ok and w.txs.get(r.transaction_id).payout_state == "REVIEW"
    w.economy.fail_deposit = None
    w.recovery.retry_pending()
    assert w.money(seller) == 0  # no automatic second payment


def test_economy_unavailable_blocks_buy_but_not_browse(w):
    seller, lid = listed(w); b = w.player("B", 5000); w.economy.available = False
    assert not w.tm.buy(b, lid).ok and w.listings.get(lid).status == "ACTIVE"
    assert w.lm.browse(1).total == 1


def test_expired_listing_cannot_be_bought(w):
    seller, lid = listed(w); w.clock.advance(49 * 3600)
    assert not w.tm.buy(w.player("B", 5000), lid).ok


# ---------------------------------------------------------------- cancel / reclaim / expire
def test_cancel_returns_item(w):
    seller, lid = listed(w); before = seller.inventory.count("minecraft:diamond")
    r = w.lm.cancel(seller, lid)
    assert r.ok and seller.inventory.count("minecraft:diamond") == before + 32
    l = w.listings.get(lid); assert l.status == "CANCELLED" and l.reclaim_state == "DONE"
    assert not w.lm.cancel(seller, lid).ok and not w.lm.reclaim(seller, lid).ok
    assert seller.inventory.count("minecraft:diamond") == before + 32  # no dupe


def test_cancel_with_full_inventory_keeps_item_reclaimable(w):
    seller, lid = listed(w)
    seller.inventory.slots[0] = FakeStack("minecraft:dirt", 64); seller.inventory.fill_full()
    r = w.lm.cancel(seller, lid)
    assert r.ok and w.listings.get(lid).reclaim_state == "PENDING"
    assert not w.lm.reclaim(seller, lid).ok
    seller.inventory.slots[5] = None
    assert w.lm.reclaim(seller, lid).ok and w.lm.reclaim(seller, lid).ok is False
    assert seller.inventory.count("minecraft:diamond") == 32


def test_only_owner_can_cancel_or_reclaim(w):
    seller, lid = listed(w); other = w.player("X")
    assert not w.lm.cancel(other, lid).ok and w.listings.get(lid).status == "ACTIVE"
    w.lm.cancel(seller, lid)
    assert not w.lm.reclaim(other, lid).ok


def test_cancel_while_being_bought_fails(w):
    seller, lid = listed(w)
    with w.db.transaction(): w.listings.claim_for_purchase(lid, "x")
    assert not w.lm.cancel(seller, lid).ok


def test_expiration_then_reclaim(w):
    seller, lid = listed(w)
    w.clock.advance(48 * 3600 + 1)
    assert w.lm.expire_due() == 1 and w.lm.expire_due() == 0
    l = w.listings.get(lid); assert l.status == "EXPIRED" and l.reclaim_state == "PENDING"
    assert w.lm.browse(1).total == 0
    assert w.lm.reclaim_all(seller).ok and seller.inventory.count("minecraft:diamond") == 64


# ---------------------------------------------------------------- search / pagination / history
def test_search_and_pagination(w):
    s = w.player("S")
    kinds = ["minecraft:diamond", "minecraft:golden_apple", "minecraft:emerald"]
    for i in range(30):
        give(s, kinds[i % 3], 1, slot=0)
        w.tm.config = w.config
        r = w.sell(s, 0, 1, 10 + i)
        if not r.ok:
            # limit of 5 active: use fresh sellers
            s = w.player(f"S{i}"); give(s, kinds[i % 3], 1, slot=0); assert w.sell(s, 0, 1, 10 + i).ok
    p1 = w.lm.browse(1); assert p1.total == 30 and len(p1.items) == 8 and p1.pages == 4
    assert len(w.lm.browse(4).items) == 6 and w.lm.browse(99).page == 4
    assert w.lm.browse(1, "golden").total == 10
    assert w.lm.browse(1, "GOLDEN APPLE").total == 10
    assert w.lm.browse(1, "%").total == 0 and w.lm.browse(1, "g_lden").total == 0 and w.lm.browse(1, "_").total == 10
    assert w.lm.browse(1, "' OR 1=1 --").total == 0
    assert w.listings.count_active() == 30


def test_history(w):
    seller, lid = listed(w); b = w.player("B", 5000)
    w.tm.buy(b, lid)
    give(seller, "minecraft:emerald", 3, slot=1); r = w.sell(seller, 1, 3, 5); w.lm.cancel(seller, r.listing_id)
    kinds = [e.kind for e in w.lm.history_page(str(seller.unique_id), 1).items]
    assert sorted(kinds) == ["CANCELLED", "SOLD"]
    assert [e.kind for e in w.lm.history_page(str(b.unique_id), 1).items] == ["PURCHASED"]


# ---------------------------------------------------------------- recovery
def crash_buy_at(w, step):
    """Leave a BUY transaction journaled at `step` as if the server died there."""
    seller, lid = listed(w); b = w.player("B", 5000)
    with w.db.transaction():
        assert w.listings.claim_for_purchase(lid, str(b.unique_id))
        tx = w.txs.create("BUY", lid, str(seller.unique_id), "Seller", 1000, "Diamond x32", "CLAIMED",
                          buyer_uuid=str(b.unique_id), buyer_name="B")
    w.txs.cas_step(tx, "CLAIMED", step)
    return seller, b, lid, tx


@pytest.mark.parametrize("step,money_taken", [("CLAIMED", False), ("WITHDRAWN", True)])
def test_recovery_safe_steps(tmp_path, step, money_taken):
    w = World(tmp_path); seller, b, lid, tx = crash_buy_at(w, step)
    if money_taken: w.economy.balances[str(b.unique_id)] -= 1000
    w2 = w.reopen(); w2.recovery.run(); w2.recovery.run()
    assert w2.listings.get(lid).status == "ACTIVE"
    assert w2.money(b) == 5000 and b.inventory.count("minecraft:diamond") == 0
    assert w2.money(seller) == 0
    assert w2.txs.get(tx).status in ("CANCELLED", "FAILED")


@pytest.mark.parametrize("step", ["WITHDRAWING", "DELIVERING", "REFUNDING"])
def test_recovery_ambiguous_steps_are_parked_not_guessed(tmp_path, step):
    w = World(tmp_path); seller, b, lid, tx = crash_buy_at(w, step)
    w2 = w.reopen(); s = w2.recovery.run(); w2.recovery.run()
    assert w2.txs.get(tx).status == "NEEDS_REVIEW" and w2.listings.get(lid).status == "FAILED"
    assert w2.money(b) == 5000 and w2.money(seller) == 0 and b.inventory.count("minecraft:diamond") == 0
    assert not w2.tm.buy(w2.player("C", 9999), lid).ok


def test_recovery_sell_steps(tmp_path):
    w = World(tmp_path, listing_fee=50)
    p = w.player("A", 1000)
    for step, expect in [("CREATED", "FAILED"), ("FEE_PAID", "FAILED"), ("REMOVING", "FAILED")]:
        with w.db.transaction():
            lid = w.listings.create_processing("ITEM", str(p.unique_id), "A", "{}", "minecraft:x", "X", "x", 1, 5, 99)
            tx = w.txs.create("SELL", lid, str(p.unique_id), "A", 5, "X x1", "CREATED", fee=50)
        w.txs.set_step(tx, step)
        w2 = w.reopen(); w2.recovery.run()
        assert w2.listings.get(lid).status == expect
        t = w2.txs.get(tx)
        if step == "CREATED": assert t.status == "CANCELLED"
        if step == "FEE_PAID": assert t.status == "FAILED" and w2.money(p) == 1050  # fee refunded once
        if step == "REMOVING": assert t.status == "NEEDS_REVIEW"
        w2.recovery.run(); assert w2.money(p) == (1050 if step == "FEE_PAID" else 1000)
        w = w2
        w.economy.balances[str(p.unique_id)] = 1000


def test_recovery_interrupted_reclaim_flagged(w):
    seller, lid = listed(w)
    with w.db.transaction():
        w.listings.cancel(lid, str(seller.unique_id)); w.listings.begin_reclaim(lid, str(seller.unique_id))
    w2 = w.reopen(); w2.recovery.run()
    assert w2.listings.get(lid).reclaim_state == "REVIEW"
    assert not w2.lm.reclaim(seller, lid).ok
    assert seller.inventory.count("minecraft:diamond") == 32


def test_recovery_payout_in_flight_not_repaid(w):
    seller, lid = listed(w); b = w.player("B", 5000)
    r = w.tm.buy(b, lid)
    w.txs.set_payout_state(r.transaction_id, "PAYING")   # crashed mid-payout
    w2 = w.reopen(); w2.recovery.run(); w2.recovery.run()
    assert w2.txs.get(r.transaction_id).payout_state == "REVIEW"
    assert w2.money(seller) == 1000   # paid exactly once (the original), never again


def test_db_unique_open_buy(w):
    seller, lid = listed(w)
    with w.db.transaction():
        w.txs.create("BUY", lid, "s", "S", 1, "x", "CLAIMED", buyer_uuid="b", buyer_name="B")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        with w.db.transaction():
            w.txs.create("BUY", lid, "s", "S", 1, "x", "CLAIMED", buyer_uuid="c", buyer_name="C")


def test_migration_idempotent(tmp_path):
    w = World(tmp_path); v = w.db.schema_version(); w.db.migrate()
    assert w.db.schema_version() == v == 1


def test_two_connections_race_for_one_listing(tmp_path):
    """Two independent SQLite connections (as if two processes) claim concurrently: exactly one wins."""
    import threading
    from endstone_gts.database.database import Database
    from endstone_gts.database.listings import ListingRepository
    w = World(tmp_path)
    seller, lid = listed(w)
    wins, barrier = [], threading.Barrier(8)

    def worker(i):
        db = Database(w.path); db.open()
        repo = ListingRepository(db, w.clock)
        barrier.wait()
        with db.transaction():
            if repo.claim_for_purchase(lid, f"buyer{i}"):
                wins.append(i)
        db.close()

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(wins) == 1


def test_foreign_database_is_refused_untouched(tmp_path):
    """Regression: a gts.db from another plugin (listings table without our columns) must not be adopted."""
    import sqlite3
    from endstone_gts.database.database import Database, DatabaseMismatch
    path = tmp_path / "gts.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE listings (id INTEGER PRIMARY KEY, seller TEXT, price REAL)")
    c.execute("PRAGMA user_version = 1"); c.commit(); c.close()
    with pytest.raises(DatabaseMismatch):
        Database(path).open()
    c = sqlite3.connect(path)
    assert [r[1] for r in c.execute("PRAGMA table_info(listings)")] == ["id", "seller", "price"]  # untouched


def test_own_database_reopens_and_old_unmarked_file_is_adopted(tmp_path):
    import sqlite3
    from endstone_gts.database.database import Database
    w = World(tmp_path); w.db.close()
    c = sqlite3.connect(tmp_path / "gts.db"); c.execute("PRAGMA application_id = 0"); c.commit(); c.close()
    db = Database(tmp_path / "gts.db"); db.open()   # v0.1.0-style file without the marker
    assert db.query_one("PRAGMA application_id")[0] == 0x47545331
