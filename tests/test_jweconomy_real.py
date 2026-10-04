"""Integration test against the REAL JWEconomy source (EconomyService, repositories, SQLite, cache, async loop)."""
import asyncio, pathlib, sys, threading, types, uuid
import pytest

JWE_SRC = pathlib.Path("/tmp/claude-0/-home-claude/3e74c4ff-e28f-531f-b816-f0f666ef68c0/scratchpad/jwe/JWEconomy-main/src")
if not JWE_SRC.exists():
    pytest.skip("JWEconomy source not available", allow_module_level=True)
sys.path.insert(0, str(JWE_SRC))
pytest.importorskip("aiosqlite")

from jweconomy.api.economy_api import EconomyAPI
from jweconomy.cache.balance_cache import BalanceCache
from jweconomy.database.database_manager import DatabaseManager
from jweconomy.database.repositories.balance_repository import BalanceRepository
from jweconomy.database.repositories.profile_repository import ProfileRepository
from jweconomy.database.repositories.transaction_repository import TransactionRepository
from jweconomy.database.schema import SchemaManager
from jweconomy.services.economy_service import EconomyService

from fakes import World, FakeStack, QuietLogger
from endstone_gts.config.config import JWEconomyBinding
from endstone_gts.economy.jweconomy import JWEconomyProvider
from endstone_gts.economy.provider import Account, EconomyAmbiguous, EconomyUnavailable


class RealJWE:
    """Stands in for the JWEconomy Plugin object: same attributes/methods main.py defines."""
    is_enabled = True

    def __init__(self, tmp):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=lambda: (asyncio.set_event_loop(self.loop), self.loop.run_forever()), daemon=True).start()
        cfg = {"default_currency": "coins", "currencies": {
            "coins": {"starting_balance": 1000.0, "max_balance": 100_000.0, "currency_symbol": "$"}}}
        log = QuietLogger()
        self.db = DatabaseManager({"type": "sqlite", "filename": "j.db"}, str(tmp), log)
        self.run_async(self.db.connect()).result(5)
        self.run_async(SchemaManager(self.db, log).create_tables("coins")).result(5)
        cache = BalanceCache()
        self.economy_service = EconomyService(BalanceRepository(self.db), TransactionRepository(self.db),
                                              ProfileRepository(self.db), cache, cfg, log)
        self.economy_api = EconomyAPI(self.economy_service, cache)

    def run_async(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def init_player(self, uid, name):
        self.run_async(self.economy_service.initialize_player(uid, "x" + uid, name)).result(5)


class Server:
    def __init__(self, plugin): self.plugin_manager = types.SimpleNamespace(get_plugin=lambda n: plugin if n == "jweconomy" else None)


@pytest.fixture
def jwe(tmp_path):
    p = RealJWE(tmp_path); yield p; p.loop.call_soon_threadsafe(p.loop.stop)


def acct(): return Account(str(uuid.uuid4()), "P")


def test_withdraw_deposit_balance_against_real_service(jwe):
    prov = JWEconomyProvider(Server(jwe), JWEconomyBinding())
    a = acct(); jwe.init_player(a.uuid, "P")
    assert prov.is_available() and prov.get_balance(a) == 1000
    assert prov.withdraw(a, 300) is True and prov.get_balance(a) == 700
    assert prov.withdraw(a, 701) is False and prov.get_balance(a) == 700     # insufficient -> refused, unchanged
    assert prov.withdraw(a, 700) is True and prov.get_balance(a) == 0        # exact drain: 0.0 is success, not failure
    assert prov.deposit(a, 250) is True and prov.get_balance(a) == 250
    assert prov.has_balance(a, 250) and not prov.has_balance(a, 251)


def test_deposit_over_max_balance_refused_not_swallowed(jwe):
    prov = JWEconomyProvider(Server(jwe), JWEconomyBinding())
    a = acct(); jwe.init_player(a.uuid, "P")
    assert prov.deposit(a, 99_001) is False and prov.get_balance(a) == 1000   # would exceed 100k: refused, nothing lost
    assert prov.deposit(a, 99_000) is True and prov.get_balance(a) == 100_000


def test_unavailable_when_plugin_missing_or_loop_dead(jwe):
    prov = JWEconomyProvider(types.SimpleNamespace(plugin_manager=types.SimpleNamespace(get_plugin=lambda n: None)), JWEconomyBinding())
    assert not prov.is_available() and "not loaded" in prov.unavailable_reason()
    with pytest.raises(EconomyUnavailable): prov.withdraw(acct(), 1)
    jwe.loop.call_soon_threadsafe(jwe.loop.stop)
    import time; time.sleep(0.2)
    jwe.run_async = lambda coro: (_ for _ in ()).throw(RuntimeError("Async loop is not running"))
    prov = JWEconomyProvider(Server(jwe), JWEconomyBinding())
    with pytest.raises(EconomyUnavailable): prov.withdraw(acct(), 1)


def test_timeout_is_ambiguous(jwe):
    prov = JWEconomyProvider(Server(jwe), JWEconomyBinding(timeout_seconds=0.5))
    a = acct(); jwe.init_player(a.uuid, "P")
    orig = jwe.economy_api.remove_balance
    async def slow(*args, **kw): await asyncio.sleep(2); return await orig(*args, **kw)
    jwe.economy_api.remove_balance = slow
    with pytest.raises(EconomyAmbiguous): prov.withdraw(a, 10)


def test_full_marketplace_sale_on_real_jweconomy(jwe, tmp_path):
    w = World(tmp_path / "gts"  if (tmp_path / "gts").mkdir() is None else tmp_path)
    prov = JWEconomyProvider(Server(jwe), JWEconomyBinding())
    w.economy = prov; w.tm.economy = prov
    seller, buyer = w.player("Seller"), w.player("Buyer")
    for p in (seller, buyer): jwe.init_player(str(p.unique_id), p.name)
    seller.inventory.slots[0] = FakeStack("minecraft:diamond", 64)
    r = w.sell(seller, 0, 32, 400); assert r.ok, r.message
    assert w.tm.buy(buyer, r.listing_id).ok
    assert prov.get_balance(Account(str(buyer.unique_id), "")) == 600
    assert prov.get_balance(Account(str(seller.unique_id), "")) == 1400
    assert buyer.inventory.count("minecraft:diamond") == 32
    assert not w.tm.buy(buyer, r.listing_id).ok     # second attempt: nothing moves
    assert prov.get_balance(Account(str(buyer.unique_id), "")) == 600
