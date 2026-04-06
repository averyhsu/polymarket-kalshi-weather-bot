# execution/ — Live Trading Cycle

This directory implements the live Kalshi order execution backend.

## What It Does

`live.py` submits real orders to the Kalshi Trade API (production or demo environment). It is the final step in the pipeline when `--mode live` is active. Orders flow through: preflight check → order recording in SQLite → API submission via `KalshiClient`.

## Entry Point

`orchestrator.run_cycle()` calls `LiveBroker.place_entry_order()` when `settings.mode == "live"`. Before any orders, the orchestrator runs `LiveBroker.preflight()` to validate credentials and API connectivity — the cycle aborts if preflight fails.

## Key Classes

- **`LiveBroker`** (`live.py`) — guarded interface that enforces preflight before any order submission. Supports dry-run mode (records order locally but skips the API call).
- **`LivePreflightResult`** — frozen dataclass reporting whether credentials, key file, and authenticated API call all succeeded.
- **`LiveExecutionResult`** — frozen dataclass with `submitted`, `order_id`, `remote_order_id`, and `message`.

## Live vs Paper Differences

| Concern | Live | Paper |
| --- | --- | --- |
| Preflight | RSA key + authenticated API call required | None (SQLite-only) |
| Order submission | `KalshiClient.create_order()` via signed HTTP | Instant local fill at decision price |
| Fill tracking | Remote `order_id` returned | Local `position_id` only |
| Dry-run | Records locally, skips API | Records locally, skips fill |
| Exit management | Not yet implemented for live | `PaperBroker.exit_position()` evaluates exit rules |
| Settlement | Handled by Kalshi platform | `PaperBroker.settle_position()` against NWS observed highs |

## Safety Rails

1. **Preflight gate** — `ensure_ready()` is called before every order; raises `RuntimeError` if preflight hasn't passed.
2. **Dry-run flag** — `settings.dry_run` records the order locally with status `"dry-run"` and returns without touching the API.
3. **Decision guard** — `place_entry_order()` returns early if `decision.approved` is false or `sizing.contracts < 1`.
4. **Environment routing** — `settings.kalshi_environment` selects production vs demo API base URL; demo is the safer default for testing.

## Authentication

- RSA-signed requests via `KALSHI_API_KEY_ID` + private key file at `KALSHI_PRIVATE_KEY_PATH`.
- `kalshi_credentials_present()` in `data/markets.py` checks that both are configured.
- The private key file path is validated during preflight.

## Commands

```bash
# Live cycle (demo environment by default)
python main.py --mode live

# Live dry-run (evaluates + records, no API calls)
python main.py --mode live --dry-run

# Live with specific profile
python main.py --mode live --profile conservative
```

## Tests

```bash
python -m unittest tests.test_live_demo -v
```

## When Modifying This Code

- Never remove the preflight gate — it prevents accidental order submission with missing/invalid credentials.
- The `KalshiClient` lives in `data/markets.py`, not here. Changes to API request signing or endpoint paths happen there.
- Live mode does not yet support exit management or position settlement — those flows are paper-only via `PaperBroker`.
- Any new order types must record to the database before API submission for auditability.
