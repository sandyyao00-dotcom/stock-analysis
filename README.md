# Stock Analysis

A simple local web app for researching stocks and viewing an explainable technical snapshot. The V1 interface is built with Streamlit, while market data comes from Yahoo Finance through the free `yfinance` Python library. No API key is required.

## V1 Features

- Search by ticker symbol, such as `AAPL`
- Current price and latest daily price change
- 20-day and 50-day moving averages
- 14-day Relative Strength Index (RSI)
- Latest trading volume
- Price and moving-average chart
- Transparent Bullish, Neutral, or Bearish technical rating

Market data may be delayed or incomplete. The technical rating is a simple educational signal, not an investment recommendation.

## Project Structure

```text
stock-analysis/
|-- .gitignore
|-- app.py
|-- requirements.txt
|-- stock_analysis/
|   |-- __init__.py
|   `-- analysis.py
`-- README.md
```

## Run Locally on Windows

Python 3.10 or newer is recommended. Verify that the Python launcher is available:

```powershell
py --version
```

If Windows cannot run that command, install Python from [python.org](https://www.python.org/downloads/windows/) and enable the installer option that adds Python to `PATH`, then open a new PowerShell window.

1. Open PowerShell and move into the project folder:

   ```powershell
   cd "C:\Users\Lenovo\Codex Projects\stock-analysis"
   ```

2. Create a virtual environment:

   ```powershell
   py -m venv .venv
   ```

3. Activate it:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

   If PowerShell blocks local activation scripts, run this once in the same window and then activate again:

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   ```

4. Install the dependencies:

   ```powershell
   py -m pip install -r requirements.txt
   ```

5. Start the app:

   ```powershell
   py -m streamlit run app.py
   ```

Streamlit will print a local address, normally `http://localhost:8501`, and usually opens it in your browser automatically. Press `Ctrl+C` in PowerShell to stop the app.

## Technical Rating Rules

The app scores three signals:

- Price above the 20-day average: `+1`; below: `-1`
- 20-day average above the 50-day average: `+1`; below: `-1`
- RSI between 50 and 70: `+1`; RSI between 30 and 50: `-1`; extreme or boundary values: `0`

A score of `+2` or higher is **Bullish**, `-2` or lower is **Bearish**, and any other score is **Neutral**.

## Planned Features

- Fundamental analysis
- News analysis
- AI-generated summaries
- Portfolio cost and position analysis
- Support and resistance levels
- Watchlists and research notes

## Privacy and Secrets

V1 uses public market data only. It does not use brokerage credentials, API keys, or stored secrets.

## Disclaimer

This project is intended for personal research and educational purposes only. It does not provide financial advice.
