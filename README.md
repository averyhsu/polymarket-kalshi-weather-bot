# Kalshi Weather Trading Bot

Kalshi temperature-market trading bot built around a Gumbel bucket-probability engine, uncertainty-aware entry filters, fee-aware Kelly sizing, SQLite-backed paper trading, and a guarded live execution path for Kalshi `KXHIGH` contracts.

Both `paper` and `live` read the same live public Kalshi market data and live weather APIs. The difference is execution:

- `paper` = live inputs + simulated local execution in SQLite
- `live` = live inputs + real Kalshi order submission
- `--dry-run` = run the logic without executing the trade

Each CLI invocation runs one cycle. This repo does not run continuously unless you schedule repeated runs yourself. Historical backtests now reuse a dedicated local dataset under `historical_data/backtests` by default.

## Project Overview

The bot supports all currently wired Kalshi daily high-temperature cities:

- `atlanta`
- `austin`
- `boston`
- `chicago`
- `dallas`
- `denver`
- `houston`
- `las_vegas`
- `los_angeles`
- `miami`
- `minneapolis`
- `new_orleans`
- `nyc`
- `oklahoma_city`
- `philadelphia`
- `phoenix`
- `san_antonio`
- `san_francisco`
- `seattle`
- `washington_dc`

The default config now enables all of the cities above so broader backtests do not silently stay narrowed to the old four-city subset.

The execution flow is:

1. Fetch active `KXHIGH` contracts from Kalshi.
2. Fetch 31-member Open-Meteo GFS ensemble highs for each city/date.
3. Convert forecast mean and sigma into a Gumbel daily-maximum distribution.
4. Estimate bucket probabilities, then shrink toward climatology.
5. Score uncertainty from boundary mass and ensemble disagreement.
6. Choose YES or NO using executable ask prices, spread filters, and dynamic EV thresholds.
7. Size trades with fee-aware fractional Kelly.
8. Execute in paper mode or guarded live mode.
9. Persist orders, fills, positions, snapshots, forecasts, and calibration rows to SQLite.

## Architecture

```text
kalshi-weather-bot/
├── main.py
├── config.py
├── orchestrator.py
├── requirements.txt
├── README.md
├── .env.example
├── .gitignore
├── core/
│   ├── probability.py
│   ├── decision.py
│   ├── risk.py
│   ├── sizing.py
│   └── exits.py
├── execution/
│   ├── paper.py
│   └── live.py
├── data/
│   ├── weather.py
│   ├── markets.py
│   └── climatology.py
├── analytics/
│   ├── pnl.py
│   ├── calibration.py
│   └── replay.py
├── db/
│   └── models.py
└── tests/
    ├── test_probability.py
    ├── test_decision.py
    ├── test_risk.py
    └── test_sizing.py
```

## Installation

The command `python -m venv .venv` creates a virtual environment in a local folder named `.venv`. It does not activate it. You usually only need to create `.venv` once per project clone, then activate the same environment in future shell sessions. Activation is different on Windows versus macOS/Linux.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks activation scripts, run this once for the current session and then activate again:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

Windows Command Prompt:

```cmd
python -m venv .venv
.\.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
copy .env.example .env
```

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

After activation, you can use `python` and `pip` normally inside that shell session.

## Environment Variables

Key runtime settings live in `.env.example`.

Important values:

- `BOT_MODE=paper|live`
- `BOT_PROFILE=conservative|balanced|aggressive`
- `KALSHI_ENVIRONMENT=production|demo`
- `DB_PATH=kalshi_weather_bot.sqlite3`
- `HISTORICAL_DATA_DIR=historical_data/backtests`
- `ENABLED_CITIES=atlanta,austin,boston,chicago,dallas,denver,houston,las_vegas,los_angeles,miami,minneapolis,new_orleans,nyc,oklahoma_city,philadelphia,phoenix,san_antonio,san_francisco,seattle,washington_dc`
- `BLACKLISTED_CITIES=`
- `NO_ONLY=true`
- `YES_ENABLED=false`
- `YES_MIN_EV=0.08`
- `NO_MIN_EV=0.04`
- `YES_MIN_PRICE_CENTS=10`
- `NO_MID_PRICE_FILTER_ENABLED=true`
- `NO_MID_PRICE_MIN_CENTS=10`
- `NO_MID_PRICE_MAX_CENTS=50`
- `YES_KELLY_FRACTION_MULT=0.35`
- `MAX_POSITIONS_PER_CITY_DAY=3`
- `MAX_YES_POSITIONS_PER_CITY_DAY=1`
- `ALLOW_MIXED_SIDES_PER_CITY_DAY=false`
- `EVENT_WORST_CASE_PENALTY=0.35`
- `CITY_EV_BUFFER_OVERRIDES={"chicago": 0.06}`
- `CALIBRATION_ENABLED=true`
- `MAX_SPREAD_CENTS=5`
- `BASE_MIN_EV=0.04`
- `DAILY_MAX_LOSS_PCT=0.10`
- `KALSHI_API_KEY_ID=...`
- `KALSHI_PRIVATE_KEY_PATH=...`

