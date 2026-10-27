"""Validation shared by local commands and the deterministic store."""

from decimal import Decimal
import re


class SellerError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


CURRENCY_DIGITS = {"EUR": 2, "USD": 2, "GBP": 2, "CHF": 2, "CAD": 2,
                   "AUD": 2, "NZD": 2, "JPY": 0}
MAX_AMOUNT_MINOR = 10**12


def normalize_currency(value: str) -> str:
    if not isinstance(value, str) or value.upper() not in CURRENCY_DIGITS:
        raise SellerError("invalid_currency", "Use an explicit supported currency: " + ", ".join(CURRENCY_DIGITS))
    return value.upper()


def positive_int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= MAX_AMOUNT_MINOR:
        raise SellerError("invalid_value", f"{name} must be a positive integer within the supported range.")
    return value


def parse_amount(value: str, currency: str) -> int:
    currency = normalize_currency(currency)
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,12}(?:\.\d{1,2})?", value):
        raise SellerError("invalid_amount", "Use a positive decimal amount, without currency symbols, grouping or exponent.")
    digits = CURRENCY_DIGITS[currency]
    if "." in value and len(value.split(".")[1]) > digits:
        raise SellerError("invalid_amount", f"{currency} accepts at most {digits} decimal places.")
    return positive_int(int(Decimal(value) * (10**digits)), "amount")


def format_amount(minor: int, currency: str) -> str:
    currency = normalize_currency(currency)
    positive_int(minor, "amount")
    return f"{Decimal(minor) / (10**CURRENCY_DIGITS[currency]):.{CURRENCY_DIGITS[currency]}f}"


def text(value, name: str, *, limit: int = 4000, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise SellerError("invalid_value", f"{name} must be {'nonempty ' if required else ''}text of at most {limit} characters.")
    return value.strip()
