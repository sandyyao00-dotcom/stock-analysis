"""Company fundamentals and a transparent, missing-data-aware score."""

from dataclasses import dataclass
import math
from typing import Callable

import streamlit as st
import yfinance as yf


@dataclass(frozen=True)
class FundamentalScoreComponent:
    """One weighted category in the fundamental score."""

    name: str
    weight: int
    points: float | None
    metrics_found: int
    metrics_possible: int
    explanation: str


@dataclass(frozen=True)
class FundamentalSummary:
    """Company information, score, and high-level assessments."""

    ticker: str
    market: str
    company_name: str
    sector: str | None
    industry: str | None
    country: str | None
    description: str | None
    metrics: dict[str, object]
    score: int | None
    score_label: str
    coverage_percent: int
    coverage_quality: str
    available_metric_count: int
    applicable_metric_count: int
    missing_metrics: tuple[str, ...]
    components: tuple[FundamentalScoreComponent, ...]
    valuation_assessment: str
    growth_assessment: str
    profitability_assessment: str
    health_assessment: str
    main_positive_factor: str
    main_risk_factor: str
    pays_dividend: bool


Rule = tuple[str, str, Callable[[float], float]]


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    # These fallbacks have equivalent or directly compatible meanings.
    "longName": ("longName", "shortName"),
    "pegRatio": ("pegRatio", "trailingPegRatio"),
}

SCORING_METRIC_LABELS: dict[str, str] = {
    "trailingPE": "滚动市盈率（P/E）",
    "forwardPE": "预期市盈率（P/E）",
    "priceToSalesTrailing12Months": "市销率",
    "priceToBook": "市净率（P/B）",
    "pegRatio": "PEG（市盈增长比）",
    "enterpriseToEbitda": "EV/EBITDA",
    "revenueGrowth": "营收增长",
    "earningsGrowth": "盈利增长",
    "epsGrowth": "EPS 增长",
    "earningsQuarterlyGrowth": "季度盈利增长",
    "revenueQuarterlyGrowth": "季度营收增长",
    "grossMargins": "毛利率",
    "operatingMargins": "营业利润率",
    "profitMargins": "净利率",
    "returnOnEquity": "净资产收益率（ROE）",
    "returnOnAssets": "总资产收益率",
    "debtToEquity": "负债权益比",
    "currentRatio": "流动比率",
    "quickRatio": "速动比率",
    "cashToDebt": "现金/债务",
    "freeCashflow": "自由现金流",
    "operatingCashflow": "经营现金流",
    "dividendYield": "股息率",
    "payoutRatio": "派息率",
}