Live execution requires both Kalshi credential variables. Paper mode does not. `KALSHI_ENVIRONMENT=demo` uses Kalshi's demo API root and `KALSHI_ENVIRONMENT=production` uses the production API root.

## Modes

The bot has two execution modes and one safety flag:

- `python main.py --mode paper`
  Uses live Kalshi quotes and live weather data, then simulates entries, exits, settlements, cash, and positions locally.
- `python main.py --mode paper --dry-run`
  Uses the same live inputs, but does not create or close paper trades.
- `python main.py --mode live`
  Uses the same live inputs, then submits real orders to Kalshi if a trade is approved and credentials are configured.
- `python main.py --mode live --dry-run`
  Uses the live decision path, but does not submit the order.

This means the current repo is:

- not a separate market-data sandbox
- not a continuous loop by default

The live path supports both Kalshi demo and production environments. The deployment recommendation below uses demo only.

## Paper Trading Quickstart

Run one paper cycle with a $100 starting balance:

```bash
python main.py --mode paper --balance 100
```

Useful commands:

```bash
python main.py --mode paper --balance 100 --dry-run
python main.py --stats
python main.py --positions
python main.py --replay
python main.py --warm-backtest-cache --backtest-days 30
python main.py --warm-backtest-cache --backtest-max-range
python main.py --backtest --backtest-days 14
python main.py --backtest --backtest-max-range
python main.py --close-all-paper
```

Paper mode is the main strategy-testing mode because it keeps a full local ledger of paper cash, fills, positions, exits, and P&L.

## Historical Backtesting

Run a historical day-ahead backtest over settled markets:

```bash
python main.py --warm-backtest-cache --backtest-days 30
python main.py --warm-backtest-cache --backtest-max-range
python main.py --backtest --backtest-days 14
python main.py --backtest --backtest-max-range
python main.py --backtest --backtest-start 2026-03-01 --backtest-end 2026-03-31
python main.py --backtest --backtest-days 30 --backtest-entry-hour-utc 20
python main.py --backtest --backtest-days 30 --backtest-refresh-cache
python main.py --backtest --backtest-days 30 --backtest-raw
python main.py --backtest --backtest-days 30 --backtest-no-save
python main.py --backtest --backtest-days 30 --backtest-baseline historical_data/backtests/results/<baseline>.json
```

What the backtest does:

- loads settled historical `KXHIGH` contracts for the requested target-date range
- reconstructs day-ahead entry quotes from Kalshi historical hourly candlesticks
- reconstructs historical forecasts from archived Open-Meteo single runs on the entry day
- runs the same probability, risk, decision, and Kelly sizing logic as live and paper mode
- enters once per cycle and holds positions to settlement
- reports P&L, drawdown, win rate, Brier score, skip reasons, and trade-level details
- stores historical market definitions, entry quotes, and archived forecast snapshots in a persistent local dataset directory so repeated backtests do not refetch the same month of data every time

Recommended workflow:

- run `python main.py --warm-backtest-cache --backtest-days 30` once to download the last month of backtest inputs
- run `python main.py --warm-backtest-cache --backtest-max-range` when you want the full currently supported historical window in one shot
- then run `python main.py --backtest --backtest-days 14` or any overlapping range and it will reuse the local cache
- use `--backtest-refresh-cache` when you want to overwrite the saved dataset
- use `--backtest-no-cache` if you explicitly want a one-off uncached run
- by default the saved dataset lives in `historical_data/backtests`; set `HISTORICAL_DATA_DIR` if you want to keep it somewhere else

