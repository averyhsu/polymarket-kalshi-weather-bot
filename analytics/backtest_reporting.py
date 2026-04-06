"""Presentation helpers for human-readable backtest output and saved artifacts."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from config import Settings


def build_backtest_result_package(
    raw_result: Dict[str, Any],
    settings: Settings,
    *,
    save_artifacts: bool = True,
    baseline_path: Optional[Path] = None,
    artifact_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a human-friendly package around a raw backtest result."""

    backtest = dict(raw_result["backtest"])
    generated_at = datetime.now(timezone.utc).replace(microsecond=0)
    run_id = artifact_name if artifact_name else _build_run_id(backtest, settings, generated_at)
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
    if baseline_path is not None:
        package["comparison"] = _build_comparison(package, baseline_path)
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
        + (" (refreshed)" if summary["cache_refreshed"] else "")
        + (f" | Exits: {'on' if summary.get('simulate_exits') else 'off'}"),
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
        "Candidates",
        f"- Approved candidates: {diagnostics['candidate_summary']['approved_candidates']}",
        f"- Selected candidates: {diagnostics['candidate_summary']['selected_candidates']}",
        f"- Approved but not selected: {diagnostics['candidate_summary']['approved_not_selected']}",
        f"- Rejected candidates: {diagnostics['candidate_summary']['rejected_candidates']}",
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
    exit_reasons = diagnostics.get("exit_reasons", [])
    if exit_reasons:
        lines.extend(["", "Exit Simulation"])
        lines.append(f"- Early exits: {summary.get('early_exits', 0)}")
        for item in exit_reasons:
            lines.append(f"- {item['reason']}: {item['count']}")
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
    comparison = package.get("comparison")
    if comparison is not None:
        lines.extend(
            [
                "",
                "Baseline Delta",
                f"- Baseline file: {comparison['baseline_path']}",
                f"- P&L delta: {_fmt_signed_currency(comparison['total_pnl_delta'])}",
                f"- Return delta: {_fmt_pct(comparison['return_pct_delta'])}",
                f"- Drawdown delta: {_fmt_pct(comparison['max_drawdown_delta'])}",
            ]
        )
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
        "## Candidate Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Approved candidates | {diagnostics['candidate_summary']['approved_candidates']} |",
        f"| Selected candidates | {diagnostics['candidate_summary']['selected_candidates']} |",
        f"| Approved but not selected | {diagnostics['candidate_summary']['approved_not_selected']} |",
        f"| Rejected candidates | {diagnostics['candidate_summary']['rejected_candidates']} |",
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
    exit_reasons = diagnostics.get("exit_reasons", [])
    if exit_reasons:
        lines.extend(
            [
                "",
                "## Exit Simulation",
                "",
                f"- Exit simulation: {'enabled' if summary.get('simulate_exits') else 'disabled'}",
                f"- Early exits: {summary.get('early_exits', 0)}",
                "",
                "| Reason | Count |",
                "| --- | ---: |",
            ]
        )
        for item in exit_reasons:
            lines.append(f"| {item['reason']} | {item['count']} |")
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
            "## EV Bin Performance",
            "",
            "| Bin | Trades | Win Rate | Realized P&L |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for item in diagnostics["ev_bins"]:
        lines.append(f"| {item['name']} | {item['trades']} | {_fmt_pct(item['win_rate'])} | {_fmt_signed_currency(item['realized_pnl'])} |")
    lines.extend(
        [
            "",
            "## Price Bin Performance",
            "",
            "| Bin | Trades | Win Rate | Realized P&L |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for item in diagnostics["price_bins"]:
        lines.append(f"| {item['name']} | {item['trades']} | {_fmt_pct(item['win_rate'])} | {_fmt_signed_currency(item['realized_pnl'])} |")
    lines.extend(
        [
            "",
            "## Concentration By City/Date",
            "",
            "| City/Date | Trades | Realized P&L |",
            "| --- | ---: | ---: |",
        ]
    )
    for item in diagnostics["city_date_concentration"]:
        lines.append(f"| {item['name']} | {item['trades']} | {_fmt_signed_currency(item['realized_pnl'])} |")
    lines.extend(
        [
            "",
            "## Candidate Ranking",
            "",
            "| Bucket | Ticker | Side | Rank Score | EV |",
            "| --- | --- | --- | ---: | ---: |",
        ]
    )
    for bucket_name, items in (
        ("selected", diagnostics["selected_candidates"]),
        ("approved_not_selected", diagnostics["approved_not_selected"]),
    ):
        if not items:
            lines.append(f"| {bucket_name} | None | - | 0.000 | 0.000 |")
            continue
        for item in items:
            lines.append(
                f"| {bucket_name} | {item['ticker']} | {item['side']} | {item['ranking_score']:.3f} | {item['expected_value']:.3f} |"
            )
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
    comparison = package.get("comparison")
    if comparison is not None:
        lines.extend(
            [
                "",
                "## Baseline Comparison",
                "",
                f"- Baseline file: {comparison['baseline_path']}",
                f"- Total P&L delta: {_fmt_signed_currency(comparison['total_pnl_delta'])}",
                f"- Return delta: {_fmt_pct(comparison['return_pct_delta'])}",
                f"- Max drawdown delta: {_fmt_pct(comparison['max_drawdown_delta'])}",
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
        "simulate_exits": bool(backtest.get("simulate_exits", False)),
        "early_exits": int(backtest.get("early_exits", 0)),
    }


def _build_diagnostics(backtest: Dict[str, Any]) -> Dict[str, Any]:
    trades = list(backtest["trades"])
    candidates = list(backtest.get("candidates", []))
    return {
        "by_city": _normalize_breakdown(backtest["by_city"]),
        "by_side": _normalize_breakdown(backtest["by_side"], preferred_order=["NO", "YES"]),
        "daily_equity": _summarize_daily_equity(list(backtest["daily"])),
        "top_winners": _top_trades(trades, reverse=True),
        "top_losers": _top_trades(trades, reverse=False),
        "skip_reasons": _summarize_skip_reasons(list(backtest["skipped"])),
        "candidate_summary": _summarize_candidates(candidates),
        "selected_candidates": _top_candidates(candidates, selected=True),
        "approved_not_selected": _top_candidates(candidates, selected=False, approved=True),
        "ev_bins": _bin_trade_performance(trades, key="expected_value"),
        "price_bins": _bin_trade_performance(trades, key="entry_price"),
        "city_date_concentration": _city_date_concentration(trades),
        "exit_reasons": _summarize_exit_reasons(trades),
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


def _summarize_candidates(candidates: List[Dict[str, Any]]) -> Dict[str, int]:
    approved = [item for item in candidates if bool(item.get("approved"))]
    selected = [item for item in approved if bool(item.get("selected"))]
    return {
        "approved_candidates": len(approved),
        "selected_candidates": len(selected),
        "approved_not_selected": len([item for item in approved if not bool(item.get("selected"))]),
        "rejected_candidates": len([item for item in candidates if not bool(item.get("approved"))]),
    }


def _top_candidates(candidates: List[Dict[str, Any]], *, selected: bool, approved: bool = True) -> List[Dict[str, Any]]:
    filtered = [
        item
        for item in candidates
        if bool(item.get("selected")) == selected and bool(item.get("approved")) == approved
    ]
    ranked = sorted(
        filtered,
        key=lambda item: (float(item.get("ranking_score", 0.0)), float(item.get("expected_value", 0.0))),
        reverse=True,
    )
    return [
        {
            "ticker": str(item["ticker"]),
            "side": str(item.get("side", "NONE")),
            "ranking_score": float(item.get("ranking_score", 0.0)),
            "expected_value": float(item.get("expected_value", 0.0)),
        }
        for item in ranked[:5]
    ]


def _bin_trade_performance(trades: List[Dict[str, Any]], *, key: str) -> List[Dict[str, Any]]:
    if key == "expected_value":
        bins = [
            ("0.00-0.10", 0.0, 0.10),
            ("0.10-0.20", 0.10, 0.20),
            ("0.20-0.35", 0.20, 0.35),
            ("0.35+", 0.35, float("inf")),
        ]
    else:
        bins = [
            ("0-10c", 0.0, 0.10),
            ("10-25c", 0.10, 0.25),
            ("25-50c", 0.25, 0.50),
            ("50-75c", 0.50, 0.75),
            ("75c+", 0.75, float("inf")),
        ]
    rows: List[Dict[str, Any]] = []
    for label, lower, upper in bins:
        bucket = [trade for trade in trades if lower <= float(trade[key]) < upper]
        if not bucket:
            continue
        wins = sum(1 for trade in bucket if float(trade["realized_pnl"]) > 0)
        rows.append(
            {
                "name": label,
                "trades": len(bucket),
                "win_rate": wins / len(bucket),
                "realized_pnl": sum(float(trade["realized_pnl"]) for trade in bucket),
            }
        )
    return rows


def _city_date_concentration(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for trade in trades:
        label = f"{trade['city']} {trade['target_date']}"
        grouped.setdefault(label, {"name": label, "trades": 0, "realized_pnl": 0.0})
        grouped[label]["trades"] += 1
        grouped[label]["realized_pnl"] += float(trade["realized_pnl"])
    return sorted(grouped.values(), key=lambda item: (item["trades"], abs(item["realized_pnl"])), reverse=True)[:8]


def _summarize_exit_reasons(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Counter[str] = Counter()
    for trade in trades:
        if trade.get("exited_early"):
            counts[str(trade.get("exit_reason", "unknown"))] += 1
    return [
        {"reason": reason, "count": count}
        for reason, count in sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))
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
        elif lowered.startswith("yes price") and "below minimum" in lowered:
            categories.append("YES price below minimum")
        elif lowered.startswith("spread ") and "exceeds max" in lowered:
            categories.append("spread exceeds max")
        elif "max open positions reached" in lowered:
            categories.append("max open positions reached")
        elif "higher ranked candidates filled available slots" in lowered:
            categories.append("higher ranked candidates filled available slots")
        elif "city/date exposure cap reached" in lowered:
            categories.append("city/date exposure cap reached")
        elif "historical quote unavailable" in lowered:
            categories.append("historical quote unavailable")
        elif "historical forecast unavailable" in lowered:
            categories.append("historical forecast unavailable")
        elif "kelly size below 1 contract" in lowered:
            categories.append("Kelly size below 1 contract")
        elif "insufficient backtest cash" in lowered:
            categories.append("insufficient backtest cash")
        elif "tail-risk penalty" in lowered:
            categories.append("YES tail-risk penalty applied")
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


def _build_comparison(package: Dict[str, Any], baseline_path: Path) -> Dict[str, Any]:
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    baseline_summary = baseline.get("summary", baseline.get("backtest", {}))
    summary = package["summary"]
    return {
        "baseline_path": str(Path(baseline_path).resolve()),
        "total_pnl_delta": float(summary["total_pnl"]) - float(baseline_summary["total_pnl"]),
        "return_pct_delta": float(summary["return_pct"]) - float(baseline_summary["return_pct"]),
        "max_drawdown_delta": float(summary["max_drawdown"]) - float(baseline_summary["max_drawdown"]),
    }


def _fmt_currency(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_signed_currency(value: float) -> str:
    return f"{value:+.2f}"


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _fmt_brier(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"
