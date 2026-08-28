"""Streamlit interface for the personal stock analysis app."""

from datetime import datetime, timezone
from html import escape
import math

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from stock_analysis.analysis import fetch_stock_data, summarize_stock
from stock_analysis.explanations import (
    agreement_analysis,
    build_narrative,
    fundamental_explanations,
    localize_technical_text,
    rank_factors,
    technical_explanations,
)
from stock_analysis.fundamentals import analyze_fundamentals, fetch_company_info
from stock_analysis.markets import (
    MARKET_A_SHARE,
    SUPPORTED_MARKETS,
    SymbolValidationError,
    currency_label,
    format_money,
    normalize_symbol,
    reliable_currency,
)
from stock_analysis.news import (
    LABEL_NEGATIVE,
    LABEL_NEUTRAL,
    LABEL_POSITIVE,
    NewsResult,
    fetch_market_news,
    label_counts,
    recent_catalysts_and_risks,
    relative_age,
)
from stock_analysis.realtime_ui import render_ashare_realtime_panel
from stock_analysis.ui_theme import apply_app_theme


def price_volume_chart(data, sessions: int, currency: str) -> go.Figure:
    """创建带成交量与均线的交互式 K 线图。"""
    view = data.tail(sessions)
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.75, 0.25])
    figure.add_trace(
        go.Candlestick(x=view.index, open=view["Open"], high=view["High"], low=view["Low"], close=view["Close"], name="价格"),
        row=1,
        col=1,
    )
    for column, color in (("MA20", "#1f77b4"), ("MA50", "#ff7f0e"), ("MA200", "#9467bd")):
        figure.add_trace(go.Scatter(x=view.index, y=view[column], name=column, line={"width": 1.5, "color": color}), row=1, col=1)
    colors = ["#2ca02c" if close >= opened else "#d62728" for close, opened in zip(view["Close"], view["Open"])]
    figure.add_trace(go.Bar(x=view.index, y=view["Volume"], name="成交量", marker_color=colors), row=2, col=1)
    figure.update_layout(height=650, xaxis_rangeslider_visible=False, hovermode="x unified", margin={"t": 25})
    figure.update_yaxes(title_text=f"价格（{currency}）", row=1, col=1)
    figure.update_yaxes(title_text="成交量", row=2, col=1)
    return figure


def macd_chart(data, sessions: int) -> go.Figure:
    """创建 MACD 线、信号线和柱状图。"""
    view = data.tail(sessions)
    colors = ["#2ca02c" if value >= 0 else "#d62728" for value in view["MACD_HISTOGRAM"]]
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=view.index, y=view["MACD"], name="MACD", line={"color": "#1f77b4"}))
    figure.add_trace(go.Scatter(x=view.index, y=view["MACD_SIGNAL"], name="信号线", line={"color": "#ff7f0e"}))
    figure.add_trace(go.Bar(x=view.index, y=view["MACD_HISTOGRAM"], name="柱状图", marker_color=colors))
    figure.add_hline(y=0, line_width=1, line_color="gray")
    figure.update_layout(height=350, hovermode="x unified", margin={"t": 25})
    return figure


def rsi_chart(data, sessions: int) -> go.Figure:
    """创建带超买、超卖线的 RSI 图。"""
    view = data.tail(sessions)
    figure = go.Figure(go.Scatter(x=view.index, y=view["RSI"], name="RSI (14)", line={"color": "#9467bd"}))
    figure.add_hline(y=70, line_dash="dash", line_color="#d62728", annotation_text="超买 70")
    figure.add_hline(y=30, line_dash="dash", line_color="#2ca02c", annotation_text="超卖 30")
    figure.update_yaxes(range=[0, 100])
    figure.update_layout(height=300, hovermode="x unified", margin={"t": 25})
    return figure


