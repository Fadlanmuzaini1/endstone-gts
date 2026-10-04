"""Versioned schema migrations. Append new (version, [statements]) entries; never edit old ones."""

from __future__ import annotations

LISTING_STATUSES = ("ACTIVE", "SOLD", "CANCELLED", "EXPIRED", "PROCESSING", "FAILED")
RECLAIM_STATES = ("NONE", "PENDING", "DELIVERING", "DONE", "REVIEW")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


MIGRATIONS: list[tuple[int, list[str]]] = [
    (
        1,
        [
            f"""
            CREATE TABLE listings (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_type    TEXT    NOT NULL DEFAULT 'ITEM',
                seller_uuid     TEXT    NOT NULL,
                seller_name     TEXT    NOT NULL,
                item_data       TEXT    NOT NULL,
                item_identifier TEXT    NOT NULL,
                display_name    TEXT    NOT NULL,
                search_text     TEXT    NOT NULL,
                amount          INTEGER NOT NULL CHECK (amount > 0),
                price           INTEGER NOT NULL CHECK (price > 0),
                status          TEXT    NOT NULL CHECK (status IN ({_in(LISTING_STATUSES)})),
                reclaim_state   TEXT    NOT NULL DEFAULT 'NONE'
                                CHECK (reclaim_state IN ({_in(RECLAIM_STATES)})),
                created_at      INTEGER NOT NULL,
                expires_at      INTEGER NOT NULL,
                updated_at      INTEGER NOT NULL
            )
            """,
            "CREATE INDEX idx_listings_status ON listings(status)",
            "CREATE INDEX idx_listings_seller_uuid ON listings(seller_uuid)",
            "CREATE INDEX idx_listings_created_at ON listings(created_at)",
            "CREATE INDEX idx_listings_item_identifier ON listings(item_identifier)",
            "CREATE INDEX idx_listings_reclaim ON listings(seller_uuid, reclaim_state)",
            """
            CREATE TABLE transactions (
                transaction_id TEXT    PRIMARY KEY,
                kind           TEXT    NOT NULL CHECK (kind IN ('SELL', 'BUY')),
                listing_id     INTEGER NOT NULL REFERENCES listings(id),
                seller_uuid    TEXT    NOT NULL,
                seller_name    TEXT    NOT NULL,
                buyer_uuid     TEXT,
                buyer_name     TEXT,
                price          INTEGER NOT NULL,
                fee            INTEGER NOT NULL DEFAULT 0,
                item_summary   TEXT    NOT NULL,
                status         TEXT    NOT NULL
                               CHECK (status IN ('PROCESSING', 'COMPLETED', 'FAILED', 'CANCELLED', 'NEEDS_REVIEW')),
                step           TEXT    NOT NULL,
                payout_state   TEXT    NOT NULL DEFAULT 'NONE'
                               CHECK (payout_state IN ('NONE', 'PENDING', 'PAYING', 'PAID', 'REVIEW')),
                detail         TEXT,
                created_at     INTEGER NOT NULL,
                completed_at   INTEGER
            )
            """,
            "CREATE INDEX idx_tx_status ON transactions(status)",
            "CREATE INDEX idx_tx_listing ON transactions(listing_id)",
            "CREATE INDEX idx_tx_seller ON transactions(seller_uuid)",
            "CREATE INDEX idx_tx_buyer ON transactions(buyer_uuid)",
            "CREATE INDEX idx_tx_created_at ON transactions(created_at)",
            # Defence in depth: at most one in-flight purchase per listing, enforced by the DB.
            "CREATE UNIQUE INDEX ux_tx_open_buy ON transactions(listing_id) "
            "WHERE kind = 'BUY' AND status = 'PROCESSING'",
        ],
    ),
]
