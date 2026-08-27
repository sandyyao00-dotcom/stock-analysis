"""Yahoo Finance news retrieval and deterministic headline classification."""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Iterable

import streamlit as st
import yfinance as yf


LABEL_POSITIVE = "潜在利好"
LABEL_NEGATIVE = "潜在利空"
LABEL_NEUTRAL = "中性/等待确认"


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
    explanation: str
    freshness: str


@dataclass(frozen=True)
class NewsResult:
    """A failure-isolated result that never blocks the rest of the app."""

    raw_item_count: int
    articles: tuple[NewsArticle, ...]
    error: str | None = None


CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Priority is intentional: specific event types win over broad technology/industry words.
    ("监管/诉讼", ("regulator", "regulation", "antitrust", "lawsuit", "court", "legal", "probe", "investigation", "fine", "ban", "doj", "ftc", "sec", "european commission", "诉讼", "调查", "监管", "反垄断", "罚款")),
    ("分析师评级", ("analyst", "rating", "rating upgrade", "rating downgrade", "upgraded to", "downgraded to", "price target", "target price", "overweight", "underweight", "buy rating", "sell rating", "outperform", "underperform", "分析师", "评级", "目标价")),
    ("财报/业绩", ("earnings", "revenue", "profit", "sales", "guidance", "forecast", "quarter", "quarterly", "eps", "margin", "results", "outlook", "财报", "营收", "利润", "业绩", "指引")),
    ("股东回报", ("dividend", "buyback", "repurchase", "shareholder return", "股息", "分红", "回购")),
    ("并购/投资", ("acquire", "acquisition", "merger", "takeover", "investment", "invest", "stake", "partnership", "deal", "joint venture", "收购", "并购", "投资", "持股", "合作")),
    ("管理层", ("ceo", "cfo", "executive", "management", "chairman", "resign", "retirement", "appoint", "appointment", "管理层", "董事长", "首席执行官", "辞任", "任命")),
    ("产品/新品", ("launch", "launches", "launched", "release", "releases", "released", "unveil", "unveils", "unveiled", "debut", "product", "iphone", "ipad", "mac", "device", "model", "feature", "camera upgrade", "发布", "新品", "产品", "设备", "功能升级")),
    ("AI/技术", ("ai", "artificial intelligence", "machine learning", "chip", "semiconductor", "software", "technology", "siri", "cloud", "data center", "人工智能", "机器学习", "芯片", "半导体", "软件", "云计算", "数据中心")),
    ("宏观/行业", ("fed", "federal reserve", "interest rate", "inflation", "tariff", "economy", "economic", "recession", "industry", "china demand", "demand in china", "consumer spending", "sector", "supply chain", "美联储", "利率", "通胀", "关税", "经济", "行业", "供应链")),
)

POSITIVE_RULES = (
    "beats expectations",
    "beats earnings estimates",
    "beats revenue estimates",
    "beats estimates",
    "beat estimates",
    "raises guidance",
    "raised guidance",
    "record revenue",
    "record profit",
    "stronger demand",
    "strong demand",
    "price target raised",
    "raises price target",
    "analyst upgrades",
    "upgraded to buy",
    "new product launch",
    "unveils major",
    "major camera upgrade",
    "major product upgrade",
    "share buyback",
    "buyback",
    "repurchase",
    "dividend increase",
    "wins approval",
    "receives approval",
    "approval",
    "wins contract",
    "major contract",
    "partnership",
    "revenue growth",
    "profit growth",
    "margin improvement",
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
    "misses earnings estimates",
    "misses revenue estimates",
    "missed estimates",
    "cuts guidance",
    "cut guidance",
    "lowers guidance",
    "lowered guidance",
    "price target cut",
    "cuts price target",
    "analyst downgrades",
    "investigation",
    "lawsuit",
    "recall",
    "downgrade",
    "regulatory action",
    "antitrust",
    "weak demand",
    "weaker demand",
    "declining sales",
    "revenue decline",
    "profit decline",
    "margin pressure",
    "layoffs",
    "ceo resignation",
    "regulatory risk",
    "不及预期",
    "下调指引",
    "调查",
    "诉讼",
    "召回",
    "下调评级",
    "监管行动",
    "反垄断",
)

NEGATIVE_CONTEXT_EXCLUSIONS = (
    "lawsuit dismissed",
    "dismisses lawsuit",
    "investigation closed",
    "cleared of allegations",
)

