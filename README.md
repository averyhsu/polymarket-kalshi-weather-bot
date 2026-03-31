# Kalshi Weather Trading Bot

Kalshi temperature-market trading bot built around a Gumbel bucket-probability engine, uncertainty-aware entry filters, fee-aware Kelly sizing, SQLite-backed paper trading, and a guarded live execution path for Kalshi `KXHIGH` contracts.

Both `paper` and `live` read the same live public Kalshi market data and live weather APIs. The difference is execution:

- `paper` = live inputs + simulated local execution in SQLite
- `live` = live inputs + real Kalshi order submission
- `--dry-run` = run the logic without executing the trade

Each CLI invocation runs one cycle. This repo does not run continuously unless you schedule repeated runs yourself.

## Project Overview

The bot trades day-ahead daily high-temperature contracts for:

- `nyc`
- `chicago`
- `los_angeles`
- `denver`

Miami is blacklisted by default because of the strategy’s settlement-risk and regime-noise concerns, but it can be re-enabled in config.

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

The command `python -m venv .venv` creates a virtual environment in a local folder named `.venv`. It does not activate it. Activation is different on Windows versus macOS/Linux.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
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
pip install -r requirements.txt
copy .env.example .env
```

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

After activation, you can use `python` and `pip` normally inside that shell session.

## Environment Variables

Key runtime settings live in `.env.example`.

Important values:

- `BOT_MODE=paper|live`
- `BOT_PROFILE=conservative|balanced|aggressive`
- `DB_PATH=kalshi_weather_bot.sqlite3`
- `ENABLED_CITIES=nyc,chicago,los_angeles,denver`
- `BLACKLISTED_CITIES=miami`
- `NO_ONLY=false`
- `MAX_SPREAD_CENTS=5`
- `BASE_MIN_EV=0.04`
- `DAILY_MAX_LOSS_PCT=0.10`
- `KALSHI_API_KEY_ID=...`
- `KALSHI_PRIVATE_KEY_PATH=...`

Live execution requires both Kalshi credential variables. Paper mode does not.

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
- not a Kalshi demo-account mode yet
- not a continuous loop by default

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
python main.py --close-all-paper
```

Paper mode is the main strategy-testing mode because it keeps a full local ledger of paper cash, fills, positions, exits, and P&L.

## Live Trading Warnings

Live mode is intentionally conservative:

- It only submits orders when `--mode live` is used.
- It reads the same live public Kalshi quotes as paper mode; the difference is that execution is real.
- It refuses to place live orders without `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH`.
- `--dry-run` works in live mode, so you can validate the scan/decision path without sending orders.
- The current live path submits entry orders only and records them locally; you should test with very small size first.

Example:

```bash
python main.py --mode live --profile conservative --dry-run
python main.py --mode live --profile conservative
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
- The replay harness reuses stored snapshots and forecasts, but it is not a full historical event simulator yet.

## Settlement Caveats

Kalshi weather markets settle from the linked NWS Climatological Report, not from whatever consumer weather site or intraday sensor reading looks closest in the moment.

Important practical caveats:

- Preliminary ASOS and consumer-app temperatures can diverge from the final reported settlement value.
- One-minute ASOS readings and rounding/conversion behavior can create intraday mismatch risk.
- This bot therefore avoids same-day trading by default and only targets day-ahead or earlier contracts.

## Future Improvements

- Replace seeded climatology with station-specific historical normals.
- Add richer replay and backtesting over archived snapshots.
- Add live exit-order management and reconciliation.
- Add portfolio-level city-correlation controls.
- Add richer calibration and settlement-quality reports.
