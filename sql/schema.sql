-- Unified database schema for alpaca-shirt trading system
-- Updated to match existing structure with improvements

-- Portfolio table: Current positions (matches existing structure, removes unused side column)
CREATE TABLE IF NOT EXISTS portfolio (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    qty INTEGER NOT NULL,
    avg_price DOUBLE PRECISION NOT NULL,
    entry_date DATE DEFAULT CURRENT_DATE,
    UNIQUE(symbol)
);

-- Trade history table: All executed trades (updated to use trade_date instead of timestamp)
CREATE TABLE IF NOT EXISTS trade_history (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL DEFAULT CURRENT_DATE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    qty INTEGER NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    order_status TEXT DEFAULT 'submitted',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Daily prices table: OHLCV data (matches your existing structure exactly)
CREATE TABLE IF NOT EXISTS daily_prices (
    trade_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume BIGINT,
    CONSTRAINT daily_prices_pkey PRIMARY KEY (trade_date, symbol)
);

-- Snapshots table: RSI calculations (matches your existing structure exactly)
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    rsi DOUBLE PRECISION,
    price DOUBLE PRECISION,
    CONSTRAINT snapshots_pkey PRIMARY KEY (snapshot_date, symbol)
);

-- Trade log table: Application-level logging (NEW - replaces CSV logging)
CREATE TABLE IF NOT EXISTS trade_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    qty INTEGER NOT NULL,
    status TEXT NOT NULL,
    price DOUBLE PRECISION,
    notes TEXT
);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_portfolio_symbol ON portfolio(symbol);

CREATE INDEX IF NOT EXISTS idx_trade_history_symbol ON trade_history(symbol);
CREATE INDEX IF NOT EXISTS idx_trade_history_date ON trade_history(trade_date);

-- Match your existing index
CREATE INDEX IF NOT EXISTS idx_daily_prices_symbol_date 
    ON daily_prices(symbol, trade_date);

-- Match your existing index  
CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_date
    ON snapshots(symbol, snapshot_date);

-- New indexes for trade_log
CREATE INDEX IF NOT EXISTS idx_trade_log_timestamp ON trade_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_trade_log_symbol ON trade_log(symbol);

-- Comments for documentation
COMMENT ON TABLE portfolio IS 'Current trading positions with entry prices and dates';
COMMENT ON TABLE trade_history IS 'Historical record of all executed trades';
COMMENT ON TABLE daily_prices IS 'Daily OHLCV market data for all symbols';
COMMENT ON TABLE snapshots IS 'RSI calculations and prices for specific analysis dates';
COMMENT ON TABLE trade_log IS 'Application-level trade logging (replaces CSV logs)';

COMMENT ON COLUMN daily_prices.trade_date IS 'Market date for the price data';
COMMENT ON COLUMN snapshots.snapshot_date IS 'Analysis date for RSI calculation';
COMMENT ON COLUMN trade_history.trade_date IS 'Date when trade was executed';