EXPLANATION_TEMPLATES = {
    (LABEL_POSITIVE, "财报/业绩"): "业绩、增长或指引出现正面信号，可能改善市场预期，但仍需结合正式财报确认。",
    (LABEL_NEGATIVE, "财报/业绩"): "业绩或指引出现负面信号，可能影响市场对未来盈利能力的预期。",
    (LABEL_POSITIVE, "产品/新品"): "新品发布或重要升级可能增强产品竞争力，但实际影响仍需观察后续销量和市场反馈。",
    (LABEL_NEGATIVE, "产品/新品"): "产品需求、召回或销售相关负面信号可能增加经营压力，需关注后续进展。",
    (LABEL_POSITIVE, "分析师评级"): "分析师上调评级或目标价，反映市场预期改善，但不代表公司基本面已经发生变化。",
    (LABEL_NEGATIVE, "分析师评级"): "分析师下调评级或目标价，反映预期转弱，但仍需核对其假设和依据。",
    (LABEL_POSITIVE, "并购/投资"): "合作、投资或并购可能扩展业务能力，但最终价值取决于执行与整合效果。",
    (LABEL_NEGATIVE, "并购/投资"): "交易或投资相关负面进展可能增加执行和财务风险，需关注后续披露。",
    (LABEL_POSITIVE, "监管/诉讼"): "监管或法律事项出现有利进展，但事件是否彻底解决仍需等待正式结果。",
    (LABEL_NEGATIVE, "监管/诉讼"): "监管或法律事件可能增加经营不确定性及潜在成本，需关注后续进展。",
    (LABEL_POSITIVE, "股东回报"): "股息增加或股份回购可能提升股东回报，但不代表未来回报得到保证。",
    (LABEL_NEGATIVE, "管理层"): "关键管理层变动可能增加执行不确定性，需关注继任安排与经营连续性。",
    (LABEL_POSITIVE, "AI/技术"): "技术能力或基础设施取得积极进展，潜在商业影响仍需后续验证。",
    (LABEL_NEGATIVE, "宏观/行业"): "宏观或行业环境出现不利信号，可能影响需求、成本或市场预期。",
}


def _matches(text: str, phrase: str) -> bool:
    """Match English phrases at word boundaries and Chinese phrases by substring."""
    if phrase.isascii():
        words = phrase.split()
        pattern = r"[\W_]+".join(re.escape(word) for word in words)
        return re.search(rf"(?<!\w){pattern}(?!\w)", text) is not None
    return phrase in text


def classify_category(title: str) -> str:
    """Assign the first matching transparent event category."""
    searchable = title.casefold()
    for category, keywords in CATEGORY_RULES:
        if any(_matches(searchable, keyword) for keyword in keywords):
            return category
    return "其他"


def sentiment_evidence(title: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the exact positive and negative rule evidence used for a title."""
    searchable = title.casefold()
    positive_matches = [keyword for keyword in POSITIVE_RULES if _matches(searchable, keyword)]
    negative_matches = [keyword for keyword in NEGATIVE_RULES if _matches(searchable, keyword)]
    category = classify_category(title)
    product_launch = category == "产品/新品" and (
        re.search(r"(?<!\w)(?:unveil|unveils|unveiled|launch|launches|launched)\s+(?:a\s+|its\s+|the\s+)?new(?!\w)", searchable)
        or re.search(r"(?<!\w)new\b.{0,40}\b(?:product\s+)?(?:launch|launches|launched)(?!\w)", searchable)
    )
    if product_launch:
        positive_matches.append("产品发布/揭晓语境")
    if any(_matches(searchable, phrase) for phrase in NEGATIVE_CONTEXT_EXCLUSIONS):
        negative_matches = []
    return tuple(positive_matches), tuple(negative_matches)


def classify_event_label(title: str) -> str:
    """Conservatively describe apparent event direction, not price impact."""
    positive_matches, negative_matches = sentiment_evidence(title)
    positive = bool(positive_matches)
    negative = bool(negative_matches)
    if positive and not negative:
        return LABEL_POSITIVE
    if negative and not positive:
        return LABEL_NEGATIVE
    return LABEL_NEUTRAL


def build_news_explanation(category: str, event_label: str) -> str:
    """Build a short local explanation from category and event label."""
    if event_label == LABEL_NEUTRAL:
        return "当前标题信息不足以确认明确方向，暂列为中性/等待确认。"
    specific = EXPLANATION_TEMPLATES.get((event_label, category))
    if specific:
        return specific
    if event_label == LABEL_POSITIVE:
        return "标题呈现潜在正面事件，但实际影响仍需结合后续披露和经营数据确认。"
    return "标题呈现潜在负面事件，可能增加不确定性，但实际影响仍需等待后续信息确认。"


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
    category = classify_category(clean_title)
    event_label = classify_event_label(clean_title)
    return NewsArticle(
        title=clean_title,
        publisher=publisher,
        published_at=published_at,
        publication_timestamp=timestamp,
        url=url,
        article_type=str(article_type) if article_type else None,
        related_tickers=_related_tickers(content, item),
        category=category,
        event_label=event_label,
        explanation=build_news_explanation(category, event_label),
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
            short_title = article.title if len(article.title) <= 90 else f"{article.title[:87]}..."
            target.append(f"[{article.category}] {short_title}（{relative_age(article.published_at)}）")
    return tuple(positives), tuple(negatives)
