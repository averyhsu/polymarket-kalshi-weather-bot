"""CLI entrypoint for the Kalshi weather bot."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, timedelta
from typing import Any, Dict

from config import load_settings
from orchestrator import WeatherTradingOrchestrator


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser."""

    parser = argparse.ArgumentParser(description="Kalshi weather trading bot")
    parser.add_argument("--mode", choices=["paper", "live"], help="Execution mode")
    parser.add_argument("--profile", choices=["conservative", "balanced", "aggressive"], help="Risk profile")
    parser.add_argument("--balance", type=float, help="Initial paper balance override")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate decisions without placing orders")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--stats", action="store_true", help="Print P&L and calibration stats")
    parser.add_argument("--replay", action="store_true", help="Replay decisions from stored snapshots")
    parser.add_argument("--backtest", action="store_true", help="Run a historical day-ahead backtest")
    parser.add_argument("--warm-backtest-cache", action="store_true", help="Download and cache a historical backtest dataset")
    parser.add_argument("--backtest-start", type=str, help="Inclusive target-date start for backtests (YYYY-MM-DD)")
    parser.add_argument("--backtest-end", type=str, help="Inclusive target-date end for backtests (YYYY-MM-DD)")
    parser.add_argument("--backtest-days", type=int, help="Shortcut for a recent backtest window ending yesterday")
    parser.add_argument("--backtest-entry-hour-utc", type=int, default=20, help="Entry hour in UTC for backtests")
    parser.add_argument("--backtest-entry-minute-utc", type=int, default=0, help="Entry minute in UTC for backtests")
    parser.add_argument("--backtest-no-cache", action="store_true", help="Ignore the local historical cache for this run")
    parser.add_argument("--backtest-refresh-cache", action="store_true", help="Refetch and overwrite the cached historical dataset")
    parser.add_argument("--positions", action="store_true", help="Show open positions")
    parser.add_argument("--close-all-paper", action="store_true", help="Close all open paper positions")
    return parser


def configure_logging(verbose: bool) -> None:
    """Configure logging output."""

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)


def _print_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _parse_iso_date(raw_value: str) -> date:
    return date.fromisoformat(raw_value)


def _resolve_backtest_window(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[date, date]:
    if args.backtest_days:
        end_date = date.today() - timedelta(days=1)
        start_date = end_date - timedelta(days=max(args.backtest_days - 1, 0))
        return start_date, end_date
    if not args.backtest_start or not args.backtest_end:
        parser.error("--backtest requires either --backtest-days or both --backtest-start and --backtest-end")
    return _parse_iso_date(args.backtest_start), _parse_iso_date(args.backtest_end)


def main() -> int:
    """Run the CLI."""

    parser = build_parser()
    args = parser.parse_args()
    configure_logging(bool(args.verbose))

    overrides = {
        "mode": args.mode,
        "profile": args.profile,
        "initial_balance": args.balance,
        "dry_run": args.dry_run or None,
        "verbose": args.verbose or None,
    }
    settings = load_settings(overrides)
    orchestrator = WeatherTradingOrchestrator(settings)

    if args.stats:
        _print_json(orchestrator.stats())
        return 0
    if args.replay:
        _print_json(orchestrator.replay())
        return 0
    if args.backtest or args.warm_backtest_cache:
        start_date, end_date = _resolve_backtest_window(args, parser)
    if args.warm_backtest_cache:
        _print_json(
            orchestrator.warm_backtest_cache(
                start_date=start_date,
                end_date=end_date,
                entry_hour_utc=args.backtest_entry_hour_utc,
                entry_minute_utc=args.backtest_entry_minute_utc,
                refresh_cache=args.backtest_refresh_cache,
            )
        )
        return 0
    if args.backtest:
        _print_json(
            orchestrator.backtest(
                start_date=start_date,
                end_date=end_date,
                entry_hour_utc=args.backtest_entry_hour_utc,
                entry_minute_utc=args.backtest_entry_minute_utc,
                use_cache=not args.backtest_no_cache,
                refresh_cache=args.backtest_refresh_cache,
            )
        )
        return 0
    if args.positions:
        positions = [
            {
                "ticker": position.ticker,
                "city": position.city_key,
                "side": position.side,
                "contracts": position.contracts,
                "avg_price": position.avg_price,
                "current_mark": position.current_mark,
                "unrealized_pnl": position.unrealized_pnl,
            }
            for position in orchestrator.positions()
        ]
        _print_json({"positions": positions})
        return 0
    if args.close_all_paper:
        closed = orchestrator.close_all_paper()
        _print_json({"closed_positions": closed})
        return 0

    summary = orchestrator.run_cycle()
    _print_json(
        {
            "mode": settings.mode,
            "profile": settings.profile,
            "scanned_markets": summary.scanned_markets,
            "forecasts_loaded": summary.forecasts_loaded,
            "entries_attempted": summary.entries_attempted,
            "entries_executed": summary.entries_executed,
            "exits_executed": summary.exits_executed,
            "settlements": summary.settlements,
            "skipped": summary.skipped[:20],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
