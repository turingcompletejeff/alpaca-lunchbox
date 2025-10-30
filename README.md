📈 Alpaca-lunchbox

A trading bot and data pipeline for the S&P 500, built around RSI-based
strategies.
This repo fetches RSI snapshots of the S&P500 (via Polygon.io), saves
them to Postgres/CSV/plots, and then lets you interactively approve
trades with Alpaca (paper trading supported).

------------------------------------------------------------------------

🚀 Features

-   RSI Snapshots: Pull daily bars from Polygon.io, compute RSI, save to
    Postgres + CSV + plots
-   Trade Executor: Interactive terminal menu for approving trades via
    Alpaca
-   Database Integration: PostgreSQL for portfolio, trade history, and
    daily RSI snapshots
-   Risk Controls: Configurable thresholds (entry/exit RSI, allocation
    sizing, stop-loss)
-   Logging: Trade log CSV + DB inserts for tracking
-   SPY Benchmarking: Compare strategy performance to SPY

------------------------------------------------------------------------

🗂️ Repo Structure

    alpaca-lunchbox/
    │
    ├── 0sync_portfolio.py       # syncs the alpaca portfolio with local DB
    ├── 1rsi-compare.py          # Fetch RSI snapshots + save to DB (Polygon)
    ├── 2trade_executor.py       # Interactive trading loop (Alpaca)
    ├── 3portfolio_analyzer.py   # Analyzes exit strategies and AVG down.
    ├── db_utils.py              # Common DB utils
    ├── config/
      ├── strategy_config.json     # Strategy thresholds and settings
    │
    ├── csv/                     # Daily RSI snapshots (saved)
    ├── plots/                   # RSI histograms + SPY RSI plots
    ├── logs/                    # Trade logs
    ├── sql/                     # A record of the schema
    ├── requirements.txt         # Dependencies
    └── README.md                # You're here!

------------------------------------------------------------------------

⚙️ Setup

1. Clone the repo

    git clone https://github.com/turingcompletejeff/alpaca-lunchbox.git
    cd alpaca-lunchbox

2. Configure .env

Create a .env file in the repo root with your API keys + Postgres
credentials:

    # Alpaca API
    API_KEY=your_api_key
    API_SECRET=your_api_secret
    BASE_URL=https://paper-api.alpaca.markets

    # Polygon API
    POLYGON_API_KEY=your_polygon_api_key
    # free tier limits requests to 5 per minute. 60 / 5 = 12s per request
    RATE_LIMIT_SECONDS=12

    # PostgreSQL
    PGDATABASE=alpaca_db
    PGUSER=alpaca_user
    PGPASSWORD=secret_password
    PGHOST=localhost
    PGPORT=5432

👉 You can get a free Polygon API key here: https://polygon.io
- Free tier = 5 API calls per minute, 2 years of daily historical bars.
- More than enough for RSI snapshots once per day.

3. Create a virtual environment
```
    python -m venv venv
    venv\Scripts\activate   # (Windows)
    # source venv/bin/activate  # (Mac/Linux)

    pip install -r requirements.txt
```
------------------------------------------------------------------------

📊 Usage

Generate RSI Snapshots (Polygon-powered ✅):

    python 1rsi-compare.py

Outputs: - csv/YYYY-MM-DD-sp500_rsi_snapshot.csv -
plots/YYYY-MM-DD-sp500_rsi_hist.png - Inserts into Postgres snapshots
table

Execute Trades (Alpaca):

    python 2trade_executor.py

-   Shows available cash 💰
-   Previews oversold/overbought candidates
-   Interactive menu: [y]es / [n]o / [s]kip rest
-   Inserts trades into Postgres + appends to logs/trade_log.csv

------------------------------------------------------------------------

📝 Strategy

-   Entry:
    -   Buy when RSI < low threshold (e.g., 25)
    -   Short when RSI > high threshold (e.g., 80)
-   Exit:
    -   Return to neutral RSI (40–60)
    -   Or take profits at +10–15%
-   Stop-Loss:
    -   Default: -15% per position
-   Weighting:
    -   Baseline dollar allocation
    -   Extra weighting for extreme RSI dips

Configurable in strategy_config.json (example):
```
{
  "strategy_name": "sp500_rsi_harvest_v1",
  "universe": {
    "source_mode": "db",
    "source": "csv/sp500_tickers.csv",
    "use_spy_as_benchmark": true
  },
  "schedule": {
    "snapshot_every_days": 1,
    "trade_review_days": ["TUE", "FRI"],
    "trade_review_time_utc": "13:30:00",   /* 09:30 ET = 13:30 UTC during standard time */
    "allow_emergency_trades": true
  },
  "entry": {
    "rsi_thresholds": {
      "extreme": 20,
      "primary": 25,
      "watch": 30
    },
  "position_sizing": {
    "mode": "dollar",                      /* "dollar" or "shares" */
    "baseline_dollars": 1000,
    "max_portfolio_exposure_pct": 0.30,    /* never allocate >30% of total capital to open trades */
    "max_sector_exposure_pct": null
  },
  "averaging_down": {
    "enabled": false,
    "trigger_pct": -0.07,                 /* add when position down 7% */
    "add_fraction": 0.5,                  /* add 50% of original position size */
    "max_adds": 2
  },
  "filters": {
    "exclude_news_recent_hours": 24,      /* optional: require manual check if major news likely */
    "exclude_large_gap_pct": 0.10         /* skip stocks that gapped >10% yesterday */
  },
  "execution": {
    "account_type": "paper",
    "qty_mode": "dollar_to_shares",       /* "dollar_to_shares" or "fixed_shares" */
  },
  ...
}
```

------------------------------------------------------------------------

📈 Example Plot

Example RSI histogram for the S&P500:
<img src="plots/2025-09-07-sp500_rsi_hist.png">

------------------------------------------------------------------------

🛠️ Roadmap

-   ☐ Portfolio performance tracking
-   ☐ Smarter averaging-down logic
-   ☐ Limit orders, stop-losses, take-profit
-   ☐ how to adjust portfolio week-to-week ?
-   ☐ Backtesting module

------------------------------------------------------------------------

⚠️ Disclaimer

This project is for educational purposes only.
Trading involves risk. Use paper trading accounts before committing real
capital.
