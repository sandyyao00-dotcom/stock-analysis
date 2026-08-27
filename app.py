"""Streamlit interface for the personal stock analysis app."""

import streamlit as st

from stock_analysis.analysis import fetch_stock_data, summarize_stock


st.set_page_config(page_title="Stock Analysis", page_icon="📈", layout="wide")

st.title("Personal Stock Analysis")
st.caption("A simple technical snapshot using public market data. Not financial advice.")

ticker = st.text_input(
    "Stock ticker",
    value="AAPL",
    placeholder="Enter a ticker, for example AAPL",
).strip().upper()

if not ticker:
    st.info("Enter a stock ticker to begin.")
    st.stop()

try:
    with st.spinner(f"Loading market data for {ticker}..."):
        history = fetch_stock_data(ticker)
        summary = summarize_stock(history)
except ValueError as error:
    st.error(str(error))
    st.stop()
except Exception:
    st.error("Market data could not be loaded right now. Check your connection and try again.")
    st.stop()

st.subheader(f"{ticker} technical snapshot")

price_column, change_column, ma20_column, ma50_column, rsi_column, volume_column = st.columns(6)
price_column.metric("Current price", f"${summary.current_price:,.2f}")
change_column.metric(
    "Recent change",
    f"{summary.price_change:+,.2f}",
    f"{summary.price_change_percent:+.2f}%",
)
ma20_column.metric("20-day average", f"${summary.ma20:,.2f}")
ma50_column.metric("50-day average", f"${summary.ma50:,.2f}")
rsi_column.metric("RSI (14-day)", f"{summary.rsi:.1f}")
volume_column.metric("Recent volume", f"{summary.volume:,.0f}")

st.subheader("Price history")
chart_data = history[["Close", "MA20", "MA50"]].rename(
    columns={"Close": "Price", "MA20": "20-day average", "MA50": "50-day average"}
)
st.line_chart(chart_data)

st.subheader("Technical summary")
if summary.rating == "Bullish":
    st.success(f"Bullish — score: {summary.score:+d}")
elif summary.rating == "Bearish":
    st.error(f"Bearish — score: {summary.score:+d}")
else:
    st.info(f"Neutral — score: {summary.score:+d}")

for signal in summary.signals:
    st.write(f"- {signal}")

with st.expander("How the rating works"):
    st.markdown(
        """
Each signal adds **+1**, **0**, or **-1**:

- Current price above/below the 20-day moving average
- 20-day moving average above/below the 50-day moving average
- RSI: 50–70 is positive, 30–50 is negative, and extreme/near-boundary readings are neutral

A total of **+2 or more** is Bullish, **-2 or less** is Bearish, and anything else is Neutral.
        """
    )

st.divider()
st.subheader("Planned analysis modules")
future_columns = st.columns(2)
with future_columns[0]:
    st.markdown("#### Fundamental analysis")
    st.caption("Coming later: financial statements, valuation, growth, and profitability.")
    st.markdown("#### News analysis")
    st.caption("Coming later: recent company news and sentiment.")
    st.markdown("#### AI summary")
    st.caption("Coming later: a plain-language synthesis of the available analysis.")
with future_columns[1]:
    st.markdown("#### Portfolio cost and position analysis")
    st.caption("Coming later: cost basis, position size, gains, and portfolio exposure.")
    st.markdown("#### Support and resistance")
    st.caption("Coming later: potential technical price levels and chart context.")

st.caption("Data is provided by Yahoo Finance through yfinance and may be delayed or incomplete.")
