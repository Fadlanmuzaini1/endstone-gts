"""Listing lifecycle that does not involve money: browse, cancel, expire, reclaim."""

from __future__ import annotations

from typing import Any

from ..common import Log, Result
from ..config.config import GtsConfig
from ..i18n import tr
from ..database.database import Database
from ..database.history import HistoryRepository
from ..database.listings import ListingRepository, Page
from ..items.inventory import ItemDelivery
from ..items.listing import Listing, Reclaim, Status


class ListingManager:
    def __init__(
        self,
        db: Database,
        repo: ListingRepository,
        history: HistoryRepository,
        delivery: ItemDelivery,
        config: GtsConfig,
        log: Log,
    ):
        self.db = db
        self.repo = repo
        self.history = history
        self.delivery = delivery
        self.config = config
        self.log = log

    # -- queries -------------------------------------------------------------------
    def browse(self, page: int, search: str | None = None) -> Page:
        return self.repo.browse(page, self.config.items_per_page, search)

    def my_listings(self, seller_uuid: str, view: str, page: int) -> Page:
        return self.repo.by_seller(seller_uuid, view, page, self.config.items_per_page)

    def counts(self, seller_uuid: str) -> dict[str, int]:
        return self.repo.seller_counts(seller_uuid)

    def history_page(self, player_uuid: str, page: int) -> Page:
        return self.history.page(player_uuid, page, self.config.items_per_page)

    def get(self, listing_id: int) -> Listing | None:
        return self.repo.get(listing_id)

    # -- cancel --------------------------------------------------------------------
    def cancel(self, player: Any, listing_id: int) -> Result:
        """ACTIVE -> CANCELLED atomically; the item is then handed back (or stays reclaimable)."""
        seller_uuid = str(player.unique_id)
        with self.db.transaction():
            if not self.repo.cancel(listing_id, seller_uuid):
                return Result.fail(tr("That listing can no longer be cancelled (sold, expired or not yours)."))
        self.log.info(f"Listing #{listing_id} cancelled by {player.name}")
        back = self.reclaim(player, listing_id)
        if back.ok:
            return Result.success(tr("Listing cancelled. The item was returned to your inventory."),
                                  listing_id=listing_id)
        return Result.success(
            tr("Listing cancelled. Your item is safe: free some inventory space, then open My Listings > Reclaim."),
            listing_id=listing_id,
        )

    # -- reclaim -------------------------------------------------------------------
    def reclaim(self, player: Any, listing_id: int) -> Result:
        seller_uuid = str(player.unique_id)
        listing = self.repo.get(listing_id)
        if (
            listing is None
            or listing.seller_uuid != seller_uuid
            or listing.reclaim_state != Reclaim.PENDING
            or listing.status not in (Status.CANCELLED, Status.EXPIRED)
        ):
            return Result.fail(tr("Nothing to reclaim for that listing."))
        if not self.delivery.can_deliver(player, listing.item_data):
            return Result.fail(tr("Your inventory is full. Free some space and try again."))

        with self.db.transaction():
            if not self.repo.begin_reclaim(listing_id, seller_uuid):
                return Result.fail(tr("That item was already reclaimed."))
        res = self.delivery.deliver(player, listing.item_data)
        if res.delivered:
            with self.db.transaction():
                self.repo.finish_reclaim(listing_id)
            self.log.info(f"Listing #{listing_id} reclaimed by {player.name}")
            return Result.success(tr("You reclaimed {title}.", title=listing.title), listing_id=listing_id)
        if res.ambiguous:
            with self.db.transaction():
                self.repo.flag_reclaim_review(listing_id)
            self.log.critical(
                f"Reclaim of listing #{listing_id} by {player.name} is ambiguous ({res.reason}). "
                f"Flagged for review; item payload: {listing.item_data}"
            )
            return Result.fail(tr("Something went wrong. An administrator must review this listing."))
        with self.db.transaction():
            self.repo.abort_reclaim(listing_id)
        return Result.fail(tr("Could not return the item ({reason}). It is still safe; try again.", reason=res.reason))

    def reclaim_all(self, player: Any) -> Result:
        pending = self.repo.pending_reclaims(str(player.unique_id))
        if not pending:
            return Result.fail(tr("You have nothing to reclaim."))
        done = 0
        last_failure = ""
        for listing in pending:
            res = self.reclaim(player, listing.id)
            if res.ok:
                done += 1
            else:
                last_failure = res.message
                break
        if done and not last_failure:
            return Result.success(tr("Reclaimed {n} item(s).", n=done))
        if done:
            return Result.success(tr("Reclaimed {n} item(s). {extra}", n=done, extra=last_failure))
        return Result.fail(last_failure)

    # -- expiration ----------------------------------------------------------------
    def expire_due(self) -> int:
        """Expired listings are never deleted: they become reclaimable by their seller."""
        with self.db.transaction():
            n = self.repo.expire_due()
        if n:
            self.log.info(f"{n} listing(s) expired")
        return n
