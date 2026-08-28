"""Constituent retrieval and transparent candidate pools for hotspot boards."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
import re
from threading import RLock
from typing import Callable, Iterable

import pandas as pd

from stock_analysis.market_hotspots import (
    BOARD_TYPE_CONCEPT,
    BOARD_TYPE_INDUSTRY,
    SOURCE_EASTMONEY,
    HotspotBoard,
    HotspotResult,
)
from stock_analysis.markets import MARKET_A_SHARE, SymbolValidationError, normalize_symbol


CONSTITUENTS_CACHE_TTL_SECONDS = 2700
FAILURE_COOLDOWN_SECONDS = 900
STALE_MAX_AGE_SECONDS = 86400
DEFAULT_CANDIDATES_PER_BOARD = 5

_CACHE_LOCK = RLock()


@dataclass(frozen=True)
class ConstituentStock:
    symbol: str | None
    name: str | None
    latest_price: float | None
    change_percent: float | None
    change_amount: float | None
    volume: float | None
    amount: float | None
    amplitude: float | None
    turnover_rate: float | None
    pe: float | None
    pb: float | None
    market_cap: float | None
    float_market_cap: float | None
    board_type: str
    board_name: str | None
    board_code: str | None
    source_provider: str
    fetched_at: datetime


@dataclass(frozen=True)
class MatchedBoard:
    board_type: str
    board_name: str | None
    board_code: str | None
    board_rank: int | None


@dataclass(frozen=True)
class HotspotCandidate:
    symbol: str
    name: str
    latest_price: float | None
    change_percent: float
    change_amount: float | None
    volume: float | None
    amount: float | None
    amplitude: float | None
    turnover_rate: float | None
    pe: float | None
    pb: float | None
    market_cap: float | None
    float_market_cap: float | None
    source_provider: str
    fetched_at: datetime
    relative_rank: int
    selection_reasons: tuple[str, ...]
    matched_boards: tuple[MatchedBoard, ...]
    matched_board_count: int


@dataclass(frozen=True)
class BoardCandidateResult:
    board_type: str
    board_name: str | None
    board_code: str | None
    available: bool
    stale: bool
    source_provider: str | None
    fetched_at: datetime | None
    candidate_count: int
    candidates: tuple[HotspotCandidate, ...] = ()
    error: str | None = None
    skip_reason: str | None = None


@dataclass(frozen=True)
class CandidatePoolResult:
    available: bool
    stale: bool
    candidates: tuple[HotspotCandidate, ...]
    board_results: tuple[BoardCandidateResult, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class _ConstituentCacheEntry:
    fetched_at: datetime
    data: tuple[ConstituentStock, ...]


_constituent_cache: dict[tuple[str, str, str], _ConstituentCacheEntry] = {}
_constituent_failures: dict[tuple[str, str, str], tuple[datetime, str]] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _fetch_concept_constituents(identifier: str) -> pd.DataFrame:
    import akshare as ak

    return ak.stock_board_concept_cons_em(symbol=identifier)


def _fetch_industry_constituents(identifier: str) -> pd.DataFrame:
    import akshare as ak

    return ak.stock_board_industry_cons_em(symbol=identifier)


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


def _normalize_a_share_symbol(value: object) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    try:
        return normalize_symbol(MARKET_A_SHARE, text).yahoo_symbol
    except (SymbolValidationError, AttributeError, TypeError):
        return None


def _board_identifier(board: HotspotBoard) -> str | None:
    if board.source_provider == SOURCE_EASTMONEY and board.board_code:
        code = board.board_code.strip().upper()
        if re.fullmatch(r"BK\d+", code):
            return code
    return _clean_text(board.board_name)


def _cache_key(board: HotspotBoard, identifier: str) -> tuple[str, str, str]:
    return SOURCE_EASTMONEY, board.board_type, identifier.casefold()


def normalize_eastmoney_constituents(
    frame: object, *, board: HotspotBoard, fetched_at: datetime
) -> tuple[ConstituentStock, ...]:
    """Normalize EastMoney constituent rows without inventing missing values."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("EastMoney returned an empty or invalid constituent DataFrame")
    if "代码" not in frame.columns:
        raise KeyError("EastMoney constituent data is missing 代码")
    fetched_at = _as_utc(fetched_at)
    stocks: list[ConstituentStock] = []
    for _, row in frame.iterrows():
        stocks.append(
            ConstituentStock(
                symbol=_normalize_a_share_symbol(row.get("代码")),
                name=_clean_text(row.get("名称")),
                latest_price=_safe_float(row.get("最新价")),
                change_percent=_safe_float(row.get("涨跌幅")),
                change_amount=_safe_float(row.get("涨跌额")),
                volume=_safe_float(row.get("成交量")),
                amount=_safe_float(row.get("成交额")),
                amplitude=_safe_float(row.get("振幅")),
                turnover_rate=_safe_float(row.get("换手率")),
                pe=_safe_float(row.get("市盈率-动态")),
                pb=_safe_float(row.get("市净率")),
                market_cap=_safe_float(row.get("总市值")),
                float_market_cap=_safe_float(row.get("流通市值")),
                board_type=board.board_type,
                board_name=board.board_name,
                board_code=board.board_code,
                source_provider=SOURCE_EASTMONEY,
                fetched_at=fetched_at,
            )
        )
    if not stocks:
        raise ValueError("EastMoney returned no constituent rows")
    return tuple(stocks)


