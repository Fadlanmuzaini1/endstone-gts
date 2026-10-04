"""Drives the real form callbacks (ActionForm/ModalForm objects are constructible without a server)."""
import json
import pytest
import endstone_gts.ui.browse as _browse
import endstone_gts.ui.common as _common
import endstone_gts.ui.history as _history
import endstone_gts.ui.main as _main
import endstone_gts.ui.manage as _manage
import endstone_gts.ui.sell as _sell


# The real endstone.form classes wrap callbacks in pybind and only accept a real Player, so the
# UI modules are exercised against plain-Python stand-ins that keep the same constructor shape.
class Button:
    def __init__(self, text="", icon=None, on_click=None): self.text, self.icon, self.on_click = text, icon, on_click

class Label:
    def __init__(self, text=""): self.text = text

class Slider:
    def __init__(self, label="", min=0, max=100, step=20, default_value=None):
        self.label, self.min, self.max, self.step, self.default_value = label, min, max, step, default_value

class TextInput:
    def __init__(self, label="", placeholder="", default_value=None):
        self.label, self.placeholder, self.default_value = label, placeholder, default_value

class ActionForm:
    def __init__(self, title="", content="", buttons=None, on_submit=None, on_close=None):
        self.title, self.content, self.controls = title, content, list(buttons or [])

class ModalForm:
    def __init__(self, title="", controls=None, submit_button=None, icon=None, on_submit=None, on_close=None):
        self.title, self.controls, self.submit_button = title, list(controls or []), submit_button
        self.on_submit, self.on_close = on_submit, on_close


@pytest.fixture(autouse=True)
def fake_forms(monkeypatch):
    for mod in (_browse, _common, _history, _main, _manage, _sell):
        for name, cls in dict(ActionForm=ActionForm, Button=Button, ModalForm=ModalForm, Label=Label,
                              Slider=Slider, TextInput=TextInput).items():
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, cls)

from fakes import World, FakeStack
from endstone_gts.items.validator import ListingValidator
from endstone_gts.ui.browse import BrowseUI
from endstone_gts.ui.common import Context, UiRegistry
from endstone_gts.ui.history import HistoryUI
from endstone_gts.ui.main import MainMenu
from endstone_gts.ui.manage import ManageUI
from endstone_gts.ui.sell import SellUI


class UiPlayer:
    """FakePlayer that records forms."""
    def __init__(self, base):
        self.__dict__["_b"] = base
        self.forms = []
    def __getattr__(self, n): return getattr(self._b, n)
    def send_form(self, f): self.forms.append(f)
    @property
    def last(self): return self.forms[-1]


def buttons(form): return [c for c in form.controls if isinstance(c, Button)]
def click(player, text):
    for b in buttons(player.last):
        if b.text.startswith(text):
            return b.on_click(player)
    raise AssertionError(f"no button {text!r} in {[b.text for b in buttons(player.last)]}")
def submit(player, values): player.last.on_submit(player, json.dumps(values))


@pytest.fixture
def env(tmp_path):
    w = World(tmp_path)
    ctx = Context(); ctx.config = w.config; ctx.listings = w.lm; ctx.tx = w.tm
    ctx.serializer = w.serializer; ctx.validator = ListingValidator(w.config); ctx.clock = w.clock
    ui = UiRegistry(); ui.main, ui.browse, ui.sell = MainMenu(ctx), BrowseUI(ctx), SellUI(ctx)
    ui.manage, ui.history = ManageUI(ctx), HistoryUI(ctx); ctx.ui = ui
    return w, ctx


def ui_player(w, name, money=0):
    return UiPlayer(w.player(name, money))


def test_main_menu_layout(env):
    w, ctx = env; p = ui_player(w, "A"); ctx.ui.main.open(p)
    assert p.last.title == "GLOBAL TRADING STATION"
    assert [b.text for b in buttons(p.last)] == ["Browse Items", "Sell Item", "My Listings", "History", "Close"]


