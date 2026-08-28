"""Cached, normalized market-hotspot board lists with provider fallback."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
import math
from threading import RLock
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd


BOARD_LIST_CACHE_TTL_SECONDS = 1200
FAILURE_COOLDOWN_SECONDS = 900
STALE_MAX_AGE_SECONDS = 86400

BOARD_TYPE_INDUSTRY = "industry"
BOARD_TYPE_CONCEPT = "concept"
SOURCE_THS = "ths"
SOURCE_EASTMONEY = "eastmoney"

_SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
_CACHE_LOCK = RLock()


@dataclass(frozen=True)
class HotspotBoard:
    date: date | None
    fetched_at: datetime
    board_type: str
    board_code: str | None
    board_name: str | None
    rank: int | None
    latest_price: float | None
    change_amount: float | None
    change_percent: float | None
    market_cap: float | None
    turnover_rate: float | None
    up_count: int | None
    down_count: int | None
    leader_name: str | None
    leader_change_percent: float | None
    source_provider: str


@dataclass(frozen=True)
class HotspotResult:
    available: bool
    stale: bool
    metrics_complete: bool
    source_provider: str | None
    fetched_at: datetime | None
    data: tuple[HotspotBoard, ...]
    error: str | None = None
    loading: bool = False


@dataclass(frozen=True)
class _CacheEntry:
    fetched_at: datetime
    data: tuple[HotspotBoard, ...]
    metrics_complete: bool


_board_cache: dict[tuple[str, str], _CacheEntry] = {}
_provider_failures: dict[tuple[str, str], tuple[datetime, str]] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _fetch_ths_industry() -> pd.DataFrame:
    import akshare as ak

    return ak.stock_board_industry_summary_ths()


def _fetch_eastmoney_industry() -> pd.DataFrame:
    import akshare as ak

    return ak.stock_board_industry_name_em()


def _fetch_eastmoney_concept() -> pd.DataFrame:
    import akshare as ak

    return ak.stock_board_concept_name_em()


def _fetch_ths_concept_directory() -> pd.DataFrame:
    import akshare as ak

    return ak.stock_board_concept_name_ths()


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _safe_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if pd.isna(value):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_int(value: object) -> int | None:
    number = _safe_float(value)
    return int(number) if number is not None else None


def _frame_value(row: pd.Series, column: str) -> object:
    return row.get(column) if column in row.index else None


def _snapshot_date(fetched_at: datetime) -> date:
    return fetched_at.astimezone(_SHANGHAI_TIMEZONE).date()


def _standard_board(
    *,
    row: pd.Series,
    fetched_at: datetime,
    board_type: str,
    source_provider: str,
    columns: dict[str, str | None],
    include_snapshot_date: bool = True,
) -> HotspotBoard:
    def value(field: str) -> object:
        column = columns.get(field)
        return _frame_value(row, column) if column else None

    return HotspotBoard(
        date=_snapshot_date(fetched_at) if include_snapshot_date else None,
        fetched_at=fetched_at,
        board_type=board_type,
        board_code=_clean_text(value("board_code")),
        board_name=_clean_text(value("board_name")),
        rank=_safe_int(value("rank")),
        latest_price=_safe_float(value("latest_price")),
        change_amount=_safe_float(value("change_amount")),
        change_percent=_safe_float(value("change_percent")),
        market_cap=_safe_float(value("market_cap")),
        turnover_rate=_safe_float(value("turnover_rate")),
        up_count=_safe_int(value("up_count")),
        down_count=_safe_int(value("down_count")),
        leader_name=_clean_text(value("leader_name")),
        leader_change_percent=_safe_float(value("leader_change_percent")),
        source_provider=source_provider,
    )


def _normalize_full_metrics(
    frame: object,
    *,
    fetched_at: datetime,
    board_type: str,
    source_provider: str,
    columns: dict[str, str | None],
) -> tuple[HotspotBoard, ...]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError(f"{source_provider} returned an empty or invalid {board_type} DataFrame")
    name_column = columns.get("board_name")
    if name_column is None or name_column not in frame.columns:
        raise KeyError(f"{source_provider} {board_type} data is missing the board name column")
    boards = [
        _standard_board(
            row=row,
            fetched_at=fetched_at,
            board_type=board_type,
            source_provider=source_provider,
            columns=columns,
        )
        for _, row in frame.iterrows()
    ]
    boards = [board for board in boards if board.board_name]
    if not boards:
        raise ValueError(f"{source_provider} returned no usable {board_type} boards")
    boards.sort(
        key=lambda board: (
            board.change_percent is not None,
            board.change_percent if board.change_percent is not None else float("-inf"),
        ),
        reverse=True,
    )
    return tuple(replace(board, rank=index) for index, board in enumerate(boards, start=1))


def normalize_ths_industry(frame: object, *, fetched_at: datetime) -> tuple[HotspotBoard, ...]:
    """Normalize the THS industry summary without inventing unavailable metrics."""
    return _normalize_full_metrics(
        frame,
        fetched_at=_as_utc(fetched_at),
        board_type=BOARD_TYPE_INDUSTRY,
        source_provider=SOURCE_THS,
        columns={
            "rank": "序号",
            "board_code": None,
            "board_name": "板块",
            "latest_price": None,
            "change_amount": None,
            "change_percent": "涨跌幅",
            "market_cap": None,
            "turnover_rate": None,
            "up_count": "上涨家数",
            "down_count": "下跌家数",
            "leader_name": "领涨股",
            "leader_change_percent": "领涨股-涨跌幅",
        },
    )


def normalize_eastmoney_boards(
    frame: object, *, board_type: str, fetched_at: datetime
) -> tuple[HotspotBoard, ...]:
    """Normalize EastMoney industry or concept board-market rows."""
    if board_type not in (BOARD_TYPE_INDUSTRY, BOARD_TYPE_CONCEPT):
        raise ValueError(f"Unsupported board type: {board_type}")
    return _normalize_full_metrics(
        frame,
        fetched_at=_as_utc(fetched_at),
        board_type=board_type,
        source_provider=SOURCE_EASTMONEY,
        columns={
            "rank": "排名",
            "board_code": "板块代码",
            "board_name": "板块名称",
            "latest_price": "最新价",
            "change_amount": "涨跌额",
            "change_percent": "涨跌幅",
            "market_cap": "总市值",
            "turnover_rate": "换手率",
            "up_count": "上涨家数",
            "down_count": "下跌家数",
            "leader_name": "领涨股票",
            "leader_change_percent": "领涨股票-涨跌幅",
        },
    )


def normalize_ths_concept_directory(
    frame: object, *, fetched_at: datetime
) -> tuple[HotspotBoard, ...]:
    """Normalize the THS concept directory without presenting it as a ranking."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("ths returned an empty or invalid concept directory DataFrame")
    if "name" not in frame.columns:
        raise KeyError("ths concept directory is missing the name column")
    fetched_at = _as_utc(fetched_at)
    columns = {"board_name": "name", "board_code": "code" if "code" in frame.columns else None}
    boards = [
        _standard_board(
            row=row,
            fetched_at=fetched_at,
            board_type=BOARD_TYPE_CONCEPT,
            source_provider=SOURCE_THS,
            columns=columns,
            include_snapshot_date=False,
        )
        for _, row in frame.iterrows()
    ]
    boards = [board for board in boards if board.board_name]
    if not boards:
        raise ValueError("ths returned no usable concept directory entries")
    return tuple(boards)


