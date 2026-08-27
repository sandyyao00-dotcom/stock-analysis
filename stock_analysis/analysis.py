"""Market-data retrieval and transparent technical-analysis calculations."""

from dataclasses import dataclass

import pandas as pd
import streamlit as st
import yfinance as yf


@dataclass(frozen=True)
class ScoreComponent:
    """One visible part of the 100-point technical score."""

    name: str
    points: int
    maximum: int
    explanation: str


@dataclass(frozen=True)
class StockSummary:
    """Latest calculated values displayed by the app."""

    current_price: float
    price_change: float
    price_change_percent: float
    ma20: float
    ma50: float
    ma200: float | None
    ma_alignment: str
    rsi: float
    rsi_status: str
    macd: float
    macd_signal: float
    macd_histogram: float
    momentum: str
    volume: float
    average_volume: float
    volume_status: str
    volume_confirmation: str
    support: float
    resistance: float
    trend: str
    price_structure: str
    recent_high: float
    recent_low: float
    high_52_week: float | None
    low_52_week: float | None
    distance_from_high: float | None
    distance_from_low: float | None
    technical_score: int
    score_label: str
    score_components: tuple[ScoreComponent, ...]
    main_bullish_factor: str
    main_risk_factor: str


@st.cache_data(ttl=900, show_spinner=False)
def fetch_stock_data(ticker: str) -> pd.DataFrame:
    """Download daily public market data and add technical indicators."""
    data = yf.download(
        ticker,
        period="18mo",
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    required = {"Open", "High", "Low", "Close", "Volume"}
    if data.empty or not required.issubset(data.columns):
        raise ValueError(f"No usable market data was found for '{ticker}'. Check the ticker and try again.")

    data = data.copy().dropna(subset=list(required)).sort_index()
    if len(data) < 51:
        raise ValueError(f"Not enough price history was found for '{ticker}' to calculate the indicators.")

    data["MA20"] = data["Close"].rolling(20).mean()
    data["MA50"] = data["Close"].rolling(50).mean()
    data["MA200"] = data["Close"].rolling(200).mean()
    data["RSI"] = calculate_rsi(data["Close"])
    ema12 = data["Close"].ewm(span=12, adjust=False).mean()
    ema26 = data["Close"].ewm(span=26, adjust=False).mean()
    data["MACD"] = ema12 - ema26
    data["MACD_SIGNAL"] = data["MACD"].ewm(span=9, adjust=False).mean()
    data["MACD_HISTOGRAM"] = data["MACD"] - data["MACD_SIGNAL"]
    return data


def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI using Wilder's exponentially smoothed averages."""
    changes = prices.diff()
    gains = changes.clip(lower=0)
    losses = -changes.clip(upper=0)
    average_gain = gains.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    average_loss = losses.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    relative_strength = average_gain / average_loss
    rsi = 100 - (100 / (1 + relative_strength))
    rsi = rsi.mask((average_loss == 0) & (average_gain > 0), 100)
    return rsi.mask((average_loss == 0) & (average_gain == 0), 50)


def estimate_support_resistance(data: pd.DataFrame, lookback: int = 90) -> tuple[float, float]:
    """Find nearby levels from recent five-session pivot lows and highs."""
    recent = data.tail(lookback)
    current_price = float(recent["Close"].iloc[-1])
    pivot_lows = recent.loc[recent["Low"] == recent["Low"].rolling(5, center=True).min(), "Low"]
    pivot_highs = recent.loc[recent["High"] == recent["High"].rolling(5, center=True).max(), "High"]
    supports = pivot_lows[pivot_lows < current_price]
    resistances = pivot_highs[pivot_highs > current_price]
    support = float(supports.max()) if not supports.empty else float(recent["Low"].min())
    resistance = float(resistances.min()) if not resistances.empty else float(recent["High"].max())
    return support, resistance


def _score_label(score: int) -> str:
    if score >= 80:
        return "Strong Bullish"
    if score >= 60:
        return "Bullish"
    if score >= 40:
        return "Neutral"
    if score >= 20:
        return "Bearish"
    return "Strong Bearish"


def _technical_score(
    data: pd.DataFrame, rsi: float, structure: str, volume_confirmation: str
) -> tuple[int, tuple[ScoreComponent, ...]]:
    """Calculate six visible components whose maximum points total 100."""
    latest = data.iloc[-1]
    price = float(latest["Close"])
    ma20 = float(latest["MA20"])
    ma50 = float(latest["MA50"])
    ma200 = None if pd.isna(latest["MA200"]) else float(latest["MA200"])

    ma50_rising = ma50 > float(data["MA50"].iloc[-6])
    trend_points = (10 if price > ma50 else 0) + (10 if ma50_rising else 0)
    trend_text = f"Price is {'above' if price > ma50 else 'below'} MA50; MA50 is {'rising' if ma50_rising else 'falling'} over five sessions."

    ma_points = (5 if price > ma20 else 0) + (7 if ma20 > ma50 else 0)
    if ma200 is None:
        ma_points += 4
        long_term_text = "MA200 is unavailable, so its 8-point signal receives 4 neutral points."
    else:
        ma_points += 8 if ma50 > ma200 else 0
        long_term_text = f"MA50 is {'above' if ma50 > ma200 else 'below'} MA200."
    ma_text = f"Price is {'above' if price > ma20 else 'below'} MA20; MA20 is {'above' if ma20 > ma50 else 'below'} MA50; {long_term_text}"

    if 50 <= rsi <= 70:
        rsi_points = 15
    elif 40 <= rsi < 50:
        rsi_points = 8
    elif rsi > 70:
        rsi_points = 10
    elif 30 <= rsi < 40:
        rsi_points = 5
    else:
        rsi_points = 3
    rsi_text = f"RSI is {rsi:.1f}; extremes are cautions, not automatic trades."

    above_signal = float(latest["MACD"]) > float(latest["MACD_SIGNAL"])
    above_zero = float(latest["MACD"]) > 0
    macd_points = (10 if above_signal else 0) + (10 if above_zero else 0)
    macd_text = f"MACD is {'above' if above_signal else 'below'} its signal line and {'above' if above_zero else 'below'} zero."

    structure_points = 15 if structure == "Higher highs and higher lows" else 0 if structure == "Lower highs and lower lows" else 8
    structure_text = f"The latest two 20-session ranges show {structure.lower()}."

    if volume_confirmation.startswith("Bullish"):
        volume_points = 10
    elif volume_confirmation.startswith("Bearish"):
        volume_points = 0
    else:
        volume_points = 5

    components = (
        ScoreComponent("Trend", trend_points, 20, trend_text),
        ScoreComponent("Moving averages", ma_points, 20, ma_text),
        ScoreComponent("RSI", rsi_points, 15, rsi_text),
        ScoreComponent("MACD", macd_points, 20, macd_text),
        ScoreComponent("Price structure", structure_points, 15, structure_text),
        ScoreComponent("Volume", volume_points, 10, volume_confirmation),
    )
    return sum(item.points for item in components), components


def summarize_stock(data: pd.DataFrame) -> StockSummary:
    """Build all V2 metrics and explanations from an indicator-enriched frame."""
    latest = data.iloc[-1]
    previous = data.iloc[-2]
    price = float(latest["Close"])
    previous_price = float(previous["Close"])
    ma20 = float(latest["MA20"])
    ma50 = float(latest["MA50"])
    ma200 = None if pd.isna(latest["MA200"]) else float(latest["MA200"])
    rsi = float(latest["RSI"])

    if ma200 is not None and ma20 > ma50 > ma200:
        ma_alignment = "Bullish alignment (MA20 > MA50 > MA200)"
    elif ma200 is not None and ma20 < ma50 < ma200:
        ma_alignment = "Bearish alignment (MA20 < MA50 < MA200)"
    elif ma20 > ma50:
        ma_alignment = "Short-term bullish alignment (MA20 > MA50)"
    elif ma20 < ma50:
        ma_alignment = "Short-term bearish alignment (MA20 < MA50)"
    else:
        ma_alignment = "Mixed moving-average alignment"

    rsi_status = "Overbought" if rsi > 70 else "Oversold" if rsi < 30 else "Neutral"
    macd = float(latest["MACD"])
    macd_signal = float(latest["MACD_SIGNAL"])
    macd_histogram = float(latest["MACD_HISTOGRAM"])
    if macd > macd_signal and macd_histogram > 0:
        momentum = "Bullish MACD momentum"
    elif macd < macd_signal and macd_histogram < 0:
        momentum = "Bearish MACD momentum"
    else:
        momentum = "Neutral/mixed MACD momentum"

    recent_volume = float(latest["Volume"])
    average_volume = float(data["Volume"].tail(20).mean())
    volume_ratio = recent_volume / average_volume if average_volume else 1.0
    volume_status = "Unusually high" if volume_ratio >= 1.5 else "Unusually low" if volume_ratio <= 0.7 else "Near average"
    five_day_move = price / float(data["Close"].iloc[-6]) - 1
    five_day_volume = float(data["Volume"].tail(5).mean())
    if five_day_volume >= average_volume and five_day_move > 0:
        volume_confirmation = "Bullish confirmation: price rose on above-average recent volume."
    elif five_day_volume >= average_volume and five_day_move < 0:
        volume_confirmation = "Bearish confirmation: price fell on above-average recent volume."
    else:
        volume_confirmation = "No strong volume confirmation: recent average volume did not exceed the 20-day average."

    current_range = data.tail(20)
    prior_range = data.iloc[-40:-20]
    recent_high = float(current_range["High"].max())
    recent_low = float(current_range["Low"].min())
    higher_high = recent_high > float(prior_range["High"].max())
    higher_low = recent_low > float(prior_range["Low"].min())
    if higher_high and higher_low:
        structure = "Higher highs and higher lows"
    elif not higher_high and not higher_low:
        structure = "Lower highs and lower lows"
    else:
        structure = "Mixed highs and lows"

    ma50_rising = ma50 > float(data["MA50"].iloc[-6])
    trend = "Uptrend" if price > ma50 and ma50_rising else "Downtrend" if price < ma50 and not ma50_rising else "Sideways/mixed"
    support, resistance = estimate_support_resistance(data)

    year_data = data.tail(252)
    high_52 = float(year_data["High"].max()) if len(year_data) >= 200 else None
    low_52 = float(year_data["Low"].min()) if len(year_data) >= 200 else None
    distance_high = ((price / high_52) - 1) * 100 if high_52 else None
    distance_low = ((price / low_52) - 1) * 100 if low_52 else None

    score, components = _technical_score(data, rsi, structure, volume_confirmation)
    strongest = max(components, key=lambda item: item.points / item.maximum)
    weakest = min(components, key=lambda item: item.points / item.maximum)

    return StockSummary(
        current_price=price,
        price_change=price - previous_price,
        price_change_percent=((price / previous_price) - 1) * 100 if previous_price else 0.0,
        ma20=ma20,
        ma50=ma50,
        ma200=ma200,
        ma_alignment=ma_alignment,
        rsi=rsi,
        rsi_status=rsi_status,
        macd=macd,
        macd_signal=macd_signal,
        macd_histogram=macd_histogram,
        momentum=momentum,
        volume=recent_volume,
        average_volume=average_volume,
        volume_status=volume_status,
        volume_confirmation=volume_confirmation,
        support=support,
        resistance=resistance,
        trend=trend,
        price_structure=structure,
        recent_high=recent_high,
        recent_low=recent_low,
        high_52_week=high_52,
        low_52_week=low_52,
        distance_from_high=distance_high,
        distance_from_low=distance_low,
        technical_score=score,
        score_label=_score_label(score),
        score_components=components,
        main_bullish_factor=f"{strongest.name}: {strongest.explanation}",
        main_risk_factor=f"{weakest.name}: {weakest.explanation}",
    )