def numeric(value):
    """安全读取 Yahoo 数字字段。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def format_ratio(value, suffix: str = "") -> str:
    number = numeric(value)
    return "N/A" if number is None else f"{number:,.2f}{suffix}"


def format_percent(value) -> str:
    number = numeric(value)
    return "N/A" if number is None else f"{number * 100:+.2f}%"


def format_date(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    timestamp = numeric(value)
    if timestamp is None:
        return "N/A"
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return "N/A"


def show_metrics(items: tuple[tuple[str, str], ...], columns: int = 4) -> None:
    """用固定列数显示一组标签和值。"""
    metric_columns = st.columns(columns)
    for index, (label, value) in enumerate(items):
        metric_columns[index % columns].metric(label, value)


TECHNICAL_LABELS = {
    "Uptrend": "上升趋势",
    "Downtrend": "下降趋势",
    "Sideways/mixed": "横盘/混合",
    "Strong Bullish": "强势看多",
    "Bullish": "看多",
    "Neutral": "中性",
    "Bearish": "看空",
    "Strong Bearish": "强势看空",
    "Overbought": "超买",
    "Oversold": "超卖",
    "Unusually high": "异常偏高",
    "Unusually low": "异常偏低",
    "Near average": "接近平均",
}


st.set_page_config(page_title="股票分析 V4.1", page_icon="📈", layout="wide")
apply_app_theme()
st.title("个人股票分析")
st.caption("使用免费公开市场数据，分别展示技术面与基本面分析；无需 API 密钥。")

market_column, ticker_column, range_column = st.columns([1, 2, 1])
with market_column:
    selected_market = st.selectbox("市场", SUPPORTED_MARKETS, index=0)
with ticker_column:
    user_symbol = st.text_input("股票代码", value="AAPL", placeholder="美股 AAPL / A股 600519 / 港股 700").strip()
with range_column:
    chart_period = st.selectbox("图表区间", ("3 个月", "6 个月", "1 年"), index=1)
sessions = {"3 个月": 63, "6 个月": 126, "1 年": 252}[chart_period]

try:
    market_symbol = normalize_symbol(selected_market, user_symbol)
except SymbolValidationError as error:
    st.error(str(error))
    st.stop()
ticker = market_symbol.yahoo_symbol

try:
    with st.spinner(f"正在加载 {ticker} 的市场数据..."):
        history = fetch_stock_data(ticker)
        summary = summarize_stock(history)
except ValueError as error:
    st.error("未找到该证券的数据，请检查市场和代码是否正确。")
    st.stop()
except Exception:
    st.error("暂时无法加载市场数据，请检查网络或股票代码后重试。")
    st.stop()

fundamentals = None
fundamental_error = None
try:
    with st.spinner(f"正在加载 {ticker} 的基本面数据..."):
        company_info = fetch_company_info(ticker)
        fundamentals = analyze_fundamentals(ticker, company_info, market_symbol.market)
except ValueError as error:
    fundamental_error = str(error)
except Exception:
    fundamental_error = "基本面分析暂时无法完成，请稍后重试。"

try:
    news_result = fetch_market_news(market_symbol)
except Exception:
    news_result = NewsResult(0, (), "新闻数据暂时不可用，不影响技术面与基本面分析。")

provider_currency = fundamentals.metrics.get("currency") if fundamentals else None
currency = reliable_currency(provider_currency, market_symbol.default_currency)
technical_details = technical_explanations(summary, history, currency)
technical_strengths, technical_risks = rank_factors(technical_details)
fundamental_details = fundamental_explanations(fundamentals, currency) if fundamentals else ()
fundamental_strengths, fundamental_risks = rank_factors(fundamental_details)
fundamental_score = fundamentals.score if fundamentals else None
fundamental_label = fundamentals.score_label if fundamentals else "数据不足"
narrative = build_narrative(
    summary.technical_score,
    TECHNICAL_LABELS.get(summary.score_label, summary.score_label),
    fundamental_score,
    fundamental_label,
    technical_strengths,
    technical_risks,
    fundamental_strengths,
    fundamental_risks,
)

st.header("股票概览")
st.caption(
    f"市场：{market_symbol.market} ｜ 输入代码：{market_symbol.user_symbol} ｜ "
    f"数据代码：{market_symbol.yahoo_symbol} ｜ 币种：{currency_label(currency)}"
)
overview_columns = st.columns(6)
with overview_columns[0]:
    with st.container(key="primary-price"):
        st.metric("当前价格", format_money(summary.current_price, currency))
overview_columns[1].metric("日涨跌", format_money(summary.price_change, currency), f"{summary.price_change_percent:+.2f}%")
overview_columns[2].metric("MA20", format_money(summary.ma20, currency))
overview_columns[3].metric("MA50", format_money(summary.ma50, currency))
overview_columns[4].metric("MA200", format_money(summary.ma200, currency))
overview_columns[5].metric("近期成交量", f"{summary.volume:,.0f}")

if market_symbol.market == MARKET_A_SHARE:
    render_ashare_realtime_panel(market_symbol, currency, summary)

st.subheader("技术面与基本面对比")
with st.container(key="primary-scores"):
    comparison_columns = st.columns(2)
    comparison_columns[0].metric("技术评分", f"{summary.technical_score}/100", TECHNICAL_LABELS.get(summary.score_label, summary.score_label))
    if fundamentals and fundamentals.score is not None:
        comparison_columns[1].metric("基本面评分", f"{fundamentals.score}/100", fundamentals.score_label)
    else:
        comparison_columns[1].metric("基本面评分", "N/A", "数据不可用")
st.caption("两套评分彼此独立；V4 不生成综合投资评分或投资结论。")

st.subheader("一句话分析摘要")
summary_rows = (
    ("技术面一句话", narrative.technical_sentence),
    ("基本面一句话", narrative.fundamental_sentence),
    ("当前主要优势", narrative.main_strength),
    ("当前主要风险", narrative.main_risk),
    ("技术面与基本面是否一致", narrative.agreement),
)
summary_html = "".join(
    '<div class="quick-summary-row">'
    f'<span class="quick-summary-label">{escape(label)}：</span>'
    f'<span class="quick-summary-text">{escape(text)}</span>'
    "</div>"
    for label, text in summary_rows
)
st.markdown(f'<div class="quick-summary">{summary_html}</div>', unsafe_allow_html=True)

st.header("技术面摘要")
with st.container(key="technical-summary"):
    summary_columns = st.columns(5)
    summary_columns[0].metric("整体趋势", TECHNICAL_LABELS.get(summary.trend, summary.trend))
    summary_columns[1].metric("技术评分", f"{summary.technical_score}/100", TECHNICAL_LABELS.get(summary.score_label, summary.score_label))
    with summary_columns[2]:
        with st.container(key="momentum-summary"):
            st.metric("动能", localize_technical_text(summary.momentum))
    summary_columns[3].metric("最近支撑", format_money(summary.support, currency))
    summary_columns[4].metric("最近阻力", format_money(summary.resistance, currency))
factor_columns = st.columns(2)
factor_columns[0].success(
    f"主要看多因素 — {technical_strengths[0].detail}" if technical_strengths else "主要看多因素 — 数据不足"
)
factor_columns[1].warning(
    f"主要风险因素 — {technical_risks[0].detail}" if technical_risks else "主要风险因素 — 数据不足"
)

st.header("价格与成交量")
st.plotly_chart(price_volume_chart(history, sessions, currency), use_container_width=True)
volume_columns = st.columns(3)
volume_columns[0].metric("最新成交量", f"{summary.volume:,.0f}")
volume_columns[1].metric("20 日平均成交量", f"{summary.average_volume:,.0f}")
volume_columns[2].metric("成交量水平", TECHNICAL_LABELS.get(summary.volume_status, summary.volume_status))
st.write(localize_technical_text(summary.volume_confirmation))

st.header("趋势 / 移动平均线")
st.write(f"**均线排列：** {localize_technical_text(summary.ma_alignment)}")
st.write(f"**价格结构：** {localize_technical_text(summary.price_structure)}")
structure_columns = st.columns(4)
structure_columns[0].metric("近 20 日高点", format_money(summary.recent_high, currency))
structure_columns[1].metric("近 20 日低点", format_money(summary.recent_low, currency))
if summary.high_52_week is not None and summary.low_52_week is not None:
    structure_columns[2].metric("52 周高点", format_money(summary.high_52_week, currency), f"距高点 {summary.distance_from_high:.2f}%")
    structure_columns[3].metric("52 周低点", format_money(summary.low_52_week, currency), f"较低点 {summary.distance_from_low:+.2f}%")
else:
    structure_columns[2].metric("52 周高点", "N/A")
    structure_columns[3].metric("52 周低点", "N/A")

st.header("动能（RSI + MACD）")
momentum_columns = st.columns(4)
momentum_columns[0].metric("RSI (14)", f"{summary.rsi:.1f}", TECHNICAL_LABELS.get(summary.rsi_status, summary.rsi_status))
momentum_columns[1].metric("MACD", f"{summary.macd:.3f}")
momentum_columns[2].metric("信号线", f"{summary.macd_signal:.3f}")
momentum_columns[3].metric("柱状图", f"{summary.macd_histogram:+.3f}")
st.caption("RSI > 70 标记为超买，RSI < 30 标记为超卖；RSI 本身不是买卖信号。")
chart_columns = st.columns(2)
with chart_columns[0]:
    st.plotly_chart(rsi_chart(history, sessions), use_container_width=True)
with chart_columns[1]:
    st.plotly_chart(macd_chart(history, sessions), use_container_width=True)

st.header("支撑与阻力")
level_columns = st.columns(2)
level_columns[0].metric("附近支撑", format_money(summary.support, currency))
level_columns[1].metric("附近阻力", format_money(summary.resistance, currency))
st.caption("根据约 90 个交易日内的五日枢轴高低点估算；找不到附近枢轴时使用近期区间。参考位并非保证。")

st.header("技术评分明细")
st.progress(summary.technical_score, text=f"{summary.technical_score}/100 — {TECHNICAL_LABELS.get(summary.score_label, summary.score_label)}")
for detail in technical_details:
    with st.expander(f"{detail.name}：{detail.points:.0f}/{detail.maximum}"):
        for metric in detail.metrics:
            st.write(f"- {metric}")
        st.write(f"**解释：** {detail.explanation}")
st.caption("80–100 强势看多；60–79 看多；40–59 中性；20–39 看空；0–19 强势看空。")

st.divider()
st.header("基本面分析")
if fundamentals is None:
    st.warning(f"基本面数据不可用：{fundamental_error}")
else:
    st.subheader("公司概况")
    show_metrics(
        (
            ("公司名称", fundamentals.company_name),
            ("市场", market_symbol.market),
            ("股票代码", market_symbol.user_symbol),
            ("数据代码", market_symbol.yahoo_symbol),
            ("币种", currency_label(currency)),
            ("板块", fundamentals.sector or "N/A"),
            ("行业", fundamentals.industry or "N/A"),
            ("市值", format_money(fundamentals.metrics.get("marketCap"), currency, compact=True)),
            ("国家/地区", fundamentals.country or "N/A"),
        ),
        columns=3,
    )
    if fundamentals.description:
        with st.expander("公司业务简介"):
            st.write(fundamentals.description)

    st.subheader("基本面摘要")
    fundamental_columns = st.columns(5)
    fundamental_columns[0].metric("基本面评分", f"{fundamentals.score}/100" if fundamentals.score is not None else "N/A", fundamentals.score_label)
    fundamental_columns[1].metric("估值", fundamentals.valuation_assessment)
    fundamental_columns[2].metric("增长", fundamentals.growth_assessment)
    fundamental_columns[3].metric("盈利能力", fundamentals.profitability_assessment)
    fundamental_columns[4].metric("财务健康", fundamentals.health_assessment)
    factor_columns = st.columns(2)
    factor_columns[0].success(f"主要正面因素 — {fundamentals.main_positive_factor}")
    factor_columns[1].warning(f"主要风险因素 — {fundamentals.main_risk_factor}")
    st.info(
        f"数据覆盖率约 {fundamentals.coverage_percent}%（{fundamentals.coverage_quality}）。"
        "缺失指标不按零分处理，而是从适用权重中排除。"
    )
    if market_symbol.market != "美股" and fundamentals.coverage_percent < 80:
        st.warning(f"{market_symbol.market} 在 Yahoo Finance 的基本面字段覆盖较有限；当前评分仅基于已提供的数据。")

    st.subheader("数据质量")
    with st.expander(f"{fundamentals.coverage_quality} — 覆盖率 {fundamentals.coverage_percent}%"):
        show_metrics(
            (
                ("市场", fundamentals.market),
                ("标准化代码", market_symbol.yahoo_symbol),
                ("币种", currency_label(currency)),
                ("可用评分指标", str(fundamentals.available_metric_count)),
                ("预期/适用指标", str(fundamentals.applicable_metric_count)),
                ("覆盖率", f"{fundamentals.coverage_percent}%"),
            ),
            columns=3,
        )
        if fundamentals.missing_metrics:
            st.write("**当前缺失的评分指标：**")
            for metric in fundamentals.missing_metrics:
                st.write(f"- {metric}")
        else:
            st.write("当前适用的评分指标均有数据。")

    st.subheader("估值")
    show_metrics(
        (
            ("滚动市盈率（P/E）", format_ratio(fundamentals.metrics.get("trailingPE"))),
            ("预期市盈率（P/E）", format_ratio(fundamentals.metrics.get("forwardPE"))),
            ("市销率", format_ratio(fundamentals.metrics.get("priceToSalesTrailing12Months"))),
            ("市净率（P/B）", format_ratio(fundamentals.metrics.get("priceToBook"))),
            ("PEG（市盈增长比）", format_ratio(fundamentals.metrics.get("pegRatio"))),
            ("企业价值（EV）", format_money(fundamentals.metrics.get("enterpriseValue"), currency, compact=True)),
            ("EV/EBITDA", format_ratio(fundamentals.metrics.get("enterpriseToEbitda"))),
        )
    )
    st.caption("Yahoo Finance 未提供历史估值序列时，仅展示当前可用估值，不推测历史区间。")

    st.subheader("增长")
    show_metrics(
        (
            ("营收增长", format_percent(fundamentals.metrics.get("revenueGrowth"))),
            ("盈利增长", format_percent(fundamentals.metrics.get("earningsGrowth"))),
            ("EPS 增长", format_percent(fundamentals.metrics.get("epsGrowth"))),
            ("季度营收增长", format_percent(fundamentals.metrics.get("revenueQuarterlyGrowth"))),
            ("季度盈利增长", format_percent(fundamentals.metrics.get("earningsQuarterlyGrowth"))),
        )
    )

    st.subheader("盈利能力")
    show_metrics(
        (
            ("毛利率", format_percent(fundamentals.metrics.get("grossMargins"))),
            ("营业利润率", format_percent(fundamentals.metrics.get("operatingMargins"))),
            ("净利率", format_percent(fundamentals.metrics.get("profitMargins"))),
            ("净资产收益率（ROE）", format_percent(fundamentals.metrics.get("returnOnEquity"))),
            ("总资产收益率", format_percent(fundamentals.metrics.get("returnOnAssets"))),
        )
    )

    st.subheader("财务健康与现金流")
    show_metrics(
        (
            ("现金总额", format_money(fundamentals.metrics.get("totalCash"), currency, compact=True)),
            ("债务总额", format_money(fundamentals.metrics.get("totalDebt"), currency, compact=True)),
            ("负债权益比", format_ratio(fundamentals.metrics.get("debtToEquity"))),
            ("流动比率", format_ratio(fundamentals.metrics.get("currentRatio"))),
            ("速动比率", format_ratio(fundamentals.metrics.get("quickRatio"))),
            ("自由现金流", format_money(fundamentals.metrics.get("freeCashflow"), currency, compact=True)),
            ("经营现金流", format_money(fundamentals.metrics.get("operatingCashflow"), currency, compact=True)),
        )
    )

    st.subheader("股息信息")
    if fundamentals.pays_dividend:
        show_metrics(
            (
                ("股息率", format_percent(fundamentals.metrics.get("dividendYield"))),
                ("年度股息", format_money(fundamentals.metrics.get("dividendRate"), currency)),
                ("派息率", format_percent(fundamentals.metrics.get("payoutRatio"))),
                ("除息日", format_date(fundamentals.metrics.get("exDividendDate"))),
            )
        )
    else:
        st.info("Yahoo Finance 当前数据未显示该公司支付股息。股息类别不计入基本面总分。")

    st.subheader("52 周价格背景")
    show_metrics(
        (
            ("当前价格", format_money(summary.current_price, currency)),
            ("52 周高点", format_money(summary.high_52_week, currency)),
            ("距 52 周高点", f"{summary.distance_from_high:.2f}%" if summary.distance_from_high is not None else "N/A"),
            ("52 周低点", format_money(summary.low_52_week, currency)),
            ("较 52 周低点", f"{summary.distance_from_low:+.2f}%" if summary.distance_from_low is not None else "N/A"),
        )
    )

    st.subheader("基本面评分明细")
    if fundamentals.score is not None:
        st.progress(fundamentals.score, text=f"{fundamentals.score}/100 — {fundamentals.score_label}")
    for detail in fundamental_details:
        points = "N/A" if detail.points is None else f"{detail.points:.1f}"
        with st.expander(f"{detail.name}：{points}/{detail.maximum}（覆盖 {detail.coverage}）"):
            if detail.metrics:
                st.write("**本项使用的可用指标：**")
                for metric in detail.metrics:
                    st.write(f"- {metric}")
            st.write(f"**解释：** {detail.explanation}")
    st.caption("权重：估值 20、增长 20、盈利能力 20、财务健康 20、现金流 15、股东回报/股息 5。80–100 强；60–79 良好；40–59 一般；20–39 偏弱；0–19 很弱。")

st.divider()
st.header("新闻与催化剂")
news_source_label = "东方财富" if news_result.source_provider == "eastmoney" else "Yahoo Finance / yfinance"
st.caption(f"新闻数据来源：{news_source_label}")
if news_result.error:
    st.warning(news_result.error)
elif not news_result.articles:
    st.info("当前数据源未返回可用新闻。")
else:
    news_counts = label_counts(news_result.articles)
    latest_article = news_result.articles[0]
    news_columns = st.columns(5)
    news_columns[0].metric("最近新闻", f"{len(news_result.articles)} 条")
    news_columns[1].metric("最新消息", relative_age(latest_article.published_at))
    news_columns[2].metric(LABEL_POSITIVE, news_counts[LABEL_POSITIVE])
    news_columns[3].metric(LABEL_NEGATIVE, news_counts[LABEL_NEGATIVE])
    news_columns[4].metric(LABEL_NEUTRAL, news_counts[LABEL_NEUTRAL])

    catalysts, news_risks = recent_catalysts_and_risks(news_result.articles)
    catalyst_column, risk_column = st.columns(2)
    with catalyst_column:
        st.subheader("主要近期催化剂")
        if catalysts:
            for item in catalysts:
                st.write(f"- {item}")
        else:
            st.caption("暂无明确近期催化剂。")
    with risk_column:
        st.subheader("主要近期风险")
        if news_risks:
            for item in news_risks:
                st.write(f"- {item}")
        else:
            st.caption("暂无明确近期风险。")

    st.subheader("最新新闻")
    for index, article in enumerate(news_result.articles):
        with st.container(border=True):
            st.markdown(f"**[{article.category}] [{article.event_label}]**")
            st.write(article.title)
            if article.summary_or_content:
                summary = article.summary_or_content
                st.write(summary if len(summary) <= 300 else f"{summary[:297]}...")
            st.write(article.explanation)
            published_text = article.published_at.strftime("%Y-%m-%d %H:%M（协调世界时）") if article.published_at else "发布时间未知"
            st.caption(
                f"来源：{article.publisher or 'N/A'} ｜ {published_text} ｜ "
                f"{relative_age(article.published_at)} ｜ {article.freshness}"
            )
            if article.url:
                st.link_button("查看原文", article.url, key=f"news-link-{index}")

st.caption("新闻分类与利好/利空标签由本地规则生成，仅用于信息整理，不代表未来股价表现或投资建议。")

st.divider()
st.header("关键优势与风险")
factor_sections = (
    ("技术面前三项优势", technical_strengths),
    ("技术面前三项风险", technical_risks),
    ("基本面前三项优势", fundamental_strengths),
    ("基本面前三项风险", fundamental_risks),
)
factor_columns = st.columns(2)
for index, (title, factors) in enumerate(factor_sections):
    with factor_columns[index % 2]:
        st.subheader(title)
        if factors:
            for rank, factor in enumerate(factors, start=1):
                st.write(f"{rank}. **{factor.name}（{factor.points:.1f}/{factor.maximum}）** — {factor.detail}")
        else:
            st.caption("可用数据不足。")

st.header("技术面与基本面的共振 / 分歧")
st.info(agreement_analysis(summary.technical_score, fundamental_score))
st.caption("这里只比较两套现有评分的方向，不生成新的综合分数，也不构成投资建议。")

st.divider()
st.header("未来版本")
future_items = (
    ("智能分析", "综合技术面与基本面的自然语言分析。"),
    ("持仓与成本分析", "仓位、平均成本、盈亏和风险敞口。"),
    ("自选股", "保存股票代码和个人研究笔记。"),
    ("A 股与港股支持", "扩展地区代码、财报字段和市场惯例。"),
)
future_columns = st.columns(2)
for index, (title, description) in enumerate(future_items):
    with future_columns[index % 2]:
        st.markdown(f"#### {title}")
        st.caption(f"计划中：{description}")

st.divider()
st.caption("技术面与基本面分析仅供信息和学习用途，不构成财务建议。Yahoo Finance 数据可能延迟、缺失或口径不一致。")
