"""Economy abstraction. GTS never talks to a concrete economy directly.

Contract (this is what makes money handling dupe-safe):

* ``withdraw`` / ``deposit`` return ``True``  -> the balance changed by exactly ``amount``.
* they return ``False``                        -> definitely refused, balance unchanged.
* they raise ``EconomyUnavailable``            -> definitely not attempted, balance unchanged.
* they raise ``EconomyAmbiguous``              -> the call was made and the outcome is unknown.
  GTS never retries, refunds or completes such a transaction automatically: it is parked
  for an administrator.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class EconomyError(Exception):
    pass


class EconomyUnavailable(EconomyError):
    """The economy cannot be used right now; nothing was attempted."""


class EconomyAmbiguous(EconomyError):
    """A balance-changing call was made but its outcome is unknown."""


@dataclass(frozen=True)
class Account:
    uuid: str
    name: str


class EconomyProvider(ABC):
    name: str = "unknown"

    @abstractmethod
    def is_available(self) -> bool:
        """True if balance operations can currently be performed."""

    def unavailable_reason(self) -> str:
        return "Economy is not available."

    @abstractmethod
    def get_balance(self, account: Account) -> int:
        """Current balance. Raises EconomyUnavailable if it cannot be read."""

    def has_balance(self, account: Account, amount: int) -> bool:
        return self.get_balance(account) >= amount

    @abstractmethod
    def withdraw(self, account: Account, amount: int) -> bool:
        """See module docstring for the return/raise contract."""

    @abstractmethod
    def deposit(self, account: Account, amount: int) -> bool:
        """See module docstring for the return/raise contract."""


class UnavailableEconomy(EconomyProvider):
    """Used when no working provider is configured. Fails closed; never pretends to work."""

    name = "none"

    def __init__(self, reason: str):
        self._reason = reason

    def is_available(self) -> bool:
        return False

    def unavailable_reason(self) -> str:
        return self._reason

    def get_balance(self, account: Account) -> int:
        raise EconomyUnavailable(self._reason)

    def withdraw(self, account: Account, amount: int) -> bool:
        raise EconomyUnavailable(self._reason)

    def deposit(self, account: Account, amount: int) -> bool:
        raise EconomyUnavailable(self._reason)
