"""Regenerates src/endstone_gts/data/item_icons.json from Mojang's bedrock-samples resource pack.

    curl -O https://raw.githubusercontent.com/Mojang/bedrock-samples/main/resource_pack/textures/item_texture.json
    (same for textures/terrain_texture.json and blocks.json, into tools/mojang/)
    python tools/build_icons.py

Output: {"diamond_pickaxe": "textures/items/diamond_pickaxe", "stone": "textures/blocks/stone", ...}
(keys are identifiers without the "minecraft:" prefix; values are vanilla resource-pack texture paths).
"""
import json, pathlib, re

HERE = pathlib.Path(__file__).parent
SRC = HERE / "mojang"
OUT = HERE.parent / "src" / "endstone_gts" / "data" / "item_icons.json"


def load(name):
    text = re.sub(r"^\s*//.*$", "", (SRC / name).read_text(), flags=re.M)
    return json.loads(text)


def paths_of(entry):
    t = entry.get("textures") if isinstance(entry, dict) else entry
    if isinstance(t, str):
        return [t]
    out = []
    for x in t or []:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict) and isinstance(x.get("path"), str):
            out.append(x["path"])
    return out


icons: dict[str, str] = {}

# 1. Items: every texture path in the item atlas; its file name is almost always the identifier.
for key, entry in load("item_texture.json")["texture_data"].items():
    ps = [p for p in paths_of(entry) if p.startswith("textures/items/")]
    for p in ps:
        base = p.rsplit("/", 1)[-1]
        icons.setdefault(base, p)
    if ps:
        icons.setdefault(key, ps[0])

# 2. Legacy naming differences between texture files and item identifiers.
for base, p in list(icons.items()):
    if base.startswith("egg_"):
        icons.setdefault(f"{base[4:]}_spawn_egg", p)
    if base.startswith("record_"):
        icons.setdefault(f"music_disc_{base[7:]}", p)
    if re.fullmatch(r"boat_[a-z_]+", base):
        icons.setdefault(f"{base[5:]}_boat", p)
    parts = base.split("_")
    if len(parts) == 2 and parts[1] in ("golden", "iron", "diamond", "wooden", "stone", "netherite", "gold"):
        icons.setdefault(f"{parts[1]}_{parts[0]}", p)  # apple_golden -> golden_apple
icons.setdefault("enchanted_book", "textures/items/book_enchanted")
icons.setdefault("writable_book", "textures/items/book_writable")
icons.setdefault("written_book", "textures/items/book_written")
icons.setdefault("clock", "textures/items/clock_item")
icons.setdefault("compass", "textures/items/compass_item")
icons.setdefault("golden_apple", "textures/items/apple_golden")
icons["chest"] = "textures/blocks/chest_front"
icons["trapped_chest"] = "textures/blocks/chest_front"
icons.setdefault("potion", "textures/items/potion_bottle_drinkable")

# 3. Blocks: blocks.json -> terrain_texture.json (flat face texture; the client cannot render the 3D icon).
terrain = load("terrain_texture.json")["texture_data"]
blocks = load("blocks.json")
for name, spec in blocks.items():
    if not isinstance(spec, dict) or name in icons:
        continue
    tex = spec.get("textures")
    if isinstance(tex, dict):
        tex = tex.get("side") or tex.get("up") or next(iter(tex.values()), None)
    if not isinstance(tex, str) or tex not in terrain:
        continue
    ps = paths_of(terrain[tex])
    if ps:
        icons[name] = ps[0]

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(dict(sorted(icons.items())), separators=(",", ":")))
print(f"{len(icons)} icons -> {OUT} ({OUT.stat().st_size // 1024} KiB)")
