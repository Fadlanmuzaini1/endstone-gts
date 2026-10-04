from __future__ import annotations

from typing import Any

from endstone.form import ActionForm, Button

from ..items.details import describe_item
from ..i18n import tr
from . import icons
from .common import Context, ago, clean, confirm, remaining, show_result

VIEW_TITLES = {
    "active": "Active Listings",
    "sold": "Sold",
    "cancelled": "Cancelled",
    "expired": "Expired",
    "reclaim": "Reclaim",
}


def view_title(view: str) -> str:
    return tr(VIEW_TITLES[view])


class ManageUI:
    """MY LISTINGS: Active / Sold / Cancelled / Expired / Reclaim."""

    def __init__(self, ctx: Context):
        self.ctx = ctx

    def open(self, player: Any) -> None:
        ctx = self.ctx
        counts = ctx.listings.counts(str(player.unique_id))
        buttons = [
            Button(f"{view_title(v)} ({counts[v]})", icons.menu(v), on_click=lambda p, v=v: self.view(p, v, 1))
            for v in ("active", "sold", "cancelled", "expired", "reclaim")
        ]
        if counts["reclaim"] > 1:
            buttons.append(Button(tr("Reclaim All"), icons.menu("reclaim"), on_click=lambda p: self._reclaim_all(p)))
        buttons.append(Button(tr("Back"), icons.menu("back"), on_click=lambda p: ctx.ui.main.open(p)))
        content = tr("Active: {n}/{max}", n=counts["active"], max=ctx.config.max_active_listings)
        if counts["reclaim"]:
            content += "\n" + tr("§eYou have {n} item(s) waiting to be reclaimed.", n=counts["reclaim"])
        player.send_form(ActionForm(title=tr("MY LISTINGS"), content=content, buttons=buttons))

    def view(self, player: Any, view: str, page: int) -> None:
        ctx = self.ctx
        pg = ctx.listings.my_listings(str(player.unique_id), view, page)
        buttons = []
        for listing in pg.items:
            label = f"{clean(listing.title)}\n§2{ctx.money(listing.price)}"
            if view == "active":
                label += " §8| §7" + tr("{t} left", t=remaining(listing.expires_at - int(ctx.clock())))
            elif listing.reclaim_state == "PENDING":
                label += " §8| §e" + tr("Reclaim")
            buttons.append(Button(label, icons.item(listing.item_identifier),
                                  on_click=lambda p, lid=listing.id, n=pg.page: self.detail(p, lid, view, n)))
        if pg.page > 1:
            buttons.append(Button(tr("« Previous"), icons.menu("previous"), on_click=lambda p, n=pg.page - 1: self.view(p, view, n)))
        if pg.page < pg.pages:
            buttons.append(Button(tr("Next »"), icons.menu("next"), on_click=lambda p, n=pg.page + 1: self.view(p, view, n)))
        buttons.append(Button(tr("Back"), icons.menu("back"), on_click=lambda p: self.open(p)))
        content = tr("Page {page}/{pages}  |  {total} listing(s)", page=pg.page, pages=pg.pages, total=pg.total)
        if not pg.items:
            content += "\n\n" + tr("§7Nothing here.")
        player.send_form(ActionForm(title=view_title(view), content=content, buttons=buttons))

    def detail(self, player: Any, listing_id: int, view: str, page: int) -> None:
        ctx = self.ctx
        listing = ctx.listings.get(listing_id)
        back = lambda p: self.view(p, view, page)  # noqa: E731
        if listing is None or listing.seller_uuid != str(player.unique_id):
            return back(player)
        now = int(ctx.clock())
        lines = [
            tr("§lItem:§r {v}", v=clean(listing.display_name, 64)),
            tr("§lAmount:§r {v}", v=listing.amount),
            tr("§lPrice:§r {v}", v=ctx.money(listing.price)),
            tr("§lStatus:§r {v}", v=tr(listing.status)),
            tr("§lCreated:§r {v}", v=ago(now - listing.created_at)),
        ]
        if listing.status == "ACTIVE":
            lines.append(tr("§lExpires:§r in {v}", v=remaining(listing.expires_at - now)))
        lines.append(tr("§lListing ID:§r #{v}", v=listing.id))
        props = describe_item(listing.item_data)
        if props:
            lines.append(tr("§lProperties:§r"))
            lines.extend(props)
        buttons = []
        if listing.status == "ACTIVE":
            buttons.append(Button(tr("Cancel Listing"), icons.menu("cancel_listing"), on_click=lambda p: self.confirm_cancel(p, listing_id, view, page)))
        if listing.reclaim_state == "PENDING" and listing.status in ("CANCELLED", "EXPIRED"):
            buttons.append(Button(tr("Reclaim"), icons.menu("reclaim"), on_click=lambda p: self._reclaim(p, listing_id, view, page)))
        if listing.reclaim_state in ("REVIEW", "DELIVERING"):
            lines.append(tr("\n§eThis item is being reviewed by staff."))
        buttons.append(Button(tr("Back"), icons.menu("back"), on_click=back))
        player.send_form(ActionForm(title=tr("Listing"), content="\n".join(lines), buttons=buttons))

    def confirm_cancel(self, player: Any, listing_id: int, view: str, page: int) -> None:
        confirm(
            player, tr("Cancel Listing"), tr("Cancel this listing?"), "YES", "NO",
            on_yes=lambda p: self._cancel(p, listing_id, view, page),
            on_no=lambda p: self.detail(p, listing_id, view, page),
        )

    def _cancel(self, player: Any, listing_id: int, view: str, page: int) -> None:
        result = self.ctx.listings.cancel(player, listing_id)
        player.send_message(("§a" if result.ok else "§c") + "[GTS] " + result.message)
        show_result(player, tr("Cancel Listing"), result, lambda p: self.open(p))

    def _reclaim(self, player: Any, listing_id: int, view: str, page: int) -> None:
        result = self.ctx.listings.reclaim(player, listing_id)
        player.send_message(("§a" if result.ok else "§c") + "[GTS] " + result.message)
        show_result(player, tr("Reclaim"), result, lambda p: self.open(p))

    def _reclaim_all(self, player: Any) -> None:
        result = self.ctx.listings.reclaim_all(player)
        player.send_message(("§a" if result.ok else "§c") + "[GTS] " + result.message)
        show_result(player, tr("Reclaim"), result, lambda p: self.open(p))
