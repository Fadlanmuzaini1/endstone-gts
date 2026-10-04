"""Typed, validated configuration for GTS."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Prices above this are never accepted regardless of config (keeps every value exactly
# representable as a float and far below SQLite's 64-bit integer limit).
HARD_MAX_PRICE = 10**15


@dataclass(frozen=True)
class JWEconomyBinding:
    plugin_name: str = "jweconomy"
    currency: str = ""  # empty = JWEconomy's default currency
    timeout_seconds: float = 5.0


@dataclass(frozen=True)
class GtsConfig:
    enabled: bool = True
    max_active_listings: int = 5
    minimum_price: int = 1
    maximum_price: int = 1_000_000_000
    expiration_hours: int = 48
    listing_fee: int = 0
    tax_percent: int = 0
    banned_items: frozenset[str] = frozenset()
    strict_item_roundtrip: bool = True
    allow_lossy_serialization: bool = False
    items_per_page: int = 8
    currency_symbol: str = "$"
    icons: bool = True
    language: str = "en"
    economy_provider: str = "jweconomy"
    jweconomy: JWEconomyBinding = field(default_factory=JWEconomyBinding)
    database_file: str = "gts.db"
    sweep_interval_seconds: int = 60
    warnings: tuple[str, ...] = ()

    @property
    def expiration_seconds(self) -> int:
        return self.expiration_hours * 3600

    def format_money(self, amount: int) -> str:
        return f"{self.currency_symbol}{amount:,}"


def _section(data: dict, key: str) -> dict:
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}


def _int(section: dict, key: str, default: int, lo: int, hi: int, warnings: list[str]) -> int:
    raw = section.get(key, default)
    # bool is an int subclass; reject it explicitly.
    if isinstance(raw, bool) or not isinstance(raw, int):
        warnings.append(f"{key}: expected an integer, using {default}")
        return default
    if raw < lo or raw > hi:
        clamped = min(max(raw, lo), hi)
        warnings.append(f"{key}: {raw} out of range [{lo}, {hi}], using {clamped}")
        return clamped
    return raw


def _str(section: dict, key: str, default: str) -> str:
    raw = section.get(key, default)
    return raw if isinstance(raw, str) else default


def _bool(section: dict, key: str, default: bool) -> bool:
    raw = section.get(key, default)
    return raw if isinstance(raw, bool) else default


def parse_config(data: dict[str, Any] | None) -> GtsConfig:
    """Build a GtsConfig from the parsed config.toml dict. Never raises on bad values."""
    data = data or {}
    warnings: list[str] = []
    gts = _section(data, "gts")
    listing = _section(data, "listing")
    display = _section(data, "display")
    economy = _section(data, "economy")
    jwe = _section(economy, "jweconomy")
    database = _section(data, "database")
    maintenance = _section(data, "maintenance")

    minimum = _int(listing, "minimum_price", 1, 1, HARD_MAX_PRICE, warnings)
    maximum = _int(listing, "maximum_price", 1_000_000_000, 1, HARD_MAX_PRICE, warnings)
    if maximum < minimum:
        warnings.append("maximum_price is below minimum_price, using minimum_price")
        maximum = minimum

    banned_raw = listing.get("banned_items", [])
    banned = frozenset(x.strip().lower() for x in banned_raw if isinstance(x, str)) if isinstance(
        banned_raw, list
    ) else frozenset()

    db_file = _str(database, "file", "gts.db").strip() or "gts.db"
    # The database always lives inside the plugin data folder: no path components allowed.
    if "/" in db_file or "\\" in db_file or db_file.startswith("."):
        warnings.append("database.file must be a plain file name, using gts.db")
        db_file = "gts.db"

    timeout = jwe.get("timeout_seconds", 5)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.5 <= timeout <= 30:
        warnings.append("economy.jweconomy.timeout_seconds must be between 0.5 and 30, using 5")
        timeout = 5
    jwe_plugin = _str(jwe, "plugin_name", "jweconomy").strip() or "jweconomy"

    language = _str(display, "language", "en").strip().lower()
    if language not in ("en", "id"):
        warnings.append(f"display.language: unknown language {language!r}, using en (available: en, id)")
        language = "en"

    return GtsConfig(
        enabled=_bool(gts, "enabled", True),
        max_active_listings=_int(listing, "max_active_listings", 5, 1, 1000, warnings),
        minimum_price=minimum,
        maximum_price=maximum,
        expiration_hours=_int(listing, "expiration_hours", 48, 1, 24 * 365, warnings),
        listing_fee=_int(listing, "listing_fee", 0, 0, HARD_MAX_PRICE, warnings),
        tax_percent=_int(listing, "tax_percent", 0, 0, 100, warnings),
        banned_items=banned,
        strict_item_roundtrip=_bool(listing, "strict_item_roundtrip", True),
        allow_lossy_serialization=_bool(listing, "allow_lossy_serialization", False),
        items_per_page=_int(display, "items_per_page", 8, 1, 30, warnings),
        currency_symbol=_str(display, "currency_symbol", "$")[:8],
        icons=_bool(display, "icons", True),
        language=language,
        economy_provider=_str(economy, "provider", "jweconomy").strip().lower(),
        jweconomy=JWEconomyBinding(
            plugin_name=jwe_plugin,
            currency=_str(jwe, "currency", "").strip(),
            timeout_seconds=float(timeout),
        ),
        database_file=db_file,
        sweep_interval_seconds=_int(maintenance, "sweep_interval_seconds", 60, 5, 3600, warnings),
        warnings=tuple(warnings),
    )
