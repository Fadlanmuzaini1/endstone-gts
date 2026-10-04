"""Per-player history: purchases, sales (from the transaction journal) plus cancelled/expired listings."""

from __future__ import annotations

from dataclasses import dataclass

from .database import Database
from .listings import Page


@dataclass
class HistoryEntry:
    kind: str  # PURCHASED | SOLD | CANCELLED | EXPIRED
    summary: str
    counterparty: str | None
    price: int
    timestamp: int


_UNION = """
SELECT 'PURCHASED' AS kind, item_summary AS summary, seller_name AS other, price, completed_at AS ts
  FROM transactions WHERE kind = 'BUY' AND status = 'COMPLETED' AND buyer_uuid = :u
UNION ALL
SELECT 'SOLD', item_summary, buyer_name, price - fee, completed_at
  FROM transactions WHERE kind = 'BUY' AND status = 'COMPLETED' AND seller_uuid = :u
UNION ALL
SELECT 'CANCELLED', display_name || ' x' || amount, NULL, price, updated_at
  FROM listings WHERE status = 'CANCELLED' AND seller_uuid = :u
UNION ALL
SELECT 'EXPIRED', display_name || ' x' || amount, NULL, price, updated_at
  FROM listings WHERE status = 'EXPIRED' AND seller_uuid = :u
"""


class HistoryRepository:
    def __init__(self, db: Database):
        self.db = db

    def page(self, player_uuid: str, page: int, per_page: int) -> Page:
        total = int(self.db.query_one(f"SELECT COUNT(*) FROM ({_UNION})", {"u": player_uuid})[0])
        last = max(1, -(-total // per_page))
        page = min(max(1, int(page)), last)
        rows = self.db.query(
            f"SELECT * FROM ({_UNION}) ORDER BY ts DESC LIMIT :limit OFFSET :offset",
            {"u": player_uuid, "limit": per_page, "offset": (page - 1) * per_page},
        )
        entries = [HistoryEntry(r["kind"], r["summary"], r["other"], r["price"], r["ts"] or 0) for r in rows]
        return Page(entries, total, page, per_page)
