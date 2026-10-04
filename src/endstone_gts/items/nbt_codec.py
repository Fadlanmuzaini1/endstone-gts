"""Lossless, type-preserving JSON encoding of ``endstone.nbt`` tags.

A plain ``CompoundTag.to_dict()`` would turn ByteTag(1), ShortTag(1) and IntTag(1) into the
same Python ``1`` and an item would come back different. Every tag is therefore stored as
``{"t": <type>, "v": <value>}`` and rebuilt with exactly the same tag class.
"""

from __future__ import annotations

from typing import Any

from endstone import nbt as _nbt

_SCALARS = {
    "byte": _nbt.ByteTag,
    "short": _nbt.ShortTag,
    "int": _nbt.IntTag,
    "long": _nbt.LongTag,
    "float": _nbt.FloatTag,
    "double": _nbt.DoubleTag,
    "string": _nbt.StringTag,
}
_NAME_BY_CLASS = {cls: name for name, cls in _SCALARS.items()}

MAX_DEPTH = 64


class NbtCodecError(ValueError):
    pass


def encode(tag: Any, _depth: int = 0) -> dict:
    if _depth > MAX_DEPTH:
        raise NbtCodecError("NBT nesting too deep")
    if isinstance(tag, _nbt.CompoundTag):
        return {"t": "compound", "v": {k: encode(tag[k], _depth + 1) for k in sorted(tag.keys())}}
    if isinstance(tag, _nbt.ListTag):
        return {"t": "list", "v": [encode(t, _depth + 1) for t in tag]}
    if isinstance(tag, _nbt.ByteArrayTag):
        return {"t": "byte_array", "v": [int(b) for b in tag]}
    if isinstance(tag, _nbt.IntArrayTag):
        return {"t": "int_array", "v": [int(i) for i in tag]}
    name = _NAME_BY_CLASS.get(type(tag))
    if name is None:
        raise NbtCodecError(f"Unsupported NBT tag type {type(tag).__name__}")
    return {"t": name, "v": tag.value}


def decode(node: dict, _depth: int = 0) -> Any:
    if _depth > MAX_DEPTH:
        raise NbtCodecError("NBT nesting too deep")
    try:
        t, v = node["t"], node["v"]
    except (KeyError, TypeError) as exc:
        raise NbtCodecError("Malformed NBT node") from exc
    if t == "compound":
        out = _nbt.CompoundTag()
        for key, child in v.items():
            out[key] = decode(child, _depth + 1)
        return out
    if t == "list":
        out = _nbt.ListTag()
        for child in v:
            out.append(decode(child, _depth + 1))
        return out
    if t == "byte_array":
        return _nbt.ByteArrayTag(list(v))
    if t == "int_array":
        return _nbt.IntArrayTag(list(v))
    cls = _SCALARS.get(t)
    if cls is None:
        raise NbtCodecError(f"Unknown NBT node type {t!r}")
    return cls(v)
