"""Market-data retrieval and beginner-friendly technical calculations."""

from dataclasses import dataclass

import pandas as pd
import streamlit as st
import yfinance as yf


@dataclass(frozen=True)
class StockSummary:
    """The latest calculated values displayed by the app."""

    current_price: float
    price_change: float
    price_change_percent: float
    ma20: float
    ma50: float
    rsi: float
    volume: float
    rating: str
    score: int
    signals: tuple[str, ...]


@st.cache_data(ttl=900, show_spinner=False)
def fetch_stock_data(ticker: str) -> pd.DataFrame:
    """Download six months of daily data and add technical indicators."""
    data = yf.download(
        ticker,
        period="6mo",
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
    )

    # Some yfinance versions return a two-level column index for one ticker.
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    required_columns = {"Close", "Volume"}
    if data.empty or not required_columns.issubset(data.columns):
        raise ValueError(f"No usable market data was found for '{ticker}'. Check the ticker and try again.")

    data = data.copy().dropna(subset=["Close", "Volume"])
    if len(data) < 51:
        raise ValueError(f"Not enough price history was found for '{ticker}' to calculate the indicators.")

    data["MA20"] = data["Close"].rolling(window=20).mean()
    data["MA50"] = data["Close"].rolling(window=50).mean()
    data["RSI"] = calculate_rsi(data["Close"])
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

    # Handle uninterrupted rising or flat periods without displaying infinity/NaN.
    rsi = rsi.mask((average_loss == 0) & (average_gain > 0), 100)
    return rsi.mask((average_loss == 0) & (average_gain == 0), 50)


def summarize_stock(data: pd.DataFrame) -> StockSummary:
    """Build the latest metrics and an explainable three-signal rating."""
    latest = data.iloc[-1]
    previous = data.iloc[-2]

    current_price = float(latest["Close"])
    previous_price = float(previous["Close"])
    price_change = current_price - previous_price
    price_change_percent = (price_change / previous_price) * 100 if previous_price else 0.0
    ma20 = float(latest["MA20"])
    ma50 = float(latest["MA50"])
    rsi = float(latest["RSI"])

    score = 0
    signals: list[str] = []

    if current_price > ma20:
        score += 1
        signals.append("Price is above the 20-day moving average (+1).")
    elif current_price < ma20:
        score -= 1
        signals.append("Price is below the 20-day moving average (-1).")
    else:
        signals.append("Price is at the 20-day moving average (0).")

    if ma20 > ma50:
        score += 1
        signals.append("The 20-day moving average is above the 50-day average (+1).")
    elif ma20 < ma50:
        score -= 1
        signals.append("The 20-day moving average is below the 50-day average (-1).")
    else:
        signals.append("The moving averages are equal (0).")

    if 50 < rsi < 70:
        score += 1
        signals.append("RSI is between 50 and 70, showing positive momentum (+1).")
    elif 30 < rsi < 50:
        score -= 1
        signals.append("RSI is between 30 and 50, showing negative momentum (-1).")
    elif rsi >= 70:
        signals.append("RSI is 70 or higher, an overbought caution signal (0).")
    elif rsi <= 30:
        signals.append("RSI is 30 or lower, an oversold caution signal (0).")
    else:
        signals.append("RSI is on a neutral boundary (0).")

    rating = "Bullish" if score >= 2 else "Bearish" if score <= -2 else "Neutral"
    return StockSummary(
        current_price=current_price,
        price_change=price_change,
        price_change_percent=price_change_percent,
        ma20=ma20,
        ma50=ma50,
        rsi=rsi,
        volume=float(latest["Volume"]),
        rating=rating,
        score=score,
        signals=tuple(signals),
    )
