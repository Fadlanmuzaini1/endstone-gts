"""UI plumbing shared by every screen. Screens only ever call the managers; they contain no
business rules, so everything money/item related stays in one audited place."""

from __future__ import annotations

import re
import time
from typing import Any, Callable

from endstone.form import ActionForm, Button

from ..common import Result
from ..i18n import tr
from . import icons

_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def clean(text: Any, limit: int = 48) -> str:
    """Make player-controlled text (item names, player names) safe for one line of a form."""
    out = _CTRL.sub(" ", str(text)).strip()
    return out if len(out) <= limit else out[: limit - 1] + "…"


def ago(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return tr("just now")
    if seconds < 3600:
        return tr("{n}m ago", n=seconds // 60)
    if seconds < 86400:
        return tr("{n}h ago", n=seconds // 3600)
    return tr("{n}d ago", n=seconds // 86400)


def remaining(seconds: int) -> str:
    seconds = int(seconds)
    if seconds <= 0:
        return tr("expired")
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return tr("{days}d {hours}h", days=days, hours=hours)
    if hours:
        return tr("{hours}h {minutes}m", hours=hours, minutes=minutes)
    return tr("{minutes}m", minutes=max(1, minutes))


class Context:
    """Everything a screen needs. Filled in by the plugin (and refreshed on /gts reload)."""

    def __init__(self) -> None:
        self.config: Any = None
        self.listings: Any = None       # ListingManager
        self.tx: Any = None             # TransactionManager
        self.serializer: Any = None
        self.validator: Any = None
        self.ui: Any = None             # UiRegistry
        self.clock: Callable[[], float] = time.time

    def money(self, amount: int) -> str:
        return self.config.format_money(amount)


class UiRegistry:
    """Lets screens refer to each other without circular imports."""

    main: Any
    browse: Any
    sell: Any
    manage: Any
    history: Any


def show_result(player: Any, title: str, result: Result, then: Callable[[Any], None]) -> None:
    colour = "§a" if result.ok else "§c"
    player.send_form(
        ActionForm(
            title=title,
            content=f"{colour}{result.message}",
            buttons=[Button(tr("OK"), icons.menu("ok"), then), Button(tr("Close"), icons.menu("close"))],
        )
    )


def confirm(
    player: Any,
    title: str,
    content: str,
    yes: str,
    no: str,
    on_yes: Callable[[Any], None],
    on_no: Callable[[Any], None],
) -> None:
    """Two-button confirmation. Uses an ActionForm so each button has its own callback
    (no reliance on how a MessageForm numbers its buttons)."""
    player.send_form(
        ActionForm(title=title, content=content,
                   buttons=[Button(tr(yes), icons.menu("confirm"), on_yes), Button(tr(no), icons.menu("cancel"), on_no)])
    )