def _sort_key(stock: ConstituentStock) -> tuple[float, float, str]:
    return (
        stock.change_percent if stock.change_percent is not None else float("-inf"),
        stock.amount if stock.amount is not None else float("-inf"),
        stock.symbol or "",
    )


def _select_candidates(
    stocks: tuple[ConstituentStock, ...],
    board: HotspotBoard,
    *,
    limit: int,
) -> tuple[HotspotCandidate, ...]:
    if limit < 1:
        return ()
    eligible = [
        stock
        for stock in stocks
        if stock.symbol and stock.name and stock.change_percent is not None
    ]
    eligible.sort(key=_sort_key, reverse=True)
    amount_order = sorted(
        (stock for stock in eligible if stock.amount is not None),
        key=lambda stock: (stock.amount, stock.symbol or ""),
        reverse=True,
    )
    active_symbols = {
        stock.symbol for stock in amount_order[: max(1, math.ceil(len(amount_order) / 2))]
    }
    matched_board = MatchedBoard(
        board_type=board.board_type,
        board_name=board.board_name,
        board_code=board.board_code,
        board_rank=board.rank,
    )
    selected: list[HotspotCandidate] = []
    for rank, stock in enumerate(eligible[:limit], start=1):
        assert stock.symbol is not None
        assert stock.name is not None
        assert stock.change_percent is not None
        reasons = ["板块内涨幅排名靠前"]
        if stock.amount is not None and stock.symbol in active_symbols:
            reasons.append("成交额在板块内相对活跃")
        selected.append(
            HotspotCandidate(
                symbol=stock.symbol,
                name=stock.name,
                latest_price=stock.latest_price,
                change_percent=stock.change_percent,
                change_amount=stock.change_amount,
                volume=stock.volume,
                amount=stock.amount,
                amplitude=stock.amplitude,
                turnover_rate=stock.turnover_rate,
                pe=stock.pe,
                pb=stock.pb,
                market_cap=stock.market_cap,
                float_market_cap=stock.float_market_cap,
                source_provider=stock.source_provider,
                fetched_at=stock.fetched_at,
                relative_rank=rank,
                selection_reasons=tuple(reasons),
                matched_boards=(matched_board,),
                matched_board_count=1,
            )
        )
    return tuple(selected)


def _unavailable_board(
    board: HotspotBoard,
    *,
    error: str | None = None,
    skip_reason: str | None = None,
) -> BoardCandidateResult:
    return BoardCandidateResult(
        board.board_type,
        board.board_name,
        board.board_code,
        False,
        False,
        None,
        None,
        0,
        error=error,
        skip_reason=skip_reason,
    )


