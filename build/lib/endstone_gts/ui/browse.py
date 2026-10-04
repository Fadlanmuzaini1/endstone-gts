from __future__ import annotations

import json
from typing import Any

from endstone.form import ActionForm, Button, Label, ModalForm, TextInput

from ..items.details import describe_item, short_tags
from ..i18n import tr
from . import icons
from .common import Context, clean, confirm, remaining, show_result


class BrowseUI:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    # -- list ----------------------------------------------------------------------
    def open(self, player: Any, page: int = 1, query: str | None = None) -> None:
        ctx = self.ctx
        pg = ctx.listings.browse(page, query)
        buttons: list[Button] = []
        for listing in pg.items:
            label = (
                f"{clean(listing.title)}{short_tags(listing.item_data)}\n"
                f"§7{clean(listing.seller_name, 16)} §8| §2{ctx.money(listing.price)}"
            )
            buttons.append(
                Button(label, icons.item(listing.item_identifier),
                       on_click=lambda p, lid=listing.id, n=pg.page: self.detail(p, lid, n, query))
            )
        if pg.page > 1:
            buttons.append(Button(tr("« Previous"), icons.menu("previous"), on_click=lambda p, n=pg.page - 1: self.open(p, n, query)))
        if pg.page < pg.pages:
            buttons.append(Button(tr("Next »"), icons.menu("next"), on_click=lambda p, n=pg.page + 1: self.open(p, n, query)))
        buttons.append(Button(tr("Search"), icons.menu("search"), on_click=lambda p: self.search_form(p)))
        if query:
            buttons.append(Button(tr("Clear Search"), icons.menu("clear"), on_click=lambda p: self.open(p)))
        buttons.append(Button(tr("Back"), icons.menu("back"), on_click=lambda p: ctx.ui.main.open(p)))

        header = tr("Page {page}/{pages}  |  {total} listing(s)", page=pg.page, pages=pg.pages, total=pg.total)
        if query:
            header += "\n" + tr("§7Search: §f{query}", query=clean(query, 32))
        if not pg.items:
            header += "\n\n" + tr("§7No listings found.")
        player.send_form(ActionForm(title=tr("GLOBAL MARKET"), content=header, buttons=buttons))

    # -- search --------------------------------------------------------------------
    def search_form(self, player: Any) -> None:
        def submit(p: Any, data: str) -> None:
            try:
                values = json.loads(data)
                text = str(values[0] or "").strip()[:64]
            except (ValueError, IndexError, TypeError):
                text = ""
            self.open(p, 1, text or None)

        player.send_form(
            ModalForm(
                title=tr("Search Items"),
                controls=[TextInput(tr("Item name or identifier"), tr("e.g. diamond"), "")],
                submit_button=tr("Search"),
                on_submit=submit,
                on_close=lambda p: self.open(p),
            )
        )

    # -- detail --------------------------------------------------------------------
    def detail(self, player: Any, listing_id: int, page: int, query: str | None) -> None:
        ctx = self.ctx
        listing = ctx.listings.get(listing_id)
        back = lambda p: self.open(p, page, query)  # noqa: E731
        if listing is None or not listing.is_active:
            player.send_form(
                ActionForm(title=tr("Listing"), content=tr("§cThat listing is no longer available."),
                           buttons=[Button(tr("Back"), icons.menu("back"), on_click=back)])
            )
            return
        own = listing.seller_uuid == str(player.unique_id)
        content = (
            tr("§lItem:§r\n{v}\n\n", v=clean(listing.display_name, 64))
            + tr("§lAmount:§r\n{v}\n\n", v=listing.amount)
            + tr("§lSeller:§r\n{v}\n\n", v=clean(listing.seller_name, 24))
            + tr("§lPrice:§r\n{v}\n\n", v=ctx.money(listing.price))
            + tr("§lListing ID:§r\n#{v}\n\n", v=listing.id)
        )
        props = describe_item(listing.item_data)
        if props:
            content += tr("§lProperties:§r") + "\n" + "\n".join(props) + "\n\n"
        content += tr("§7Expires in {t}", t=remaining(listing.expires_at - int(ctx.clock())))
        buttons = []
        if own:
            content += "\n\n" + tr("§eThis is your listing. Manage it from My Listings.")
        else:
            buttons.append(Button(tr("BUY"), icons.item(listing.item_identifier) or icons.menu("buy"), on_click=lambda p: self.confirm_buy(p, listing_id, page, query)))
        buttons.append(Button(tr("BACK"), icons.menu("back"), on_click=back))
        player.send_form(ActionForm(title=tr("Item"), content=content, buttons=buttons))

    def confirm_buy(self, player: Any, listing_id: int, page: int, query: str | None) -> None:
        ctx = self.ctx
        listing = ctx.listings.get(listing_id)
        if listing is None or not listing.is_active:
            return self.detail(player, listing_id, page, query)
        confirm(
            player,
            tr("Confirm Purchase"),
            tr("Are you sure you want to buy:\n\n§l{item}§r\n\nfor:\n\n§l{price}", item=clean(listing.title, 64), price=ctx.money(listing.price)),
            "CONFIRM",
            "CANCEL",
            on_yes=lambda p: self._buy(p, listing_id, page, query),
            on_no=lambda p: self.detail(p, listing_id, page, query),
        )

    def _buy(self, player: Any, listing_id: int, page: int, query: str | None) -> None:
        result = self.ctx.tx.buy(player, listing_id)
        player.send_message(("§a" if result.ok else "§c") + "[GTS] " + result.message)
        show_result(player, tr("Purchase"), result, lambda p: self.open(p, page, query))
