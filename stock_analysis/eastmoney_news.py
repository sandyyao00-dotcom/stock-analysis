"""Cached, failure-isolated EastMoney A-share news retrieval and normalization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from threading import RLock
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd


EASTMONEY_NEWS_CACHE_TTL_SECONDS = 600
EASTMONEY_NEWS_FAILURE_COOLDOWN_SECONDS = 600
DEFAULT_NEWS_LOOKBACK_DAYS = 30
SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class StandardNewsItem:
    title: str | None
    published_at: datetime | None
    source_name: str | None
    source_provider: str
    url: str | None
    summary_or_content: str | None
    symbol: str


@dataclass(frozen=True)
class EastMoneyNewsResult:
    available: bool
    news: tuple[StandardNewsItem, ...]
    error: str | None = None
    raw_item_count: int = 0


_CACHE_LOCK = RLock()
_news_cache: dict[str, tuple[datetime, EastMoneyNewsResult]] = {}
_failures: dict[str, tuple[datetime, str]] = {}

_COLUMN_ALIASES = {
    "title": ("新闻标题", "title"),
    "published_at": ("发布时间", "date", "published_at"),
    "source_name": ("文章来源", "mediaName", "source"),
    "url": ("新闻链接", "url"),
    "summary_or_content": ("新闻内容", "content", "summary"),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _fetch_eastmoney_frame(symbol: str) -> pd.DataFrame:
    import akshare as ak

    return ak.stock_news_em(symbol=symbol)


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


def _find_column(frame: pd.DataFrame, field: str) -> str | None:
    return next((name for name in _COLUMN_ALIASES[field] if name in frame.columns), None)


def _parse_eastmoney_time(value: object) -> datetime | None:
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
        parsed = parsed.replace(tzinfo=SHANGHAI_TIMEZONE)
    return parsed.astimezone(timezone.utc)


def _normalized_title(title: str) -> str:
    return re.sub(r"\W+", "", title.casefold())


def normalize_eastmoney_frame(
    frame: object,
    symbol: str,
    *,
    now: datetime | None = None,
    lookback_days: int = DEFAULT_NEWS_LOOKBACK_DAYS,
) -> tuple[StandardNewsItem, ...]:
    """Normalize, time-filter, deduplicate, and sort an EastMoney DataFrame."""
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("EastMoney news payload is not a DataFrame")
    if frame.empty:
        return ()
    current_time = _as_utc(now or _utcnow())
    cutoff = current_time - timedelta(days=lookback_days)
    columns = {field: _find_column(frame, field) for field in _COLUMN_ALIASES}
    if columns["published_at"] is None:
        raise ValueError("EastMoney news payload is missing 发布时间")

    items: list[StandardNewsItem] = []
    seen: set[tuple[str, ...]] = set()
    for _, row in frame.iterrows():
        published_at = _parse_eastmoney_time(row.get(columns["published_at"]))
        if published_at is None or published_at < cutoff or published_at > current_time:
            continue
        title = _clean_text(row.get(columns["title"])) if columns["title"] else None
        source_name = _clean_text(row.get(columns["source_name"])) if columns["source_name"] else None
        url = _clean_text(row.get(columns["url"])) if columns["url"] else None
        content = _clean_text(row.get(columns["summary_or_content"])) if columns["summary_or_content"] else None
        if url:
            dedupe_key = ("url", url.casefold())
        elif title:
            dedupe_key = ("title_time", _normalized_title(title), published_at.isoformat())
        else:
            dedupe_key = ("untitled", published_at.isoformat(), content or "")
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        items.append(
            StandardNewsItem(
                title=title,
                published_at=published_at,
                source_name=source_name,
                source_provider="eastmoney",
                url=url,
                summary_or_content=content,
                symbol=symbol,
            )
        )
    items.sort(key=lambda item: item.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return tuple(items)


def get_eastmoney_news(
    symbol: str,
    *,
    fetcher: Callable[[str], pd.DataFrame] | None = None,
    now: datetime | None = None,
) -> EastMoneyNewsResult:
    """Return cached normalized news for one six-digit A-share code."""
    if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}", symbol):
        return EastMoneyNewsResult(False, (), "东方财富新闻股票代码无效。")
    current_time = _as_utc(now or _utcnow())
    with _CACHE_LOCK:
        cached = _news_cache.get(symbol)
        if cached and (current_time - cached[0]).total_seconds() <= EASTMONEY_NEWS_CACHE_TTL_SECONDS:
            return cached[1]
        failure = _failures.get(symbol)
        if failure and (current_time - failure[0]).total_seconds() < EASTMONEY_NEWS_FAILURE_COOLDOWN_SECONDS:
            return EastMoneyNewsResult(False, (), failure[1])
        try:
            frame = (fetcher or _fetch_eastmoney_frame)(symbol)
            raw_count = len(frame) if isinstance(frame, pd.DataFrame) else 0
            items = normalize_eastmoney_frame(frame, symbol, now=current_time)
            if not items:
                raise ValueError("东方财富未返回时间窗口内的可用新闻。")
            result = EastMoneyNewsResult(True, items, raw_item_count=raw_count)
        except Exception as exc:
            safe_error = f"东方财富新闻暂时不可用（{type(exc).__name__}）。"
            _failures[symbol] = (current_time, safe_error)
            return EastMoneyNewsResult(False, (), safe_error)
        _news_cache[symbol] = (current_time, result)
        _failures.pop(symbol, None)
        return result


def _reset_eastmoney_news_cache_for_tests() -> None:
    with _CACHE_LOCK:
        _news_cache.clear()
        _failures.clear()