def get_board_constituents(
    board: HotspotBoard,
    *,
    metrics_complete: bool = True,
    max_candidates: int = DEFAULT_CANDIDATES_PER_BOARD,
    fetcher: Callable[[str], pd.DataFrame] | None = None,
    now: datetime | None = None,
) -> BoardCandidateResult:
    """Fetch/cache one complete hotspot board and return transparent candidates."""
    if not metrics_complete:
        return _unavailable_board(board, skip_reason="incomplete_board_metrics")
    if board.board_type not in (BOARD_TYPE_CONCEPT, BOARD_TYPE_INDUSTRY):
        return _unavailable_board(board, skip_reason="unsupported_board_type")
    identifier = _board_identifier(board)
    if identifier is None:
        return _unavailable_board(board, skip_reason="missing_board_identifier")
    current_time = _as_utc(now or _utcnow())
    key = _cache_key(board, identifier)
    with _CACHE_LOCK:
        cached = _constituent_cache.get(key)
        cache_age = (
            (current_time - cached.fetched_at).total_seconds() if cached is not None else None
        )
        if cache_age is not None and 0 <= cache_age <= CONSTITUENTS_CACHE_TTL_SECONDS:
            candidates = _select_candidates(cached.data, board, limit=max_candidates)
            return BoardCandidateResult(
                board.board_type,
                board.board_name,
                board.board_code,
                True,
                False,
                SOURCE_EASTMONEY,
                cached.fetched_at,
                len(candidates),
                candidates,
            )
        failure = _constituent_failures.get(key)
        failure_age = (
            (current_time - failure[0]).total_seconds() if failure is not None else None
        )
        if failure_age is not None and 0 <= failure_age < FAILURE_COOLDOWN_SECONDS:
            error = failure[1]
        else:
            provider = fetcher or (
                _fetch_concept_constituents
                if board.board_type == BOARD_TYPE_CONCEPT
                else _fetch_industry_constituents
            )
            try:
                data = normalize_eastmoney_constituents(
                    provider(identifier), board=board, fetched_at=current_time
                )
            except Exception as exc:
                error = f"eastmoney constituents unavailable ({type(exc).__name__}: {exc})"
                _constituent_failures[key] = (current_time, error)
            else:
                entry = _ConstituentCacheEntry(current_time, data)
                _constituent_cache[key] = entry
                _constituent_failures.pop(key, None)
                candidates = _select_candidates(data, board, limit=max_candidates)
                return BoardCandidateResult(
                    board.board_type,
                    board.board_name,
                    board.board_code,
                    True,
                    False,
                    SOURCE_EASTMONEY,
                    current_time,
                    len(candidates),
                    candidates,
                )
        if cached is not None and cache_age is not None and 0 <= cache_age <= STALE_MAX_AGE_SECONDS:
            candidates = _select_candidates(cached.data, board, limit=max_candidates)
            return BoardCandidateResult(
                board.board_type,
                board.board_name,
                board.board_code,
                True,
                True,
                SOURCE_EASTMONEY,
                cached.fetched_at,
                len(candidates),
                candidates,
                error=error,
            )
        return _unavailable_board(board, error=error)


def _candidate_strength(candidate: HotspotCandidate) -> tuple[float, float, str]:
    return (
        candidate.change_percent,
        candidate.amount if candidate.amount is not None else float("-inf"),
        candidate.symbol,
    )


def _merge_candidates(candidates: Iterable[HotspotCandidate]) -> tuple[HotspotCandidate, ...]:
    merged: dict[str, HotspotCandidate] = {}
    for candidate in candidates:
        existing = merged.get(candidate.symbol)
        if existing is None:
            merged[candidate.symbol] = candidate
            continue
        boards = list(existing.matched_boards)
        for board in candidate.matched_boards:
            if board not in boards:
                boards.append(board)
        reasons = tuple(dict.fromkeys(existing.selection_reasons + candidate.selection_reasons))
        strongest = max((existing, candidate), key=_candidate_strength)
        merged[candidate.symbol] = replace(
            strongest,
            relative_rank=min(existing.relative_rank, candidate.relative_rank),
            selection_reasons=reasons,
            matched_boards=tuple(boards),
            matched_board_count=len(boards),
        )
    return tuple(sorted(merged.values(), key=_candidate_strength, reverse=True))


def build_hotspot_candidate_pool(
    hotspot_results: Iterable[HotspotResult],
    *,
    max_candidates_per_board: int = DEFAULT_CANDIDATES_PER_BOARD,
    concept_fetcher: Callable[[str], pd.DataFrame] | None = None,
    industry_fetcher: Callable[[str], pd.DataFrame] | None = None,
    now: datetime | None = None,
) -> CandidatePoolResult:
    """Build a symbol-unique pool from caller-selected complete hotspot boards."""
    board_results: list[BoardCandidateResult] = []
    errors: list[str] = []
    all_candidates: list[HotspotCandidate] = []
    for hotspot_result in hotspot_results:
        if not hotspot_result.available:
            if hotspot_result.error:
                errors.append(hotspot_result.error)
            continue
        for board in hotspot_result.data:
            fetcher = (
                concept_fetcher if board.board_type == BOARD_TYPE_CONCEPT else industry_fetcher
            )
            result = get_board_constituents(
                board,
                metrics_complete=hotspot_result.metrics_complete,
                max_candidates=max_candidates_per_board,
                fetcher=fetcher,
                now=now,
            )
            board_results.append(result)
            all_candidates.extend(result.candidates)
            if result.error:
                errors.append(f"{board.board_name or board.board_code}: {result.error}")
    candidates = _merge_candidates(all_candidates)
    return CandidatePoolResult(
        available=bool(candidates),
        stale=any(result.stale for result in board_results if result.available),
        candidates=candidates,
        board_results=tuple(board_results),
        errors=tuple(errors),
    )


def _reset_candidate_cache_for_tests() -> None:
    with _CACHE_LOCK:
        _constituent_cache.clear()
        _constituent_failures.clear()
