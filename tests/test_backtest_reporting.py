"""Tests for backtest presentation and artifact generation."""

from __future__ import annotations

import io
import json
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import main
from analytics.backtest_reporting import build_backtest_result_package, render_backtest_terminal_report
from config import load_settings


def _settings(root: Path):
    return load_settings(
        {
            "initial_balance": 100.0,
            "historical_data_dir": root / "historical_data",
            "cache_dir": root / ".cache",
            "db_path": root / "bot.sqlite3",
        }
    )


def _sample_backtest() -> dict:
    return {
        "backtest": {
            "start_date": "2026-03-01",
            "end_date": "2026-03-03",
            "entry_time_utc": "20:00",
            "markets_considered": 12,
            "quotes_loaded": 12,
            "forecasts_loaded": 12,
            "entries_attempted": 4,
            "entries_executed": 4,
            "settled_positions": 4,
            "final_settlements": 0,
            "starting_balance": 100.0,
            "ending_balance": 97.25,
            "total_pnl": -2.75,
            "return_pct": -0.0275,
            "total_fees": 0.18,
            "win_rate": 0.5,
            "max_drawdown": 0.06,
            "brier_score": 0.171,
            "by_city": {
                "chicago": {"trades": 2, "wins": 1, "realized_pnl": -1.80},
                "denver": {"trades": 2, "wins": 1, "realized_pnl": -0.95},
            },
            "by_side": {
                "NO": {"trades": 3, "wins": 2, "realized_pnl": 0.45},
                "YES": {"trades": 1, "wins": 0, "realized_pnl": -3.20},
            },
            "daily": [
                {
                    "cycle_day": "2026-02-28",
                    "target_date": "2026-03-01",
                    "evaluated_markets": 4,
                    "realized_pnl": 0.0,
                    "cash": 95.00,
                    "open_positions": 4,
                    "equity": 99.40,
                },
                {
                    "cycle_day": "2026-03-01",
                    "target_date": "2026-03-02",
                    "evaluated_markets": 4,
                    "realized_pnl": -1.50,
                    "cash": 96.00,
                    "open_positions": 2,
                    "equity": 96.00,
                },
                {
                    "cycle_day": "2026-03-02",
                    "target_date": "2026-03-03",
                    "evaluated_markets": 4,
                    "realized_pnl": -1.25,
                    "cash": 97.25,
                    "open_positions": 0,
                    "equity": 97.25,
                },
            ],
            "trades": [
                {
                    "ticker": "A",
                    "city": "chicago",
                    "target_date": "2026-03-01",
                    "side": "NO",
                    "contracts": 2,
                    "entry_price": 0.80,
                    "predicted_probability_yes": 0.10,
                    "expected_value": 0.12,
                    "actual_high_f": 45.0,
                    "settled_yes": 0.0,
                    "payout": 1.0,
                    "realized_pnl": 0.36,
                    "fees": 0.02,
                },
                {
                    "ticker": "B",
                    "city": "chicago",
                    "target_date": "2026-03-01",
                    "side": "YES",
                    "contracts": 8,
                    "entry_price": 0.20,
                    "predicted_probability_yes": 0.62,
                    "expected_value": 0.40,
                    "actual_high_f": 45.0,
                    "settled_yes": 0.0,
                    "payout": 0.0,
                    "realized_pnl": -1.68,
                    "fees": 0.08,
                },
                {
                    "ticker": "C",
                    "city": "denver",
                    "target_date": "2026-03-02",
                    "side": "NO",
                    "contracts": 2,
                    "entry_price": 0.72,
                    "predicted_probability_yes": 0.08,
                    "expected_value": 0.18,
                    "actual_high_f": 60.0,
                    "settled_yes": 0.0,
                    "payout": 1.0,
                    "realized_pnl": 0.54,
                    "fees": 0.02,
                },
                {
                    "ticker": "D",
                    "city": "denver",
                    "target_date": "2026-03-02",
                    "side": "NO",
                    "contracts": 2,
                    "entry_price": 0.70,
                    "predicted_probability_yes": 0.09,
                    "expected_value": 0.16,
                    "actual_high_f": 60.0,
                    "settled_yes": 1.0,
                    "payout": 0.0,
                    "realized_pnl": -1.97,
                    "fees": 0.06,
                },
            ],
            "skipped": [
                "AAA: NO EV 0.011 below dynamic minimum 0.048",
                "BBB: YES EV 0.003 below dynamic minimum 0.060",
                "CCC: max open positions reached",
                "DDD: spread 6c exceeds max 5c; max open positions reached",
            ],
            "cache_used": True,
            "cache_refreshed": False,
            "cache_dir": "C:\\cache",
        }
    }


