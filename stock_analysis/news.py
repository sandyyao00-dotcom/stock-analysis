"""Yahoo Finance news retrieval and deterministic headline classification."""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Iterable

import streamlit as st
import yfinance as yf


LABEL_POSITIVE = "潜在利好"
LABEL_NEGATIVE = "潜在利空"
LABEL_NEUTRAL = "中性/待确认"


@dataclass(frozen=True)
class NewsArticle:
    """A stable internal representation across yfinance payload versions."""

    title: str
    publisher: str | None
    published_at: datetime | None
    publication_timestamp: int | None
    url: str
    article_type: str | None
    related_tickers: tuple[str, ...]
    category: str
    event_label: str
    freshness: str


@dataclass(frozen=True)
class NewsResult:
    """A failure-isolated result that never blocks the rest of the app."""

    raw_item_count: int
    articles: tuple[NewsArticle, ...]
    error: str | None = None


CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("业绩指引", ("guidance", "outlook", "forecast", "业绩指引", "展望", "预测")),
    ("监管 / 法律", ("lawsuit", "investigation", "regulator", "regulatory", " sec ", "antitrust", "probe", "诉讼", "调查", "监管", "反垄断")),
    ("股息 / 回购", ("dividend", "buyback", "repurchase", "股息", "分红", "回购")),
    ("分析师评级", ("upgrade", "downgrade", "price target", "rating", "initiates coverage", "上调评级", "下调评级", "目标价")),
    ("并购 / 投资", ("acquisition", "acquire", "merger", "takeover", "investment", "stake", "收购", "并购", "投资", "持股")),
    ("管理层变动", ("ceo", "cfo", "chairman", "executive", "resigns", "appoints", "管理层", "董事长", "首席执行官", "辞任", "任命")),
    ("宏观 / 政策", ("interest rate", "inflation", "tariff", "federal reserve", "central bank", "policy", "利率", "通胀", "关税", "央行", "政策")),
    ("产品 / 新业务", ("launch", "new product", "approval", "contract", "partnership", "product", "发布", "新产品", "获批", "合同", "合作")),
    ("财报 / 业绩", ("earnings", "revenue", "profit", " eps ", "quarterly results", "财报", "营收", "利润", "业绩")),
    ("行业动态", ("industry", "sector", "market share", "supply chain", "行业", "产业", "市场份额", "供应链")),
)

POSITIVE_RULES = (
    "beats expectations",
    "beat estimates",
    "raises guidance",
    "raised guidance",
    "record revenue",
    "share buyback",
    "repurchase",
    "dividend increase",
    "wins approval",
    "receives approval",
    "major contract",
    "超出预期",
    "上调指引",
    "创纪录营收",
    "股份回购",
    "提高股息",
    "获得批准",
    "重大合同",
)

NEGATIVE_RULES = (
    "misses expectations",
    "missed estimates",
    "cuts guidance",
    "cut guidance",
    "investigation",
    "lawsuit",
    "recall",
    "downgrade",
    "regulatory action",
    "antitrust",
    "不及预期",
    "下调指引",
    "调查",
    "诉讼",
    "召回",
    "下调评级",
    "监管行动",
    "反垄断",
)


def classify_category(title: str) -> str:
    """Assign the first matching transparent event category."""
    searchable = f" {title.casefold()} "
    for category, keywords in CATEGORY_RULES:
        if any(keyword in searchable for keyword in keywords):
            return category
    return "其他"


def classify_event_label(title: str) -> str:
    """Conservatively describe apparent event direction, not price impact."""
    searchable = title.casefold()
    positive = any(keyword in searchable for keyword in POSITIVE_RULES)
    negative = any(keyword in searchable for keyword in NEGATIVE_RULES)
    if positive and not negative:
        return LABEL_POSITIVE
    if negative and not positive:
        return LABEL_NEGATIVE
    return LABEL_NEUTRAL


def freshness_label(published_at: datetime | None, now: datetime | None = None) -> str:
    """Return a deterministic age bucket."""
    if published_at is None:
        return "时间未知"
    current = now or datetime.now(timezone.utc)
    age = max(current - published_at, published_at - published_at)
    hours = age.total_seconds() / 3600
    if hours <= 24:
        return "最新"
    if hours <= 72:
        return "近期"
    if hours <= 168:
        return "本周"
    if hours <= 720:
        return "较早"
    return "历史"


