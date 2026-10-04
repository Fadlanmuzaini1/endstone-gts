from __future__ import annotations

import json
from typing import Any

from endstone.form import ActionForm, Button, Label, ModalForm, Slider, TextInput

from ..common import Result
from ..items.inventory import slot_range
from ..items.serializer import SerializationError
from ..items.validator import ValidationError
from ..items.details import describe_item
from ..i18n import tr
from . import icons
from .common import Context, clean, confirm, remaining, show_result


class SellUI:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    # 1. pick a stack from the inventory ------------------------------------------------
    def open(self, player: Any) -> None:
        ctx = self.ctx
        inv = player.inventory
        buttons: list[Button] = []
        for slot in slot_range(inv):
            stack = inv.get_item(slot)
            try:
                ctx.validator.check_item(stack)
                item = ctx.serializer.serialize(stack)
            except (ValidationError, SerializationError):
                continue
            where = tr("Hotbar {n}", n=slot + 1) if slot < 9 else tr("Slot {n}", n=slot + 1)
            buttons.append(
                Button(f"{clean(item.display_name, 32)} x{item.amount}\n§8{where}", icons.item(item.identifier),
                       on_click=lambda p, s=slot: self.choose(p, s))
            )
        buttons.append(Button(tr("Back"), icons.menu("back"), on_click=lambda p: ctx.ui.main.open(p)))
        content = tr("Select the stack you want to sell.")
        if len(buttons) == 1:
            content = tr("§cYou have nothing you can sell.")
        player.send_form(ActionForm(title=tr("Sell Item"), content=content, buttons=buttons))

    # 2. amount + price -------------------------------------------------------------------
    def choose(self, player: Any, slot: int, error: str = "", price_text: str = "", amount: int | None = None) -> None:
        ctx = self.ctx
        stack = player.inventory.get_item(slot)
        try:
            ctx.validator.check_item(stack)
            item = ctx.serializer.serialize(stack)
        except (ValidationError, SerializationError):
            return self._result(player, Result.fail(tr("That item is no longer in that slot.")))
        available = item.amount
        cfg = ctx.config

        props = describe_item(item.payload)
        controls: list = [
            Label(f"{error + chr(10) if error else ''}§l{clean(item.display_name, 48)}§r  " + tr("(you have {n})", n=available)
                  + ("\n" + "\n".join(props) if props else ""))
        ]
        if available > 1:
            controls.append(Slider(tr("Amount to sell"), 1, available, 1, amount or available))
        else:
            controls.append(Label(tr("Amount to sell: 1")))
        controls.append(
            TextInput(
                tr("Price ({lo} - {hi})", lo=ctx.money(cfg.minimum_price), hi=ctx.money(cfg.maximum_price)),
                tr("whole number, e.g. 10000"),
                price_text,
            )
        )

        def submit(p: Any, data: str) -> None:
            try:
                values = json.loads(data)
                chosen = int(values[1]) if available > 1 else 1
                raw_price = str(values[2] if values[2] is not None else "")
            except (ValueError, IndexError, TypeError):
                return self.choose(p, slot, tr("§cInvalid input."))
            try:
                price = ctx.validator.parse_price(raw_price)
                chosen = ctx.validator.check_amount(chosen, available)
            except ValidationError as exc:
                return self.choose(p, slot, f"§c{exc}", raw_price, chosen)
            self.confirm(p, slot, chosen, price)

        player.send_form(
            ModalForm(
                title=tr("Sell Item"),
                controls=controls,
                submit_button=tr("Continue"),
                on_submit=submit,
                on_close=lambda p: self.open(p),
            )
        )

    # 3. confirmation ---------------------------------------------------------------------
    def confirm(self, player: Any, slot: int, amount: int, price: int) -> None:
        ctx = self.ctx
        stack = player.inventory.get_item(slot)
        try:
            ctx.validator.check_item(stack)
            item = ctx.serializer.serialize(stack, amount=amount)
        except (ValidationError, SerializationError, Exception):
            return self._result(player, Result.fail(tr("That item is no longer in that slot.")))
        cfg = ctx.config
        lines = [
            tr("Sell §l{item} x{amount}§r", item=clean(item.display_name, 48), amount=amount),
            tr("for §l{price}§r?", price=ctx.money(price)),
            "",
            tr("§7Listing lasts {t}.", t=remaining(cfg.expiration_seconds)),
        ]
        if cfg.listing_fee:
            lines.append(tr("§7Listing fee: {v}", v=ctx.money(cfg.listing_fee)))
        if cfg.tax_percent:
            lines.append(tr("§7Sales tax: {v}%", v=cfg.tax_percent))
        props = describe_item(item.payload)
        if props:
            lines += ["", tr("§lProperties:§r"), *props]
        payload = item.payload  # the exact item the player confirmed
        confirm(
            player, tr("Confirm Listing"), "\n".join(lines), "CONFIRM", "CANCEL",
            on_yes=lambda p: self._sell(p, slot, amount, price, payload),
            on_no=lambda p: self.open(p),
        )

    def _sell(self, player: Any, slot: int, amount: int, price: int, payload: str) -> None:
        result = self.ctx.tx.sell(player, slot, amount, price, payload)
        player.send_message(("§a" if result.ok else "§c") + "[GTS] " + result.message)
        self._result(player, result)

    def _result(self, player: Any, result: Result) -> None:
        show_result(player, tr("Sell Item"), result, lambda p: self.ctx.ui.main.open(p))
