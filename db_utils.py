"""
db_utils.py

Shared database utilities for the alpaca-shirt trading system.
Provides standardized database connection, error handling, and common operations.
"""

import os
import logging
import psycopg2
from datetime import datetime, date
from typing import Optional, List, Tuple, Dict, Any
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DatabaseManager:
    """Manages database connections and common operations with error handling."""
    
    def __init__(self):
        self.conn = None
        self.connect()
    
    def connect(self):
        """Establish database connection."""
        try:
            self.conn = psycopg2.connect(
                dbname=os.getenv("PGDATABASE"),
                user=os.getenv("PGUSER"),
                password=os.getenv("PGPASSWORD"),
                host=os.getenv("PGHOST", "localhost"),
                port=os.getenv("PGPORT", "5432")
            )
            self.conn.autocommit = True
            logger.info("Database connection established")
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise
    
    def execute_query(self, query: str, params: Optional[tuple] = None) -> List[tuple]:
        """Execute a SELECT query and return results."""
        try:
            with self.conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchall()
        except Exception as e:
            logger.error(f"Query execution failed: {query[:100]}... Error: {e}")
            return []
    
    def execute_insert(self, query: str, params: tuple, description: str = "") -> bool:
        """Execute an INSERT/UPDATE/DELETE query with error handling."""
        try:
            with self.conn.cursor() as cur:
                cur.execute(query, params)
                logger.debug(f"Successfully executed: {description}")
                return True
        except Exception as e:
            logger.error(f"Insert failed ({description}): {e}")
            return False
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")

# Global database manager instance
db = DatabaseManager()

def connect_db():
    """Return database connection (legacy compatibility)."""
    return db.conn

def insert_trade_log(symbol: str, side: str, qty: int, status: str, 
                    price: Optional[float] = None, notes: Optional[str] = None) -> bool:
    """Insert trade log entry into database."""
    query = """
        INSERT INTO trade_log (symbol, side, qty, status, price, notes)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    params = (symbol, side, qty, status, price, notes)
    return db.execute_insert(query, params, f"trade log for {symbol}")

def insert_trade_history(symbol: str, side: str, qty: int, price: float,
                        order_status: str = "submitted", trade_date: Optional[date] = None) -> bool:
    """Insert trade into trade history table."""
    if trade_date is None:
        trade_date = date.today()
    
    query = """
        INSERT INTO trade_history (symbol, side, qty, price, order_status, trade_date)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    params = (symbol, side, qty, float(price), order_status, trade_date)
    return db.execute_insert(query, params, f"trade history for {symbol}")

def insert_daily_prices(df: pd.DataFrame) -> int:
    """Insert daily prices from DataFrame with error handling."""
    success_count = 0
    
    for _, row in df.iterrows():
        query = """
            INSERT INTO daily_prices (symbol, trade_date, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (trade_date, symbol) DO NOTHING
        """
        params = (
            row["symbol"],
            row["date"],
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            int(row["volume"])
        )
        
        if db.execute_insert(query, params, f"daily price for {row['symbol']} {row['date']}"):
            success_count += 1
    
    logger.info(f"Successfully inserted {success_count}/{len(df)} daily price records")
    return success_count

def insert_snapshots(df: pd.DataFrame, snapshot_date: date) -> int:
    """Insert RSI snapshots from DataFrame with error handling."""
    success_count = 0
    
    for _, row in df.iterrows():
        query = """
            INSERT INTO snapshots (snapshot_date, symbol, rsi, price)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (snapshot_date, symbol) DO UPDATE SET
            rsi = EXCLUDED.rsi, price = EXCLUDED.price
        """
        params = (
            snapshot_date,
            row["Symbol"],
            float(row["RSI"]) if not pd.isna(row["RSI"]) else None,
            float(row["Close"]) if not pd.isna(row["Close"]) else None
        )
        
        if db.execute_insert(query, params, f"snapshot for {row['Symbol']} {snapshot_date}"):
            success_count += 1
    
    logger.info(f"Successfully inserted {success_count}/{len(df)} snapshot records")
    return success_count

