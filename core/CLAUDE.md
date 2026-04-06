# core/ + execution/paper.py — Paper Trading Cycle

This covers the paper trading pipeline: the core decision engine and the paper execution backend.

## What It Does

When `--mode paper` (the default), the bot runs a full scan-decide-execute cycle against live Kalshi market data but executes trades in a local SQLite ledger instead of the real exchange. Paper mode also handles exit evaluation and settlement against NWS observed highs.

## Pipeline Per Cycle

`orchestrator.run_cycle()` → for each city:

1. **Market fetch** — `data/markets.py` pulls live KXHIGH bucket contracts from Kalshi.
2. **Forecast fetch** — `data/weather.py` fetches 31-member GFS ensemble from Open-Meteo (cached 20 min).
3. **Probability estimation** — `core/probability.py` fits a Gumbel distribution to get bucket probabilities, blended with climatology via pseudo-counts.
4. **Risk assessment** — `core/risk.py` combines boundary mass (60%) and ensemble disagreement (40%) into an uncertainty score. Circuit breakers fire if source health degrades.
5. **Trade decision** — `core/decision.py` chooses YES/NO side based on EV thresholds, spread filters, and optional calibration adjustment.
6. **Entry selection** — `core/entry_selection.py` ranks approved candidates and caps by position limits, city/day exposure, and slot availability.
7. **Position sizing** — `core/sizing.py` applies fee-aware Kelly criterion with uncertainty and source-health multipliers.
8. **Paper execution** — `execution/paper.py` fills instantly at the decision price, deducting from paper cash.

Before new entries, the orchestrator also:
- **Settles matured positions** — compares bucket range to NWS observed high after a configurable buffer period.
- **Evaluates exits** — `core/exits.py` checks open positions for closeout (2h before event), stop-loss (-15c), probability drift (12%), EV gone (<1c), and profit-take (+10c), in that priority order.

## Key Modules

| Module | Role |
| --- | --- |
| `core/probability.py` | Gumbel CDF → bucket probability, boundary mass, ensemble disagreement |
| `core/risk.py` | Uncertainty score, dynamic EV thresholds, size multipliers, circuit breakers |
| `core/decision.py` | Side selection (YES/NO), EV check, spread filter, calibration adjustment |
| `core/entry_selection.py` | Candidate ranking, slot allocation, city/day caps, mixed-side rules |
| `core/exits.py` | Exit rule priority chain for open positions |
| `core/sizing.py` | Fee-aware Kelly with `b = ((1-cost) - fee) / (cost + fee)`, caps at 2% balance / $10 |
| `core/calibration.py` | Online calibration context from historical prediction accuracy |
| `execution/paper.py` | Paper fills, mark-to-market, exit fills, settlement against observed highs |
| `data/climatology.py` | Historical base-rate lookup for climatology shrinkage |

## Key Data Structures

All core data structures are **frozen dataclasses**:
- `ProbabilityResult` — `p_bucket_yes`, `boundary_mass`, `disagreement`, fitted params
- `RiskAssessment` — `uncertainty`, `size_mult`, `source_health_mult`, circuit breaker flags
- `TradeDecision` — `approved`, `side`, `price`, `expected_value`, `rationale`
- `SizingResult` — `contracts`, `debug` dict with Kelly breakdown
- `ExecutionResult` — `executed`, `order_id`, `position_id`, `fill_price`

## Commands

```bash
# Paper trading cycle
python main.py --mode paper

# With risk profile
python main.py --mode paper --profile aggressive

# Dry-run (evaluate without filling)
python main.py --mode paper --dry-run

# Analytics
python main.py --stats          # P&L and calibration summary
python main.py --replay         # re-run decisions on stored snapshots
python main.py --positions      # list open paper positions
python main.py --close-all-paper  # close all open paper positions
```

## Tests

```bash
python -m unittest tests.test_probability -v
python -m unittest tests.test_decision -v
python -m unittest tests.test_risk -v
python -m unittest tests.test_sizing -v
python -m unittest tests.test_entry_signal -v
```

## When Modifying This Code

- `probability.py` uses a Gumbel distribution (extreme-value theory) — do not switch to Gaussian without understanding the tail behavior change.
- The sigma floor in `probability.py` prevents degenerate zero-width distributions. Do not remove it.
- `risk.py` uncertainty weights (60% boundary mass, 40% disagreement) are tuned constants — changes cascade into EV thresholds and sizing.
- `decision.py` enforces minimum YES price and spread filters before the EV check. Order matters.
- `exits.py` evaluates rules in strict priority order. Adding a new rule means choosing where it sits relative to stop-loss, drift, etc.
- `sizing.py` caps are intentionally conservative (2% bankroll or $10 USD per position). Raising these increases tail risk.
- Paper fills are instantaneous at the decision price — there is no slippage simulation in paper mode (backtests use a slippage parameter).