Backtest output now has two layers:

- terminal output is a human-readable summary by default
- each run is also saved under `historical_data/backtests/results`
- every saved run gets:
  - one JSON artifact with the full machine-readable result, including all trades and skips
  - one Markdown report with the same run summarized for humans

Useful flags:

- `--backtest-raw` prints the full machine-readable backtest package to stdout as JSON
- `--backtest-no-save` skips writing the JSON and Markdown artifacts for that run
- `--backtest-baseline <path>` compares the current run against a prior JSON artifact and shows delta vs baseline
- `--backtest-max-range` uses the earliest fully reconstructable historical date through yesterday

This makes it easier to compare model iterations over time:

- use the terminal summary for quick evaluation
- open the saved Markdown report for a readable archive of the run
- use the saved JSON artifact when you want every trade, skip, and metric in full detail

What it does not do yet:

- it does not simulate intraday exits from historical quote paths
- it does not replay true archived ensemble members; uncertainty is approximated from multiple archived deterministic runs on the entry day
- it does not model queue position or partial fills

## Live Trading Warnings

Live mode is intentionally conservative:

- It only submits orders when `--mode live` is used.
- It reads the same live public Kalshi quotes as paper mode; the difference is that execution is real.
- It refuses to place live orders without `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH`.
- It validates the configured Kalshi environment and does an authenticated preflight before submitting orders.
- `--dry-run` works in live mode, so you can validate the scan/decision path without sending orders.
- The current live path submits entry orders only and records them locally; you should test with very small size first.

Example:

```bash
python main.py --mode live --profile conservative --dry-run
python main.py --mode live --profile conservative
```

Kalshi docs:

