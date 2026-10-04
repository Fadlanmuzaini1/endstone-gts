"""Small shared types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Result:
    ok: bool
    message: str
    listing_id: int | None = None
    transaction_id: str | None = None

    @staticmethod
    def success(message: str, **kw) -> "Result":
        return Result(True, message, **kw)

    @staticmethod
    def fail(message: str, **kw) -> "Result":
        return Result(False, message, **kw)


class Log:
    """Thin wrapper so the core never imports Endstone: tests pass a plain logger."""

    def __init__(self, logger):
        self._l = logger

    def info(self, msg: str) -> None:
        self._l.info(f"[GTS] {msg}")

    def warning(self, msg: str) -> None:
        self._l.warning(f"[GTS] {msg}")

    def error(self, msg: str) -> None:
        self._l.error(f"[GTS] {msg}")

    def critical(self, msg: str) -> None:
        # Endstone's Logger exposes critical(); fall back to error() if absent.
        fn = getattr(self._l, "critical", None) or self._l.error
        fn(f"[GTS] {msg}")
