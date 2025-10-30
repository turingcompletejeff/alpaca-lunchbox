"""
portfolio_analyzer.py

Analyzes portfolio positions against exit and average down rules:
- Exit candidates: RSI >= rsi_exit OR HoldingDays > hold_max_days
- Average down candidates: Unrealized loss <= -10% AND RSI <= 30
- Interactive menu to confirm sell/hold/average down
- Uses centralized database utilities and logging
"""

import os
import pandas as pd
from datetime import date
from dotenv import load_dotenv
import json
from alpaca_trade_api.rest import REST
import logging

# Import our database utilities
from db_utils import (
    db, get_latest_snapshots, insert_trade_log, insert_trade_history,
    update_portfolio_position
)

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------
# CONFIG
# -----------------------------
with open("config/strategy_config.json", "r") as f:
    CFG = json.load(f)

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
BASE_URL = os.getenv("BASE_URL")

RSI_EXIT = CFG["exit"]["rsi_exit"]
HOLD_MAX_DAYS = CFG["exit"]["hold_max_days"]
AVG_DOWN_THRESH = CFG["exit"].get("avg_down_loss_pct", -10)
AVG_DOWN_RSI = CFG["exit"].get("avg_down_rsi", 30)
ALLOC_CAP = CFG["exit"].get("avg_down_max_qty", 200)

# -----------------------------
# Helpers
# -----------------------------
def submit_order(api, symbol: str, side: str, qty: int, price: float):
    """Submit a market order through Alpaca with database logging."""
    try:
        api.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            type="market",
            time_in_force="day"
        )
        logger.info(f"✅ {side.upper()} {qty} shares of {symbol}")
        
        # Log successful submission
        insert_trade_log(symbol, side, qty, "submitted", price)
        insert_trade_history(symbol, side, qty, price, "submitted")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to submit {side.upper()} for {symbol}: {e}")
        return False

def get_portfolio_with_prices():
    """Fetch portfolio merged with latest snapshot prices/RSI."""
    query = """
        SELECT p.symbol, p.qty, p.avg_price, p.entry_date,
               s.rsi, s.price
        FROM portfolio p
        LEFT JOIN snapshots s
        ON p.symbol = s.symbol
        WHERE s.snapshot_date = (
            SELECT MAX(snapshot_date) FROM snapshots
        )
    """
    
    results = db.execute_query(query)
    
    if not results:
        logger.warning("No portfolio data found or no snapshots available")
        return pd.DataFrame()
    
    cols = ["Symbol", "Qty", "AvgPrice", "EntryDate", "RSI", "Close"]
    df = pd.DataFrame(results, columns=cols)

    # Normalize types
    df["AvgPrice"] = df["AvgPrice"].astype(float)
    df["Qty"] = df["Qty"].astype(int)
    df["Close"] = df["Close"].astype(float)
    df["RSI"] = df["RSI"].astype(float)

    # P/L and holding days
    df["UnrealizedPL"] = (df["Close"] - df["AvgPrice"]) * df["Qty"]
    df["UnrealizedPLPct"] = ((df["Close"] - df["AvgPrice"]) / df["AvgPrice"]) * 100
    df["EntryDate"] = pd.to_datetime(df["EntryDate"]).dt.date
    df["HoldingDays"] = df["EntryDate"].apply(lambda d: (date.today() - d).days)
    
    df = df.sort_values(by="UnrealizedPLPct", ascending=False)

    logger.info(f"Loaded {len(df)} portfolio positions with current prices")
    return df

