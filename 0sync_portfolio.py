"""
sync_portfolio.py

Reconcile local Postgres portfolio table with Alpaca positions.
- Connects to Alpaca API and Postgres using db_utils.
- Updates qty and avg_price for existing positions.
- Inserts new positions if missing.
- Deletes local positions no longer held in Alpaca.
- Uses centralized error handling and logging.
"""

import os
from alpaca_trade_api.rest import REST
from dotenv import load_dotenv
import logging

# Import our database utilities
from db_utils import db

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------
# CONFIG
# -----------------------------
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
BASE_URL = os.getenv("BASE_URL")

api = REST(API_KEY, API_SECRET, base_url=BASE_URL)

# -----------------------------
# FUNCTIONS
# -----------------------------
def fetch_alpaca_positions():
    """Return Alpaca positions as dict {symbol: (qty, avg_price)}"""
    positions = {}
    try:
        for pos in api.list_positions():
            symbol = pos.symbol
            qty = int(float(pos.qty))  # qty is string decimal
            avg_price = float(pos.avg_entry_price)
            positions[symbol] = (qty, avg_price)
        logger.info(f"Fetched {len(positions)} positions from Alpaca")
    except Exception as e:
        logger.error(f"Failed to fetch Alpaca positions: {e}")
    
    return positions

def fetch_local_portfolio():
    """Return local portfolio as dict {symbol: (qty, avg_price)}"""
    query = "SELECT symbol, qty, avg_price FROM portfolio"
    results = db.execute_query(query)
    
    portfolio = {r[0]: (r[1], float(r[2])) for r in results}
    logger.info(f"Fetched {len(portfolio)} positions from local database")
    return portfolio

def reconcile_portfolio():
    """Reconcile local portfolio with Alpaca positions."""
    alpaca_positions = fetch_alpaca_positions()
    local_positions = fetch_local_portfolio()
    
    changes_made = 0
    
    try:
        # Update or insert positions from Alpaca
        for symbol, (qty, avg_price) in alpaca_positions.items():
            if symbol in local_positions:
                local_qty, local_avg = local_positions[symbol]
                if local_qty != qty or abs(local_avg - avg_price) > 1e-6:
                    logger.info(f"🔄 Updating {symbol}: {local_qty}@{local_avg:.4f} → {qty}@{avg_price:.4f}")
                    
                    success = db.execute_insert(
                        """
                        UPDATE portfolio
                        SET qty = %s, avg_price = %s
                        WHERE symbol = %s
                        """,
                        (qty, avg_price, symbol),
                        f"portfolio update for {symbol}"
                    )
                    
                    if success:
                        changes_made += 1
            else:
                logger.info(f"➕ Inserting new position {symbol}: {qty}@{avg_price:.4f}")
                
                success = db.execute_insert(
                    """
                    INSERT INTO portfolio (symbol, qty, avg_price)
                    VALUES (%s, %s, %s)
                    """,
                    (symbol, qty, avg_price),
                    f"new position for {symbol}"
                )
                
                if success:
                    changes_made += 1

        # Remove local positions that no longer exist in Alpaca
        for symbol in set(local_positions.keys()) - set(alpaca_positions.keys()):
            local_qty, local_avg = local_positions[symbol]
            logger.info(f"🗑 Removing local position {symbol} (no longer in Alpaca)")
            
            success = db.execute_insert(
                "DELETE FROM portfolio WHERE symbol = %s",
                (symbol,),
                f"portfolio deletion for {symbol}"
            )
            
            if success:
                changes_made += 1

        logger.info(f"✅ Portfolio sync complete. Made {changes_made} changes.")
        
        
    except Exception as e:
        logger.error(f"Portfolio sync failed: {e}")
        raise

def get_portfolio_summary():
    """Display current portfolio summary."""
    query = """
        SELECT symbol, qty, avg_price, entry_date,
               (qty * avg_price) as position_value
        FROM portfolio 
        ORDER BY position_value DESC
    """
    
    results = db.execute_query(query)
    
    if not results:
        print("📊 Portfolio is empty")
        return
    
    print("\n📊 Current Portfolio Summary:")
    print("-" * 70)
    print(f"{'Symbol':<8} {'Qty':<8} {'Avg Price':<12} {'Value':<12} {'Entry Date':<12}")
    print("-" * 70)
    
    total_value = 0
    for symbol, qty, avg_price, entry_date, position_value in results:
        print(f"{symbol:<8} {qty:<8} ${avg_price:<11.4f} ${position_value:<11.2f} {entry_date}")
        total_value += position_value
    
    print("-" * 70)
    print(f"{'Total Portfolio Value:':<45} ${total_value:,.2f}")
    print()

# -----------------------------
# MAIN
# -----------------------------
def main():
    """Main sync function with error handling."""
    try:
        print("🔄 Starting portfolio sync with Alpaca...")
        reconcile_portfolio()
        get_portfolio_summary()
        
    except Exception as e:
        logger.error(f"Portfolio sync failed: {e}")
        print(f"❌ Sync failed: {e}")
        
if __name__ == "__main__":
    main()
