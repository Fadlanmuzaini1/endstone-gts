"""JWEconomy adapter (JWEconomy 2.0.x, github.com/JWDev/JWEconomy).

JWEconomy exposes ``plugin.economy_api`` whose methods are coroutines that run on the plugin's
own asyncio thread; ``plugin.run_async(coro)`` schedules one and returns a
``concurrent.futures.Future``. This adapter maps that onto GTS's EconomyProvider contract:

  get_balance  -> api.get_balance(uuid, currency)                 (read only)
  withdraw     -> api.remove_balance(uuid, amount, currency)      None  = insufficient funds (nothing changed)
  deposit      -> api.add_balance(uuid, amount, currency)         refused up front if it would exceed
                                                                  max_balance (JWEconomy silently clamps)

Failure classification (see provider.py):
  * JWEconomy not loaded / loop not running / bad argument  -> EconomyUnavailable (nothing was sent)
  * timeout or any exception AFTER the call was submitted    -> EconomyAmbiguous (it may have applied)
"""

from __future__ import annotations

from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable

from ..config.config import JWEconomyBinding
from .provider import (
    Account,
    EconomyAmbiguous,
    EconomyProvider,
    EconomyUnavailable,
    UnavailableEconomy,
)


class JWEconomyProvider(EconomyProvider):
    name = "jweconomy"

    def __init__(self, server: Any, binding: JWEconomyBinding):
        self._server = server
        self._b = binding

    # -- resolution ----------------------------------------------------------------
    def _plugin(self) -> Any:
        manager = self._server.plugin_manager
        plugin = None
        for name in dict.fromkeys((self._b.plugin_name, "jweconomy", "JWEconomy")):
            try:
                plugin = manager.get_plugin(name)
            except Exception:
                plugin = None
            if plugin is not None:
                break
        if plugin is None:
            raise EconomyUnavailable(f"Plugin '{self._b.plugin_name}' is not loaded.")
        if not getattr(plugin, "is_enabled", True):
            raise EconomyUnavailable("JWEconomy is not enabled.")
        if not callable(getattr(plugin, "run_async", None)) or getattr(plugin, "economy_api", None) is None:
            raise EconomyUnavailable("This JWEconomy version does not expose economy_api / run_async.")
        return plugin

    @property
    def _currency(self) -> str | None:
        return self._b.currency or None

    def _submit(self, plugin: Any, coro_factory: Callable[[Any], Any]) -> Any:
        """Schedule a coroutine on JWEconomy's loop. Raises Unavailable if it could not be submitted."""
        coro = coro_factory(plugin.economy_api)
        try:
            return plugin.run_async(coro)
        except Exception as exc:  # loop not running: nothing was scheduled
            close = getattr(coro, "close", None)
            if close:
                close()
            raise EconomyUnavailable(f"JWEconomy is not ready: {exc}") from exc

    # -- EconomyProvider -----------------------------------------------------------
    def is_available(self) -> bool:
        try:
            self._plugin()
            return True
        except EconomyUnavailable:
            return False

    def unavailable_reason(self) -> str:
        try:
            self._plugin()
        except EconomyUnavailable as exc:
            return str(exc)
        return ""

    def _read_balance(self, plugin: Any, account: Account) -> float:
        fut = self._submit(plugin, lambda api: api.get_balance(account.uuid, self._currency))
        try:
            return float(fut.result(timeout=self._b.timeout_seconds))
        except Exception as exc:  # read-only: no balance can have changed
            raise EconomyUnavailable(f"JWEconomy get_balance failed: {exc!r}") from exc

    def get_balance(self, account: Account) -> int:
        return int(self._read_balance(self._plugin(), account))  # floor: never overstate funds

    def withdraw(self, account: Account, amount: int) -> bool:
        if amount <= 0:
            raise ValueError("amount must be positive")
        plugin = self._plugin()
        fut = self._submit(plugin, lambda api: api.remove_balance(account.uuid, float(amount), self._currency))
        try:
            result = fut.result(timeout=self._b.timeout_seconds)
        except FutureTimeout as exc:
            raise EconomyAmbiguous("JWEconomy remove_balance timed out") from exc
        except Exception as exc:
            raise EconomyAmbiguous(f"JWEconomy remove_balance raised {type(exc).__name__}: {exc}") from exc
        return result is not None  # None = insufficient funds; note 0.0 is a valid success

    def deposit(self, account: Account, amount: int) -> bool:
        if amount <= 0:
            raise ValueError("amount must be positive")
        plugin = self._plugin()
        # JWEconomy clamps add_balance at max_balance and would silently swallow the excess.
        before = self._read_balance(plugin, account)
        try:
            limit = float(plugin.economy_service.get_max_balance(self._currency))
        except Exception as exc:
            raise EconomyUnavailable(f"Cannot read JWEconomy max_balance: {exc!r}") from exc
        if before + amount > limit:
            return False  # definite refusal; the payout/refund stays pending and is retried
        fut = self._submit(plugin, lambda api: api.add_balance(account.uuid, float(amount), self._currency))
        try:
            fut.result(timeout=self._b.timeout_seconds)
        except FutureTimeout as exc:
            raise EconomyAmbiguous("JWEconomy add_balance timed out") from exc
        except Exception as exc:
            raise EconomyAmbiguous(f"JWEconomy add_balance raised {type(exc).__name__}: {exc}") from exc
        return True


_FACTORIES: dict[str, Callable[[Any, Any], EconomyProvider]] = {
    "jweconomy": lambda server, config: JWEconomyProvider(server, config.jweconomy),
}


def register_provider(name: str, factory: Callable[[Any, Any], EconomyProvider]) -> None:
    _FACTORIES[name.lower()] = factory


def create_provider(server: Any, config: Any) -> EconomyProvider:
    factory = _FACTORIES.get(config.economy_provider)
    if factory is None:
        return UnavailableEconomy(f"Unknown economy provider '{config.economy_provider}'.")
    return factory(server, config)
