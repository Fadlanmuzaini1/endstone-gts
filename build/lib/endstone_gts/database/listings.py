"""Listing persistence. Every state change is a single guarded UPDATE: the WHERE clause names
the state the row must currently be in, and the caller checks ``rowcount``. That compare-and-swap
is what makes double purchases / double cancels / double reclaims impossible."""

from __future__ import annotations

from dataclasses import dataclass

from ..items.listing import Listing, ListingFactory, Reclaim, Status
from .database import Database

SELLER_VIEWS = {
    "active": ("status = 'ACTIVE'", ()),
    "sold": ("status = 'SOLD'", ()),
    "cancelled": ("status = 'CANCELLED'", ()),
    "expired": ("status = 'EXPIRED'", ()),
    "reclaim": ("reclaim_state = 'PENDING' AND status IN ('CANCELLED', 'EXPIRED')", ()),
}


@dataclass
class Page:
    items: list
    total: int
    page: int  # 1-based
    per_page: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.per_page))


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ListingRepository:
    def __init__(self, db: Database, clock):
        self.db = db
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    # -- creation ------------------------------------------------------------------
    def count_open_by_seller(self, seller_uuid: str) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) FROM listings WHERE seller_uuid = ? "
            "AND status IN ('ACTIVE', 'PROCESSING')",
            (seller_uuid,),
        )
        return int(row[0])

    def create_processing(
        self,
        listing_type: str,
        seller_uuid: str,
        seller_name: str,
        item_data: str,
        item_identifier: str,
        display_name: str,
        search_text: str,
        amount: int,
        price: int,
        expires_at: int,
    ) -> int:
        now = self._now()
        cur = self.db.execute(
            "INSERT INTO listings (listing_type, seller_uuid, seller_name, item_data, item_identifier, "
            "display_name, search_text, amount, price, status, reclaim_state, created_at, expires_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PROCESSING', 'NONE', ?, ?, ?)",
            (listing_type, seller_uuid, seller_name, item_data, item_identifier, display_name,
             search_text, amount, price, now, expires_at, now),
        )
        return int(cur.lastrowid)

    # -- reads ---------------------------------------------------------------------
    def get(self, listing_id: int) -> Listing | None:
        row = self.db.query_one("SELECT * FROM listings WHERE id = ?", (listing_id,))
        return ListingFactory.from_row(row) if row else None

    def count_active(self) -> int:
        return int(self.db.query_one("SELECT COUNT(*) FROM listings WHERE status = 'ACTIVE'")[0])

    def browse(self, page: int, per_page: int, search: str | None = None) -> Page:
        where = "status = 'ACTIVE' AND expires_at > ?"
        params: list = [self._now()]
        if search:
            where += " AND search_text LIKE ? ESCAPE '\\'"
            params.append(f"%{escape_like(search.strip().lower())}%")
        return self._page(where, params, "created_at DESC, id DESC", page, per_page)

    def by_seller(self, seller_uuid: str, view: str, page: int, per_page: int) -> Page:
        clause, extra = SELLER_VIEWS[view]
        order = "created_at ASC, id ASC" if view == "active" else "updated_at DESC, id DESC"
        return self._page(f"seller_uuid = ? AND {clause}", [seller_uuid, *extra], order, page, per_page)

    def seller_counts(self, seller_uuid: str) -> dict[str, int]:
        counts = {}
        for view, (clause, extra) in SELLER_VIEWS.items():
            row = self.db.query_one(
                f"SELECT COUNT(*) FROM listings WHERE seller_uuid = ? AND {clause}", (seller_uuid, *extra)
            )
            counts[view] = int(row[0])
        return counts

    def pending_reclaims(self, seller_uuid: str, limit: int = 50) -> list[Listing]:
        rows = self.db.query(
            "SELECT * FROM listings WHERE seller_uuid = ? AND reclaim_state = 'PENDING' "
            "AND status IN ('CANCELLED', 'EXPIRED') ORDER BY id LIMIT ?",
            (seller_uuid, limit),
        )
        return [ListingFactory.from_row(r) for r in rows]

    def _page(self, where: str, params: list, order: str, page: int, per_page: int) -> Page:
        page = max(1, int(page))
        total = int(self.db.query_one(f"SELECT COUNT(*) FROM listings WHERE {where}", params)[0])
        last = max(1, -(-total // per_page))
        page = min(page, last)
        rows = self.db.query(
            f"SELECT * FROM listings WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, per_page, (page - 1) * per_page],
        )
        return Page([ListingFactory.from_row(r) for r in rows], total, page, per_page)

    # -- guarded state transitions (return True only if THIS call changed the row) -----
    def _swap(self, sql: str, params: tuple) -> bool:
        return self.db.execute(sql, params).rowcount == 1

    def claim_for_purchase(self, listing_id: int, buyer_uuid: str) -> bool:
        """ACTIVE -> PROCESSING. Exactly one caller can win."""
        now = self._now()
        return self._swap(
            "UPDATE listings SET status = 'PROCESSING', updated_at = ? "
            "WHERE id = ? AND status = 'ACTIVE' AND expires_at > ? AND seller_uuid != ?",
            (now, listing_id, now, buyer_uuid),
        )

    def activate(self, listing_id: int, from_status: str = Status.PROCESSING) -> bool:
        return self._swap(
            "UPDATE listings SET status = 'ACTIVE', updated_at = ? WHERE id = ? AND status = ?",
            (self._now(), listing_id, from_status),
        )

    def mark_sold(self, listing_id: int) -> bool:
        return self._swap(
            "UPDATE listings SET status = 'SOLD', reclaim_state = 'NONE', updated_at = ? "
            "WHERE id = ? AND status IN ('PROCESSING', 'FAILED')",
            (self._now(), listing_id),
        )

    def mark_failed(self, listing_id: int, reclaim_state: str = Reclaim.NONE) -> bool:
        return self._swap(
            "UPDATE listings SET status = 'FAILED', reclaim_state = ?, updated_at = ? "
            "WHERE id = ? AND status IN ('PROCESSING', 'ACTIVE', 'FAILED')",
            (reclaim_state, self._now(), listing_id),
        )

    def cancel(self, listing_id: int, seller_uuid: str) -> bool:
        """ACTIVE -> CANCELLED and the item becomes reclaimable, in one statement."""
        return self._swap(
            "UPDATE listings SET status = 'CANCELLED', reclaim_state = 'PENDING', updated_at = ? "
            "WHERE id = ? AND seller_uuid = ? AND status = 'ACTIVE'",
            (self._now(), listing_id, seller_uuid),
        )

    def expire_due(self) -> int:
        now = self._now()
        return self.db.execute(
            "UPDATE listings SET status = 'EXPIRED', reclaim_state = 'PENDING', updated_at = ? "
            "WHERE status = 'ACTIVE' AND expires_at <= ?",
            (now, now),
        ).rowcount

    def begin_reclaim(self, listing_id: int, seller_uuid: str) -> bool:
        return self._swap(
            "UPDATE listings SET reclaim_state = 'DELIVERING', updated_at = ? "
            "WHERE id = ? AND seller_uuid = ? AND reclaim_state = 'PENDING' "
            "AND status IN ('CANCELLED', 'EXPIRED')",
            (self._now(), listing_id, seller_uuid),
        )

    def finish_reclaim(self, listing_id: int) -> bool:
        return self._swap(
            "UPDATE listings SET reclaim_state = 'DONE', updated_at = ? "
            "WHERE id = ? AND reclaim_state = 'DELIVERING'",
            (self._now(), listing_id),
        )

    def abort_reclaim(self, listing_id: int) -> bool:
        """Delivery definitely did not happen: make the item claimable again."""
        return self._swap(
            "UPDATE listings SET reclaim_state = 'PENDING', updated_at = ? "
            "WHERE id = ? AND reclaim_state = 'DELIVERING'",
            (self._now(), listing_id),
        )

    def flag_reclaim_review(self, listing_id: int) -> bool:
        return self._swap(
            "UPDATE listings SET reclaim_state = 'REVIEW', updated_at = ? "
            "WHERE id = ? AND reclaim_state = 'DELIVERING'",
            (self._now(), listing_id),
        )

    def resolve_reclaim_review(self, listing_id: int, delivered: bool) -> bool:
        new_state = Reclaim.DONE if delivered else Reclaim.PENDING
        return self._swap(
            "UPDATE listings SET reclaim_state = ?, updated_at = ? "
            "WHERE id = ? AND reclaim_state = 'REVIEW'",
            (new_state, self._now(), listing_id),
        )

    # -- recovery helpers ----------------------------------------------------------
    def reclaims_in_flight(self) -> list[Listing]:
        rows = self.db.query("SELECT * FROM listings WHERE reclaim_state = 'DELIVERING'")
        return [ListingFactory.from_row(r) for r in rows]

    def reclaims_in_review(self) -> list[Listing]:
        rows = self.db.query("SELECT * FROM listings WHERE reclaim_state = 'REVIEW' ORDER BY id")
        return [ListingFactory.from_row(r) for r in rows]

    def orphaned_processing(self) -> list[Listing]:
        """PROCESSING listings with no in-flight transaction (should never exist)."""
        rows = self.db.query(
            "SELECT * FROM listings l WHERE l.status = 'PROCESSING' AND NOT EXISTS "
            "(SELECT 1 FROM transactions t WHERE t.listing_id = l.id AND t.status = 'PROCESSING')"
        )
        return [ListingFactory.from_row(r) for r in rows]
