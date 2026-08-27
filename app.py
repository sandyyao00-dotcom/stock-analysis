"""Streamlit interface for the personal stock analysis app."""

from datetime import datetime, timezone
import math

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from stock_analysis.analysis import fetch_stock_data, summarize_stock
from stock_analysis.fundamentals import analyze_fundamentals, fetch_company_info


def price_volume_chart(data, sessions: int) -> go.Figure:
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
    figure.update_yaxes(title_text="价格", row=1, col=1)
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


def format_number(value, currency: bool = False) -> str:
    """以易读单位显示大数字，缺失时显示 N/A。"""
    number = numeric(value)
    if number is None:
        return "N/A"
    prefix = "$" if currency else ""
    for divisor, suffix in ((1_000_000_000_000, "T"), (1_000_000_000, "B"), (1_000_000, "M")):
        if abs(number) >= divisor:
            return f"{prefix}{number / divisor:,.2f}{suffix}"
    return f"{prefix}{number:,.2f}"


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


st.set_page_config(page_title="股票分析 V3", page_icon="📈", layout="wide")
st.title("个人股票分析")
st.caption("使用免费公开市场数据，分别展示技术面与基本面分析；无需 API Key。")

ticker_column, range_column = st.columns([3, 1])
with ticker_column:
    ticker = st.text_input("股票代码", value="AAPL", placeholder="例如 AAPL").strip().upper()
with range_column:
    chart_period = st.selectbox("图表区间", ("3 个月", "6 个月", "1 年"), index=1)
sessions = {"3 个月": 63, "6 个月": 126, "1 年": 252}[chart_period]

if not ticker:
    st.info("请输入股票代码。")
    st.stop()

try:
    with st.spinner(f"正在加载 {ticker} 的市场数据..."):
        history = fetch_stock_data(ticker)
        summary = summarize_stock(history)
except ValueError as error:
    st.error(str(error))
    st.stop()
except Exception:
    st.error("暂时无法加载市场数据，请检查网络或股票代码后重试。")
    st.stop()

fundamentals = None
fundamental_error = None
try:
    with st.spinner(f"正在加载 {ticker} 的基本面数据..."):
        company_info = fetch_company_info(ticker)
        fundamentals = analyze_fundamentals(ticker, company_info)
except ValueError as error:
    fundamental_error = str(error)
except Exception:
    fundamental_error = "基本面分析暂时无法完成，请稍后重试。"

st.header("股票概览")
overview_columns = st.columns(6)
overview_columns[0].metric("当前价格", f"${summary.current_price:,.2f}")
overview_columns[1].metric("日涨跌", f"${summary.price_change:+,.2f}", f"{summary.price_change_percent:+.2f}%")
overview_columns[2].metric("MA20", f"${summary.ma20:,.2f}")
overview_columns[3].metric("MA50", f"${summary.ma50:,.2f}")
overview_columns[4].metric("MA200", f"${summary.ma200:,.2f}" if summary.ma200 is not None else "N/A")
overview_columns[5].metric("近期成交量", f"{summary.volume:,.0f}")

st.subheader("技术面 / 基本面对比")
comparison_columns = st.columns(2)
comparison_columns[0].metric("技术评分", f"{summary.technical_score}/100", TECHNICAL_LABELS.get(summary.score_label, summary.score_label))
if fundamentals and fundamentals.score is not None:
    comparison_columns[1].metric("基本面评分", f"{fundamentals.score}/100", fundamentals.score_label)
else:
    comparison_columns[1].metric("基本面评分", "N/A", "数据不可用")
st.caption("两套评分彼此独立；V3 不生成综合投资评分或投资结论。")

st.header("技术面摘要")
summary_columns = st.columns(5)
summary_columns[0].metric("整体趋势", TECHNICAL_LABELS.get(summary.trend, summary.trend))
summary_columns[1].metric("技术评分", f"{summary.technical_score}/100", TECHNICAL_LABELS.get(summary.score_label, summary.score_label))
summary_columns[2].metric("动能", summary.momentum.replace("Bullish", "看多").replace("Bearish", "看空").replace("Neutral/mixed", "中性/混合").replace(" MACD momentum", ""))
summary_columns[3].metric("最近支撑", f"${summary.support:,.2f}")
summary_columns[4].metric("最近阻力", f"${summary.resistance:,.2f}")
factor_columns = st.columns(2)
factor_columns[0].success(f"主要看多因素 — {summary.main_bullish_factor}")
factor_columns[1].warning(f"主要风险因素 — {summary.main_risk_factor}")

st.header("价格与成交量")
st.plotly_chart(price_volume_chart(history, sessions), use_container_width=True)
volume_columns = st.columns(3)
volume_columns[0].metric("最新成交量", f"{summary.volume:,.0f}")
volume_columns[1].metric("20 日平均成交量", f"{summary.average_volume:,.0f}")
volume_columns[2].metric("成交量水平", TECHNICAL_LABELS.get(summary.volume_status, summary.volume_status))
st.write(summary.volume_confirmation)