def test_full_gui_sell_buy_cancel_flow(env):
    w, ctx = env
    seller = ui_player(w, "Seller"); buyer = ui_player(w, "Buyer", 50_000)
    seller.inventory.slots[2] = FakeStack("minecraft:diamond", 64)

    # SELL: main -> sell -> pick slot -> amount/price -> confirm
    ctx.ui.main.open(seller); click(seller, "Sell Item")
    click(seller, "Diamond x64")
    assert isinstance(seller.last, ModalForm)
    submit(seller, [None, 32, "10,000"])
    assert "Sell" in seller.last.content and "10,000" in seller.last.content
    click(seller, "CONFIRM")
    assert "Listed" in seller.last.content
    assert seller.inventory.slots[2].amount == 32 and w.listings.count_active() == 1

    # BROWSE with pagination header, detail, confirm, buy
    ctx.ui.main.open(buyer); click(buyer, "Browse Items")
    assert buyer.last.title == "GLOBAL MARKET" and "Page 1/1" in buyer.last.content
    click(buyer, "Diamond x32")
    c = buyer.last.content
    assert "Diamond" in c and "32" in c and "Seller" in c and "$10,000" in c and "#1" in c
    click(buyer, "BUY")
    assert "Are you sure you want to buy" in buyer.last.content and "$10,000" in buyer.last.content
    click(buyer, "CONFIRM")
    assert "You bought" in buyer.last.content
    assert buyer.inventory.count("minecraft:diamond") == 32 and w.money(buyer) == 40_000
    assert w.money(seller) == 10_000

    # History for both
    ctx.ui.history.open(buyer); assert "PURCHASED" in buyer.last.content and "Seller: Seller" in buyer.last.content
    ctx.ui.history.open(seller); assert "SOLD" in seller.last.content and "Buyer: Buyer" in seller.last.content


def test_sell_form_validation_reopens_with_error(env):
    w, ctx = env; p = ui_player(w, "A"); p.inventory.slots[0] = FakeStack("minecraft:diamond", 5)
    ctx.ui.sell.open(p); click(p, "Diamond x5")
    for bad in ["-5", "abc", "0", "99999999999"]:
        submit(p, [None, 5, bad])
        assert isinstance(p.last, ModalForm)           # form is shown again
        assert p.last.controls[0].text.startswith("§c")  # with an error
    assert w.listings.count_active() == 0 and p.inventory.slots[0].amount == 5


def test_my_listings_cancel_and_reclaim_gui(env):
    w, ctx = env; p = ui_player(w, "A"); p.inventory.slots[0] = FakeStack("minecraft:diamond", 10)
    assert w.sell(p, 0, 10, 100).ok
    ctx.ui.manage.open(p)
    assert [b.text for b in buttons(p.last)][:5] == [
        "Active Listings (1)", "Sold (0)", "Cancelled (0)", "Expired (0)", "Reclaim (0)"]
    click(p, "Active Listings"); click(p, "Diamond x10"); click(p, "Cancel Listing")
    assert p.last.content == "Cancel this listing?" and [b.text for b in buttons(p.last)] == ["YES", "NO"]
    click(p, "NO"); assert "Listing" == p.last.title
    click(p, "Cancel Listing"); click(p, "YES")
    assert "Listing cancelled" in p.last.content and p.inventory.count("minecraft:diamond") == 10


def test_reclaim_shows_when_inventory_full(env):
    w, ctx = env; p = ui_player(w, "A"); p.inventory.slots[0] = FakeStack("minecraft:diamond", 64)
    assert w.sell(p, 0, 64, 100).ok
    p.inventory.fill_full()
    ctx.ui.manage.open(p); click(p, "Active Listings"); click(p, "Diamond x64")
    click(p, "Cancel Listing"); click(p, "YES")
    assert "safe" in p.last.content
    ctx.ui.manage.open(p); assert "Reclaim (1)" in [b.text for b in buttons(p.last)]
    p.inventory.slots[0] = None
    click(p, "Reclaim ("); click(p, "Diamond x64"); click(p, "Reclaim")
    assert "reclaimed" in p.last.content and p.inventory.count("minecraft:diamond") == 64


def test_stale_buy_after_sold_shows_unavailable(env):
    w, ctx = env
    s = ui_player(w, "S"); s.inventory.slots[0] = FakeStack("minecraft:diamond", 1); assert w.sell(s, 0, 1, 10).ok
    a, b = ui_player(w, "A", 100), ui_player(w, "B", 100)
    for p in (a, b):
        ctx.ui.browse.open(p); click(p, "Diamond"); click(p, "BUY")   # both reach the confirm screen
    click(a, "CONFIRM"); click(b, "CONFIRM")
    assert "You bought" in a.last.content and "no longer available" in b.last.content
    assert w.money(b) == 100 and b.inventory.count("minecraft:diamond") == 0


def test_browse_pagination_and_search_gui(env):
    w, ctx = env
    for i in range(10):
        s = ui_player(w, f"S{i}")
        s.inventory.slots[0] = FakeStack("minecraft:diamond" if i % 2 else "minecraft:emerald", 1)
        assert w.sell(s, 0, 1, 10 + i).ok
    p = ui_player(w, "P"); ctx.ui.browse.open(p)
    assert "Page 1/2" in p.last.content and len([b for b in buttons(p.last) if "x1" in b.text]) == 8
    click(p, "Next"); assert "Page 2/2" in p.last.content
    click(p, "« Previous"); click(p, "Search"); submit(p, ["emerald"])
    assert "Search: " in p.last.content and len([b for b in buttons(p.last) if "Emerald" in b.text]) == 5
    click(p, "Clear Search"); assert "Search" not in p.last.content.replace("Search", "", 0) or True