def relative_age(published_at: datetime | None, now: datetime | None = None) -> str:
    """Format a compact Chinese relative age."""
    if published_at is None:
        return "时间未知"
    current = now or datetime.now(timezone.utc)
    seconds = max(0, int((current - published_at).total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    return f"{seconds // 86400} 天前"


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _nested_url(value: object) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, dict):
        url = value.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url
    return None


def _related_tickers(content: dict[str, object], item: dict[str, object]) -> tuple[str, ...]:
    candidates: list[object] = []
    for value in (content.get("relatedTickers"), item.get("relatedTickers")):
        if isinstance(value, list):
            candidates.extend(value)
    finance = content.get("finance")
    if isinstance(finance, dict):
        for key in ("stockTickers", "relatedTickers"):
            value = finance.get(key)
            if isinstance(value, list):
                candidates.extend(value)
    normalized: list[str] = []
    for candidate in candidates:
        symbol = candidate.get("symbol") if isinstance(candidate, dict) else candidate
        if isinstance(symbol, str) and symbol and symbol not in normalized:
            normalized.append(symbol)
    return tuple(normalized)


def normalize_news_item(item: object, now: datetime | None = None) -> NewsArticle | None:
    """Normalize current nested and legacy flat yfinance news payloads."""
    if not isinstance(item, dict):
        return None
    nested = item.get("content")
    content = nested if isinstance(nested, dict) else item
    title = content.get("title") or item.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    provider = content.get("provider")
    publisher = provider.get("displayName") if isinstance(provider, dict) else None
    if not isinstance(publisher, str) or not publisher:
        legacy_publisher = content.get("publisher") or item.get("publisher")
        publisher = legacy_publisher if isinstance(legacy_publisher, str) else None

    url = None
    for candidate in (
        content.get("canonicalUrl"),
        content.get("clickThroughUrl"),
        content.get("link"),
        item.get("link"),
    ):
        url = _nested_url(candidate)
        if url:
            break
    if not url:
        return None

    date_value = content.get("pubDate") or content.get("providerPublishTime") or item.get("providerPublishTime")
    published_at = _parse_datetime(date_value)
    timestamp = int(published_at.timestamp()) if published_at else None
    article_type = content.get("contentType") or content.get("type") or item.get("type")
    clean_title = title.strip()
    return NewsArticle(
        title=clean_title,
        publisher=publisher,
        published_at=published_at,
        publication_timestamp=timestamp,
        url=url,
        article_type=str(article_type) if article_type else None,
        related_tickers=_related_tickers(content, item),
        category=classify_category(clean_title),
        event_label=classify_event_label(clean_title),
        freshness=freshness_label(published_at, now),
    )


def normalize_news_items(items: object, limit: int = 10, now: datetime | None = None) -> tuple[NewsArticle, ...]:
    """Normalize, deduplicate, sort newest-first, and cap usable articles."""
    if not isinstance(items, list):
        return ()
    articles: list[NewsArticle] = []
    seen: set[str] = set()
    for item in items:
        article = normalize_news_item(item, now)
        if article is None:
            continue
        key = article.url.casefold() if article.url else re.sub(r"\W+", "", article.title.casefold())
        if key in seen:
            continue
        seen.add(key)
        articles.append(article)
    articles.sort(key=lambda article: article.publication_timestamp or 0, reverse=True)
    return tuple(articles[:limit])


@st.cache_data(ttl=1200, show_spinner=False)
def fetch_news(yahoo_symbol: str, count: int = 15) -> NewsResult:
    """Fetch cached Yahoo news while isolating every retrieval/payload failure."""
    try:
        raw_items = yf.Ticker(yahoo_symbol).get_news(count=count)
        raw_count = len(raw_items) if isinstance(raw_items, list) else 0
        articles = normalize_news_items(raw_items, limit=10)
        return NewsResult(raw_count, articles)
    except Exception:
        return NewsResult(0, (), "新闻数据暂时不可用，不影响技术面与基本面分析。")


def label_counts(articles: Iterable[NewsArticle]) -> dict[str, int]:
    """Count conservative event labels for the summary panel."""
    counts = {LABEL_POSITIVE: 0, LABEL_NEGATIVE: 0, LABEL_NEUTRAL: 0}
    for article in articles:
        counts[article.event_label] += 1
    return counts


def recent_catalysts_and_risks(
    articles: Iterable[NewsArticle], limit: int = 3
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return headline-backed positive and negative event summaries."""
    positives: list[str] = []
    negatives: list[str] = []
    for article in articles:
        target = positives if article.event_label == LABEL_POSITIVE else negatives if article.event_label == LABEL_NEGATIVE else None
        if target is not None and len(target) < limit:
            target.append(f"[{article.category}] {article.title}")
    return tuple(positives), tuple(negatives)
