"""Deterministic explanations for existing technical and fundamental scores."""

from dataclasses import dataclass
import math

import pandas as pd

from stock_analysis.analysis import StockSummary
from stock_analysis.fundamentals import FundamentalSummary
from stock_analysis.markets import format_money


@dataclass(frozen=True)
class ScoreExplanation:
    """One score component with the exact metrics behind its existing points."""

    name: str
    points: float | None
    maximum: int
    coverage: str | None
    metrics: tuple[str, ...]
    explanation: str

    @property
    def ratio(self) -> float | None:
        return None if self.points is None else self.points / self.maximum


@dataclass(frozen=True)
class RankedFactor:
    """A real score component ranked as a relative strength or risk."""

    name: str
    points: float
    maximum: int
    detail: str


@dataclass(frozen=True)
class AnalysisNarrative:
    """Short factual summary shown near the top of the page."""

    technical_sentence: str
    fundamental_sentence: str
    main_strength: str
    main_risk: str
    agreement: str


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _percent(value: object) -> str | None:
    number = _number(value)
    return None if number is None else f"{number * 100:+.2f}%"


def _ratio(value: object) -> str | None:
    number = _number(value)
    return None if number is None else f"{number:,.2f}"


def _money(value: object, currency: str) -> str | None:
    number = _number(value)
    return None if number is None else format_money(number, currency, compact=True)


def _available_metric(label: str, value: str | None) -> str | None:
    return None if value is None else f"{label}：{value}"


def technical_explanations(summary: StockSummary, data: pd.DataFrame, currency: str = "USD") -> tuple[ScoreExplanation, ...]:
    """Explain the existing technical components without changing their points."""
    components = {component.name: component for component in summary.score_components}
    latest = data.iloc[-1]
    ma50_previous = float(data["MA50"].iloc[-6])

    details = {
        "Trend": (
            "趋势",
            (
                f"当前价格：{format_money(summary.current_price, currency)}",
                f"MA50：{format_money(summary.ma50, currency)}",
                f"5 个交易日前 MA50：{format_money(ma50_previous, currency)}",
            ),
            f"价格位于 MA50 {'上方' if summary.current_price > summary.ma50 else '下方'}，MA50 近 5 个交易日{'上升' if summary.ma50 > ma50_previous else '下降'}；现有规则分别据此计分。",
        ),
        "Moving averages": (
            "移动平均线",
            tuple(
                item
                for item in (
                    f"当前价格：{format_money(summary.current_price, currency)}",
                    f"MA20：{format_money(summary.ma20, currency)}",
                    f"MA50：{format_money(summary.ma50, currency)}",
                    f"MA200：{format_money(summary.ma200, currency)}" if summary.ma200 is not None else None,
                )
                if item is not None
            ),
            f"价格相对 MA20、MA20 相对 MA50，以及 MA50 相对 MA200 的位置共同决定本项得分。当前为：{summary.ma_alignment}。",
        ),
        "RSI": (
            "RSI",
            (f"RSI(14)：{summary.rsi:.2f}", f"状态：{summary.rsi_status}"),
            f"RSI 当前为 {summary.rsi:.2f}。现有评分规则把该区间作为动能参考；超买或超卖仅是提示，不是买卖信号。",
        ),
        "MACD": (
            "MACD",
            (
                f"MACD：{summary.macd:.4f}",
                f"信号线：{summary.macd_signal:.4f}",
                f"柱状图：{summary.macd_histogram:+.4f}",
            ),
            f"MACD 当前位于信号线{'上方' if summary.macd > summary.macd_signal else '下方'}，并位于零轴{'上方' if summary.macd > 0 else '下方'}；这两个条件各自贡献现有分数。",
        ),
        "Price structure": (
            "价格结构",
            (f"近 20 日高点：{format_money(summary.recent_high, currency)}", f"近 20 日低点：{format_money(summary.recent_low, currency)}"),
            f"最近两个 20 交易日区间被识别为“{summary.price_structure}”，现有价格结构分数据此产生。",
        ),
        "Volume": (
            "成交量",
            (f"最新成交量：{summary.volume:,.0f}", f"20 日平均成交量：{summary.average_volume:,.0f}"),
            summary.volume_confirmation,
        ),
    }

    explanations: list[ScoreExplanation] = []
    for key in ("Trend", "Moving averages", "RSI", "MACD", "Price structure", "Volume"):
        component = components[key]
        name, metrics, explanation = details[key]
        explanations.append(ScoreExplanation(name, float(component.points), component.maximum, None, metrics, explanation))
    return tuple(explanations)


