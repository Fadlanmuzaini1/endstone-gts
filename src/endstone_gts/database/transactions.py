"""Transaction journal. Each money/item step is written here BEFORE it happens (intent) and
again AFTER it succeeds, so a crash always leaves a record of how far the saga got."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from .database import Database

# Transaction kinds
SELL = "SELL"
BUY = "BUY"

# Statuses
PROCESSING = "PROCESSING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass
class TxRecord:
    transaction_id: str
    kind: str
    listing_id: int
    seller_uuid: str
    seller_name: str
    buyer_uuid: str | None
    buyer_name: str | None
    price: int
    fee: int
    item_summary: str
    status: str
    step: str
    payout_state: str
    detail: str | None
    created_at: int
    completed_at: int | None


def _record(row) -> TxRecord:
    return TxRecord(**{k: row[k] for k in row.keys()})


def new_transaction_id() -> str:
    return "GTS-" + secrets.token_hex(4).upper()


class TransactionRepository:
    def __init__(self, db: Database, clock):
        self.db = db
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    def create(
        self,
        kind: str,
        listing_id: int,
        seller_uuid: str,
        seller_name: str,
        price: int,
        item_summary: str,
        step: str,
        buyer_uuid: str | None = None,
        buyer_name: str | None = None,
        fee: int = 0,
    ) -> str:
        tx_id = new_transaction_id()
        self.db.execute(
            "INSERT INTO transactions (transaction_id, kind, listing_id, seller_uuid, seller_name, "
            "buyer_uuid, buyer_name, price, fee, item_summary, status, step, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PROCESSING', ?, ?)",
            (tx_id, kind, listing_id, seller_uuid, seller_name, buyer_uuid, buyer_name, price, fee,
             item_summary, step, self._now()),
        )
        return tx_id

    def get(self, tx_id: str) -> TxRecord | None:
        row = self.db.query_one("SELECT * FROM transactions WHERE transaction_id = ?", (tx_id,))
        return _record(row) if row else None

    def set_step(self, tx_id: str, step: str) -> None:
        """Journal write. Only valid while the transaction is still PROCESSING."""
        n = self.db.execute(
            "UPDATE transactions SET step = ? WHERE transaction_id = ? AND status = 'PROCESSING'",
            (step, tx_id),
        ).rowcount
        if n != 1:
            raise RuntimeError(f"Transaction {tx_id} is not PROCESSING; cannot move to {step}")

    def cas_step(self, tx_id: str, expected: str, new: str) -> bool:
        """Move step ``expected`` -> ``new`` only if nobody else already did."""
        return self.db.execute(
            "UPDATE transactions SET step = ? WHERE transaction_id = ? AND status = 'PROCESSING' AND step = ?",
            (new, tx_id, expected),
        ).rowcount == 1

    def finish(self, tx_id: str, status: str, step: str | None = None, detail: str | None = None,
               payout_state: str | None = None) -> None:
        sets = ["status = ?", "completed_at = ?"]
        params: list = [status, self._now()]
        if step is not None:
            sets.append("step = ?")
            params.append(step)
        if detail is not None:
            sets.append("detail = ?")
            params.append(detail)
        if payout_state is not None:
            sets.append("payout_state = ?")
            params.append(payout_state)
        params.append(tx_id)
        self.db.execute(f"UPDATE transactions SET {', '.join(sets)} WHERE transaction_id = ?", params)

    def set_payout_state(self, tx_id: str, state: str, only_from: tuple[str, ...] | None = None) -> bool:
        sql = "UPDATE transactions SET payout_state = ? WHERE transaction_id = ?"
        params: list = [state, tx_id]
        if only_from:
            sql += f" AND payout_state IN ({', '.join('?' for _ in only_from)})"
            params.extend(only_from)
        return self.db.execute(sql, params).rowcount == 1

    # -- recovery queries ----------------------------------------------------------
    def in_flight(self) -> list[TxRecord]:
        rows = self.db.query("SELECT * FROM transactions WHERE status = 'PROCESSING' ORDER BY created_at")
        return [_record(r) for r in rows]

    def payouts(self, state: str) -> list[TxRecord]:
        rows = self.db.query(
            "SELECT * FROM transactions WHERE kind = 'BUY' AND status = 'COMPLETED' AND payout_state = ? "
            "ORDER BY created_at",
            (state,),
        )
        return [_record(r) for r in rows]

    def needs_review(self) -> list[TxRecord]:
        rows = self.db.query(
            "SELECT * FROM transactions WHERE status = 'NEEDS_REVIEW' OR "
            "(status = 'COMPLETED' AND payout_state = 'REVIEW') ORDER BY created_at"
        )
        return [_record(r) for r in rows]
