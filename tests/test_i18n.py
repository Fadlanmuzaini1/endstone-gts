import ast
import json
import re
from pathlib import Path

import pytest
from fakes import FakeStack, World
from endstone_gts import i18n
from endstone_gts.config.config import parse_config
from endstone_gts.items.details import describe_item
from endstone_gts.i18n import ID, tr

SRC = Path(__file__).parent.parent / "src" / "endstone_gts"


@pytest.fixture(autouse=True)
def _reset_language():
    i18n.configure("en")
    yield
    i18n.configure("en")


def _tr_literals():
    found = set()
    for path in SRC.rglob("*.py"):
        if path.name == "i18n.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr"
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                found.add((path.name, node.args[0].value))
    return found


def test_every_tr_literal_has_an_indonesian_translation():
    missing = sorted(text for _, text in _tr_literals() if text not in ID)
    assert not missing, missing


def test_dynamic_keys_are_translated():
    from endstone_gts.transactions.manager import REVIEW_MESSAGE
    for key in ("PURCHASED", "SOLD", "ACTIVE", "CANCELLED", "EXPIRED", "PROCESSING", "FAILED",
                "YES", "NO", "CONFIRM", "CANCEL", REVIEW_MESSAGE, "Active Listings", "Reclaim"):
        assert key in ID, key


def test_translations_keep_the_same_placeholders():
    pat = re.compile(r"\{(\w+)\}")
    for en, idn in ID.items():
        assert set(pat.findall(en)) == set(pat.findall(idn)), en


def test_tr_defaults_to_english_and_translates_when_configured():
    assert tr("Search") == "Search"
    i18n.configure("id")
    assert tr("Search") == "Cari"
    assert tr("You bought {summary}.", summary="Dirt x1") == "Kamu membeli Dirt x1."
    assert tr("not in catalog {x}", x=1) == "not in catalog 1"      # unknown -> English fallback
    i18n.configure("xx")
    assert i18n.current() == "en"


def test_config_language_parsing():
    assert parse_config({}).language == "en"
    assert parse_config({"display": {"language": "ID"}}).language == "id"
    cfg = parse_config({"display": {"language": "fr"}})
    assert cfg.language == "en" and any("language" in w for w in cfg.warnings)


def test_messages_are_indonesian_end_to_end(tmp_path):
    w = World(tmp_path)
    i18n.configure("id")
    s = w.player("S")
    s.inventory.slots[0] = FakeStack("minecraft:dirt", 5)
    r = w.sell(s, 0, 1, 100)
    assert r.ok and "berhasil dijual" in r.message
    b = w.player("B", 0)
    r = w.tm.buy(b, r.listing_id)
    assert not r.ok and "Kamu butuh" in r.message
    assert "tidak dapat dibatalkan" in w.lm.cancel(s, 999).message


def test_renamed_item_shows_base_name_in_properties():
    stack = FakeStack("minecraft:diamond_pickaxe", 1, name="§bOld Faithful", max_durability=1561)
    payload = _payload(stack)
    assert "§7Item: §fDiamond Pickaxe" in describe_item(payload)
    i18n.configure("id")
    assert "§7Item asli: §fDiamond Pickaxe" in describe_item(payload)


def test_no_base_name_line_when_not_renamed_or_same_name():
    assert not any("Item" in l for l in describe_item(_payload(FakeStack("minecraft:diamond_pickaxe", 1, max_durability=1561))))
    same = _payload(FakeStack("minecraft:diamond_pickaxe", 1, name="Diamond Pickaxe", max_durability=1561))
    assert not any("Item" in l for l in describe_item(same))


def _payload(stack):
    from endstone_gts.items.serializer import ItemSerializer
    return ItemSerializer(stack_factory=lambda *a, **k: stack).serialize(stack).payload
