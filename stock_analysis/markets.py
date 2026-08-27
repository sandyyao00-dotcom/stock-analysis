"""Market-specific symbol validation, normalization, and money formatting."""

from dataclasses import dataclass
import math
import re


MARKET_US = "美股"
MARKET_A_SHARE = "A股"
MARKET_HK = "港股"
SUPPORTED_MARKETS = (MARKET_US, MARKET_A_SHARE, MARKET_HK)


@dataclass(frozen=True)
class MarketSymbol:
    """Preserve both what the user typed and the Yahoo Finance data symbol."""

    market: str
    user_symbol: str
    yahoo_symbol: str
    default_currency: str


class SymbolValidationError(ValueError):
    """A friendly validation error safe to show in the UI."""


def normalize_symbol(market: str, user_input: str) -> MarketSymbol:
    """Validate and convert a user symbol into Yahoo Finance format."""
    original = user_input.strip().upper()
    if market == MARKET_US:
        if not original or not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,14}", original):
            raise SymbolValidationError("请输入有效的美股 Ticker，例如 AAPL 或 NVDA。")
        return MarketSymbol(market, original, original, "USD")

    if market == MARKET_A_SHARE:
        code = original.removesuffix(".SS").removesuffix(".SZ")
        if not re.fullmatch(r"\d{6}", code):
            raise SymbolValidationError("请输入 6 位 A 股代码，例如 600519 或 300750。")
        if code.startswith(("5", "6", "9")):
            suffix = ".SS"
        elif code.startswith(("0", "1", "2", "3")):
            suffix = ".SZ"
        else:
            raise SymbolValidationError("暂不支持该 A 股代码前缀；目前支持沪市和深市常见 6 位代码。")
        return MarketSymbol(market, original, f"{code}{suffix}", "CNY")

    if market == MARKET_HK:
        code = original.removesuffix(".HK")
        if not re.fullmatch(r"\d{1,5}", code):
            raise SymbolValidationError("请输入港股代码，例如 700、0700、1810 或 9988。")
        normalized = code.zfill(4) if len(code) <= 4 else code
        return MarketSymbol(market, original, f"{normalized}.HK", "HKD")

    raise SymbolValidationError("请选择受支持的市场：美股、A股或港股。")


def reliable_currency(value: object, fallback: str) -> str:
    """Use a plausible provider currency code, otherwise the market default."""
    if isinstance(value, str):
        currency = value.strip().upper()
        if re.fullmatch(r"[A-Z]{3}", currency):
            return currency
    return fallback


def currency_prefix(currency: str) -> str:
    """Return an unambiguous display prefix for common market currencies."""
    return {"USD": "$", "CNY": "¥", "HKD": "HK$"}.get(currency.upper(), f"{currency.upper()} ")


def currency_label(currency: str) -> str:
    """Return a localized currency label without guessing an exchange."""
    code = currency.upper()
    return {"USD": "USD / 美元", "CNY": "CNY / 人民币", "HKD": "HKD / 港元"}.get(code, code)


def format_money(value: object, currency: str, compact: bool = False) -> str:
    """Format a monetary value with a market-aware currency prefix."""
    if isinstance(value, bool) or value is None:
        return "N/A"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if not math.isfinite(number):
        return "N/A"

    code = currency.upper()
    prefix = currency_prefix(code)
    if compact:
        if code == "CNY":
            if abs(number) >= 1_000_000_000_000:
                return f"{number / 1_000_000_000_000:,.2f} 万亿元人民币"
            if abs(number) >= 100_000_000:
                return f"{number / 100_000_000:,.2f} 亿元人民币"
        if code == "HKD":
            if abs(number) >= 1_000_000_000_000:
                return f"{number / 1_000_000_000_000:,.2f} 万亿港元"
            if abs(number) >= 100_000_000:
                return f"{number / 100_000_000:,.2f} 亿港元"
        for divisor, suffix in ((1_000_000_000_000, "T"), (1_000_000_000, "B"), (1_000_000, "M")):
            if abs(number) >= divisor:
                return f"{prefix}{number / divisor:,.2f}{suffix}"
    return f"{prefix}{number:,.2f}"
