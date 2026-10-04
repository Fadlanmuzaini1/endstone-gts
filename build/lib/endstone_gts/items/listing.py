"""Listing domain model.

``Listing`` is the generic marketplace entry (seller, price, lifecycle). ``ItemListing`` is
the only concrete type in version 1. A future ``PokemonListing`` registers itself with
``ListingFactory.register`` and reuses the same table (``listing_type`` column), the same
lifecycle, the same transaction manager. ``item_data`` holds the type-specific payload.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import ClassVar


class Status:
    ACTIVE = "ACTIVE"
    SOLD = "SOLD"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    PROCESSING = "PROCESSING"
    FAILED = "FAILED"


class Reclaim:
    NONE = "NONE"
    PENDING = "PENDING"  # item is still held by GTS and waiting for the seller (ITEM_PENDING_RECLAIM)
    DELIVERING = "DELIVERING"
    DONE = "DONE"
    REVIEW = "REVIEW"  # outcome unknown after a crash; needs an admin


@dataclass
class Listing:
    id: int
    listing_type: str
    seller_uuid: str
    seller_name: str
    item_data: str
    item_identifier: str
    display_name: str
    amount: int
    price: int
    status: str
    reclaim_state: str
    created_at: int
    expires_at: int
    updated_at: int = 0

    TYPE: ClassVar[str] = "BASE"

    @property
    def title(self) -> str:
        raise NotImplementedError

    @property
    def is_active(self) -> bool:
        return self.status == Status.ACTIVE


class ItemListing(Listing):
    TYPE: ClassVar[str] = "ITEM"

    @property
    def title(self) -> str:
        return f"{self.display_name} x{self.amount}"


class ListingFactory:
    _types: dict[str, type[Listing]] = {ItemListing.TYPE: ItemListing}

    @classmethod
    def register(cls, listing_type: str, klass: type[Listing]) -> None:
        cls._types[listing_type] = klass

    @classmethod
    def supported_types(cls) -> tuple[str, ...]:
        return tuple(cls._types)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Listing:
        klass = cls._types.get(row["listing_type"])
        if klass is None:
            raise ValueError(f"Unsupported listing type {row['listing_type']!r}")
        return klass(
            id=row["id"],
            listing_type=row["listing_type"],
            seller_uuid=row["seller_uuid"],
            seller_name=row["seller_name"],
            item_data=row["item_data"],
            item_identifier=row["item_identifier"],
            display_name=row["display_name"],
            amount=row["amount"],
            price=row["price"],
            status=row["status"],
            reclaim_state=row["reclaim_state"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            updated_at=row["updated_at"],
        )
