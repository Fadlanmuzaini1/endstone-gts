from __future__ import annotations

from typing import Any

from endstone.form import ActionForm, Button

from ..i18n import tr
from . import icons
from .common import Context


class MainMenu:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    def open(self, player: Any) -> None:
        ui = self.ctx.ui
        player.send_form(
            ActionForm(
                title=tr("GLOBAL TRADING STATION"),
                content=tr("Buy and sell items with other players."),
                buttons=[
                    Button(tr("Browse Items"), icons.menu("browse"), on_click=lambda p: ui.browse.open(p)),
                    Button(tr("Sell Item"), icons.menu("sell"), on_click=lambda p: ui.sell.open(p)),
                    Button(tr("My Listings"), icons.menu("listings"), on_click=lambda p: ui.manage.open(p)),
                    Button(tr("History"), icons.menu("history"), on_click=lambda p: ui.history.open(p)),
                    Button(tr("Close"), icons.menu("close")),
                ],
            )
        )
