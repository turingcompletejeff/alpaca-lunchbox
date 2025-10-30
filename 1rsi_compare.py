"""
rsi-compare.py

Fetches daily bars from Polygon (bulk grouped bars).
- Default run: fetches yesterday's grouped bars (1 request).
- Backfill mode (--backfill): fetches past 60 days (60 requests, sleeps between).
- Stores ALL tickers in daily_prices.
- Filters to S&P500 tickers for RSI snapshots, CSVs, and plots.
- NOW: Checks existing data to avoid duplicate fetches and uses standardized column names.
"""

import os
import time
import json
import pandas as pd
import exchange_calendars as ecals
import matplotlib.pyplot as plt
from datetime import date, timedelta
from dotenv import load_dotenv
from polygon import RESTClient
from tqdm import tqdm
import argparse
import logging

# Import our database utilities
from db_utils import (
    insert_daily_prices, insert_snapshots, get_existing_daily_prices_dates,
    get_existing_snapshot_dates
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------
# Load config
# -----------------------------
load_dotenv()

with open("config/strategy_config.json", "r") as f:
    CFG = json.load(f)

POLYGON_KEY = os.getenv("POLYGON_KEY")
RATE_LIMIT = int(os.getenv("RATE_LIMIT_SECONDS", "21"))  # default 21s between requests
client = RESTClient(api_key=POLYGON_KEY)

# S&P500 ticker set
sp500_tickers = set(pd.read_csv(CFG["universe"]["source"])["Symbol"].tolist())

# RSI period (days) to calc RSI
RSI_PERIOD = CFG["calculator"]["rsi-period"]

# -----------------------------
# Helpers
# -----------------------------
def rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Calculate RSI for a price series."""
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = -delta.clip(upper=0).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def fetch_grouped_bars(fetch_date: date) -> pd.DataFrame:
    """Fetch grouped daily bars for a given date."""
    try:
        bars = client.get_grouped_daily_aggs(date=fetch_date.strftime("%Y-%m-%d"))
        if not bars:
            return pd.DataFrame()

        df = pd.DataFrame([{
            "symbol": b.ticker,
            "date": pd.to_datetime(b.timestamp, unit="ms").date(),
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume
        } for b in bars])

        return df
    except Exception as e:
        logger.error(f"Failed to fetch grouped bars for {fetch_date}: {e}")
        return pd.DataFrame()

def calculate_rsi_for_symbols(symbols: set, period: int = RSI_PERIOD) -> pd.DataFrame:
    """Calculate RSI for given symbols using existing daily price data."""
    from db_utils import db

    # Get last N+10 days of data for RSI calculation
    end_date = date.today()
    start_date = end_date - timedelta(days=period + 10)  # buffer for weekends/holidays

    query = """
        SELECT symbol, trade_date, close
        FROM daily_prices
        WHERE symbol = ANY(%s) 
        AND trade_date >= %s 
        AND trade_date <= %s
        ORDER BY symbol, trade_date
    """

    results = db.execute_query(query, (list(symbols), start_date, end_date))
    if not results:
        logger.warning("No daily price data found for RSI calculation")
        return pd.DataFrame()

    df = pd.DataFrame(results, columns=['symbol', 'trade_date', 'close'])
    df['trade_date'] = pd.to_datetime(df['trade_date'])

    rsi_results = []
    for symbol in symbols:
        symbol_data = df[df['symbol'] == symbol].sort_values('trade_date')
        if len(symbol_data) >= period:
            symbol_data['rsi'] = rsi(symbol_data['close'], period)
            latest = symbol_data.iloc[-1]
            rsi_results.append({
                'Symbol': symbol,
                'RSI': latest['rsi'],
                'Close': latest['close']
            })

    return pd.DataFrame(rsi_results)

# -----------------------------
# Main
# -----------------------------
def main(backfill=False):
    snapshot_date = date.today()

    # Determine date range
    if backfill:
        backfill_days = 60
        start_date = snapshot_date - timedelta(days=backfill_days)
        end_date = snapshot_date - timedelta(days=1)
    else:
        start_date = snapshot_date - timedelta(days=1)
        end_date = snapshot_date - timedelta(days=1)

    # Get NYSE trading sessions
    nyse = ecals.get_calendar("XNYS")
    trading_days = nyse.sessions_in_range(pd.Timestamp(start_date), pd.Timestamp(end_date))
    trading_days = [d.date() for d in trading_days]

    # Get existing data once
    existing_daily_dates = set(get_existing_daily_prices_dates())
    existing_snapshot_dates = set(get_existing_snapshot_dates())

    # Determine which trading days still need fetching
    dates_to_fetch = []
    for d in trading_days:
        if backfill:
            if d not in existing_daily_dates:
                dates_to_fetch.append(d)
        else:
            yesterday = snapshot_date - timedelta(days=1)
            if snapshot_date not in existing_snapshot_dates and d == snapshot_date:
                dates_to_fetch.append(d)
            elif yesterday not in existing_daily_dates and d == yesterday:
                dates_to_fetch.append(d)

    if not dates_to_fetch:
        logger.info("📊 All data already exists, proceeding to RSI calculation...")
    else:
        logger.info(f"📡 Fetching {len(dates_to_fetch)} trading days...")

        # Fetch grouped bars with retry/backoff
        for d in tqdm(dates_to_fetch, desc="Fetching days", unit="day"):
            max_retries = 5
            delay = 1  # start with 1 second
            for attempt in range(max_retries):
                try:
                    df = fetch_grouped_bars(d)
                    if not df.empty:
                        success_count = insert_daily_prices(df)
                        logger.info(f"Inserted {success_count} daily price records for {d}")
                        break
                    else:
                        if attempt < max_retries - 1:
                            logger.warning(f"No bars for {d}. Retrying in {delay}s...")
                            time.sleep(delay)
                            delay *= 2
                        else:
                            logger.error(f"Failed to fetch bars for {d} after {max_retries} attempts.")
                except Exception as e:
                    logger.error(f"Failed to fetch {d}: {e}")

            if backfill:
                time.sleep(RATE_LIMIT)

    # -----------------------------
    # Calculate RSI and save results
    # -----------------------------
    logger.info("📊 Calculating RSI from daily price data...")
    rsi_df = calculate_rsi_for_symbols(sp500_tickers)

    if rsi_df.empty:
        logger.warning("⚠️ No RSI values calculated. Exiting.")
        return

    # Sort lowest/highest
    lowest = rsi_df.sort_values("RSI").head(10)
    highest = rsi_df.sort_values("RSI").tail(10)

    # Save CSVs
    os.makedirs("csv", exist_ok=True)
    lowest.to_csv(f"csv/{snapshot_date}-sp500_rsi_lowest.csv", index=False)
    highest.to_csv(f"csv/{snapshot_date}-sp500_rsi_highest.csv", index=False)
    rsi_df.to_csv(f"csv/{snapshot_date}-sp500_rsi_snapshot.csv", index=False)

    # Insert snapshot into DB
    success_count = insert_snapshots(rsi_df, snapshot_date)
    logger.info(f"📊 Saved {success_count} snapshot rows to DB for {snapshot_date}")

    # Plot histogram
    plt.figure(figsize=(10, 6))
    plt.hist(rsi_df["RSI"].dropna(), bins=30, color="skyblue", edgecolor="black")
    plt.axvline(30, color="green", linestyle="--")
    plt.axvline(70, color="red", linestyle="--")
    plt.title(f"S&P500 RSI Distribution – {snapshot_date}")
    plt.xlabel("RSI")
    plt.ylabel("Frequency")
    os.makedirs("plots", exist_ok=True)
    plt.savefig(f"plots/{snapshot_date}-sp500_rsi_hist.png")
    plt.close()

    logger.info("✅ Done.")

# -----------------------------
# Entry
# -----------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true", help="Fetch last 60 days (slow, rate-limited).")
    args = parser.parse_args()

    main(backfill=args.backfill)
