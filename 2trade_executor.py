"""
trade_executor.py

Features:
- Reads latest RSI snapshots from database or CSV fallback
- Extreme RSI thresholds (<20 buy, >80 short)
- Checks market status and uses live price if open
- Falls back to previous daily close if market closed
- Interactive terminal menu for trade approval
- Displays estimated cost per trade and available cash
- Logs submitted orders to database instead of CSV
"""

import os
import pandas as pd
import json
from datetime import datetime, timedelta
from alpaca_trade_api.rest import REST, TimeFrame
from dotenv import load_dotenv
import logging

# Import our database utilities
from db_utils import (
    get_latest_snapshots, insert_trade_log, insert_trade_history,
    update_portfolio_position
)

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------
# CONFIG
# -----------------------------
# load strategy config
with open("config/strategy_config.json","r") as f:
    CFG = json.load(f)

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
BASE_URL = os.getenv("BASE_URL")

CSV_FOLDER = "csv"
LOWEST_CSV = os.path.join(CSV_FOLDER, "sp500_rsi_lowest.csv")
HIGHEST_CSV = os.path.join(CSV_FOLDER, "sp500_rsi_highest.csv")

RSI_EXTREME = CFG["entry"]["rsi_thresholds"]["extreme"]
RSI_PRIMARY = CFG["entry"]["rsi_thresholds"]["primary"]
QTY_BASE = CFG["entry"]["weighting"]["baseline_dollar_per_trade"]
EXT_MULT = CFG["entry"]["weighting"]["extreme_multiplier"]
RSI_LOVAL = 26
RSI_HIVAL = 80

# -----------------------------
# HELPER FUNCTIONS
# -----------------------------
def submit_order(api, symbol: str, side: str, qty: int, price: float):
    """Submit order and log to database."""
    timestamp = datetime.now()
    status = ""
    
    try:
        api.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            type="market",
            time_in_force="day"
        )
        status = "submitted"
        logger.info(f"✅ {side.upper()} {qty} shares of {symbol}")
        
        # Log successful submission to both trade_log and trade_history
        insert_trade_log(symbol, side, qty, status, price)
        insert_trade_history(symbol, side, qty, price, status)
        
        return True
        
    except Exception as e:
        status = f"failed: {e}"
        logger.error(f"❌ Failed to submit {side.upper()} for {symbol}: {e}")
        
        return False

def is_market_open(api):
    """Returns True if market is currently open."""
    try:
        clock = api.get_clock()
        return clock.is_open
    except Exception:
        return False

def get_price_or_last_close(api, symbol: str, market_open: bool):
    """Returns price and live flag. If market closed, uses previous daily close."""
    # Try live 1-minute bar if market open
    if market_open:
        try:
            bar = api.get_bars(symbol, TimeFrame.Minute, limit=1).df
            if not bar.empty:
                return bar['close'].iloc[-1], True
        except Exception:
            pass

    # Fallback: last available daily close (shift end date back 1 day)
    today = datetime.today()
    end = today - timedelta(days=1)   # <- end is yesterday
    start = end - timedelta(days=5)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")
    
    try:
        bar = api.get_bars(symbol, TimeFrame.Day, start=start_str, end=end_str).df
        if not bar.empty:
            return bar['close'].iloc[-1], False
    except Exception:
        pass

    return None, False

def interactive_menu(api, df: pd.DataFrame, side: str, market_open: bool, cash_available: float = None):
    """Iterates through candidates and asks for approval."""
    for i, row in df.iterrows():
        price, live = get_price_or_last_close(api, row["Symbol"], market_open)
        if price is None:
            logger.warning(f"No price available for {row['Symbol']}, skipping.")
            continue
        price_type = "Live" if live else "Prev Close"

        print(f"\n{i+1}/{len(df)} - {side.upper()} candidate")
        print(f"Symbol: {row['Symbol']}, RSI: {row['RSI']:.2f}, {price_type} Price: ${price:.2f}")

        # Compute allocation from config
        dollars, qty = calculate_allocation(row["RSI"], price)
        if qty == 0:
            print(f"Skipping {row['Symbol']} (RSI not extreme enough)")
            continue

        cost = price * qty
        print(f"Suggested allocation: ${dollars:.2f} → {qty} shares at ${price:.2f} (Total: ${cost:,.2f})")

        # Cash check
        if side == "buy" and cash_available is not None and cost > cash_available:
            print(f"⚠️ Not enough cash to buy {qty} shares of {row['Symbol']} "
                  f"(need ${cost:,.2f}, have ${cash_available:,.2f}). Skipping.")
            continue

        choice = input("Options: [y]es / [n]o / [s]kip rest: ").strip().lower()
        if choice == "y":
            success = submit_order(api, row["Symbol"], side, qty, float(price))
            if success:
                # Update portfolio position using db_utils
                update_portfolio_position(row["Symbol"], qty, float(price), side)
                if side == "buy" and cash_available is not None:
                    cash_available -= cost
        elif choice == "s":
            print(f"Skipping rest of {side} candidates.")
            break
        else:
            print(f"Skipped {row['Symbol']}")

    return cash_available

