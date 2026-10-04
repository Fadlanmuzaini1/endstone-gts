"""Endstone entry point: wires config, database, economy, managers, UI, commands and events."""


import time
import uuid
from pathlib import Path

from endstone import Player
from endstone.event import PlayerJoinEvent, event_handler
from endstone.plugin import Plugin

from .commands.gts import GtsCommand, PERM_ADMIN, PERM_USE
from . import i18n
from .common import Log
from .config.config import GtsConfig, parse_config
from .database.database import Database
from .database.history import HistoryRepository
from .database.listings import ListingRepository
from .database.transactions import TransactionRepository
from .economy.jweconomy import create_provider
from .economy.provider import EconomyProvider, UnavailableEconomy
from .i18n import tr
from .items.inventory import ItemDelivery
from .items.serializer import ItemSerializer
from .items.validator import ListingValidator
from .listings.manager import ListingManager
from .transactions.manager import TransactionManager
from .transactions.recovery import AdminResolver, RecoveryService
from .ui import icons as ui_icons
from .ui.browse import BrowseUI
from .ui.common import Context, UiRegistry
from .ui.history import HistoryUI
from .ui.main import MainMenu
from .ui.manage import ManageUI
from .ui.sell import SellUI

TICKS_PER_SECOND = 20


class GtsPlugin(Plugin):
    prefix = "GTS"
    soft_depend = ["jweconomy"]
    services_ready = False
    api_version = "0.11"
    description = "Global Trading Station: a GUI item marketplace backed by SQLite and JWEconomy."
    authors = ["GTS"]

    commands = {
        "gts": {
            "description": "Open the Global Trading Station",
            "usages": [
                "/gts",
                "/gts (sell|items|listings|history|reload|review|inspect)<action: GtsAction>",
                "/gts (search)<action: GtsSearchAction> <query: message>",
                "/gts (resolve)<action: GtsResolveAction> <kind: str> <id: str> <result: str>",
            ],
            "aliases": ["market"],
            # No command-level permission: everyone may open /gts (same pattern as JWEconomy's
            # /balance and /pay). Admin subcommands are checked in code against gts.command.admin.
        }
    }

    permissions = {
        PERM_ADMIN: {"description": "Reload, review and resolve GTS transactions", "default": "op"},
    }

    # ------------------------------------------------------------------ lifecycle
    def on_load(self) -> None:
        self.services_ready = False
        self.gts_config: GtsConfig = parse_config({})
        self.logger.info("[GTS] Plugin loaded")

    def on_enable(self) -> None:
        self.services_ready = False
        self.log = Log(self.logger)
        self.save_default_config()
        self.gts_config = self._load_config()

        self.data_folder.mkdir(parents=True, exist_ok=True)
        db_path = Path(self.data_folder) / self.gts_config.database_file
        self.db = Database(db_path)
        try:
            self.db.open()  # also runs migrations
        except Exception as exc:
            self.logger.error(f"[GTS] Database failed to open: {exc}")
            self.logger.error("[GTS] GTS stays disabled until this is fixed.")
            return
        self.log.info(f"Database initialized (schema v{self.db.schema_version()})")

        clock = time.time
        self.serializer = ItemSerializer()
        self.delivery = ItemDelivery(self.serializer)
        self.listing_repo = ListingRepository(self.db, clock)
        self.txs = TransactionRepository(self.db, clock)
        self.economy: EconomyProvider = self._make_economy()

        self.tx_manager = TransactionManager(
            self.db, self.listing_repo, self.txs, self.economy, self.serializer, self.delivery,
            self.gts_config, self.log, clock, notify=self._notify,
        )
        self.listing_manager = ListingManager(
            self.db, self.listing_repo, HistoryRepository(self.db), self.delivery, self.gts_config, self.log
        )
        self.recovery = RecoveryService(self.db, self.listing_repo, self.txs, self.tx_manager, self.log)
        self.resolver = AdminResolver(self.db, self.listing_repo, self.txs, self.log)

        try:
            self.recovery.run()
        except Exception as exc:  # never block startup, but make it loud
            self.log.critical(f"Recovery failed: {exc}")

        ui_icons.configure(self.gts_config.icons)
        i18n.configure(self.gts_config.language)
        self.ctx = Context()
        self._fill_context()
        ui = UiRegistry()
        ui.main, ui.browse, ui.sell = MainMenu(self.ctx), BrowseUI(self.ctx), SellUI(self.ctx)
        ui.manage, ui.history = ManageUI(self.ctx), HistoryUI(self.ctx)
        self.ctx.ui = ui

        self.log.info(f"Loaded {self.listing_repo.count_active()} active listings")
        self._command = GtsCommand(self)
        self.register_events(self)
        interval = self.gts_config.sweep_interval_seconds * TICKS_PER_SECOND
        self.server.scheduler.run_task(self, self._sweep, delay=interval, period=interval)
        self.services_ready = True
        self.log.info("Startup complete")

    def on_disable(self) -> None:
        self.services_ready = False
        try:
            self.server.scheduler.cancel_tasks(self)
        except Exception:
            pass
        db = getattr(self, "db", None)
        if db is not None:
            db.close()

    # ------------------------------------------------------------------ commands
    def on_command(self, sender, command, args) -> bool:
        if command.name != "gts":
            return False
        try:
            return self._command.handle(sender, list(args))
        except Exception as exc:
            self.logger.error(f"[GTS] Command error: {exc!r}")
            sender.send_error_message("GTS hit an internal error. Nothing was changed; please tell staff.")
            return True

    # ------------------------------------------------------------------ events
    @event_handler
    def on_player_join(self, event: PlayerJoinEvent) -> None:
        if not self.services_ready:
            return
        try:
            player = event.player
            waiting = len(self.listing_repo.pending_reclaims(str(player.unique_id), limit=50))
            if waiting:
                player.send_message(
                    tr("§e[GTS] You have {n} item(s) waiting. Open /gts > My Listings > Reclaim.", n=waiting)
                )
        except Exception as exc:
            self.logger.error(f"[GTS] Join notice failed: {exc!r}")

    # ------------------------------------------------------------------ helpers
    def _load_config(self) -> GtsConfig:
        cfg = parse_config(self.config)
        for warning in cfg.warnings:
            self.logger.warning(f"[GTS] config: {warning}")
        return cfg

    def _make_economy(self) -> EconomyProvider:
        try:
            economy = create_provider(self.server, self.gts_config)
        except Exception as exc:
            economy = UnavailableEconomy(f"Economy provider failed to start: {exc}")
        state = "ready" if economy.is_available() else f"UNAVAILABLE - {economy.unavailable_reason()}"
        self.log.info(f"Economy provider: {self.gts_config.economy_provider} ({state})")
        return economy

    def _fill_context(self) -> None:
        c = self.ctx
        c.config = self.gts_config
        c.listings = self.listing_manager
        c.tx = self.tx_manager
        c.serializer = self.serializer
        c.validator = ListingValidator(self.gts_config)

    def reload_gts(self) -> list[str]:
        """/gts reload: re-read config.toml and re-bind the economy. Database path needs a restart."""
        old_db = self.gts_config.database_file
        self.reload_config()  # re-read config.toml from disk (self.config is cached)
        self.gts_config = self._load_config()
        lines = ["[GTS] Configuration reloaded."]
        if self.gts_config.database_file != old_db:
            lines.append("[GTS] database.file changed: restart the server to use the new file.")
            object.__setattr__(self.gts_config, "database_file", old_db)
        self.economy = self._make_economy()
        self.tx_manager.economy = self.economy
        self.tx_manager.set_config(self.gts_config)
        self.listing_manager.config = self.gts_config
        ui_icons.configure(self.gts_config.icons)
        i18n.configure(self.gts_config.language)
        self._fill_context()
        lines.append(f"[GTS] Economy: {'ready' if self.economy.is_available() else self.economy.unavailable_reason()}")
        lines.extend(f"[GTS] config: {w}" for w in self.gts_config.warnings)
        return lines

    def _sweep(self) -> None:
        if not self.services_ready:
            return
        try:
            self.listing_manager.expire_due()
            self.recovery.retry_pending()
        except Exception as exc:
            self.logger.error(f"[GTS] Sweep failed: {exc!r}")

    def _notify(self, player_uuid: str, message: str) -> None:
        try:
            player = self.server.get_player(uuid.UUID(player_uuid))
        except Exception:
            player = None
        if player is not None:
            player.send_message(f"§a[GTS] {message}")
