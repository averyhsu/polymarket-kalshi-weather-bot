"""Tests for the local backtest dashboard."""

from __future__ import annotations

import io
import json
import shutil
import sys
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen
from uuid import uuid4
from unittest.mock import patch

import main
from analytics.backtest_dashboard import (
    build_dashboard_payload,
    create_backtest_dashboard_server,
    list_backtest_artifacts,
    load_backtest_artifact,
    resolve_backtest_artifact,
)
from analytics.backtest_reporting import build_backtest_result_package
from config import load_settings


def _settings(root: Path):
    return load_settings(
        {
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
                }
            ],
            "skipped": [
                "AAA: NO EV 0.011 below dynamic minimum 0.048",
                "CCC: max open positions reached",
            ],
            "cache_used": True,
            "cache_refreshed": False,
            "cache_dir": "C:\\cache",
        }
    }


class BacktestDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(".cache") / "unit-test-dashboard" / str(uuid4())
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings = _settings(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_package(self, *, save_artifacts: bool = True) -> Path:
        package = build_backtest_result_package(_sample_backtest(), self.settings, save_artifacts=save_artifacts)
        if save_artifacts:
            return Path(package["artifacts"]["json_path"])
        path = (self.settings.historical_data_dir / "results" / "manual.json").resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(package), encoding="utf-8")
        return path

    def test_resolve_backtest_artifact_defaults_to_latest(self) -> None:
        first = self._write_package()
        second = (self.settings.historical_data_dir / "results" / "zzz_latest.json").resolve()
        second.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")

        artifacts = list_backtest_artifacts(self.settings.historical_data_dir / "results")
        self.assertEqual(artifacts[0]["path"], str(second))
        self.assertEqual(resolve_backtest_artifact(self.settings.historical_data_dir / "results"), second)

    def test_build_dashboard_payload_uses_saved_artifact(self) -> None:
        artifact_path = self._write_package()
        package = load_backtest_artifact(artifact_path)
        payload = build_dashboard_payload(package, artifact_path)

        self.assertEqual(payload["artifact"]["path"], str(artifact_path.resolve()))
        self.assertEqual(payload["summary"]["start_date"], "2026-03-01")
        self.assertIn("diagnostics", payload)

    def test_dashboard_server_serves_artifact_list_and_payload(self) -> None:
        artifact_path = self._write_package()
        server, url = create_backtest_dashboard_server(
            results_dir=self.settings.historical_data_dir / "results",
            artifact_path=artifact_path,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            artifacts_payload = json.loads(urlopen(f"{url}api/artifacts").read().decode("utf-8"))
            artifact_payload = json.loads(
                urlopen(f"{url}api/artifact?path={quote(str(artifact_path))}").read().decode("utf-8")
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(artifacts_payload["default_artifact_path"], str(artifact_path.resolve()))
        self.assertEqual(artifact_payload["artifact"]["name"], artifact_path.name)
        self.assertEqual(artifact_payload["summary"]["entries_executed"], 4)


class MainDashboardTests(unittest.TestCase):
    def test_dashboard_cli_launches_without_touching_orchestrator(self) -> None:
        root = Path(".cache") / "unit-test-main-dashboard" / str(uuid4())
        root.mkdir(parents=True, exist_ok=True)
        try:
            settings = _settings(root)
            stdout = io.StringIO()

            class _KeyboardInterruptEvent:
                def wait(self, _timeout):
                    raise KeyboardInterrupt

            with patch("main.load_settings", return_value=settings), patch("main.launch_backtest_dashboard", return_value="http://127.0.0.1:9999/") as launch_mock, patch("main.WeatherTradingOrchestrator") as orchestrator_cls, patch("main.threading.Event", return_value=_KeyboardInterruptEvent()):
                with patch.object(sys, "argv", ["main.py", "--dashboard", "--dashboard-no-open"]):
                    with redirect_stdout(stdout):
                        exit_code = main.main()
        finally:
            shutil.rmtree(root, ignore_errors=True)

        self.assertEqual(exit_code, 0)
        self.assertIn("Backtest dashboard serving at http://127.0.0.1:9999/", stdout.getvalue())
        launch_mock.assert_called_once()
        orchestrator_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
