# analytics/ — Backtesting Engine

This directory implements the historical backtesting system and related reporting/dashboard tools.

## What It Does

The backtest engine replays the full probability→decision→sizing pipeline on settled Kalshi KXHIGH markets using reconstructed historical data. Positions are entered once per cycle day. By default they are held to settlement, but the `--backtest-exits` flag enables intraday exit simulation using hourly candlestick data.

## Architecture

### Data Reconstruction

Historical data is reconstructed from two sources:

1. **Kalshi settled markets** — `data/markets.py:fetch_historical_market_definitions()` fetches markets that have already settled, providing the actual high temperature (ground truth).
2. **Hourly candlesticks** — `data/markets.py:fetch_historical_market_quote()` reconstructs what the order book looked like at the entry time by reading the OHLCV candle for that hour.
3. **Archived GFS runs** — `data/weather.py:fetch_historical_forecast_snapshot()` approximates ensemble spread from 4 deterministic GFS runs (0Z/6Z/12Z/18Z) archived by Open-Meteo.

### Simulation Loop (`analytics/replay.py:run_historical_backtest`)

For each cycle day in [start-1, end]:
1. Settle matured positions from prior days — if `--backtest-exits` is enabled, first walk the full hourly candlestick series to check for early exit triggers (stop-loss, profit-take, closeout, EV-gone); if an exit fires, use exit P&L instead of settlement P&L
2. Build rolling calibration context from settled trades (if enabled)
3. For each market targeting the next day:
   - Load or fetch historical quote at the entry time
   - Load or fetch historical forecast snapshot
   - Run `estimate_bucket_probability()` → `assess_entry_risk()` → `choose_trade()`
   - If approved, run `calculate_kelly_size()` and queue as candidate
4. Rank candidates via `select_ranked_candidates()` respecting position limits
5. Execute selected entries, deducting from simulated cash
6. After all cycle days, force-settle any remaining open positions

### Caching (`HistoricalBacktestCache`)

Cache layout under `historical_data/backtests/`:
```
markets/              {target_date}.json     — settled market definitions per day
quotes/{HHMMz}/       {ticker}.json          — reconstructed quote at entry time
forecasts/{HHMMz}/    {city}_{date}.json     — reconstructed forecast at entry time
candlesticks/{HHMMz}/ {ticker}.json          — full hourly series for exit simulation
results/              {run_id}.json + .md    — saved backtest artifacts
```

Cache is keyed by entry time (e.g., `2000Z`). Schema version (`MARKET_CACHE_SCHEMA_VERSION = 2`) gates automatic invalidation of stale cache.

### Reporting (`analytics/backtest_reporting.py`)

`build_backtest_result_package()` wraps raw results with:
- Summary metrics (P&L, return, drawdown, win rate, Brier score, fees)
- Diagnostics (by-city, by-side, EV bins, price bins, candidate ranking, daily equity)
- Optional baseline comparison (delta P&L, return, drawdown)
- Saved JSON + Markdown artifacts in `historical_data/backtests/results/`

`render_backtest_terminal_report()` produces a human-readable terminal summary.

### Dashboard (`analytics/backtest_dashboard.py`)

Local HTTP server serving a static dashboard (`dashboard/index.html` + `app.js` + `styles.css`) that visualizes saved JSON artifacts. Lists all artifacts, loads one at a time, and renders charts/tables client-side.

## Key Modules

| Module | Role |
| --- | --- |
| `replay.py` | `run_historical_backtest()` — main simulation loop; `HistoricalBacktestCache` — disk cache; `replay_from_database()` — replay on stored live/paper data |
| `backtest_reporting.py` | Result packaging, terminal rendering, Markdown artifact generation, baseline comparison |
| `backtest_dashboard.py` | Local HTTP dashboard for browsing saved artifacts |
| `calibration.py` | Summarize calibration statistics from database |
| `pnl.py` | Summarize P&L from database (paper mode) |

## Key Differences from Live/Paper

