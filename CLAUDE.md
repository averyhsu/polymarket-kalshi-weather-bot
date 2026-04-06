# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Kalshi weather trading bot that trades day-ahead daily-high-temperature contracts (KXHIGH series). Single-cycle architecture: each CLI invocation fetches data, makes decisions, and executes — it does not loop. Scheduling is external (e.g., cron).

## Section-Specific Guides

Detailed guidance for each major subsystem lives in its own CLAUDE.md:

- **`core/CLAUDE.md`** — Paper trading cycle: probability estimation, risk assessment, decision logic, sizing, exits, and paper execution.
- **`execution/CLAUDE.md`** — Live trading cycle: Kalshi API order submission, preflight checks, dry-run safety, authentication.
- **`analytics/CLAUDE.md`** — Backtesting engine: historical data reconstruction, simulation loop, caching, reporting, and dashboard.

## Commands

```bash
# Setup (one-time)
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env        # then fill in credentials

# Run a trading cycle
python main.py --mode paper                     # paper trading (default)
python main.py --mode live                      # live Kalshi execution
python main.py --mode paper --dry-run           # evaluate without executing
python main.py --mode paper --profile aggressive

# Analytics
python main.py --stats                          # P&L summary
python main.py --replay                         # re-run decisions on stored data
python main.py --positions                      # list open positions
python main.py --close-all-paper                # close all paper positions

# Historical backtest
python main.py --backtest --backtest-start 2025-03-01 --backtest-end 2025-03-15
python main.py --backtest --backtest-days 30
python main.py --backtest --backtest-max-range

# Dashboard
python main.py --dashboard

# Tests
python -m unittest discover -s tests -v         # all tests
python -m unittest tests.test_probability -v    # single module
```

## Architecture

**Pipeline per cycle:** `main.py` → `orchestrator.run_cycle()` → for each city:
1. `data/markets.py` fetches Kalshi KXHIGH bucket contracts
2. `data/weather.py` fetches 31-member GFS ensemble forecast (Open-Meteo), cached 20 min
3. `core/probability.py` fits Gumbel distribution → bucket probabilities, with climatology shrinkage (`data/climatology.py`)
4. `core/risk.py` scores uncertainty (boundary mass + ensemble disagreement), applies circuit breakers
5. `core/decision.py` chooses YES/NO side based on EV thresholds and spread filters
6. `core/exits.py` evaluates open positions for closeout/stop-loss/drift/profit-take
7. `core/sizing.py` applies fee-aware Kelly criterion for position sizing
8. `execution/paper.py` or `execution/live.py` executes the trade

**Persistence:** `db/models.py` manages SQLite with tables for orders, fills, positions, market snapshots, daily P&L, calibration, and forecasts. Paper and live modes use separate position state.

**Config:** `config.py` uses Pydantic Settings loaded from `.env`. Three profile presets (conservative/balanced/aggressive) scale Kelly fraction, max position, EV buffer, and dampening.

## Key Design Decisions

- **Gumbel distribution** models daily max temperatures (extreme-value theory). Sigma has a floor to prevent degenerate distributions.
- **Boundary mass** measures how much probability density sits near bucket edges — high boundary mass means the model is uncertain about which bucket the temperature falls in.
- **Ensemble disagreement** is the second uncertainty signal, measuring spread across the 31 GFS members.
- **Climatology shrinkage** blends forecast-derived probabilities toward historical base rates using pseudo-counts.
- All core data structures are **frozen dataclasses** for immutability through the pipeline.
- The bot returns **JSON output** from each cycle for machine consumption.
- **Uncertainty scoring** combines boundary mass (60%) and ensemble disagreement (40%), both normalized. This drives dynamic EV thresholds and position size multipliers (floor 0.35×).
- **Fee-aware Kelly**: sizing uses `b = ((1-cost) - fee) / (cost + fee)` as the net odds, then applies Kelly fraction, profile multiplier, uncertainty multiplier, and caps (2% balance / $10 USD).

## External APIs

- **Open-Meteo GFS Ensemble** — weather forecasts (no auth required)
- **NWS Observations API** — settlement verification (no auth required)
- **Kalshi Trade API** — market data and order execution (RSA-signed auth via `KALSHI_API_KEY_ID` + private key file)
