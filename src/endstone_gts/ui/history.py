from __future__ import annotations

from typing import Any

from endstone.form import ActionForm, Button

from ..i18n import tr
from . import icons
from .common import Context, ago, clean


class HistoryUI:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    def open(self, player: Any, page: int = 1) -> None:
        ctx = self.ctx
        pg = ctx.listings.history_page(str(player.unique_id), page)
        now = int(ctx.clock())
        blocks: list[str] = []
        for e in pg.items:
            lines = [f"§l{tr(e.kind)}§r", f"* {clean(e.summary, 64)}"]
            if e.kind == "PURCHASED":
                lines.append(tr("* Seller: {v}", v=clean(e.counterparty or "?", 24)))
                lines.append(tr("* Price: {v}", v=ctx.money(e.price)))
            elif e.kind == "SOLD":
                lines.append(tr("* Buyer: {v}", v=clean(e.counterparty or "?", 24)))
                lines.append(tr("* Price: {v}", v=ctx.money(e.price)))
            lines.append(f"§7* {ago(now - e.timestamp)}")
            blocks.append("\n".join(lines))
        body = "\n\n".join(blocks) if blocks else tr("§7No history yet.")
        buttons = []
        if pg.page > 1:
            buttons.append(Button(tr("« Previous"), icons.menu("previous"), on_click=lambda p, n=pg.page - 1: self.open(p, n)))
        if pg.page < pg.pages:
            buttons.append(Button(tr("Next »"), icons.menu("next"), on_click=lambda p, n=pg.page + 1: self.open(p, n)))
        buttons.append(Button(tr("Back"), icons.menu("back"), on_click=lambda p: ctx.ui.main.open(p)))
        player.send_form(
            ActionForm(title=tr("HISTORY"), content=tr("Page {page}/{pages}\n\n{body}", page=pg.page, pages=pg.pages, body=body), buttons=buttons)
        )