def handle_exit_candidate(api, row, action: str):
    """Handle exit candidate based on user action."""
    symbol = row["Symbol"]
    qty = row["Qty"]
    current_price = float(row["Close"])
    
    if action == "sell":
        success = submit_order(api, symbol, "sell", qty, current_price)
        if success:
            # Remove from portfolio
            success = db.execute_insert(
                "DELETE FROM portfolio WHERE symbol = %s", 
                (symbol,),
                f"portfolio deletion for {symbol}"
            )
            if success:
                logger.info(f"Removed {symbol} from portfolio")
            return True
    
    elif action == "average_down":
        dollars = CFG["entry"]["weighting"]["baseline_dollar_per_trade"]
        qty_to_buy = int(dollars // current_price)
        
        if qty_to_buy > 0:
            try:
                account = api.get_account()
                cash_available = float(account.cash)
                cost = qty_to_buy * current_price

                if cost > cash_available:
                    print(f"⚠️ Not enough cash to average down {symbol} "
                          f"(need ${cost:,.2f}, have ${cash_available:,.2f})")
                    return False
                else:
                    success = submit_order(api, symbol, "buy", qty_to_buy, current_price)
                    if success:
                        # Update portfolio position
                        update_portfolio_position(symbol, qty_to_buy, current_price, "buy")
                        print(f"ℹ️ Averaged down: bought {qty_to_buy} shares at {current_price:.2f}")
                        return True
            except Exception as e:
                logger.error(f"Failed to check account balance: {e}")
                return False
        else:
            print("⚠️ Not enough allocation to average down.")
            return False
    
    return False

# -----------------------------
# Analyzer
# -----------------------------
def analyze_portfolio(api):
    df = get_portfolio_with_prices()
    if df.empty:
        print("⚠️ Portfolio is empty or no snapshots available.")
        return

    today = date.today()
    
    # Exit rules
    df["ExitReason"] = ""
    df.loc[df["RSI"] >= RSI_EXIT, "ExitReason"] += f"RSI ≥ {RSI_EXIT}; "
    df.loc[df["HoldingDays"] > HOLD_MAX_DAYS, "ExitReason"] += f"Held > {HOLD_MAX_DAYS}d; "
    exit_df = df[df["ExitReason"] != ""].copy()
    
    # Average down candidates
    avg_down_df = df[
        (df["UnrealizedPLPct"] <= AVG_DOWN_THRESH) &
        (df["RSI"] <= AVG_DOWN_RSI) &
        (df["Qty"] <= ALLOC_CAP)
    ].copy()
    
    if exit_df.empty and avg_down_df.empty:
        print("✅ No exit or average down candidates today.")
        return

    # Show candidates
    if not exit_df.empty:
        print("\n📉 Exit Candidates:")
        display_cols = ["Symbol", "Qty", "AvgPrice", "Close", "RSI", "HoldingDays", "UnrealizedPL", "UnrealizedPLPct", "ExitReason"]
        print(exit_df[display_cols].to_string(index=False))

    if not avg_down_df.empty:
        print("\n🔄 Average Down Candidates:")
        display_cols = ["Symbol", "Qty", "AvgPrice", "Close", "RSI", "UnrealizedPLPct", "UnrealizedPL"]
        print(avg_down_df[display_cols].to_string(index=False))
    
    # Interactive menu for exit candidates
    actions_taken = 0
    
    for _, row in exit_df.iterrows():
        print(f"\n📊 Exit Candidate: {row['Symbol']}")
        print(f"Qty: {row['Qty']} | AvgPrice: ${row['AvgPrice']:.2f} | Current: ${row['Close']:.2f}")
        print(f"RSI: {row['RSI']:.2f} | HoldingDays: {row['HoldingDays']} | P/L: ${row['UnrealizedPL']:.2f}")
        print(f"Reason: {row['ExitReason']}")

        # Check if it's also an average down candidate
        is_avg_down_candidate = (
            row["UnrealizedPLPct"] <= AVG_DOWN_THRESH and 
            row["RSI"] <= AVG_DOWN_RSI and 
            row["Qty"] <= ALLOC_CAP
        )
        
        if is_avg_down_candidate:
            choice = input("Action: [s]ell / [h]old / [a]verage down / [skip rest]: ").strip().lower()
        else:
            choice = input("Action: [s]ell / [h]old / [skip rest]: ").strip().lower()
        
        if choice == "s":
            if handle_exit_candidate(api, row, "sell"):
                actions_taken += 1
        elif choice == "a" and is_avg_down_candidate:
            if handle_exit_candidate(api, row, "average_down"):
                actions_taken += 1
        elif choice == "skip rest":
            print("Skipping remaining candidates.")
            break
        else:
            print("Holding position.")

    print(f"\n✅ Analysis complete. Took {actions_taken} actions.")
    

# -----------------------------
# Main
# -----------------------------
def main():
    """Main analysis function with error handling."""
    try:
        api = REST(API_KEY, API_SECRET, base_url=BASE_URL)
        print("📊 Starting portfolio analysis...")
        analyze_portfolio(api)
        
    except Exception as e:
        logger.error(f"Portfolio analysis failed: {e}")
        print(f"❌ Analysis failed: {e}")

if __name__ == "__main__":
    main()
