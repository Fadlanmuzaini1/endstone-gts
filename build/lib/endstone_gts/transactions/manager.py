"""Sell and buy sagas.

Money (economy plugin) and items (game inventory) live outside SQLite, so no single database
transaction can cover them. Instead every step is journaled in ``transactions.step`` BEFORE
the side effect and the side effect's outcome is classified as success / definite failure /
ambiguous. Ambiguity is never "fixed" automatically: it is parked as NEEDS_REVIEW with the
item still held by GTS, because guessing wrong would duplicate money or an item.

BUY steps:   CLAIMED -> WITHDRAWING -> WITHDRAWN -> DELIVERING -> (DELIVERED = COMPLETED)
             failure after WITHDRAWN: REFUND_PENDING -> REFUNDING -> (FAILED, listing ACTIVE again)
SELL steps:  CREATED -> [FEE_WITHDRAWING -> FEE_PAID] -> REMOVING -> (ACTIVE = COMPLETED)
             failure after FEE_PAID: FEE_REFUND_PENDING -> FEE_REFUNDING -> (FAILED)

All of this runs synchronously on the server thread: a purchase can never be interleaved with
another one in-process, and the compare-and-swap UPDATEs protect against everything else.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from ..common import Log, Result
from ..config.config import GtsConfig
from ..database import transactions as T
from ..i18n import tr
from ..database.database import Database
from ..database.listings import ListingRepository
from ..database.transactions import TransactionRepository, TxRecord
from ..economy.provider import Account, EconomyAmbiguous, EconomyError, EconomyProvider, EconomyUnavailable
from ..items.inventory import ItemDelivery
from ..items.listing import ItemListing, Reclaim, Status
from ..items.serializer import ItemSerializer, SerializationError
from ..items.validator import ListingValidator, ValidationError

# BUY steps
CLAIMED = "CLAIMED"
WITHDRAWING = "WITHDRAWING"
WITHDRAWN = "WITHDRAWN"
DELIVERING = "DELIVERING"
DELIVERED = "DELIVERED"
REFUND_PENDING = "REFUND_PENDING"
REFUNDING = "REFUNDING"
# SELL steps
CREATED = "CREATED"
FEE_WITHDRAWING = "FEE_WITHDRAWING"
FEE_PAID = "FEE_PAID"
REMOVING = "REMOVING"
ACTIVE = "ACTIVE"
FEE_REFUND_PENDING = "FEE_REFUND_PENDING"
FEE_REFUNDING = "FEE_REFUNDING"

REVIEW_MESSAGE = (
    "Something went wrong and this transaction was put on hold for an administrator. "
    "Nothing was duplicated. Please contact staff and quote transaction {tx}."
)  # translated at use: tr(REVIEW_MESSAGE, tx=...)


def account_of(player: Any) -> Account:
    return Account(str(player.unique_id), str(player.name))


class TransactionManager:
    def __init__(
        self,
        db: Database,
        listings: ListingRepository,
        txs: TransactionRepository,
        economy: EconomyProvider,
        serializer: ItemSerializer,
        delivery: ItemDelivery,
        config: GtsConfig,
        log: Log,
        clock: Callable[[], float] = time.time,
        notify: Callable[[str, str], None] | None = None,
    ):
        self.db = db
        self.listings = listings
        self.txs = txs
        self.economy = economy
        self.serializer = serializer
        self.delivery = delivery
        self.config = config
        self.validator = ListingValidator(config)
        self.log = log
        self._clock = clock
        self._notify = notify or (lambda uuid, msg: None)
        self._busy: set[str] = set()

    def set_config(self, config: GtsConfig) -> None:
        self.config = config
        self.validator = ListingValidator(config)

    # ===================================================================== SELL
    def sell(self, player: Any, slot: int, amount: int, price: int, expected_payload: str) -> Result:
        uid = str(player.unique_id)
        if not self.config.enabled:
            return Result.fail(tr("The GTS is currently disabled."))
        if uid in self._busy:
            return Result.fail(tr("Please wait, your previous request is still being processed."))
        self._busy.add(uid)
        try:
            return self._sell(player, slot, amount, price, expected_payload)
        finally:
            self._busy.discard(uid)

    def _sell(self, player: Any, slot: int, amount: int, price: int, expected_payload: str) -> Result:
        cfg = self.config
        seller = account_of(player)
        # 1-3. Validate item, amount, price. Nothing is touched yet.
        try:
            price = self.validator.check_price(price)
            stack = player.inventory.get_item(slot)
            self.validator.check_item(stack)
            amount = self.validator.check_amount(amount, int(stack.amount))
            item = self.serializer.serialize(stack, amount=amount)
        except ValidationError as exc:
            return Result.fail(str(exc))
        except SerializationError:
            return Result.fail(tr("That item cannot be listed."))
        if item.payload != expected_payload:
            return Result.fail(tr("The item changed since you selected it. Please try again."))

        # 4-5. Serialization must be exact, otherwise the item could come back different.
        if item.lossy and not cfg.allow_lossy_serialization:
            return Result.fail(tr("That item has data the GTS cannot store safely, so it cannot be listed."))
        if cfg.strict_item_roundtrip and not self.serializer.verify_roundtrip(item):
            self.log.warning(
                f"Refused listing from {seller.name}: {item.identifier} failed the serialization round trip"
            )
            return Result.fail(tr("That item cannot be stored without losing data, so it cannot be listed."))

        fee = cfg.listing_fee
        if fee > 0:
            if not self.economy.is_available():
                return Result.fail(tr("Economy unavailable: {reason}", reason=self.economy.unavailable_reason()))
            try:
                if not self.economy.has_balance(seller, fee):
                    return Result.fail(tr("You need {amount} for the listing fee.", amount=cfg.format_money(fee)))
            except EconomyError as exc:
                return Result.fail(tr("Economy unavailable: {reason}", reason=exc))

        # 6. Persist the listing as PROCESSING (not visible, not buyable) + journal. The limit
        # check and the insert share one IMMEDIATE transaction, so the limit cannot be raced.
        now = int(self._clock())
        summary = f"{item.display_name} x{item.amount}"
        with self.db.transaction():
            if self.listings.count_open_by_seller(seller.uuid) >= cfg.max_active_listings:
                return Result.fail(tr("Cannot create another listing."))
            listing_id = self.listings.create_processing(
                ItemListing.TYPE, seller.uuid, seller.name, item.payload, item.identifier,
                item.display_name, item.search_text, item.amount, price, now + cfg.expiration_seconds,
            )
            tx = self.txs.create(
                T.SELL, listing_id, seller.uuid, seller.name, price, summary, CREATED, fee=fee
            )

        # Listing fee (only kept if the listing really gets created).
        fee_paid = False
        if fee > 0:
            self.txs.set_step(tx, FEE_WITHDRAWING)
            try:
                ok = self.economy.withdraw(seller, fee)
            except EconomyAmbiguous as exc:
                return self._park(tx, listing_id, f"fee withdraw ambiguous: {exc}")
            except EconomyError:
                return self._abort_sell(tx, listing_id, "economy unavailable", tr("Economy unavailable."))
            if not ok:
                return self._abort_sell(tx, listing_id, "fee refused", tr("You need {amount}.", amount=cfg.format_money(fee)))
            fee_paid = True
            self.txs.set_step(tx, FEE_PAID)

        # 7. Remove the exact amount, only after everything else succeeded.
        self.txs.set_step(tx, REMOVING)
        removal = self.delivery.remove_from_slot(player, slot, expected_payload, item.amount)
        if removal.ambiguous:
            return self._park(tx, listing_id, f"item removal ambiguous: {removal.reason}")
        if not removal.removed:
            if fee_paid:
                self._refund_fee(tx, listing_id, seller, fee)
            else:
                self._abort_sell(tx, listing_id, removal.reason, "")
            return Result.fail(tr("Could not take the item ({reason}). Nothing was listed.", reason=removal.reason))

        # 8. Make it ACTIVE. If the database fails now, give the item straight back.
        try:
            with self.db.transaction():
                if not self.listings.activate(listing_id):
                    raise RuntimeError("listing was not in PROCESSING")
                self.txs.finish(tx, T.COMPLETED, step=ACTIVE)
        except Exception as exc:
            self.log.critical(f"Listing #{listing_id} could not be activated ({exc}); returning the item")
            back = self.delivery.deliver(player, item.payload)
            if back.delivered:
                self._safe(lambda: self._close_sell_failed(tx, listing_id, f"activation failed: {exc}"))
                return Result.fail(tr("Could not create the listing. Your item was returned."))
            self.log.critical(
                f"ITEM AT RISK: seller={seller.name} listing=#{listing_id} tx={tx} payload={item.payload}"
            )
            return Result.fail(tr(REVIEW_MESSAGE, tx=tx))

        self.log.info(f"Listing #{listing_id} created")
        self.log.info(f"Seller: {seller.name}")
        self.log.info(f"Item: {item.identifier}")
        self.log.info(f"Amount: {item.amount}")
        self.log.info(f"Price: {price}")
        return Result.success(
            tr("Listed {summary} for {price}.", summary=summary, price=cfg.format_money(price)), listing_id=listing_id, transaction_id=tx
        )

    def _abort_sell(self, tx: str, listing_id: int, detail: str, message: str) -> Result:
        self._close_sell_failed(tx, listing_id, detail)
        return Result.fail(message or tr("Could not create the listing."), listing_id=listing_id)

    def _close_sell_failed(self, tx: str, listing_id: int, detail: str) -> None:
        with self.db.transaction():
            self.listings.mark_failed(listing_id)
            self.txs.finish(tx, T.FAILED, detail=detail)

    def _refund_fee(self, tx: str, listing_id: int, seller: Account, fee: int) -> None:
        self.txs.set_step(tx, FEE_REFUND_PENDING)
        record = self.txs.get(tx)
        if record is not None:
            self.process_refund(record)

    # ===================================================================== BUY
    def buy(self, player: Any, listing_id: int) -> Result:
        uid = str(player.unique_id)
        if not self.config.enabled:
            return Result.fail(tr("The GTS is currently disabled."))
        if uid in self._busy:
            return Result.fail(tr("Please wait, your previous request is still being processed."))
        self._busy.add(uid)
        try:
            return self._buy(player, listing_id)
        finally:
            self._busy.discard(uid)

    def _buy(self, player: Any, listing_id: int) -> Result:
        cfg = self.config
        buyer = account_of(player)
        listing = self.listings.get(listing_id)
        # Cheap pre-checks (the real, race-proof check is the claim below).
        if listing is None or listing.status != Status.ACTIVE or listing.expires_at <= int(self._clock()):
            return Result.fail(tr("That listing is no longer available."))
        if listing.seller_uuid == buyer.uuid:
            return Result.fail(tr("You cannot buy your own listing."))
        if not self.economy.is_available():
            return Result.fail(tr("Economy unavailable: {reason}", reason=self.economy.unavailable_reason()))
        try:
            if not self.economy.has_balance(buyer, listing.price):
                return Result.fail(tr("You need {amount} to buy this.", amount=cfg.format_money(listing.price)))
        except EconomyError as exc:
            return Result.fail(tr("Economy unavailable: {reason}", reason=exc))
        if not self.delivery.can_deliver(player, listing.item_data):
            return Result.fail(tr("Your inventory is full. Make some room and try again."))

        tax = listing.price * cfg.tax_percent // 100
        summary = listing.title

        # Lock the listing: ACTIVE -> PROCESSING, and open the journal, in one transaction.
        with self.db.transaction():
            if not self.listings.claim_for_purchase(listing_id, buyer.uuid):
                return Result.fail(tr("That listing is no longer available."))
            tx = self.txs.create(
                T.BUY, listing_id, listing.seller_uuid, listing.seller_name, listing.price, summary,
                CLAIMED, buyer_uuid=buyer.uuid, buyer_name=buyer.name, fee=tax,
            )
        self.log.info(f"Transaction {tx} started")
        self.log.info(f"Buyer: {buyer.name}")
        self.log.info(f"Seller: {listing.seller_name}")
        self.log.info(f"Listing: #{listing_id}")
        self.log.info(f"Price: {listing.price}")

        # --- take the money ---
        self.txs.set_step(tx, WITHDRAWING)
        try:
            ok = self.economy.withdraw(buyer, listing.price)
        except EconomyAmbiguous as exc:
            return self._park(tx, listing_id, f"withdraw ambiguous: {exc}")
        except EconomyError as exc:
            self._rollback_buy(tx, listing_id, f"withdraw not attempted: {exc}")
            return Result.fail(tr("Economy unavailable. You were not charged."))
        if not ok:
            self._rollback_buy(tx, listing_id, "withdraw refused")
            return Result.fail(tr("You need {amount} to buy this.", amount=cfg.format_money(listing.price)))
        self.txs.set_step(tx, WITHDRAWN)

        # --- give the item ---
        self.txs.set_step(tx, DELIVERING)
        res = self.delivery.deliver(player, listing.item_data)
        if res.ambiguous:
            self.log.critical(f"Delivery ambiguous for {tx}: {res.reason}; buyer was charged")
            return self._park(tx, listing_id, f"delivery ambiguous: {res.reason}")
        if not res.delivered:
            # Definitely no item given: give the money back and re-list.
            self.txs.set_step(tx, REFUND_PENDING)
            record = self.txs.get(tx)
            refunded = self.process_refund(record) if record else False
            if refunded:
                return Result.fail(tr("Purchase failed ({reason}). You were not charged.", reason=res.reason))
            return Result.fail(
                tr("Purchase failed ({reason}). Your {amount} will be refunded automatically (transaction {tx}).",
                   reason=res.reason, amount=cfg.format_money(listing.price), tx=tx)
            )

        # --- item is with the buyer: this is the point of no return ---
        try:
            with self.db.transaction():
                self.listings.mark_sold(listing_id)
                self.txs.finish(
                    tx, T.COMPLETED, step=DELIVERED,
                    payout_state="PENDING" if listing.price - tax > 0 else "PAID",
                )
        except Exception as exc:
            self.log.critical(f"Transaction {tx}: item delivered but the database failed: {exc}")
            return Result.success(tr("You bought {summary}.", summary=summary), listing_id=listing_id, transaction_id=tx)

        record = self.txs.get(tx)
        if record is not None:
            self.pay_seller(record)
        self.log.info(f"Transaction {tx} completed")
        return Result.success(
            tr("You bought {summary} for {price}.", summary=summary, price=cfg.format_money(listing.price)),
            listing_id=listing_id, transaction_id=tx,
        )

    def _rollback_buy(self, tx: str, listing_id: int, detail: str) -> None:
        with self.db.transaction():
            self.listings.activate(listing_id)
            self.txs.finish(tx, T.FAILED, detail=detail)

    # ===================================================================== shared steps
    def pay_seller(self, tx: TxRecord) -> bool:
        """Credit the seller. Idempotent: the PENDING->PAYING swap lets exactly one caller pay."""
        payout = tx.price - tx.fee
        if payout <= 0:
            self.txs.set_payout_state(tx.transaction_id, "PAID", only_from=("PENDING",))
            return True
        if not self.txs.set_payout_state(tx.transaction_id, "PAYING", only_from=("PENDING",)):
            return False
        seller = Account(tx.seller_uuid, tx.seller_name)
        try:
            ok = self.economy.deposit(seller, payout)
        except EconomyAmbiguous as exc:
            self.txs.set_payout_state(tx.transaction_id, "REVIEW")
            self.log.critical(f"Payout for {tx.transaction_id} is ambiguous ({exc}); needs review")
            return False
        except EconomyError:
            self.txs.set_payout_state(tx.transaction_id, "PENDING", only_from=("PAYING",))
            self.log.warning(f"Payout for {tx.transaction_id} postponed: economy unavailable")
            return False
        if not ok:
            self.txs.set_payout_state(tx.transaction_id, "PENDING", only_from=("PAYING",))
            self.log.warning(f"Payout for {tx.transaction_id} refused by the economy; will retry")
            return False
        self.txs.set_payout_state(tx.transaction_id, "PAID", only_from=("PAYING",))
        self._safe(lambda: self._notify(
            tx.seller_uuid,
            tr("Your {summary} sold to {buyer} for {amount}.", summary=tx.item_summary, buyer=tx.buyer_name, amount=self.config.format_money(payout)),
        ))
        return True

    def process_refund(self, tx: TxRecord) -> bool:
        """Return money taken for a transaction that did not complete. Idempotent."""
        if tx.kind == T.BUY:
            pending, running = REFUND_PENDING, REFUNDING
            account = Account(tx.buyer_uuid or "", tx.buyer_name or "")
            amount = tx.price
        else:
            pending, running = FEE_REFUND_PENDING, FEE_REFUNDING
            account = Account(tx.seller_uuid, tx.seller_name)
            amount = tx.fee
        if not self.txs.cas_step(tx.transaction_id, pending, running):
            return False
        try:
            ok = self.economy.deposit(account, amount)
        except EconomyAmbiguous as exc:
            self._park(tx.transaction_id, tx.listing_id, f"refund ambiguous: {exc}")
            return False
        except EconomyError:
            self.txs.cas_step(tx.transaction_id, running, pending)
            return False
        if not ok:
            self.txs.cas_step(tx.transaction_id, running, pending)
            return False
        with self.db.transaction():
            if tx.kind == T.BUY:
                self.listings.activate(tx.listing_id)
            else:
                self.listings.mark_failed(tx.listing_id)
            self.txs.finish(tx.transaction_id, T.FAILED, detail="refunded")
        self.log.info(f"Transaction {tx.transaction_id} refunded and closed")
        return True

    def _park(self, tx: str, listing_id: int, detail: str) -> Result:
        """Hold a transaction whose outcome is unknown. The item stays inside GTS."""
        self.log.critical(f"Transaction {tx} parked for review: {detail}")
        self._safe(lambda: self._park_db(tx, listing_id, detail))
        return Result.fail(tr(REVIEW_MESSAGE, tx=tx), listing_id=listing_id, transaction_id=tx)

    def _park_db(self, tx: str, listing_id: int, detail: str) -> None:
        with self.db.transaction():
            self.listings.mark_failed(listing_id)
            self.txs.finish(tx, T.NEEDS_REVIEW, detail=detail)

    def _safe(self, fn: Callable[[], Any]) -> None:
        try:
            fn()
        except Exception as exc:  # never let bookkeeping break the caller
            self.log.error(f"Non-fatal error: {exc}")