def _number(value: object) -> float | None:
    """Return a finite number or None for missing/non-numeric Yahoo fields."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_fundamental_fields(raw_info: dict[str, object]) -> dict[str, object]:
    """Return canonical Yahoo fields using only financially compatible aliases."""
    normalized = dict(raw_info)
    for canonical, candidates in FIELD_ALIASES.items():
        if normalized.get(canonical) not in (None, ""):
            continue
        for candidate in candidates:
            value = raw_info.get(candidate)
            if value not in (None, ""):
                normalized[canonical] = value
                break
    return normalized


def _coverage_quality(coverage: int) -> str:
    if coverage >= 80:
        return "数据覆盖良好"
    if coverage >= 60:
        return "部分指标缺失"
    return "数据覆盖较低，评分参考价值下降"


def _lower_is_better(good: float, fair: float, high: float) -> Callable[[float], float]:
    def score(value: float) -> float:
        if value <= 0:
            return 0.0
        if value <= good:
            return 1.0
        if value <= fair:
            return 0.7
        if value <= high:
            return 0.4
        return 0.1

    return score


def _growth_score(value: float) -> float:
    if value >= 0.15:
        return 1.0
    if value >= 0.05:
        return 0.75
    if value >= 0:
        return 0.5
    if value >= -0.10:
        return 0.25
    return 0.0


def _higher_is_better(good: float, fair: float, minimum: float) -> Callable[[float], float]:
    def score(value: float) -> float:
        if value >= good:
            return 1.0
        if value >= fair:
            return 0.7
        if value >= minimum:
            return 0.4
        return 0.0

    return score


def _range_score(best_low: float, best_high: float, acceptable_low: float, acceptable_high: float) -> Callable[[float], float]:
    def score(value: float) -> float:
        if best_low <= value <= best_high:
            return 1.0
        if acceptable_low <= value <= acceptable_high:
            return 0.6
        return 0.2

    return score


def _component(name: str, weight: int, info: dict[str, object], rules: tuple[Rule, ...]) -> FundamentalScoreComponent:
    earned_ratios: list[float] = []
    details: list[str] = []
    for key, label, evaluator in rules:
        value = _number(info.get(key))
        if value is not None:
            ratio = max(0.0, min(1.0, evaluator(value)))
            earned_ratios.append(ratio)
            details.append(f"{label} 已计入")

    if not earned_ratios:
        return FundamentalScoreComponent(name, weight, None, 0, len(rules), "可用数据不足，本项不计入总分。")

    points = weight * sum(earned_ratios) / len(earned_ratios)
    explanation = f"按 {len(earned_ratios)} 个可用指标等权计算：" + "、".join(details) + "。"
    return FundamentalScoreComponent(name, weight, points, len(earned_ratios), len(rules), explanation)


def _assessment(component: FundamentalScoreComponent) -> str:
    if component.points is None:
        return "数据不足"
    ratio = component.points / component.weight
    if ratio >= 0.8:
        return "强"
    if ratio >= 0.6:
        return "良好"
    if ratio >= 0.4:
        return "一般"
    return "偏弱"


def _score_label(score: int | None) -> str:
    if score is None:
        return "数据不足"
    if score >= 80:
        return "强"
    if score >= 60:
        return "良好"
    if score >= 40:
        return "一般"
    if score >= 20:
        return "偏弱"
    return "很弱"


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_company_info(ticker: str) -> dict[str, object]:
    """Fetch a cached Yahoo Finance company-information dictionary."""
    try:
        info = yf.Ticker(ticker).get_info()
    except Exception as error:
        raise ValueError(f"无法获取 {ticker} 的基本面数据，请稍后重试。") from error
    if not isinstance(info, dict) or not info:
        raise ValueError(f"没有找到 {ticker} 的可用基本面数据。")
    return info


def analyze_fundamentals(ticker: str, info: dict[str, object], market: str = "未知") -> FundamentalSummary:
    """Create company details and normalize a score across available data only."""
    info = normalize_fundamental_fields(info)

    valuation_rules: tuple[Rule, ...] = (
        ("trailingPE", "滚动市盈率（P/E）", _lower_is_better(15, 25, 40)),
        ("forwardPE", "预期市盈率（P/E）", _lower_is_better(15, 25, 40)),
        ("priceToSalesTrailing12Months", "市销率", _lower_is_better(2, 5, 10)),
        ("priceToBook", "市净率（P/B）", _lower_is_better(2, 5, 10)),
        ("pegRatio", "PEG（市盈增长比）", _lower_is_better(1, 2, 3)),
        ("enterpriseToEbitda", "EV/EBITDA", _lower_is_better(10, 18, 30)),
    )
    growth_rules: tuple[Rule, ...] = (
        ("revenueGrowth", "营收增长", _growth_score),
        ("earningsGrowth", "盈利增长", _growth_score),
        ("epsGrowth", "EPS 增长", _growth_score),
        ("earningsQuarterlyGrowth", "季度盈利增长", _growth_score),
        ("revenueQuarterlyGrowth", "季度营收增长", _growth_score),
    )
    profitability_rules: tuple[Rule, ...] = (
        ("grossMargins", "毛利率", _higher_is_better(0.40, 0.20, 0)),
        ("operatingMargins", "营业利润率", _higher_is_better(0.20, 0.10, 0)),
        ("profitMargins", "净利率", _higher_is_better(0.15, 0.05, 0)),
        ("returnOnEquity", "净资产收益率（ROE）", _higher_is_better(0.20, 0.10, 0)),
        ("returnOnAssets", "总资产收益率", _higher_is_better(0.10, 0.05, 0)),
    )
    health_info = dict(info)
    cash = _number(info.get("totalCash"))
    debt = _number(info.get("totalDebt"))
    if cash is not None and debt is not None and debt > 0:
        health_info["cashToDebt"] = cash / debt
        info["cashToDebt"] = cash / debt
    health_rules: tuple[Rule, ...] = (
        ("debtToEquity", "负债权益比", _lower_is_better(50, 100, 200)),
        ("currentRatio", "流动比率", _range_score(1.5, 3.0, 1.0, 5.0)),
        ("quickRatio", "速动比率", _range_score(1.0, 2.5, 0.7, 4.0)),
        ("cashToDebt", "现金/债务", _higher_is_better(1.0, 0.5, 0.2)),
    )
    cash_flow_rules: tuple[Rule, ...] = (
        ("freeCashflow", "自由现金流", lambda value: 1.0 if value > 0 else 0.0),
        ("operatingCashflow", "经营现金流", lambda value: 1.0 if value > 0 else 0.0),
    )

    dividend_rate = _number(info.get("dividendRate"))
    dividend_yield = _number(info.get("dividendYield"))
    pays_dividend = bool((dividend_rate or 0) > 0 or (dividend_yield or 0) > 0)
    dividend_rules: tuple[Rule, ...] = (
        ("dividendYield", "股息率", _range_score(0.01, 0.06, 0.001, 0.10)),
        ("payoutRatio", "派息率", _range_score(0.20, 0.60, 0.0, 0.90)),
    )

    components = [
        _component("估值", 20, info, valuation_rules),
        _component("增长", 20, info, growth_rules),
        _component("盈利能力", 20, info, profitability_rules),
        _component("财务健康", 20, health_info, health_rules),
        _component("现金流", 15, info, cash_flow_rules),
    ]
    if pays_dividend:
        components.append(_component("股东回报/股息", 5, info, dividend_rules))
    else:
        components.append(FundamentalScoreComponent("股东回报/股息", 5, None, 0, len(dividend_rules), "公司目前没有可识别的股息，本项不适用且不计入总分。"))

    applicable = [item for item in components if item.points is not None]
    applicable_weight = sum(item.weight for item in applicable)
    score = round(sum(item.points or 0 for item in applicable) / applicable_weight * 100) if applicable_weight else None
    metrics_found = sum(item.metrics_found for item in components)
    metrics_possible = sum(item.metrics_possible for item in components[:-1]) + (len(dividend_rules) if pays_dividend else 0)
    coverage = round(metrics_found / metrics_possible * 100) if metrics_possible else 0
    applicable_keys = list(SCORING_METRIC_LABELS)[:-2]
    if pays_dividend:
        applicable_keys.extend(("dividendYield", "payoutRatio"))
    missing_metrics = tuple(
        SCORING_METRIC_LABELS[key] for key in applicable_keys if _number(info.get(key)) is None
    )

    strongest = max(applicable, key=lambda item: (item.points or 0) / item.weight, default=None)
    weakest = min(applicable, key=lambda item: (item.points or 0) / item.weight, default=None)
    positive = f"{strongest.name}（{strongest.points:.1f}/{strongest.weight}）" if strongest else "数据不足"
    risk = f"{weakest.name}（{weakest.points:.1f}/{weakest.weight}）" if weakest else "数据不足"

    return FundamentalSummary(
        ticker=ticker.strip().upper(),
        market=market,
        company_name=str(info.get("longName") or info.get("shortName") or ticker),
        sector=info.get("sector"),
        industry=info.get("industry"),
        country=info.get("country"),
        description=info.get("longBusinessSummary"),
        metrics=info,
        score=score,
        score_label=_score_label(score),
        coverage_percent=coverage,
        coverage_quality=_coverage_quality(coverage),
        available_metric_count=metrics_found,
        applicable_metric_count=metrics_possible,
        missing_metrics=missing_metrics,
        components=tuple(components),
        valuation_assessment=_assessment(components[0]),
        growth_assessment=_assessment(components[1]),
        profitability_assessment=_assessment(components[2]),
        health_assessment=_assessment(components[3]),
        main_positive_factor=positive,
        main_risk_factor=risk,
        pays_dividend=pays_dividend,
    )