class BacktestReportingTests(unittest.TestCase):
    def test_package_saves_json_and_markdown_artifacts(self) -> None:
        root = Path(".cache") / "unit-test-reporting" / str(uuid4())
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = _settings(root)
            package = build_backtest_result_package(_sample_backtest(), settings, save_artifacts=True)
            artifacts = package["artifacts"]
            json_path = Path(artifacts["json_path"])
            markdown_path = Path(artifacts["markdown_path"])

            self.assertTrue(json_path.exists())
            self.assertTrue(markdown_path.exists())
            self.assertIn("backtest_2026-03-01_2026-03-03_balanced_two_sided_2000utc_", package["run"]["run_id"])

            saved_json = json.loads(json_path.read_text(encoding="utf-8"))
            saved_markdown = markdown_path.read_text(encoding="utf-8")
            self.assertEqual(saved_json["summary"]["start_date"], "2026-03-01")
            self.assertIn("## Trade Ledger", saved_markdown)
            self.assertIn("| B | 2026-03-01 | chicago | YES |", saved_markdown)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_terminal_report_groups_skip_reasons_and_highlights_trades(self) -> None:
        root = Path(".cache") / "unit-test-reporting" / str(uuid4())
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = _settings(root)
            package = build_backtest_result_package(_sample_backtest(), settings, save_artifacts=False)
            report = render_backtest_terminal_report(package)
        finally:
            shutil.rmtree(root, ignore_errors=True)

        self.assertIn("Backtest Summary", report)
        self.assertIn("EV below dynamic minimum: 2", report)
        self.assertIn("max open positions reached: 2", report)
        self.assertIn("B (YES 8): -1.68", report)
        self.assertIn("C (NO 2): +0.54", report)


class MainBacktestOutputTests(unittest.TestCase):
    def test_default_backtest_prints_human_summary(self) -> None:
        root = Path(".cache") / "unit-test-main" / str(uuid4())
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = _settings(root)
            stdout = io.StringIO()
            with patch("main.load_settings", return_value=settings), patch("main.WeatherTradingOrchestrator") as orchestrator_cls:
                orchestrator_cls.return_value.backtest.return_value = _sample_backtest()
                with patch.object(sys, "argv", ["main.py", "--backtest", "--backtest-days", "1"]):
                    with redirect_stdout(stdout):
                        exit_code = main.main()
        finally:
            shutil.rmtree(root, ignore_errors=True)

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Backtest Summary", output)
        self.assertIn("JSON artifact:", output)
        self.assertFalse(output.lstrip().startswith("{"))

    def test_raw_backtest_flag_prints_json_package(self) -> None:
        root = Path(".cache") / "unit-test-main" / str(uuid4())
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = _settings(root)
            stdout = io.StringIO()
            with patch("main.load_settings", return_value=settings), patch("main.WeatherTradingOrchestrator") as orchestrator_cls:
                orchestrator_cls.return_value.backtest.return_value = _sample_backtest()
                with patch.object(sys, "argv", ["main.py", "--backtest", "--backtest-days", "1", "--backtest-raw", "--backtest-no-save"]):
                    with redirect_stdout(stdout):
                        exit_code = main.main()
        finally:
            shutil.rmtree(root, ignore_errors=True)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertIn("summary", payload)
        self.assertIn("artifacts", payload)
        self.assertFalse(payload["artifacts"]["saved"])


if __name__ == "__main__":
    unittest.main()