def _cache_age(entry: _CacheEntry, current_time: datetime) -> float:
    return (current_time - entry.fetched_at).total_seconds()


def _same_market_date(first: datetime, second: datetime) -> bool:
    return first.astimezone(_SHANGHAI_TIMEZONE).date() == second.astimezone(
        _SHANGHAI_TIMEZONE
    ).date()


def _fresh_result(key: tuple[str, str], current_time: datetime) -> HotspotResult | None:
    entry = _board_cache.get(key)
    if entry is None:
        return None
    if (
        _cache_age(entry, current_time) <= BOARD_LIST_CACHE_TTL_SECONDS
        and _same_market_date(entry.fetched_at, current_time)
    ):
        return HotspotResult(
            True,
            False,
            entry.metrics_complete,
            key[1],
            entry.fetched_at,
            entry.data,
        )
    return None


def _failure_in_cooldown(key: tuple[str, str], current_time: datetime) -> str | None:
    failure = _provider_failures.get(key)
    if failure is None:
        return None
    age = (current_time - failure[0]).total_seconds()
    return failure[1] if age < FAILURE_COOLDOWN_SECONDS else None


def _attempt_provider(
    *,
    board_type: str,
    source_provider: str,
    fetcher: Callable[[], pd.DataFrame],
    normalizer: Callable[..., tuple[HotspotBoard, ...]],
    metrics_complete: bool,
    current_time: datetime,
) -> tuple[HotspotResult | None, str | None]:
    key = (board_type, source_provider)
    fresh = _fresh_result(key, current_time)
    if fresh is not None:
        return fresh, None
    cooldown_error = _failure_in_cooldown(key, current_time)
    if cooldown_error is not None:
        return None, cooldown_error
    try:
        if normalizer is normalize_eastmoney_boards:
            data = normalizer(fetcher(), board_type=board_type, fetched_at=current_time)
        else:
            data = normalizer(fetcher(), fetched_at=current_time)
    except Exception as exc:
        error = f"{source_provider} {board_type} unavailable ({type(exc).__name__}: {exc})"
        _provider_failures[key] = (current_time, error)
        return None, error
    entry = _CacheEntry(current_time, data, metrics_complete)
    _board_cache[key] = entry
    _provider_failures.pop(key, None)
    return HotspotResult(
        True,
        False,
        metrics_complete,
        source_provider,
        current_time,
        data,
    ), None