st.header("趋势 / 移动平均线")
st.write(f"**均线排列：** {summary.ma_alignment}")
st.write(f"**价格结构：** {summary.price_structure}")
structure_columns = st.columns(4)
structure_columns[0].metric("近 20 日高点", f"${summary.recent_high:,.2f}")
structure_columns[1].metric("近 20 日低点", f"${summary.recent_low:,.2f}")
if summary.high_52_week is not None and summary.low_52_week is not None:
    structure_columns[2].metric("52 周高点", f"${summary.high_52_week:,.2f}", f"距高点 {summary.distance_from_high:.2f}%")
    structure_columns[3].metric("52 周低点", f"${summary.low_52_week:,.2f}", f"较低点 {summary.distance_from_low:+.2f}%")
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
level_columns[0].metric("附近支撑", f"${summary.support:,.2f}")
level_columns[1].metric("附近阻力", f"${summary.resistance:,.2f}")
st.caption("根据约 90 个交易日内的五日枢轴高低点估算；找不到附近枢轴时使用近期区间。参考位并非保证。")

st.header("技术评分明细")
st.progress(summary.technical_score, text=f"{summary.technical_score}/100 — {TECHNICAL_LABELS.get(summary.score_label, summary.score_label)}")
for component in summary.score_components:
    with st.expander(f"{component.name}: {component.points}/{component.maximum}"):
        st.write(component.explanation)
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
            ("股票代码", ticker),
            ("板块", fundamentals.sector or "N/A"),
            ("行业", fundamentals.industry or "N/A"),
            ("市值", format_number(fundamentals.metrics.get("marketCap"), currency=True)),
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
    st.info(f"数据覆盖率约 {fundamentals.coverage_percent}% 。缺失指标不按零分处理，而是从适用权重中排除。")

    st.subheader("估值")
    show_metrics(
        (
            ("滚动市盈率（Trailing P/E）", format_ratio(fundamentals.metrics.get("trailingPE"))),
            ("预期市盈率（Forward P/E）", format_ratio(fundamentals.metrics.get("forwardPE"))),
            ("市销率（P/S）", format_ratio(fundamentals.metrics.get("priceToSalesTrailing12Months"))),
            ("市净率（P/B）", format_ratio(fundamentals.metrics.get("priceToBook"))),
            ("PEG（市盈增长比）", format_ratio(fundamentals.metrics.get("pegRatio"))),
            ("企业价值（EV）", format_number(fundamentals.metrics.get("enterpriseValue"), currency=True)),
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
            ("总资产收益率（ROA）", format_percent(fundamentals.metrics.get("returnOnAssets"))),
        )
    )

    st.subheader("财务健康与现金流")
    show_metrics(
        (
            ("现金总额", format_number(fundamentals.metrics.get("totalCash"), currency=True)),
            ("债务总额", format_number(fundamentals.metrics.get("totalDebt"), currency=True)),
            ("负债权益比", format_ratio(fundamentals.metrics.get("debtToEquity"))),
            ("流动比率", format_ratio(fundamentals.metrics.get("currentRatio"))),
            ("速动比率", format_ratio(fundamentals.metrics.get("quickRatio"))),
            ("自由现金流", format_number(fundamentals.metrics.get("freeCashflow"), currency=True)),
            ("经营现金流", format_number(fundamentals.metrics.get("operatingCashflow"), currency=True)),
        )
    )

    st.subheader("股息信息")
    if fundamentals.pays_dividend:
        show_metrics(
            (
                ("股息率", format_percent(fundamentals.metrics.get("dividendYield"))),
                ("年度股息", format_number(fundamentals.metrics.get("dividendRate"), currency=True)),
                ("派息率", format_percent(fundamentals.metrics.get("payoutRatio"))),
                ("除息日", format_date(fundamentals.metrics.get("exDividendDate"))),
            )
        )
    else:
        st.info("Yahoo Finance 当前数据未显示该公司支付股息。股息类别不计入基本面总分。")

    st.subheader("52 周价格背景")
    show_metrics(
        (
            ("当前价格", f"${summary.current_price:,.2f}"),
            ("52 周高点", f"${summary.high_52_week:,.2f}" if summary.high_52_week is not None else "N/A"),
            ("距 52 周高点", f"{summary.distance_from_high:.2f}%" if summary.distance_from_high is not None else "N/A"),
            ("52 周低点", f"${summary.low_52_week:,.2f}" if summary.low_52_week is not None else "N/A"),
            ("较 52 周低点", f"{summary.distance_from_low:+.2f}%" if summary.distance_from_low is not None else "N/A"),
        )
    )

    st.subheader("基本面评分明细")
    if fundamentals.score is not None:
        st.progress(fundamentals.score, text=f"{fundamentals.score}/100 — {fundamentals.score_label}")
    for component in fundamentals.components:
        points = "N/A" if component.points is None else f"{component.points:.1f}"
        with st.expander(f"{component.name}: {points}/{component.weight}（覆盖 {component.metrics_found}/{component.metrics_possible}）"):
            st.write(component.explanation)
    st.caption("权重：估值 20、增长 20、盈利能力 20、财务健康 20、现金流 15、股东回报/股息 5。80–100 强；60–79 良好；40–59 一般；20–39 偏弱；0–19 很弱。")

st.divider()
st.header("未来版本")
future_items = (
    ("新闻与催化剂分析", "公司新闻、事件和市场情绪。"),
    ("AI 分析", "综合技术面与基本面的自然语言分析。"),
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
