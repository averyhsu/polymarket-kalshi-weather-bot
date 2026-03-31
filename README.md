# Kalshi Weather Trading Bot

Kalshi temperature-market trading bot built around a Gumbel bucket-probability engine, uncertainty-aware entry filters, fee-aware Kelly sizing, SQLite-backed paper trading, and a guarded live execution path for Kalshi `KXHIGH` contracts.

Paper mode works from public Kalshi market data plus free weather sources. Live mode requires explicit Kalshi API credentials and stays behind config flags and `--mode live`.

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

Windows PowerShell:

```powershell
& "C:\Users\avery\AppData\Roaming\uv\python\cpython-3.9-windows-x86_64-none\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Generic Python:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

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

## Live Trading Warnings

Live mode is intentionally conservative:

- It only submits orders when `--mode live` is used.
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