- [Demo Environment](https://docs.kalshi.com/getting_started/demo_env)
- [Authenticated Requests](https://docs.kalshi.com/getting_started/quick_start_authenticated_requests)

## Computer B Demo Deployment

The recommended deployment is a separate Windows machine, "Computer B", running the current `NO_ONLY=true` / `YES_ENABLED=false` strategy once per UTC day at `20:00`.

### 1. Clone a pinned repo version on Computer B

```powershell
mkdir C:\Trading
cd C:\Trading
git clone YOUR_REPO_URL weather-prediction
cd weather-prediction
git checkout YOUR_DEPLOY_COMMIT_SHA
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 2. Create Computer B's `.env`

Create `C:\Trading\weather-prediction\.env`:

```dotenv
BOT_MODE=live
BOT_PROFILE=balanced
DRY_RUN=false

NO_ONLY=true
YES_ENABLED=false

KALSHI_ENVIRONMENT=demo
KALSHI_API_KEY_ID=YOUR_DEMO_API_KEY_ID
KALSHI_PRIVATE_KEY_PATH=C:\Trading\weather-prediction\secrets\kalshi-demo.pem

DB_PATH=C:\Trading\weather-prediction\state\trading.db
HISTORICAL_DATA_DIR=C:\Trading\weather-prediction\historical_data
```

Create the supporting folders and copy your Kalshi demo private key into `C:\Trading\weather-prediction\secrets\kalshi-demo.pem`.

### 3. Run the live preflight and a manual smoke test

```powershell
cd C:\Trading\weather-prediction
.\.venv\Scripts\Activate.ps1
python main.py --mode live --dry-run
python main.py --mode live
```

### 4. Register the scheduled task

The checked-in runner [scripts/run_live_demo.ps1](C:\Users\avery\OneDrive - andrew.cmu.edu\Projects\weather prediction\scripts\run_live_demo.ps1) can safely be scheduled **hourly**. It only executes the bot when the current UTC hour is `20`, and it records the last successful UTC run date so Computer B does not double-submit.

Create the task on Computer B:

```powershell
schtasks /Create /F /SC HOURLY /MO 1 /TN "KalshiWeatherDemo" /TR "powershell.exe -ExecutionPolicy Bypass -File C:\Trading\weather-prediction\scripts\run_live_demo.ps1" /ST 00:00
```

Useful checks:

```powershell
Get-Content C:\Trading\weather-prediction\logs\live-demo.log -Tail 100
sqlite3 C:\Trading\weather-prediction\state\trading.db ".tables"
sqlite3 C:\Trading\weather-prediction\state\trading.db "select count(*) from orders;"
```

## Strategy Logic Summary

### Probability Engine

Daily maximum temperature is modeled with a Gumbel distribution:

```text
beta = sigma * sqrt(6) / pi
mu = mean - 0.5772 * beta
F(x) = exp(-exp(-(x - mu) / beta))
p_model = F(bucket_high) - F(bucket_low)
alpha = n / (n + pseudo_count)
p_final = alpha * p_model + (1 - alpha) * p_climo
```

The engine also emits:

- `p_bucket_yes`
- `p_bucket_no`
- `boundary_mass`
- `disagreement`
- debug metadata including `mean`, `sigma`, `alpha`, `p_model`, and `p_climo`

### Risk and Uncertainty

Uncertainty is derived from boundary mass and disagreement:

```text
uncertainty = 0.60 * boundary_mass / 0.25 + 0.40 * disagreement / 0.85
dynamic_min_ev = base_min_ev + uncertainty * 0.02
size_mult = max(0.35, 1 - 0.60 * uncertainty)
```

The entry layer then applies additional correctness filters:

- side-aware thresholds:
  - YES and NO use different minimum EV floors
  - YES can be disabled entirely
  - very cheap YES contracts are rejected by default
  - mid-priced NO contracts can be filtered out when that regime is underperforming
- city-aware overrides:
  - cities like Chicago can require extra EV buffer without disabling the whole strategy
- tail-risk penalty:
  - cheap YES tail bets with high modeled probability and fragile uncertainty are penalized before entry
- ranking before execution:
  - the bot now evaluates all approved candidates in a cycle, ranks them, and only then fills the best ones under the portfolio caps
- event-basket selection:
  - selection is optimized at the city/day basket level instead of treating overlapping buckets as independent trades
  - by default the bot allows at most one YES per city/day and avoids mixing YES and NO on the same event
  - event baskets are penalized for ugly worst-case outcomes before they compete for the global slot budget
- empirical calibration:
  - raw probabilities are shrunk toward historically realized frequencies by side, price regime, probability bin, and city

The bot also scores source health from:

- request success
- forecast freshness
- completeness of ensemble members
- consistency of forecast spread

### Sizing

The sizing layer uses fee-aware binary Kelly:

```text
b = ((1 - cost) - fee) / (cost + fee)
kelly = (p * (b + 1) - 1) / b
size = balance * kelly * kelly_fraction * uncertainty_mult * source_health_mult
```

Then it caps that size by:

- `max_position_pct`
- `max_position_usd`
- the selected profile preset

### Exits

Exit priority is:

1. closeout near event
2. stop loss
3. probability drift
4. EV gone
5. profit take
6. hold

## Analytics

The SQLite database stores:

- orders
- fills
- positions
- market snapshots
- daily P&L
- calibration logs
- forecasts used for decisions

Available analytics:

- `python main.py --stats`
- `python main.py --replay`
- `python main.py --backtest --backtest-days 14`

## Tests

Run the unit suite:

```bash
python -m unittest discover -s tests -v
```

## Known Limitations

- Climatology is seeded with lightweight month-level priors, not station-history datasets.
- The probability engine uses the required Gumbel framework and simple empirical diagnostics, but not a full multi-model weather blend.
- Fees are modeled conservatively as fixed per-contract approximations.
- Live execution is intentionally narrow and should be treated as a guarded MVP.
- The historical backtest currently holds positions to settlement instead of simulating intraday exit timing.
- Historical forecast uncertainty is reconstructed from archived deterministic runs, not archived ensemble-member fields.

## Settlement Caveats

Kalshi weather markets settle from the linked NWS Climatological Report, not from whatever consumer weather site or intraday sensor reading looks closest in the moment.

Important practical caveats:

- Preliminary ASOS and consumer-app temperatures can diverge from the final reported settlement value.
- One-minute ASOS readings and rounding/conversion behavior can create intraday mismatch risk.
- This bot therefore avoids same-day trading by default and only targets day-ahead or earlier contracts.

## Future Improvements

- Replace seeded climatology with station-specific historical normals.
- Add intraday historical exit simulation and richer archived replay.
- Add live exit-order management and reconciliation.
- Add portfolio-level city-correlation controls.
- Add richer calibration and settlement-quality reports.
