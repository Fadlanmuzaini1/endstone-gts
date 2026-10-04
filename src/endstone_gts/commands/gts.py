"""/gts command handling. The GUI is the main interface; subcommands are shortcuts into it."""

from __future__ import annotations

from typing import Any

from ..i18n import tr

PERM_USE = "gts.command.use"
PERM_ADMIN = "gts.command.admin"

PLAYER_ACTIONS = ("sell", "items", "listings", "history", "search")
ADMIN_ACTIONS = ("reload", "review", "resolve", "inspect")


class GtsCommand:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def handle(self, sender: Any, args: list[str]) -> bool:
        p = self.plugin
        action = args[0].lower() if args else ""

        if action in ADMIN_ACTIONS:
            if not sender.has_permission(PERM_ADMIN):
                sender.send_error_message(tr("You do not have permission to do that."))
                return True
            return getattr(self, f"_{action}")(sender, args[1:])

        player = self._player(sender)
        if player is None:
            sender.send_error_message(tr("This command can only be used by a player."))
            return True
        if not p.services_ready:
            sender.send_error_message(tr("GTS is not available right now."))
            return True
        if not p.gts_config.enabled:
            sender.send_error_message(tr("The GTS is currently disabled."))
            return True

        ui = p.ctx.ui
        if action == "":
            ui.main.open(player)
        elif action == "sell":
            ui.sell.open(player)
        elif action == "items":
            ui.browse.open(player)
        elif action == "listings":
            ui.manage.open(player)
        elif action == "history":
            ui.history.open(player)
        elif action == "search":
            query = " ".join(args[1:]).strip()[:64]
            if query:
                ui.browse.open(player, 1, query)
            else:
                ui.browse.search_form(player)
        else:
            sender.send_error_message(tr("Usage: /gts [sell|items|listings|history|search <query>]"))
        return True

    @staticmethod
    def _player(sender: Any) -> Any | None:
        # Players expose an inventory; console / command blocks do not.
        return sender if hasattr(sender, "inventory") else None

    # -- admin ---------------------------------------------------------------------
    def _reload(self, sender: Any, args: list[str]) -> bool:
        for line in self.plugin.reload_gts():
            sender.send_message(line)
        return True

    def _review(self, sender: Any, args: list[str]) -> bool:
        svc = self.plugin
        txs = svc.txs.needs_review()
        reclaims = svc.listing_repo.reclaims_in_review()
        if not txs and not reclaims:
            sender.send_message("[GTS] Nothing needs review.")
            return True
        for tx in txs:
            sender.send_message("[GTS] " + svc.resolver.describe(tx))
        for l in reclaims:
            sender.send_message(f"[GTS] reclaim listing=#{l.id} seller={l.seller_name} {l.title} (interrupted delivery)")
        sender.send_message(
            "[GTS] Resolve with: /gts resolve tx <id> <sold|activate|close|paid|retry>  or  "
            "/gts resolve reclaim <listing id> <delivered|retry>"
        )
        return True

    def _resolve(self, sender: Any, args: list[str]) -> bool:
        svc = self.plugin
        if len(args) != 3 or args[0] not in ("tx", "reclaim"):
            sender.send_error_message("Usage: /gts resolve (tx|reclaim) <id> <action>. See /gts review.")
            return True
        if args[0] == "tx":
            result = svc.resolver.resolve_tx(args[1], args[2])
        else:
            if not args[1].isdigit():
                sender.send_error_message("Listing id must be a number.")
                return True
            result = svc.resolver.resolve_reclaim(int(args[1]), args[2])
        (sender.send_message if result.ok else sender.send_error_message)("[GTS] " + result.message)
        return True

    def _inspect(self, sender: Any, args: list[str]) -> bool:
        """Diagnostics for the item in the sender's main hand: what GTS reads, stores and can rebuild."""
        player = self._player(sender)
        if player is None:
            sender.send_error_message("Hold an item and run this as a player.")
            return True
        stack = player.inventory.item_in_main_hand
        svc = self.plugin
        out: list[str] = []
        try:
            out.append(f"id={stack.type.id} amount={stack.amount} data={stack.data} max_durability={stack.type.max_durability}")
            m = stack.item_meta
            out.append(f"meta: has_damage={m.has_damage} damage={m.damage} has_enchants={m.has_enchants} "
                       f"name={m.display_name if m.has_display_name else None} unbreakable={m.is_unbreakable}")
            if m.has_enchants:
                out.append("enchants: " + ", ".join(f"{e.id}={lvl}" for e, lvl in m.enchants.items()))
            out.append("nbt: " + str(stack.nbt)[:700])
            item = svc.serializer.serialize(stack)
            out.append(f"serialized: lossy={item.lossy} round_trip_ok={svc.serializer.verify_roundtrip(item)} "
                       f"bytes={len(item.payload)}")
            from ..items.details import describe_item
            out.extend("shown: " + line for line in describe_item(item.payload))
        except Exception as exc:
            out.append(f"inspect failed: {exc!r}")
        for line in out:
            sender.send_message("[GTS] " + line)
            svc.logger.info("[GTS] inspect: " + line)
        return True