def update_portfolio_position(symbol: str, qty: int, price: float, side: str = "buy") -> bool:
    """Update portfolio position after trade execution."""
    try:
        with db.conn.cursor() as cur:
            # Check if symbol already exists
            cur.execute("SELECT qty, avg_price FROM portfolio WHERE symbol = %s", (symbol,))
            row = cur.fetchone()

            if row:
                old_qty, old_price = row
                old_qty = int(old_qty)
                old_price = float(old_price)  # <- cast to float
                
                if side == "buy":
                    new_qty = old_qty + qty
                    new_avg_price = ((old_qty * old_price) + (qty * price)) / new_qty
                    cur.execute("""
                        UPDATE portfolio
                        SET qty = %s, avg_price = %s
                        WHERE symbol = %s
                    """, (new_qty, float(new_avg_price), symbol))
                elif side == "sell":
                    new_qty = old_qty - qty
                    if new_qty <= 0:
                        cur.execute("DELETE FROM portfolio WHERE symbol = %s", (symbol,))
                    else:
                        cur.execute("""
                            UPDATE portfolio
                            SET qty = %s
                            WHERE symbol = %s
                        """, (new_qty, symbol))
            else:
                # Insert new position only if it's a buy
                if side == "buy":
                    cur.execute("""
                        INSERT INTO portfolio (symbol, qty, avg_price)
                        VALUES (%s, %s, %s)
                    """, (symbol, int(qty), float(price)))
        
        logger.info(f"Portfolio position updated: {side} {qty} {symbol} @ {price}")
        return True
        
    except Exception as e:
        logger.error(f"Portfolio update failed for {symbol}: {e}")
        return False

def get_existing_daily_prices_dates(symbol: Optional[str] = None) -> List[date]:
    """Get list of dates that already have daily price data."""
    if symbol:
        query = "SELECT DISTINCT trade_date FROM daily_prices WHERE symbol = %s ORDER BY trade_date"
        params = (symbol,)
    else:
        query = "SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date"
        params = None
    
    results = db.execute_query(query, params)
    return [row[0] for row in results]

def get_existing_snapshot_dates() -> List[date]:
    """Get list of dates that already have snapshot data."""
    query = "SELECT DISTINCT snapshot_date FROM snapshots ORDER BY snapshot_date"
    results = db.execute_query(query)
    return [row[0] for row in results]

def get_latest_snapshot_date() -> Optional[date]:
    """Get the most recent snapshot date."""
    query = "SELECT MAX(snapshot_date) FROM snapshots"
    results = db.execute_query(query)
    return results[0][0] if results and results[0][0] else None

def get_portfolio_positions() -> pd.DataFrame:
    """Get current portfolio positions."""
    query = """
        SELECT symbol, qty, avg_price, entry_date
        FROM portfolio
        ORDER BY symbol
    """
    results = db.execute_query(query)
    
    if not results:
        return pd.DataFrame()
    
    df = pd.DataFrame(results, columns=['symbol', 'qty', 'avg_price', 'entry_date'])
    return df

def get_latest_snapshots() -> pd.DataFrame:
    """Get the most recent RSI snapshot data."""
    latest_date = get_latest_snapshot_date()
    if not latest_date:
        return pd.DataFrame()
    
    query = """
        SELECT symbol, rsi, price
        FROM snapshots
        WHERE snapshot_date = %s
        ORDER BY symbol
    """
    results = db.execute_query(query, (latest_date,))
    
    if not results:
        return pd.DataFrame()
    
    df = pd.DataFrame(results, columns=['Symbol', 'RSI', 'Close'])
    logger.info(f"Loaded {len(df)} snapshot rows from DB (date {latest_date})")
    return df

def cleanup_old_data(days_to_keep: int = 90) -> Dict[str, int]:
    """Clean up old data beyond retention period."""
    cutoff_date = date.today() - pd.Timedelta(days=days_to_keep)
    cleanup_count = {}
    
    tables_and_date_cols = [
        ('trade_log', 'timestamp'),
        ('trade_history', 'trade_date'),
        ('daily_prices', 'trade_date'),
        ('snapshots', 'snapshot_date')
    ]
    
    for table, date_col in tables_and_date_cols:
        query = f"DELETE FROM {table} WHERE {date_col} < %s"
        try:
            with db.conn.cursor() as cur:
                cur.execute(query, (cutoff_date,))
                cleanup_count[table] = cur.rowcount
                logger.info(f"Cleaned up {cur.rowcount} old records from {table}")
        except Exception as e:
            logger.error(f"Cleanup failed for {table}: {e}")
            cleanup_count[table] = 0
    
    return cleanup_count

# Convenience function for backward compatibility
def get_db_connection():
    """Get database connection (legacy compatibility)."""
    return db.conn
