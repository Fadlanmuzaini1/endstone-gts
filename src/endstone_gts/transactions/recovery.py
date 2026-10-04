"""Crash recovery. Runs at startup and (for the retryable parts) on the periodic sweep.

Rule of thumb: finish things that are *provably* safe to finish, retry things that were
*provably* not applied, and park everything else for an administrator. Nothing here ever
creates an item or money on a guess, and every step is a compare-and-swap, so running
recovery twice (or two instances racing) changes nothing the second time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..common import Log
from ..database import transactions as T
from ..database.database import Database
from ..database.listings import ListingRepository
from ..database.transactions import TransactionRepository, TxRecord
from ..items.listing import Reclaim, Status
from .manager import (
    CLAIMED,
    CREATED,
    DELIVERING,
    FEE_PAID,
    FEE_REFUND_PENDING,
    FEE_REFUNDING,
    FEE_WITHDRAWING,
    REFUND_PENDING,
    REFUNDING,
    REMOVING,
    WITHDRAWING,
    WITHDRAWN,
    TransactionManager,
)


@dataclass
class RecoverySummary:
    cancelled: int = 0
    refunds_started: int = 0
    payouts_retried: int = 0
    parked: int = 0
    orphans: int = 0
    reclaims_flagged: int = 0
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"cancelled={self.cancelled} refunds={self.refunds_started} payouts={self.payouts_retried} "
            f"parked={self.parked} orphans={self.orphans} reclaims_flagged={self.reclaims_flagged}"
        )


class RecoveryService:
    def __init__(
        self,
        db: Database,
        listings: ListingRepository,
        txs: TransactionRepository,
        manager: TransactionManager,
        log: Log,
    ):
        self.db = db
        self.listings = listings
        self.txs = txs
        self.manager = manager
        self.log = log

    # -- startup -------------------------------------------------------------------
    def run(self) -> RecoverySummary:
        s = RecoverySummary()
        for tx in self.txs.in_flight():
            self._recover(tx, s)
        for listing in self.listings.reclaims_in_flight():
            # Delivery may or may not have reached the player's inventory: never guess.
            with self.db.transaction():
                if self.listings.flag_reclaim_review(listing.id):
                    s.reclaims_flagged += 1
                    self.log.critical(
                        f"Reclaim of listing #{listing.id} ({listing.seller_name}) was interrupted; flagged for review"
                    )
        for listing in self.listings.orphaned_processing():
            with self.db.transaction():
                self.listings.mark_failed(listing.id)
            s.orphans += 1
            self.log.critical(f"Listing #{listing.id} was PROCESSING without a transaction; marked FAILED (item kept)")
        self.retry_pending(s)
        self.log.info(f"Recovery finished: {s}")
        return s

    def _recover(self, tx: TxRecord, s: RecoverySummary) -> None:
        step, tid = tx.step, tx.transaction_id
        self.log.info(f"Recovering {tid} ({tx.kind}) at step {step}")
        if tx.kind == T.BUY:
            if step == CLAIMED:
                # Nothing but the lock happened. Release it.
                with self.db.transaction():
                    self.listings.activate(tx.listing_id)
                    self.txs.finish(tid, T.CANCELLED, detail="recovered: interrupted before payment")
                s.cancelled += 1
            elif step == WITHDRAWN:
                # Money was taken and DELIVERING was never journaled -> no item was given.
                self.txs.cas_step(tid, WITHDRAWN, REFUND_PENDING)
            elif step in (REFUND_PENDING,):
                pass  # handled by retry_pending
            elif step in (WITHDRAWING, DELIVERING, REFUNDING):
                self._park(tx, s, f"interrupted at {step}: outcome unknown")
            else:
                self._park(tx, s, f"unknown BUY step {step}")
        else:  # SELL
            if step == CREATED:
                with self.db.transaction():
                    self.listings.mark_failed(tx.listing_id)
                    self.txs.finish(tid, T.CANCELLED, detail="recovered: interrupted before any change")
                s.cancelled += 1
            elif step == FEE_PAID:
                # Fee taken, item not removed yet (REMOVING never journaled) -> refund the fee.
                self.txs.cas_step(tid, FEE_PAID, FEE_REFUND_PENDING)
            elif step == FEE_REFUND_PENDING:
                pass
            elif step in (FEE_WITHDRAWING, REMOVING, FEE_REFUNDING):
                self._park(tx, s, f"interrupted at {step}: outcome unknown")
            else:
                self._park(tx, s, f"unknown SELL step {step}")

    def _park(self, tx: TxRecord, s: RecoverySummary, detail: str) -> None:
        with self.db.transaction():
            self.listings.mark_failed(tx.listing_id)
            self.txs.finish(tx.transaction_id, T.NEEDS_REVIEW, detail=f"recovery: {detail}")
        s.parked += 1
        self.log.critical(
            f"Transaction {tx.transaction_id} needs review: {detail} "
            f"(listing #{tx.listing_id}, {tx.item_summary}, price {tx.price})"
        )

    # -- retryable work (startup + periodic) ---------------------------------------
    def retry_pending(self, s: RecoverySummary | None = None) -> RecoverySummary:
        s = s or RecoverySummary()
        # A payout caught mid-flight is ambiguous (the seller may or may not have been credited).
        # Payouts are synchronous, so at runtime this only ever finds leftovers from a crash.
        for tx in self.txs.payouts("PAYING"):
            self.txs.set_payout_state(tx.transaction_id, "REVIEW", only_from=("PAYING",))
            self.log.critical(f"Payout for {tx.transaction_id} was interrupted; flagged for review")
            s.parked += 1
        if not self.manager.economy.is_available():
            return s
        for tx in self.txs.in_flight():
            if tx.step in (REFUND_PENDING, FEE_REFUND_PENDING) and self.manager.process_refund(tx):
                s.refunds_started += 1
        for tx in self.txs.payouts("PENDING"):
            if self.manager.pay_seller(tx):
                s.payouts_retried += 1
        return s


class AdminResolver:
    """Manual resolution of parked items. The admin states what really happened; each action
    is a guarded transition, so resolving twice (or the wrong record) is refused."""

    TX_ACTIONS = {
        "BUY": ("sold", "activate", "close"),
        "SELL": ("activate", "close"),
        "PAYOUT": ("paid", "retry"),
    }

    def __init__(self, db: Database, listings: ListingRepository, txs: TransactionRepository, log: Log):
        self.db = db
        self.listings = listings
        self.txs = txs
        self.log = log

    def describe(self, tx: TxRecord) -> str:
        who = f"seller={tx.seller_name}" + (f" buyer={tx.buyer_name}" if tx.buyer_name else "")
        if tx.status == "COMPLETED":
            return f"{tx.transaction_id} {tx.kind} payout={tx.payout_state} listing=#{tx.listing_id} {tx.item_summary} price={tx.price} {who}"
        return (f"{tx.transaction_id} {tx.kind} step={tx.step} listing=#{tx.listing_id} {tx.item_summary} "
                f"price={tx.price} fee={tx.fee} {who} note={tx.detail}")

    def resolve_tx(self, tx_id: str, action: str):
        from ..common import Result

        tx = self.txs.get(tx_id.upper())
        if tx is None:
            return Result.fail("Unknown transaction.")
        action = action.lower()
        with self.db.transaction():
            if tx.status == "COMPLETED" and tx.payout_state == "REVIEW":
                if action not in self.TX_ACTIONS["PAYOUT"]:
                    return Result.fail("Valid actions for a payout review: paid, retry.")
                self.txs.set_payout_state(tx.transaction_id, "PAID" if action == "paid" else "PENDING",
                                          only_from=("REVIEW",))
                msg = "marked as paid" if action == "paid" else "will be retried on the next sweep"
            elif tx.status == "NEEDS_REVIEW":
                if action not in self.TX_ACTIONS[tx.kind]:
                    return Result.fail(f"Valid actions for a {tx.kind} review: {', '.join(self.TX_ACTIONS[tx.kind])}.")
                if tx.kind == "BUY" and action == "sold":
                    self.listings.mark_sold(tx.listing_id)
                    self.txs.finish(tx.transaction_id, "COMPLETED", step="DELIVERED",
                                    detail="resolved by admin: sold", payout_state="PENDING")
                    msg = "completed; the seller will be paid on the next sweep"
                elif action == "activate":
                    self.listings.activate(tx.listing_id, from_status="FAILED")
                    if tx.kind == "SELL":
                        self.txs.finish(tx.transaction_id, "COMPLETED", step="ACTIVE", detail="resolved by admin: activated")
                        msg = "listing is ACTIVE"
                    else:
                        self.txs.finish(tx.transaction_id, "FAILED", detail="resolved by admin: relisted")
                        msg = "listing is ACTIVE again. Refund the buyer manually if they were charged"
                else:  # close
                    self.listings.mark_failed(tx.listing_id)
                    self.txs.finish(tx.transaction_id, "FAILED", detail="resolved by admin: closed")
                    msg = "closed; the listing stays out of the market"
            else:
                return Result.fail("That transaction does not need review.")
        self.log.warning(f"Admin resolved {tx.transaction_id}: {action}")
        return Result.success(f"{tx.transaction_id} {msg}.")

    def resolve_reclaim(self, listing_id: int, action: str):
        from ..common import Result

        action = action.lower()
        if action not in ("delivered", "retry"):
            return Result.fail("Valid actions: delivered, retry.")
        with self.db.transaction():
            ok = self.listings.resolve_reclaim_review(listing_id, delivered=(action == "delivered"))
        if not ok:
            return Result.fail("That listing is not waiting for a reclaim review.")
        self.log.warning(f"Admin resolved reclaim of listing #{listing_id}: {action}")
        return Result.success(f"Listing #{listing_id}: {action}.")