def fundamental_explanations(summary: FundamentalSummary, currency: str = "USD") -> tuple[ScoreExplanation, ...]:
    """Explain existing fundamental categories using only available Yahoo fields."""
    info = summary.metrics
    metric_specs = {
        "估值": (
            ("滚动市盈率（Trailing P/E）", "trailingPE", _ratio),
            ("预期市盈率（Forward P/E）", "forwardPE", _ratio),
            ("市销率（P/S）", "priceToSalesTrailing12Months", _ratio),
            ("市净率（P/B）", "priceToBook", _ratio),
            ("PEG（市盈增长比）", "pegRatio", _ratio),
            ("EV/EBITDA", "enterpriseToEbitda", _ratio),
        ),
        "增长": (
            ("营收增长", "revenueGrowth", _percent),
            ("盈利增长", "earningsGrowth", _percent),
            ("EPS 增长", "epsGrowth", _percent),
            ("季度盈利增长", "earningsQuarterlyGrowth", _percent),
            ("季度营收增长", "revenueQuarterlyGrowth", _percent),
        ),
        "盈利能力": (
            ("毛利率", "grossMargins", _percent),
            ("营业利润率", "operatingMargins", _percent),
            ("净利率", "profitMargins", _percent),
            ("净资产收益率（ROE）", "returnOnEquity", _percent),
            ("总资产收益率（ROA）", "returnOnAssets", _percent),
        ),
        "财务健康": (
            ("负债权益比", "debtToEquity", _ratio),
            ("流动比率", "currentRatio", _ratio),
            ("速动比率", "quickRatio", _ratio),
            ("现金总额", "totalCash", lambda value: _money(value, currency)),
            ("债务总额", "totalDebt", lambda value: _money(value, currency)),
        ),
        "现金流": (
            ("自由现金流", "freeCashflow", lambda value: _money(value, currency)),
            ("经营现金流", "operatingCashflow", lambda value: _money(value, currency)),
        ),
        "股东回报/股息": (
            ("股息率", "dividendYield", _percent),
            ("派息率", "payoutRatio", _percent),
        ),
    }

    explanations: list[ScoreExplanation] = []
    for component in summary.components:
        metrics = tuple(
            metric
            for label, key, formatter in metric_specs[component.name]
            if (metric := _available_metric(label, formatter(info.get(key)))) is not None
        )
        if component.name == "财务健康":
            cash = _number(info.get("totalCash"))
            debt = _number(info.get("totalDebt"))
            if cash is not None and debt is not None and debt > 0:
                metrics = (*metrics, f"现金/债务：{cash / debt:.2f}")
        if component.points is None:
            text = "该类别没有足够的可用数据，不计入总分，也不按负面因素处理。"
        else:
            ratio = component.points / component.weight
            level = "较强" if ratio >= 0.8 else "良好" if ratio >= 0.6 else "中等" if ratio >= 0.4 else "偏弱"
            text = f"按照现有评分阈值和 {component.metrics_found} 个可用指标计算，本类别表现{level}。{component.explanation}"
        explanations.append(
            ScoreExplanation(
                component.name,
                component.points,
                component.weight,
                f"{component.metrics_found}/{component.metrics_possible}",
                metrics,
                text,
            )
        )
    return tuple(explanations)


def rank_factors(explanations: tuple[ScoreExplanation, ...]) -> tuple[tuple[RankedFactor, ...], tuple[RankedFactor, ...]]:
    """Rank available components by their existing percentage contribution."""
    available = [item for item in explanations if item.points is not None and item.metrics]
    strongest = sorted(available, key=lambda item: (item.ratio or 0, item.points or 0), reverse=True)[:3]
    weakest = sorted(available, key=lambda item: (item.ratio or 0, item.points or 0))[:3]

    def convert(items: list[ScoreExplanation]) -> tuple[RankedFactor, ...]:
        return tuple(
            RankedFactor(item.name, float(item.points), item.maximum, f"{item.metrics[0]}；{item.explanation}")
            for item in items
        )

    return convert(strongest), convert(weakest)


def agreement_analysis(technical_score: int, fundamental_score: int | None) -> str:
    """Describe agreement or divergence without creating another score."""
    if fundamental_score is None:
        return f"技术面为 {technical_score}/100；基本面数据不足，暂时无法判断两者是否共振。"
    technical_strong = technical_score >= 60
    fundamental_strong = fundamental_score >= 60
    technical_weak = technical_score < 40
    fundamental_weak = fundamental_score < 40
    if technical_strong and fundamental_strong:
        return f"技术面 {technical_score}/100、基本面 {fundamental_score}/100，两者均偏强，当前价格趋势与公司基本面方向一致。"
    if technical_strong and not fundamental_strong:
        return f"技术面 {technical_score}/100 较强，但基本面 {fundamental_score}/100 相对较弱，价格动能领先于基本面表现。"
    if fundamental_strong and not technical_strong:
        return f"基本面 {fundamental_score}/100 较强，但技术面 {technical_score}/100 尚未同步走强，公司质量与当前价格趋势存在分歧。"
    if technical_weak and fundamental_weak:
        return f"技术面 {technical_score}/100、基本面 {fundamental_score}/100 均偏弱，两类信号目前方向一致。"
    return f"技术面 {technical_score}/100、基本面 {fundamental_score}/100，信号整体混合，暂未形成明确共振。"


def build_narrative(
    technical_score: int,
    technical_label: str,
    fundamental_score: int | None,
    fundamental_label: str,
    technical_strengths: tuple[RankedFactor, ...],
    technical_risks: tuple[RankedFactor, ...],
    fundamental_strengths: tuple[RankedFactor, ...],
    fundamental_risks: tuple[RankedFactor, ...],
) -> AnalysisNarrative:
    """Build a short factual V4 summary from already-ranked components."""
    fundamental_sentence = (
        f"基本面评分为 {fundamental_score}/100（{fundamental_label}），评分仅基于当前可用数据。"
        if fundamental_score is not None
        else "基本面可用数据不足，暂时无法形成完整评分。"
    )
    strengths = (*technical_strengths, *fundamental_strengths)
    risks = (*technical_risks, *fundamental_risks)
    main_strength = max(strengths, key=lambda item: item.points / item.maximum).detail if strengths else "暂无足够数据。"
    main_risk = min(risks, key=lambda item: item.points / item.maximum).detail if risks else "暂无足够数据。"
    return AnalysisNarrative(
        technical_sentence=f"技术面评分为 {technical_score}/100（{technical_label}），反映当前趋势、动能、结构与成交量。",
        fundamental_sentence=fundamental_sentence,
        main_strength=main_strength,
        main_risk=main_risk,
        agreement=agreement_analysis(technical_score, fundamental_score),
    )