def _stale_result(
    *, board_type: str, provider_order: tuple[str, ...], current_time: datetime, error: str
) -> HotspotResult | None:
    candidates: list[tuple[str, _CacheEntry]] = []
    for provider in provider_order:
        entry = _board_cache.get((board_type, provider))
        if entry is not None and 0 <= _cache_age(entry, current_time) <= STALE_MAX_AGE_SECONDS:
            candidates.append((provider, entry))
    if not candidates:
        return None
    provider, entry = max(candidates, key=lambda candidate: candidate[1].fetched_at)
    return HotspotResult(
        True,
        True,
        entry.metrics_complete,
        provider,
        entry.fetched_at,
        entry.data,
        error=error,
    )


def _get_hotspots(
    *,
    board_type: str,
    providers: tuple[
        tuple[str, Callable[[], pd.DataFrame], Callable[..., tuple[HotspotBoard, ...]], bool],
        ...,
    ],
    now: datetime | None,
) -> HotspotResult:
    current_time = _as_utc(now or _utcnow())
    errors: list[str] = []
    with _CACHE_LOCK:
        for source_provider, fetcher, normalizer, metrics_complete in providers:
            result, error = _attempt_provider(
                board_type=board_type,
                source_provider=source_provider,
                fetcher=fetcher,
                normalizer=normalizer,
                metrics_complete=metrics_complete,
                current_time=current_time,
            )
            if result is not None:
                return replace(result, error="; ".join(errors) or None)
            if error:
                errors.append(error)
        combined_error = "; ".join(errors) or f"No {board_type} provider is available"
        stale = _stale_result(
            board_type=board_type,
            provider_order=tuple(provider[0] for provider in providers),
            current_time=current_time,
            error=combined_error,
        )
        if stale is not None:
            return stale
        return HotspotResult(False, False, False, None, None, (), combined_error)


def get_industry_hotspots(
    *,
    ths_fetcher: Callable[[], pd.DataFrame] | None = None,
    eastmoney_fetcher: Callable[[], pd.DataFrame] | None = None,
    now: datetime | None = None,
) -> HotspotResult:
    """Return industry boards using THS first and EastMoney as fallback."""
    return _get_hotspots(
        board_type=BOARD_TYPE_INDUSTRY,
        providers=(
            (SOURCE_THS, ths_fetcher or _fetch_ths_industry, normalize_ths_industry, True),
            (
                SOURCE_EASTMONEY,
                eastmoney_fetcher or _fetch_eastmoney_industry,
                normalize_eastmoney_boards,
                True,
            ),
        ),
        now=now,
    )


def get_concept_hotspots(
    *,
    eastmoney_fetcher: Callable[[], pd.DataFrame] | None = None,
    ths_fetcher: Callable[[], pd.DataFrame] | None = None,
    now: datetime | None = None,
) -> HotspotResult:
    """Return concept boards using EastMoney first and the THS directory as fallback."""
    return _get_hotspots(
        board_type=BOARD_TYPE_CONCEPT,
        providers=(
            (
                SOURCE_EASTMONEY,
                eastmoney_fetcher or _fetch_eastmoney_concept,
                normalize_eastmoney_boards,
                True,
            ),
            (
                SOURCE_THS,
                ths_fetcher or _fetch_ths_concept_directory,
                normalize_ths_concept_directory,
                False,
            ),
        ),
        now=now,
    )


def get_hotspot_cache_status(*, now: datetime | None = None) -> dict[str, object]:
    """Return process-local cache/failure diagnostics without exposing mutable state."""
    current_time = _as_utc(now or _utcnow())
    with _CACHE_LOCK:
        return {
            "cache": {
                f"{board_type}:{provider}": {
                    "fetched_at": entry.fetched_at,
                    "age_seconds": _cache_age(entry, current_time),
                    "size": len(entry.data),
                    "metrics_complete": entry.metrics_complete,
                }
                for (board_type, provider), entry in _board_cache.items()
            },
            "failures": {
                f"{board_type}:{provider}": {
                    "failed_at": failed_at,
                    "error": error,
                    "cooldown_until": failed_at + timedelta(seconds=FAILURE_COOLDOWN_SECONDS),
                }
                for (board_type, provider), (failed_at, error) in _provider_failures.items()
            },
        }


def _reset_hotspot_cache_for_tests() -> None:
    with _CACHE_LOCK:
        _board_cache.clear()
        _provider_failures.clear()