def calculate_allocation(rsi_value, price):
    """Calculate dollar allocation and share quantity based on RSI."""
    baseline = CFG["entry"]["weighting"]["baseline_dollar_per_trade"]
    if rsi_value < CFG["entry"]["rsi_thresholds"]["extreme"]:
        dollars = baseline * CFG["entry"]["weighting"]["extreme_multiplier"]
    elif rsi_value < CFG["entry"]["rsi_thresholds"]["primary"]:
        dollars = baseline * CFG["entry"]["weighting"]["primary_multiplier"]
    else:
        return 0, 0

    qty = int(dollars // price)
    return dollars, qty

def load_rsi_data():
    """Load RSI data from database first, fall back to CSV if needed."""
    if CFG["universe"].get("source_mode") == "db":
        snapshot_df = get_latest_snapshots()
        if not snapshot_df.empty:
            logger.info(f"Loaded {len(snapshot_df)} symbols from database snapshots")
            return snapshot_df
        else:
            logger.warning("No snapshot data found in database, falling back to CSV")
    
    # Fallback to CSV files
    try:
        if os.path.exists(LOWEST_CSV) and os.path.exists(HIGHEST_CSV):
            oversold_df = pd.read_csv(LOWEST_CSV)
            overbought_df = pd.read_csv(HIGHEST_CSV)
            
            # Combine and return as single dataframe
            combined_df = pd.concat([oversold_df, overbought_df]).drop_duplicates().reset_index(drop=True)
            logger.info(f"Loaded {len(combined_df)} symbols from CSV files")
            return combined_df
        else:
            logger.error("No CSV files found and no database snapshots available")
            return pd.DataFrame()
    except Exception as e:
        logger.error(f"Failed to load CSV data: {e}")
        return pd.DataFrame()

# -----------------------------
# MAIN FUNCTION
# -----------------------------
def main():
    api = REST(API_KEY, API_SECRET, base_url=BASE_URL)

    # Load RSI data from database or CSV
    snapshot_df = load_rsi_data()
    if snapshot_df.empty:
        print("❌ No snapshot data found. Run rsi-compare.py first.")
        return

    # Filter for extreme RSI values
    oversold_df = snapshot_df[snapshot_df["RSI"] < RSI_LOVAL].reset_index(drop=True).sort_values(by="RSI", ascending=True)
    overbought_df = snapshot_df[snapshot_df["RSI"] > RSI_HIVAL].reset_index(drop=True)

    if oversold_df.empty and overbought_df.empty:
        print("📊 No extreme RSI candidates found today.")
        return

    # Market status
    market_open = is_market_open(api)
    if not market_open:
        print("⚠️ Market is closed. Using previous daily close for all tickers.\n")

    # Show available cash
    try:
        account = api.get_account()
        cash_available = float(account.cash)
        print(f"💰 Available cash: ${cash_available:,.2f}")
    except Exception as e:
        logger.error(f"Failed to get account info: {e}")
        cash_available = None

    # Preview all candidates first
    if not oversold_df.empty:
        print("\n📉 Oversold candidates (preview)")
        print(oversold_df[['Symbol', 'RSI', 'Close']].to_string(index=False))
    if not overbought_df.empty:
        print("\n📈 Overbought candidates (preview)")
        print(overbought_df[['Symbol', 'RSI', 'Close']].to_string(index=False))

    proceed = input("\nEnter interactive menu to approve trades? (y/n): ").strip().lower()
    if proceed != "y":
        print("Aborting all trades.")
        return

    # Interactive menu
    if not oversold_df.empty:
        print("\n📉 Oversold (BUY) candidates")
        cash_available = interactive_menu(api, oversold_df, "buy", market_open, cash_available)

    if not overbought_df.empty:
        print("\n📈 Overbought (SHORT) candidates")
        interactive_menu(api, overbought_df, "sell", market_open, cash_available)

    print("\n✅ All approved trades processed. Check database trade_log for details.")

# -----------------------------
# ENTRY POINT
# -----------------------------
if __name__ == "__main__":
    main()