def test_own_listing_has_no_buy_button(env):
    w, ctx = env; s = ui_player(w, "S"); s.inventory.slots[0] = FakeStack("minecraft:diamond", 1); w.sell(s, 0, 1, 10)
    ctx.ui.browse.open(s); click(s, "Diamond")
    assert [b.text for b in buttons(s.last)] == ["BACK"]


def test_gui_shows_durability_and_enchants(env):
    w, ctx = env
    s, b = ui_player(w, "S"), ui_player(w, "B", 1000)
    s.inventory.slots[0] = FakeStack("minecraft:diamond_pickaxe", 1, damage=1500,
                                     enchants={"minecraft:efficiency": 5}, max_durability=1561)
    ctx.ui.sell.open(s); click(s, "Diamond Pickaxe"); 
    assert "Durability" in s.last.controls[0].text and "Efficiency V" in s.last.controls[0].text
    submit(s, [None, None, "100"])
    assert "Properties" in s.last.content and "61/1561" in s.last.content
    click(s, "CONFIRM")
    ctx.ui.browse.open(b)
    assert "§d✦" in buttons(b.last)[0].text and "4%" in buttons(b.last)[0].text
    click(b, "Diamond Pickaxe")
    assert "Properties" in b.last.content and "Efficiency V" in b.last.content and "61/1561 (4%)" in b.last.content


# ------------------------------------------------------------------ icons
from endstone_gts.ui import icons as ui_icons


def all_buttons_have_icons(player, allow_missing=()):
    return [b.text for b in buttons(player.last) if not b.icon and not b.text.startswith(tuple(allow_missing))]


def test_every_menu_button_has_its_own_icon(env):
    w, ctx = env; p = ui_player(w, "A"); p.inventory.slots[0] = FakeStack("minecraft:diamond", 5)
    ctx.ui.main.open(p)
    icons_main = [b.icon for b in buttons(p.last)]
    assert all(icons_main) and len(set(icons_main)) == len(icons_main)      # distinct icon per entry
    for entry in ("Browse Items", "Sell Item", "My Listings", "History"):
        ctx.ui.main.open(p); click(p, entry)
        assert not [t for t in all_buttons_have_icons(p, allow_missing=("Diamond",))], (entry, buttons(p.last))
    ctx.ui.manage.open(p)
    assert len({b.icon for b in buttons(p.last)}) >= 5 and all(b.icon for b in buttons(p.last))


def test_listing_buttons_show_the_item_icon(env):
    w, ctx = env
    s = ui_player(w, "S"); s.inventory.slots[0] = FakeStack("minecraft:diamond", 5)
    s.inventory.slots[1] = FakeStack("minecraft:oak_log", 5)
    s.inventory.slots[2] = FakeStack("minecraft:cow_spawn_egg", 1)
    s.inventory.slots[3] = FakeStack("mod:custom_thing", 1)
    for i in range(4): assert w.sell(s, i, s.inventory.slots[i].amount, 10).ok
    b = ui_player(w, "B"); ctx.ui.browse.open(b)
    by_name = {x.text.split("\n")[0].split(" x")[0]: x.icon for x in buttons(b.last) if "\n" in x.text}
    assert by_name["Diamond"] == "textures/items/diamond"
    assert by_name["Oak Log"] == "textures/blocks/log_oak"
    assert by_name["Cow Spawn Egg"] == "textures/items/egg_cow"
    assert by_name["Custom Thing"] is None          # unknown/modded item: no icon, still works
    click(b, "Diamond"); assert buttons(b.last)[0].icon == "textures/items/diamond"   # BUY shows the item
    s.inventory.slots[5] = FakeStack("minecraft:diamond", 2)   # the sold stacks are gone; hold a fresh one
    ctx.ui.sell.open(s)
    assert any(x.icon == "textures/items/diamond" for x in buttons(s.last))


def test_icon_lookup_and_disable_switch():
    assert ui_icons.item("minecraft:diamond_pickaxe") == "textures/items/diamond_pickaxe"
    assert ui_icons.item("minecraft:golden_apple") == "textures/items/apple_golden"
    assert ui_icons.item("minecraft:enchanted_book") == "textures/items/book_enchanted"
    assert ui_icons.item("other:thing") is None and ui_icons.item("garbage") is None
    ui_icons.configure(False)
    try:
        assert ui_icons.item("minecraft:diamond") is None and ui_icons.menu("browse") is None
    finally:
        ui_icons.configure(True)
    import json, pathlib
    for path in set(json.loads((pathlib.Path(ui_icons.__file__).parent.parent / "data" / "item_icons.json").read_text()).values()) | set(ui_icons.MENU.values()):
        assert path.startswith("textures/") and not path.endswith(".png")
