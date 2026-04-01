"""Presentation helpers for human-readable backtest output and saved artifacts."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from config import Settings


def build_backtest_result_package(
    raw_result: Dict[str, Any],
    settings: Settings,
    *,
    save_artifacts: bool = True,
) -> Dict[str, Any]:
    """Build a human-friendly package around a raw backtest result."""

    backtest = dict(raw_result["backtest"])
    generated_at = datetime.now(timezone.utc).replace(microsecond=0)
    run_id = _build_run_id(backtest, settings, generated_at)
    results_dir = (settings.historical_data_dir / "results").resolve()
    json_path = results_dir / f"{run_id}.json"
    markdown_path = results_dir / f"{run_id}.md"

    package: Dict[str, Any] = {
        "run": {
            "run_id": run_id,
            "generated_at_utc": generated_at.isoformat(),
            "profile": settings.profile,
            "no_only": settings.no_only,
            "entry_time_utc": backtest["entry_time_utc"],
        },
        "summary": _build_summary(backtest),
        "diagnostics": _build_diagnostics(backtest),
        "artifacts": {
            "saved": save_artifacts,
            "results_dir": str(results_dir),
            "json_path": str(json_path) if save_artifacts else None,
            "markdown_path": str(markdown_path) if save_artifacts else None,
        },
        "details": {
            "backtest": backtest,
        },
    }
    if save_artifacts:
        _write_artifacts(package, json_path=json_path, markdown_path=markdown_path)
    return package


def render_backtest_terminal_report(package: Dict[str, Any]) -> str:
    """Render a concise terminal summary for a backtest run."""

    summary = package["summary"]
    diagnostics = package["diagnostics"]
    artifacts = package["artifacts"]
    run = package["run"]
    lines = [
        "Backtest Summary",
        f"Range: {summary['start_date']} to {summary['end_date']} | Entry: {summary['entry_time_utc']} UTC | "
        f"Profile: {run['profile']} | Sides: {'NO-only' if run['no_only'] else 'YES+NO'}",
        f"Cache: {'reused' if summary['cache_used'] else 'off'}"
        + (" (refreshed)" if summary["cache_refreshed"] else ""),
        "",
        "Performance",
        f"- Starting balance: {_fmt_currency(summary['starting_balance'])}",
        f"- Ending balance: {_fmt_currency(summary['ending_balance'])}",
        f"- Total P&L: {_fmt_signed_currency(summary['total_pnl'])}",
        f"- Return: {_fmt_pct(summary['return_pct'])}",
        f"- Max drawdown: {_fmt_pct(summary['max_drawdown'])}",
        f"- Win rate: {_fmt_pct(summary['win_rate'])}",
        f"- Brier score: {_fmt_brier(summary['brier_score'])}",
        f"- Total fees: {_fmt_currency(summary['total_fees'])}",
        "",
        "Counts",
        f"- Markets considered: {summary['markets_considered']}",
        f"- Quotes loaded: {summary['quotes_loaded']}",
        f"- Forecasts loaded: {summary['forecasts_loaded']}",
        f"- Entries attempted/executed: {summary['entries_attempted']}/{summary['entries_executed']}",
        f"- Settled positions: {summary['settled_positions']}",
        f"- Final settlements: {summary['final_settlements']}",
        "",
        "By Side",
    ]
    lines.extend(_render_breakdown(diagnostics["by_side"]))
    lines.extend(["", "By City"])
    lines.extend(_render_breakdown(diagnostics["by_city"]))
    lines.extend(["", "Daily Equity"])
    daily = diagnostics["daily_equity"]
    lines.extend(
        [
            f"- Cycle days: {daily['cycle_days']}",
            f"- Best equity: {_fmt_currency(daily['best_equity']['equity'])} on {daily['best_equity']['cycle_day']}",
            f"- Worst equity: {_fmt_currency(daily['worst_equity']['equity'])} on {daily['worst_equity']['cycle_day']}",
            f"- Positive realized days: {daily['positive_realized_days']}",
            f"- Negative realized days: {daily['negative_realized_days']}",
        ]
    )
    lines.extend(["", "Top Winners"])
    lines.extend(_render_trade_lines(diagnostics["top_winners"], fallback="No winning trades"))
    lines.extend(["", "Top Losers"])
    lines.extend(_render_trade_lines(diagnostics["top_losers"], fallback="No losing trades"))
    lines.extend(["", "Skip Reasons"])
    lines.extend(_render_skip_lines(diagnostics["skip_reasons"]))
    lines.extend(["", "Artifacts"])
    if artifacts["saved"]:
        lines.extend(
            [
                f"- JSON artifact: {artifacts['json_path']}",
                f"- Markdown report: {artifacts['markdown_path']}",
            ]
        )
    else:
        lines.append(f"- Saving disabled for this run. Results directory: {artifacts['results_dir']}")
    return "\n".join(lines)


def render_backtest_markdown_report(package: Dict[str, Any]) -> str:
    """Render a Markdown artifact for later comparison."""

    summary = package["summary"]
    diagnostics = package["diagnostics"]
    artifacts = package["artifacts"]
    run = package["run"]
    backtest = package["details"]["backtest"]
    lines = [
        f"# Backtest Report: {run['run_id']}",
        "",
        f"- Generated: {run['generated_at_utc']}",
        f"- Date range: {summary['start_date']} to {summary['end_date']}",
        f"- Entry time: {summary['entry_time_utc']} UTC",
        f"- Profile: {run['profile']}",
        f"- Side mode: {'NO-only' if run['no_only'] else 'YES+NO'}",
        f"- Cache used: {summary['cache_used']}",
        f"- Cache refreshed: {summary['cache_refreshed']}",
        "",
        "## Performance",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Starting balance | {_fmt_currency(summary['starting_balance'])} |",
        f"| Ending balance | {_fmt_currency(summary['ending_balance'])} |",
        f"| Total P&L | {_fmt_signed_currency(summary['total_pnl'])} |",
        f"| Return | {_fmt_pct(summary['return_pct'])} |",
        f"| Max drawdown | {_fmt_pct(summary['max_drawdown'])} |",
        f"| Win rate | {_fmt_pct(summary['win_rate'])} |",
        f"| Brier score | {_fmt_brier(summary['brier_score'])} |",
        f"| Total fees | {_fmt_currency(summary['total_fees'])} |",
        "",
        "## Counts",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Markets considered | {summary['markets_considered']} |",
        f"| Quotes loaded | {summary['quotes_loaded']} |",
        f"| Forecasts loaded | {summary['forecasts_loaded']} |",
        f"| Entries attempted | {summary['entries_attempted']} |",
        f"| Entries executed | {summary['entries_executed']} |",
        f"| Settled positions | {summary['settled_positions']} |",
        f"| Final settlements | {summary['final_settlements']} |",
        "",
        "## By Side",
        "",
        "| Side | Trades | Wins | Win Rate | Realized P&L |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(_render_breakdown_table(diagnostics["by_side"]))
    lines.extend(
        [
            "",
            "## By City",
            "",
            "| City | Trades | Wins | Win Rate | Realized P&L |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    lines.extend(_render_breakdown_table(diagnostics["by_city"]))
    daily = diagnostics["daily_equity"]
    lines.extend(
        [
            "",
            "## Daily Equity Summary",
            "",
            f"- Cycle days: {daily['cycle_days']}",
            f"- Best equity: {_fmt_currency(daily['best_equity']['equity'])} on {daily['best_equity']['cycle_day']}",
            f"- Worst equity: {_fmt_currency(daily['worst_equity']['equity'])} on {daily['worst_equity']['cycle_day']}",
            f"- Positive realized days: {daily['positive_realized_days']}",
            f"- Negative realized days: {daily['negative_realized_days']}",
            "",
            "## Top Winners",
            "",
            "| Ticker | Date | Side | Contracts | P&L | EV |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ]
    )
    lines.extend(_render_trade_table(diagnostics["top_winners"]))
    lines.extend(
        [
            "",
            "## Top Losers",
            "",
            "| Ticker | Date | Side | Contracts | P&L | EV |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ]
    )
    lines.extend(_render_trade_table(diagnostics["top_losers"]))
    lines.extend(
        [
            "",
            "## Skip Reasons",
            "",
            "| Reason | Count |",
            "| --- | ---: |",
        ]
    )
    for item in diagnostics["skip_reasons"]:
        lines.append(f"| {item['reason']} | {item['count']} |")
    lines.extend(
        [
            "",
            "## Trade Ledger",
            "",
            "| Ticker | Date | City | Side | Contracts | Entry | Predicted YES | Payout | P&L |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for trade in backtest["trades"]:
        lines.append(
            f"| {trade['ticker']} | {trade['target_date']} | {trade['city']} | {trade['side']} | "
            f"{trade['contracts']} | {float(trade['entry_price']):.2f} | {float(trade['predicted_probability_yes']):.3f} | "
            f"{float(trade['payout']):.2f} | {float(trade['realized_pnl']):.2f} |"
        )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- JSON artifact: {artifacts['json_path']}",
            f"- Markdown report: {artifacts['markdown_path']}",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_artifacts(package: Dict[str, Any], *, json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(package, indent=2, sort_keys=True, default=str), encoding="utf-8")
    markdown_path.write_text(render_backtest_markdown_report(package), encoding="utf-8")


def _build_run_id(backtest: Dict[str, Any], settings: Settings, generated_at: datetime) -> str:
    side_label = "no_only" if settings.no_only else "two_sided"
    entry_label = str(backtest["entry_time_utc"]).replace(":", "") + "utc"
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    return "_".join(
        [
            "backtest",
            str(backtest["start_date"]),
            str(backtest["end_date"]),
            settings.profile,
            side_label,
            entry_label,
            timestamp,
        ]
    )


def _build_summary(backtest: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "start_date": backtest["start_date"],
        "end_date": backtest["end_date"],
        "entry_time_utc": backtest["entry_time_utc"],
        "markets_considered": int(backtest["markets_considered"]),
        "quotes_loaded": int(backtest["quotes_loaded"]),
        "forecasts_loaded": int(backtest["forecasts_loaded"]),
        "entries_attempted": int(backtest["entries_attempted"]),
        "entries_executed": int(backtest["entries_executed"]),
        "settled_positions": int(backtest["settled_positions"]),
        "final_settlements": int(backtest["final_settlements"]),
        "starting_balance": float(backtest["starting_balance"]),
        "ending_balance": float(backtest["ending_balance"]),
        "total_pnl": float(backtest["total_pnl"]),
        "return_pct": float(backtest["return_pct"]),
        "total_fees": float(backtest["total_fees"]),
        "win_rate": float(backtest["win_rate"]),
        "max_drawdown": float(backtest["max_drawdown"]),
        "brier_score": None if backtest["brier_score"] is None else float(backtest["brier_score"]),
        "cache_used": bool(backtest["cache_used"]),
        "cache_refreshed": bool(backtest["cache_refreshed"]),
    }


def _build_diagnostics(backtest: Dict[str, Any]) -> Dict[str, Any]:
    trades = list(backtest["trades"])
    return {
        "by_city": _normalize_breakdown(backtest["by_city"]),
        "by_side": _normalize_breakdown(backtest["by_side"], preferred_order=["NO", "YES"]),
        "daily_equity": _summarize_daily_equity(list(backtest["daily"])),
        "top_winners": _top_trades(trades, reverse=True),
        "top_losers": _top_trades(trades, reverse=False),
        "skip_reasons": _summarize_skip_reasons(list(backtest["skipped"])),
    }


def _normalize_breakdown(
    raw_breakdown: Dict[str, Dict[str, Any]],
    *,
    preferred_order: Iterable[str] | None = None,
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    order_map = {name: index for index, name in enumerate(preferred_order or [])}
    for name, payload in raw_breakdown.items():
        trades = int(payload["trades"])
        wins = int(payload["wins"])
        entries.append(
            {
                "name": name,
                "trades": trades,
                "wins": wins,
                "win_rate": (wins / trades) if trades else 0.0,
                "realized_pnl": float(payload["realized_pnl"]),
            }
        )
    return sorted(entries, key=lambda item: (order_map.get(item["name"], 999), item["name"]))


def _summarize_daily_equity(daily: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not daily:
        empty = {"cycle_day": None, "equity": 0.0}
        return {
            "cycle_days": 0,
            "best_equity": empty,
            "worst_equity": empty,
            "positive_realized_days": 0,
            "negative_realized_days": 0,
        }
    best = max(daily, key=lambda row: float(row["equity"]))
    worst = min(daily, key=lambda row: float(row["equity"]))
    return {
        "cycle_days": len(daily),
        "best_equity": {"cycle_day": best["cycle_day"], "equity": float(best["equity"])},
        "worst_equity": {"cycle_day": worst["cycle_day"], "equity": float(worst["equity"])},
        "positive_realized_days": sum(1 for row in daily if float(row["realized_pnl"]) > 0),
        "negative_realized_days": sum(1 for row in daily if float(row["realized_pnl"]) < 0),
    }


def _top_trades(trades: List[Dict[str, Any]], *, reverse: bool) -> List[Dict[str, Any]]:
    if reverse:
        filtered = [trade for trade in trades if float(trade["realized_pnl"]) > 0.0]
    else:
        filtered = [trade for trade in trades if float(trade["realized_pnl"]) < 0.0]
    ranked = sorted(
        filtered,
        key=lambda trade: float(trade["realized_pnl"]),
        reverse=reverse,
    )
    return [
        {
            "ticker": trade["ticker"],
            "target_date": trade["target_date"],
            "city": trade["city"],
            "side": trade["side"],
            "contracts": int(trade["contracts"]),
            "realized_pnl": float(trade["realized_pnl"]),
            "expected_value": float(trade["expected_value"]),
        }
        for trade in ranked[:5]
    ]


def _summarize_skip_reasons(skipped: List[str]) -> List[Dict[str, Any]]:
    counts: Counter[str] = Counter()
    for item in skipped:
        for reason in _extract_reason_categories(item):
            counts[reason] += 1
    return [
        {"reason": reason, "count": count}
        for reason, count in sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))
    ][:8]


def _extract_reason_categories(item: str) -> List[str]:
    reason_text = item.split(": ", 1)[1] if ": " in item else item
    categories: List[str] = []
    for reason in (part.strip() for part in reason_text.split(";")):
        if not reason:
            continue
        lowered = reason.lower()
        if "below dynamic minimum" in lowered:
            categories.append("EV below dynamic minimum")
        elif lowered.startswith("spread ") and "exceeds max" in lowered:
            categories.append("spread exceeds max")
        elif "max open positions reached" in lowered:
            categories.append("max open positions reached")
        elif "historical quote unavailable" in lowered:
            categories.append("historical quote unavailable")
        elif "historical forecast unavailable" in lowered:
            categories.append("historical forecast unavailable")
        elif "kelly size below 1 contract" in lowered:
            categories.append("Kelly size below 1 contract")
        elif "insufficient backtest cash" in lowered:
            categories.append("insufficient backtest cash")
        else:
            categories.append(reason)
    return categories


def _render_breakdown(items: List[Dict[str, Any]]) -> List[str]:
    if not items:
        return ["- No data"]
    return [
        f"- {item['name']}: trades={item['trades']}, wins={item['wins']}, "
        f"win_rate={_fmt_pct(item['win_rate'])}, pnl={_fmt_signed_currency(item['realized_pnl'])}"
        for item in items
    ]


def _render_trade_lines(items: List[Dict[str, Any]], *, fallback: str) -> List[str]:
    if not items:
        return [f"- {fallback}"]
    return [
        f"- {trade['ticker']} ({trade['side']} {trade['contracts']}): "
        f"{_fmt_signed_currency(trade['realized_pnl'])} on {trade['target_date']} | EV {trade['expected_value']:.3f}"
        for trade in items
    ]


def _render_skip_lines(items: List[Dict[str, Any]]) -> List[str]:
    if not items:
        return ["- No skips recorded"]
    return [f"- {item['reason']}: {item['count']}" for item in items]


def _render_breakdown_table(items: List[Dict[str, Any]]) -> List[str]:
    if not items:
        return ["| No data | 0 | 0 | 0.0% | +0.00 |"]
    return [
        f"| {item['name']} | {item['trades']} | {item['wins']} | {_fmt_pct(item['win_rate'])} | "
        f"{_fmt_signed_currency(item['realized_pnl'])} |"
        for item in items
    ]


def _render_trade_table(items: List[Dict[str, Any]]) -> List[str]:
    if not items:
        return ["| None | - | - | 0 | +0.00 | 0.000 |"]
    return [
        f"| {trade['ticker']} | {trade['target_date']} | {trade['side']} | {trade['contracts']} | "
        f"{_fmt_signed_currency(trade['realized_pnl'])} | {trade['expected_value']:.3f} |"
        for trade in items
    ]


def _fmt_currency(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_signed_currency(value: float) -> str:
    return f"{value:+.2f}"


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _fmt_brier(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"
