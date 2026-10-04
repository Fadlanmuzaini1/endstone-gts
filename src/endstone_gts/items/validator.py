"""Input validation for prices, amounts and items."""

from __future__ import annotations

import re
from typing import Any

from ..i18n import tr

from ..config.config import GtsConfig, HARD_MAX_PRICE

_DIGITS = re.compile(r"[0-9]{1,18}")


class ValidationError(Exception):
    """Message is safe to show to the player."""


class ListingValidator:
    def __init__(self, config: GtsConfig):
        self.config = config

    def parse_price(self, text: Any) -> int:
        """Strict integer parse: digits only (spaces and thousands commas tolerated)."""
        if not isinstance(text, str):
            raise ValidationError(tr("Enter the price as a whole number."))
        cleaned = text.strip().replace(",", "").replace(" ", "").replace("_", "")
        if not cleaned:
            raise ValidationError(tr("Enter a price."))
        if cleaned.startswith("-"):
            raise ValidationError(tr("Price cannot be negative."))
        if not _DIGITS.fullmatch(cleaned):
            raise ValidationError(tr("Price must be a whole number (digits only)."))
        return self.check_price(int(cleaned))

    def check_price(self, price: int) -> int:
        if isinstance(price, bool) or not isinstance(price, int):
            raise ValidationError(tr("Price must be a whole number."))
        if price < 1 or price < self.config.minimum_price:
            raise ValidationError(tr("Minimum price is {v}.", v=self.config.format_money(self.config.minimum_price)))
        if price > self.config.maximum_price or price > HARD_MAX_PRICE:
            raise ValidationError(tr("Maximum price is {v}.", v=self.config.format_money(self.config.maximum_price)))
        return price

    @staticmethod
    def check_amount(amount: Any, available: int) -> int:
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise ValidationError(tr("Invalid amount."))
        if int(amount) != amount:
            raise ValidationError(tr("Invalid amount."))
        amount = int(amount)
        if amount < 1:
            raise ValidationError(tr("Amount must be at least 1."))
        if amount > available:
            raise ValidationError(tr("You do not have that many."))
        return amount

    def check_item(self, stack: Any) -> None:
        if stack is None:
            raise ValidationError(tr("That slot is empty."))
        try:
            identifier = str(stack.type.id).lower()
            amount = int(stack.amount)
        except Exception:
            raise ValidationError(tr("That item cannot be listed.")) from None
        if amount < 1 or identifier == "minecraft:air":
            raise ValidationError(tr("That slot is empty."))
        if identifier in self.config.banned_items:
            raise ValidationError(tr("That item cannot be sold on the GTS."))
