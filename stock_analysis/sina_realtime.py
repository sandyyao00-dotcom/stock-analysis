"""Low-frequency, shared-cache Sina A-share realtime snapshot provider."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math
import re
from threading import RLock, Thread, current_thread
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd

from stock_analysis.markets import MARKET_A_SHARE, SymbolValidationError, normalize_symbol
from stock_analysis.providers import RealtimeSnapshot


SINA_CACHE_TTL_SECONDS = 600
SINA_FAILURE_COOLDOWN_SECONDS = 600

_CACHE_LOCK = RLock()
_market_cache: dict[str, dict[str, object]] | None = None
_cache_fetched_at: datetime | None = None
_last_failure_at: datetime | None = None
_last_failure_error: str | None = None
_refresh_thread: Thread | None = None
_cache_generation = 0

_COLUMN_ALIASES = {
    "code": ("代码", "code", "symbol"),
    "name": ("名称", "name"),
    "price": ("最新价", "trade", "price"),
    "change_amount": ("涨跌额", "pricechange", "change"),
    "change_percent": ("涨跌幅", "changepercent", "percent"),
    "previous_close": ("昨收", "settlement", "previous_close"),
    "open": ("今开", "open"),
    "high": ("最高", "high"),
    "low": ("最低", "low"),
    "volume": ("成交量", "volume"),
    "amount": ("成交额", "amount"),
    "timestamp": ("时间戳", "timestamp", "time"),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_sina_market() -> pd.DataFrame:
    import akshare as ak

    return ak.stock_zh_a_spot()


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _safe_float(value: object) -> float | None:
    if value is None or isinstance(value, bool) or pd.isna(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _find_column(frame: pd.DataFrame, field: str) -> str | None:
    return next((name for name in _COLUMN_ALIASES[field] if name in frame.columns), None)


def _normalize_sina_code(value: object) -> str | None:
    text = str(value).strip().upper()
    match = re.fullmatch(r"(?:SH|SZ)?(\d{6})(?:\.(?:SS|SZ))?", text)
    return match.group(1) if match else None


def _normalize_market_frame(frame: object) -> dict[str, dict[str, object]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("Sina returned an empty or invalid market DataFrame")
    code_column = _find_column(frame, "code")
    if code_column is None:
        raise ValueError("Sina market data is missing the stock code column")

    normalized: dict[str, dict[str, object]] = {}
    columns = {field: _find_column(frame, field) for field in _COLUMN_ALIASES}
    for _, row in frame.iterrows():
        code = _normalize_sina_code(row.get(code_column))
        if code is None:
            continue
        normalized[code] = {
            field: row.get(column) if column is not None else None
            for field, column in columns.items()
            if field != "code"
        }
    if not normalized:
        raise ValueError("Sina market data contains no valid A-share stock codes")
    return normalized


def _parse_quote_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = None
        text = value.strip().replace("/", "-")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed


def _unavailable(
    ticker: str,
    error: str | None,
    *,
    loading: bool = False,
    stale: bool = False,
) -> RealtimeSnapshot:
    return RealtimeSnapshot(
        ticker=ticker,
        market=MARKET_A_SHARE,
        source="sina",
        source_type="automatic",
        input_method="sina_market_cache",
        available=False,
        loading=loading,
        stale=stale,
        error=error,
    )


def _snapshot_from_row(
    ticker: str,
    row: dict[str, object],
    fetched_at: datetime,
    *,
    stale: bool,
    error: str | None,
) -> RealtimeSnapshot:
    quote_timestamp = _parse_quote_timestamp(row.get("timestamp"))
    return RealtimeSnapshot(
        ticker=ticker,
        market=MARKET_A_SHARE,
        company_name=str(row["name"]).strip() if row.get("name") is not None else None,
        current_price=_safe_float(row.get("price")),
        change_amount=_safe_float(row.get("change_amount")),
        change_percent=_safe_float(row.get("change_percent")),
        previous_close=_safe_float(row.get("previous_close")),
        open=_safe_float(row.get("open")),
        high=_safe_float(row.get("high")),
        low=_safe_float(row.get("low")),
        volume=_safe_float(row.get("volume")),
        turnover_amount=_safe_float(row.get("amount")),
        timestamp=quote_timestamp or fetched_at,
        source="sina",
        source_type="automatic",
        input_method="sina_market_cache",
        confirmed=False,
        available=True,
        loading=False,
        stale=stale,
        error=error,
        fetched_at=fetched_at,
        timestamp_is_fetch_time=quote_timestamp is None,
    )


def _cached_result(ticker: str, code: str, *, stale: bool, error: str | None) -> RealtimeSnapshot:
    if _market_cache is None or _cache_fetched_at is None:
        return _unavailable(ticker, error or "Sina market data is unavailable")
    row = _market_cache.get(code)
    if row is None:
        return _unavailable(ticker, f"Sina market data does not contain {code}", stale=stale)
    return _snapshot_from_row(ticker, row, _cache_fetched_at, stale=stale, error=error)


def _refresh_worker(
    fetcher: Callable[[], pd.DataFrame],
    completion_clock: Callable[[], datetime],
    generation: int,
) -> None:
    global _market_cache, _cache_fetched_at, _last_failure_at, _last_failure_error, _refresh_thread
    try:
        candidate = _normalize_market_frame(fetcher())
    except Exception as exc:  # Background failures are state, never caller exceptions.
        failed_at = _as_utc(completion_clock())
        with _CACHE_LOCK:
            if generation == _cache_generation:
                _last_failure_at = failed_at
                _last_failure_error = f"{type(exc).__name__}: {exc}"
    else:
        fetched_at = _as_utc(completion_clock())
        with _CACHE_LOCK:
            if generation == _cache_generation:
                _market_cache = candidate
                _cache_fetched_at = fetched_at
                _last_failure_at = None
                _last_failure_error = None
    finally:
        with _CACHE_LOCK:
            if generation == _cache_generation and _refresh_thread is current_thread():
                _refresh_thread = None


def _start_background_refresh_locked(
    fetcher: Callable[[], pd.DataFrame], completion_clock: Callable[[], datetime]
) -> bool:
    global _refresh_thread, _last_failure_at, _last_failure_error
    if _refresh_thread is not None:
        return True
    thread = Thread(
        target=_refresh_worker,
        args=(fetcher, completion_clock, _cache_generation),
        name="sina-market-refresh",
        daemon=True,
    )
    _refresh_thread = thread
    try:
        thread.start()
    except Exception as exc:
        _refresh_thread = None
        _last_failure_at = _as_utc(completion_clock())
        _last_failure_error = f"{type(exc).__name__}: {exc}"
        return False
    return True


def get_sina_cache_status(*, now: datetime | None = None) -> dict[str, object]:
    """Return a read-only snapshot of the process-local Sina cache state."""
    current_time = _as_utc(now or _utcnow())
    with _CACHE_LOCK:
        has_cache = _market_cache is not None and _cache_fetched_at is not None
        cache_age = (current_time - _cache_fetched_at).total_seconds() if has_cache else None
        cooldown_until = (
            _last_failure_at + timedelta(seconds=SINA_FAILURE_COOLDOWN_SECONDS)
            if _last_failure_at is not None
            else None
        )
        return {
            "loading": _refresh_thread is not None,
            "has_cache": has_cache,
            "stale": bool(cache_age is not None and cache_age > SINA_CACHE_TTL_SECONDS),
            "fetched_at": _cache_fetched_at,
            "last_error": _last_failure_error,
            "cooldown_until": cooldown_until,
            "cache_size": len(_market_cache) if _market_cache is not None else 0,
        }


def get_sina_a_share_snapshot(
    symbol: str,
    allow_background_refresh: bool = True,
    *,
    fetcher: Callable[[], pd.DataFrame] | None = None,
    now: datetime | None = None,
) -> RealtimeSnapshot:
    """Read one A-share snapshot and optionally trigger a non-blocking refresh."""
    try:
        market_symbol = normalize_symbol(MARKET_A_SHARE, symbol)
    except (SymbolValidationError, AttributeError, TypeError) as exc:
        return _unavailable(str(symbol), f"Unsupported A-share symbol: {exc}")

    ticker = market_symbol.yahoo_symbol
    code = ticker.split(".", 1)[0]
    current_time = _as_utc(now or _utcnow())

    with _CACHE_LOCK:
        cache_age = (
            (current_time - _cache_fetched_at).total_seconds()
            if _market_cache is not None and _cache_fetched_at is not None
            else None
        )
        if cache_age is not None and cache_age <= SINA_CACHE_TTL_SECONDS:
            return _cached_result(ticker, code, stale=False, error=None)

        failure_age = (
            (current_time - _last_failure_at).total_seconds()
            if _last_failure_at is not None
            else None
        )
        if failure_age is not None and failure_age < SINA_FAILURE_COOLDOWN_SECONDS:
            if cache_age is not None:
                return _cached_result(ticker, code, stale=True, error=_last_failure_error)
            return _unavailable(ticker, _last_failure_error or "Sina request is in failure cooldown")

        loading = _refresh_thread is not None
        if allow_background_refresh and not loading:
            completion_clock = _utcnow if now is None else lambda: current_time
            loading = _start_background_refresh_locked(fetcher or _fetch_sina_market, completion_clock)

        if cache_age is not None:
            result = _cached_result(ticker, code, stale=True, error=None)
            return replace(result, loading=loading)
        return _unavailable(
            ticker,
            None if loading else (_last_failure_error or "Sina market data is unavailable"),
            loading=loading,
        )


def _reset_sina_cache_for_tests() -> None:
    global _market_cache, _cache_fetched_at, _last_failure_at, _last_failure_error
    global _refresh_thread, _cache_generation
    with _CACHE_LOCK:
        _cache_generation += 1
        _market_cache = None
        _cache_fetched_at = None
        _last_failure_at = None
        _last_failure_error = None
        _refresh_thread = None