| Concern | Paper/Live | Backtest |
| --- | --- | --- |
| Market data | Live Kalshi API | Settled markets + hourly candlesticks |
| Forecasts | Live GFS ensemble (31 members) | 4 archived deterministic runs (synthetic spread) |
| Execution | Instant paper fill or API submission | Simulated cash ledger |
| Exits | Full exit rule chain (stop-loss, drift, etc.) | Hold to settlement by default; `--backtest-exits` enables intraday exit simulation via hourly candlesticks |
| Settlement | NWS observed high after buffer period | Known actual high from settled market |
| State | SQLite database | In-memory `BacktestPosition` list |
| Calibration | Database-backed rolling window | Rolling from settled backtest trades |

## Exit Simulation (`--backtest-exits`)

When enabled, exit rules from `core/exits.py` are evaluated at each hourly candlestick during the hold period. The full candlestick series (entry to market close) is fetched from `data/markets.py:fetch_historical_candlestick_series()` and cached in `candlesticks/{HHMMz}/`.

**How it works:** At settlement time, the engine walks every candle from entry to market close. It builds a synthetic `MarketQuote` from each candle and calls `evaluate_exit()`. If any exit rule fires (stop-loss, profit-take, closeout, EV-gone), the position is closed at the candle's bid price with exit fees deducted. If no exit fires, the position settles normally.

**Limitations:**
- **Probability is frozen at entry time.** Historical forecasts cannot be updated during the hold period. This effectively disables probability drift and makes the EV-gone rule purely price-driven.
- **Cash freed by early exits** is not available for new entries until the position's original settlement cycle day (conservative approximation).
- **Hourly granularity** is the finest available from Kalshi candlesticks.

**Key helpers in `replay.py`:**
- `_as_position_record()` — adapts `BacktestPosition` to `PositionRecord` for `evaluate_exit()`
- `_candlestick_to_quote()` — builds a synthetic `MarketQuote` from a `CandlestickPoint`
- `_simulate_exit_checkpoints()` — walks candles, returns exit info or None
- `_exit_position()` — computes realized P&L with entry + exit fees

## Commands

```bash
# Run a backtest with explicit dates
python main.py --backtest --backtest-start 2025-03-01 --backtest-end 2025-03-15

# Run a backtest for the last N days
python main.py --backtest --backtest-days 30

# Use maximum available historical range
python main.py --backtest --backtest-max-range

# Custom entry time (default 20:00 UTC)
python main.py --backtest --backtest-days 14 --backtest-entry-hour-utc 18

# Skip/refresh cache
python main.py --backtest --backtest-days 14 --backtest-no-cache
python main.py --backtest --backtest-days 14 --backtest-refresh-cache

# Machine-readable output
python main.py --backtest --backtest-days 14 --backtest-raw

# Compare against a baseline
python main.py --backtest --backtest-days 14 --backtest-baseline path/to/baseline.json

# Enable intraday exit simulation (stop-loss, profit-take, closeout, EV-gone)
python main.py --backtest --backtest-days 14 --backtest-exits

# Pre-warm the cache without running the backtest
python main.py --warm-backtest-cache --backtest-start 2025-03-01 --backtest-end 2025-03-15

# Launch the dashboard
python main.py --dashboard
python main.py --dashboard --dashboard-artifact path/to/artifact.json
python main.py --dashboard --dashboard-port 8080 --dashboard-no-open
```

## Tests

```bash
python -m unittest tests.test_replay -v
python -m unittest tests.test_backtest_reporting -v
python -m unittest tests.test_backtest_dashboard -v
```

## When Modifying This Code

- Exit simulation (`--backtest-exits`) uses frozen entry-time probability. Probability drift will never trigger. This is a known limitation — historical intraday forecast updates are not available from Open-Meteo archives.
- Historical forecasts approximate ensemble spread from 4 deterministic GFS runs. This systematically underestimates true ensemble disagreement. Keep this bias in mind when interpreting uncertainty-related metrics.
- Cache schema version must be bumped when changing the serialization format for markets, quotes, or forecasts, or old cached data will silently produce wrong results.
- `_settle_position()` uses `bucket_low <= actual_high < bucket_high` (left-inclusive, right-exclusive). This must match Kalshi's settlement logic.
- The simulated cash ledger uses `settings.initial_balance` as starting capital. There is no margin or leverage.
- `select_ranked_candidates()` is shared with the paper/live pipeline — changes there affect both paths.
- Backtest results include a `candidates` diagnostic list that tracks every approved/rejected/selected candidate. This is critical for debugging entry selection tuning.
