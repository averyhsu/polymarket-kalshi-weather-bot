"""Replay and historical backtest helpers."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from config import Settings
from core.decision import choose_trade
from core.probability import estimate_bucket_probability
from core.risk import assess_entry_risk
from core.sizing import calculate_kelly_size
from data.climatology import bucket_probability_from_climatology
from data.markets import (
    HistoricalMarketDefinition,
    MarketQuote,
    deserialize_historical_market,
    deserialize_market_quote,
    fetch_historical_market_definitions,
    fetch_historical_market_quote,
    serialize_historical_market,
    serialize_market_quote,
)
from data.weather import (
    SourceHealth,
    deserialize_forecast_snapshot,
    fetch_historical_forecast_snapshot,
    serialize_forecast_snapshot,
)
from db.models import Database


@dataclass
class BacktestPosition:
    """Open historical position held until settlement."""

    ticker: str
    city_key: str
    target_date: date
    side: str
    contracts: int
    entry_price: float
    entry_fees: float
    probability_yes: float
    expected_value: float
    bucket_low: float
    bucket_high: float
    actual_high_f: float


class HistoricalBacktestCache:
    """Disk-backed cache for historical backtest datasets."""

    def __init__(self, settings: Settings, *, entry_hour_utc: int, entry_minute_utc: int):
        self.settings = settings
        self.entry_hour_utc = entry_hour_utc
        self.entry_minute_utc = entry_minute_utc
        self.base_dir = settings.cache_dir / "historical_backtest"
        self.markets_dir = self.base_dir / "markets"
        self.quotes_dir = self.base_dir / "quotes"
        self.forecasts_dir = self.base_dir / "forecasts"
        for path in (self.markets_dir, self.quotes_dir, self.forecasts_dir):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _cached_at() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _entry_stamp(self) -> str:
        return f"{self.entry_hour_utc:02d}{self.entry_minute_utc:02d}Z"

    def _markets_path(self, target_date: date) -> Path:
        return self.markets_dir / f"{target_date.isoformat()}.json"

    def _quotes_path(self, ticker: str) -> Path:
        return self.quotes_dir / self._entry_stamp() / f"{ticker}.json"

    def _forecasts_path(self, city_key: str, target_date: date) -> Path:
        return self.forecasts_dir / self._entry_stamp() / f"{city_key}_{target_date.isoformat()}.json"

    def load_markets(self, target_date: date) -> Optional[List[HistoricalMarketDefinition]]:
        path = self._markets_path(target_date)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        try:
            return [deserialize_historical_market(item) for item in payload.get("markets", [])]
        except (KeyError, TypeError, ValueError):
            return None

    def save_markets(self, target_date: date, markets: List[HistoricalMarketDefinition]) -> None:
        path = self._markets_path(target_date)
        payload = {
            "target_date": target_date.isoformat(),
            "cached_at": self._cached_at(),
            "markets": [serialize_historical_market(market) for market in markets],
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def load_quote(self, ticker: str) -> Optional[MarketQuote]:
        path = self._quotes_path(ticker)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if payload.get("quote") is None:
            return None
        try:
            return deserialize_market_quote(payload["quote"])
        except (KeyError, TypeError, ValueError):
            return None

    def save_quote(self, ticker: str, quote: Optional[MarketQuote]) -> None:
        path = self._quotes_path(ticker)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ticker": ticker,
            "cached_at": self._cached_at(),
            "quote": None if quote is None else serialize_market_quote(quote),
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def load_forecast(self, city_key: str, target_date: date):
        path = self._forecasts_path(city_key, target_date)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if payload.get("forecast") is None:
            return None
        try:
            return deserialize_forecast_snapshot(payload["forecast"])
        except (KeyError, TypeError, ValueError):
            return None

    def save_forecast(self, city_key: str, target_date: date, forecast) -> None:
        path = self._forecasts_path(city_key, target_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "city_key": city_key,
            "target_date": target_date.isoformat(),
            "cached_at": self._cached_at(),
            "forecast": None if forecast is None else serialize_forecast_snapshot(forecast),
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def replay_from_database(settings: Settings, database: Database) -> Dict[str, object]:
    """Re-run entry logic on stored forecasts and latest snapshots."""

    forecasts = database.recent_forecasts()
    latest_by_city_date = {(row["city_key"], row["target_date"]): row for row in forecasts}
    trades: List[Dict[str, object]] = []

    for snapshot in database.latest_snapshots():
        key = (snapshot["city_key"], snapshot["target_date"])
        forecast = latest_by_city_date.get(key)
        if forecast is None:
            continue
        metadata = json.loads(snapshot["metadata"])
        source_health = SourceHealth(
            score=float(metadata.get("source_health_score", 1.0)),
            status=str(metadata.get("source_health_status", "HEALTHY")),
            success=1.0,
            freshness=1.0,
            completeness=1.0,
            consistency=1.0,
        )
        market = MarketQuote(
            ticker=snapshot["ticker"],
            series_ticker=str(metadata.get("series_ticker", "")),
            city_key=snapshot["city_key"],
            city_name=str(metadata.get("city_name", snapshot["city_key"])),
            target_date=date.fromisoformat(snapshot["target_date"]),
            bucket_low=float(metadata.get("bucket_low", float("-inf"))),
            bucket_high=float(metadata.get("bucket_high", float("inf"))),
            strike_label=str(metadata.get("strike_label", "")),
            title=str(metadata.get("title", snapshot["ticker"])),
            subtitle=str(metadata.get("subtitle", "")),
            yes_bid=float(snapshot["yes_bid"]),
            yes_ask=float(snapshot["yes_ask"]),
            no_bid=float(snapshot["no_bid"]),
            no_ask=float(snapshot["no_ask"]),
            last_price=float(metadata.get("last_price", 0.5)),
            volume=float(snapshot["volume"]),
            open_interest=float(metadata.get("open_interest", 0.0)),
            updated_time=None,
            status="open",
        )
        p_climo = bucket_probability_from_climatology(
            market.city_key,
            market.target_date,
            market.bucket_low,
            market.bucket_high,
        )
        probability = estimate_bucket_probability(
            mean_temp_f=float(forecast["mean_temp"]),
            sigma_temp_f=float(forecast["sigma_temp"]),
            bucket_low=market.bucket_low,
            bucket_high=market.bucket_high,
            p_climo=p_climo,
            pseudo_count=settings.pseudo_count,
            boundary_buffer_f=settings.boundary_buffer_f,
            min_sigma_f=settings.min_sigma_f,
        )
        risk = assess_entry_risk(
            settings=settings,
            boundary_mass=probability.boundary_mass,
            disagreement=max(probability.disagreement, float(forecast["disagreement"])),
            spread_cents=int(snapshot["spread_cents"]),
            source_health=source_health,
            open_positions=0,
            realized_pnl_today=0.0,
            bankroll_reference=settings.initial_balance,
        )
        decision = choose_trade(market=market, probability=probability, risk=risk, settings=settings)
        trades.append(
            {
                "ticker": market.ticker,
                "approved": decision.approved,
                "side": decision.side,
                "ev": decision.expected_value,
            }
        )

    approved = [trade for trade in trades if trade["approved"]]
    avg_ev = sum(float(trade["ev"]) for trade in approved) / len(approved) if approved else 0.0
    return {
        "snapshots_replayed": len(trades),
        "approved_trades": len(approved),
        "average_ev": avg_ev,
        "trades": trades[:25],
    }


def _daterange(start_date: date, end_date: date) -> List[date]:
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def _entry_timestamp(cycle_day: date, hour_utc: int, minute_utc: int) -> datetime:
    return datetime.combine(cycle_day, time(hour=hour_utc, minute=minute_utc, tzinfo=timezone.utc))


def _settle_position(position: BacktestPosition, settings: Settings) -> Dict[str, object]:
    yes_settles = 1.0 if position.bucket_low <= position.actual_high_f < position.bucket_high else 0.0
    payout = yes_settles if position.side == "YES" else 1.0 - yes_settles
    realized = payout * position.contracts - position.entry_price * position.contracts - position.entry_fees
    return {
        "ticker": position.ticker,
        "city": position.city_key,
        "target_date": position.target_date.isoformat(),
        "side": position.side,
        "contracts": position.contracts,
        "entry_price": position.entry_price,
        "predicted_probability_yes": position.probability_yes,
        "expected_value": position.expected_value,
        "actual_high_f": position.actual_high_f,
        "settled_yes": yes_settles,
        "payout": payout,
        "realized_pnl": realized,
        "fees": position.entry_fees,
    }


def _load_or_fetch_markets(
    settings: Settings,
    cache: HistoricalBacktestCache,
    *,
    start_date: date,
    end_date: date,
    refresh_cache: bool,
) -> List[HistoricalMarketDefinition]:
    if refresh_cache:
        markets = fetch_historical_market_definitions(settings, start_date, end_date)
        grouped: Dict[date, List[HistoricalMarketDefinition]] = defaultdict(list)
        for market in markets:
            grouped[market.target_date].append(market)
        for target_date in _daterange(start_date, end_date):
            cache.save_markets(target_date, grouped.get(target_date, []))
        return markets

    loaded: List[HistoricalMarketDefinition] = []
    missing_dates: List[date] = []
    for target_date in _daterange(start_date, end_date):
        cached = cache.load_markets(target_date)
        if cached is None:
            missing_dates.append(target_date)
            continue
        loaded.extend(cached)

    if missing_dates:
        fetched = fetch_historical_market_definitions(settings, min(missing_dates), max(missing_dates))
        grouped: Dict[date, List[HistoricalMarketDefinition]] = defaultdict(list)
        for market in fetched:
            grouped[market.target_date].append(market)
        for target_date in _daterange(min(missing_dates), max(missing_dates)):
            cache.save_markets(target_date, grouped.get(target_date, []))
        loaded.extend(
            market for market in fetched
            if start_date <= market.target_date <= end_date
        )
    return sorted(loaded, key=lambda item: (item.target_date, item.city_key, item.ticker))


def warm_historical_backtest_cache(
    settings: Settings,
    *,
    start_date: date,
    end_date: date,
    entry_hour_utc: int = 20,
    entry_minute_utc: int = 0,
    refresh_cache: bool = False,
) -> Dict[str, object]:
    """Download and persist a historical backtest dataset for later reuse."""

    cache = HistoricalBacktestCache(
        settings,
        entry_hour_utc=entry_hour_utc,
        entry_minute_utc=entry_minute_utc,
    )
    markets = _load_or_fetch_markets(
        settings,
        cache,
        start_date=start_date,
        end_date=end_date,
        refresh_cache=refresh_cache,
    )

    quotes_cached = 0
    forecasts_cached = 0
    quote_fetches = 0
    forecast_fetches = 0
    cycle_start = start_date - timedelta(days=1)
    cycle_end = end_date
    markets_by_target: Dict[date, List[HistoricalMarketDefinition]] = defaultdict(list)
    for market in markets:
        markets_by_target[market.target_date].append(market)

    for cycle_day in _daterange(cycle_start, cycle_end):
        entry_time_utc = _entry_timestamp(cycle_day, entry_hour_utc, entry_minute_utc)
        target_date = cycle_day + timedelta(days=1)
        for market in markets_by_target.get(target_date, []):
            if not refresh_cache and cache.load_quote(market.ticker) is not None:
                quotes_cached += 1
            else:
                quote = fetch_historical_market_quote(settings, market, entry_time_utc=entry_time_utc)
                cache.save_quote(market.ticker, quote)
                quote_fetches += 1
                if quote is not None:
                    quotes_cached += 1

            if not refresh_cache and cache.load_forecast(market.city_key, market.target_date) is not None:
                forecasts_cached += 1
            else:
                forecast = fetch_historical_forecast_snapshot(
                    settings,
                    market.city_key,
                    market.target_date,
                    entry_time_utc=entry_time_utc,
                )
                cache.save_forecast(market.city_key, market.target_date, forecast)
                forecast_fetches += 1
                if forecast is not None:
                    forecasts_cached += 1

    return {
        "cache": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "entry_time_utc": f"{entry_hour_utc:02d}:{entry_minute_utc:02d}",
            "markets_cached": len(markets),
            "quotes_cached": quotes_cached,
            "forecasts_cached": forecasts_cached,
            "quote_fetches": quote_fetches,
            "forecast_fetches": forecast_fetches,
            "cache_dir": str(cache.base_dir),
            "refreshed": refresh_cache,
        }
    }


def run_historical_backtest(
    settings: Settings,
    *,
    start_date: date,
    end_date: date,
    entry_hour_utc: int = 20,
    entry_minute_utc: int = 0,
    use_cache: bool = True,
    refresh_cache: bool = False,
) -> Dict[str, object]:
    """Run a historical day-ahead backtest on settled KXHIGH markets.

    The backtest enters positions once per cycle using historical Kalshi hourly candlesticks and
    archived Open-Meteo single runs. Positions are held to settlement, which keeps the simulation
    aligned with the data we can reliably reconstruct today.
    """

    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    cache = HistoricalBacktestCache(
        settings,
        entry_hour_utc=entry_hour_utc,
        entry_minute_utc=entry_minute_utc,
    )
    markets = _load_or_fetch_markets(
        settings,
        cache,
        start_date=start_date,
        end_date=end_date,
        refresh_cache=refresh_cache,
    ) if use_cache else fetch_historical_market_definitions(settings, start_date, end_date)
    markets_by_target: Dict[date, List[HistoricalMarketDefinition]] = defaultdict(list)
    for market in markets:
        markets_by_target[market.target_date].append(market)

    cash = settings.initial_balance
    starting_balance = settings.initial_balance
    open_positions: List[BacktestPosition] = []
    executed_trades: List[Dict[str, object]] = []
    skipped: List[str] = []
    calibration_rows: List[float] = []
    forecast_cache: Dict[tuple[str, date], Optional[object]] = {}
    forecasts_loaded = 0
    quotes_loaded = 0
    entries_attempted = 0
    entries_executed = 0
    settled_positions = 0
    max_equity = starting_balance
    max_drawdown = 0.0
    daily_summaries: List[Dict[str, object]] = []

    cycle_start = start_date - timedelta(days=1)
    cycle_end = end_date
    for cycle_day in _daterange(cycle_start, cycle_end):
        realized_today = 0.0
        still_open: List[BacktestPosition] = []
        for position in open_positions:
            if position.target_date >= cycle_day:
                still_open.append(position)
                continue
            settlement = _settle_position(position, settings)
            cash += float(settlement["payout"]) * position.contracts
            realized_today += float(settlement["realized_pnl"])
            settled_positions += 1
            executed_trades.append(settlement)
        open_positions = still_open

        target_date = cycle_day + timedelta(days=1)
        entry_time_utc = _entry_timestamp(cycle_day, entry_hour_utc, entry_minute_utc)
        evaluated_today = 0
        for market in markets_by_target.get(target_date, []):
            evaluated_today += 1
            quote = cache.load_quote(market.ticker) if use_cache and not refresh_cache else None
            if quote is None:
                quote = fetch_historical_market_quote(settings, market, entry_time_utc=entry_time_utc)
                if use_cache:
                    cache.save_quote(market.ticker, quote)
            if quote is None:
                skipped.append(f"{market.ticker}: historical quote unavailable")
                continue
            quotes_loaded += 1

            forecast_key = (market.city_key, market.target_date)
            if forecast_key not in forecast_cache:
                cached_forecast = cache.load_forecast(market.city_key, market.target_date) if use_cache and not refresh_cache else None
                if cached_forecast is None:
                    cached_forecast = fetch_historical_forecast_snapshot(
                        settings,
                        market.city_key,
                        market.target_date,
                        entry_time_utc=entry_time_utc,
                    )
                    if use_cache:
                        cache.save_forecast(market.city_key, market.target_date, cached_forecast)
                forecast_cache[forecast_key] = cached_forecast
            forecast = forecast_cache[forecast_key]
            if forecast is None:
                skipped.append(f"{market.ticker}: historical forecast unavailable")
                continue
            forecasts_loaded += 1

            p_climo = bucket_probability_from_climatology(
                market.city_key,
                market.target_date,
                market.bucket_low,
                market.bucket_high,
            )
            probability = estimate_bucket_probability(
                mean_temp_f=forecast.mean_temp_f,
                sigma_temp_f=forecast.sigma_temp_f,
                bucket_low=market.bucket_low,
                bucket_high=market.bucket_high,
                p_climo=p_climo,
                member_highs=forecast.member_highs,
                pseudo_count=settings.pseudo_count,
                boundary_buffer_f=settings.boundary_buffer_f,
                min_sigma_f=settings.min_sigma_f,
            )
            settled_yes = 1.0 if market.bucket_low <= market.actual_high_f < market.bucket_high else 0.0
            calibration_rows.append((probability.p_bucket_yes - settled_yes) ** 2)
            risk = assess_entry_risk(
                settings=settings,
                boundary_mass=probability.boundary_mass,
                disagreement=max(probability.disagreement, forecast.disagreement),
                spread_cents=quote.spread_cents,
                source_health=forecast.source_health,
                open_positions=len(open_positions),
                realized_pnl_today=realized_today,
                bankroll_reference=starting_balance,
            )
            decision = choose_trade(market=quote, probability=probability, risk=risk, settings=settings)
            if not decision.approved:
                skipped.append(f"{market.ticker}: {'; '.join(decision.rationale)}")
                continue

            entries_attempted += 1
            sizing = calculate_kelly_size(
                balance=max(cash, 0.0),
                probability=decision.win_probability,
                cost=decision.price,
                fee=settings.fee_per_contract,
                uncertainty_mult=risk.size_mult,
                source_health_mult=risk.source_health_mult,
                settings=settings,
            )
            if sizing.contracts < 1:
                skipped.append(f"{market.ticker}: Kelly size below 1 contract")
                continue

            total_cost = decision.price * sizing.contracts + settings.fee_per_contract * sizing.contracts
            if total_cost > cash:
                skipped.append(f"{market.ticker}: insufficient backtest cash")
                continue

            entries_executed += 1
            cash -= total_cost
            open_positions.append(
                BacktestPosition(
                    ticker=market.ticker,
                    city_key=market.city_key,
                    target_date=market.target_date,
                    side=decision.side,
                    contracts=sizing.contracts,
                    entry_price=decision.price,
                    entry_fees=settings.fee_per_contract * sizing.contracts,
                    probability_yes=probability.p_bucket_yes,
                    expected_value=decision.expected_value,
                    bucket_low=market.bucket_low,
                    bucket_high=market.bucket_high,
                    actual_high_f=market.actual_high_f,
                )
            )

        open_cost_basis = sum(
            position.entry_price * position.contracts for position in open_positions
        )
        equity = cash + open_cost_basis
        max_equity = max(max_equity, equity)
        if max_equity > 0:
            max_drawdown = max(max_drawdown, (max_equity - equity) / max_equity)
        daily_summaries.append(
            {
                "cycle_day": cycle_day.isoformat(),
                "target_date": target_date.isoformat(),
                "evaluated_markets": evaluated_today,
                "realized_pnl": realized_today,
                "cash": cash,
                "open_positions": len(open_positions),
                "equity": equity,
            }
        )

    final_settlements = 0
    for position in open_positions:
        settlement = _settle_position(position, settings)
        cash += float(settlement["payout"]) * position.contracts
        executed_trades.append(settlement)
        final_settlements += 1
        settled_positions += 1
    open_positions = []

    by_city: Dict[str, Dict[str, float]] = defaultdict(lambda: {"trades": 0, "realized_pnl": 0.0, "wins": 0})
    by_side: Dict[str, Dict[str, float]] = defaultdict(lambda: {"trades": 0, "realized_pnl": 0.0, "wins": 0})
    wins = 0
    for trade in executed_trades:
        city = str(trade["city"])
        side = str(trade["side"])
        pnl = float(trade["realized_pnl"])
        by_city[city]["trades"] += 1
        by_city[city]["realized_pnl"] += pnl
        by_city[city]["wins"] += int(pnl > 0)
        by_side[side]["trades"] += 1
        by_side[side]["realized_pnl"] += pnl
        by_side[side]["wins"] += int(pnl > 0)
        wins += int(pnl > 0)

    total_realized = sum(float(trade["realized_pnl"]) for trade in executed_trades)
    total_fees = sum(float(trade["fees"]) for trade in executed_trades)
    return {
        "backtest": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "entry_time_utc": f"{entry_hour_utc:02d}:{entry_minute_utc:02d}",
            "markets_considered": len(markets),
            "quotes_loaded": quotes_loaded,
            "forecasts_loaded": forecasts_loaded,
            "entries_attempted": entries_attempted,
            "entries_executed": entries_executed,
            "settled_positions": settled_positions,
            "final_settlements": final_settlements,
            "starting_balance": starting_balance,
            "ending_balance": cash,
            "total_pnl": cash - starting_balance,
            "return_pct": ((cash / starting_balance) - 1.0) if starting_balance > 0 else 0.0,
            "total_fees": total_fees,
            "win_rate": (wins / len(executed_trades)) if executed_trades else 0.0,
            "max_drawdown": max_drawdown,
            "brier_score": (sum(calibration_rows) / len(calibration_rows)) if calibration_rows else None,
            "by_city": dict(by_city),
            "by_side": dict(by_side),
            "daily": daily_summaries,
            "trades": executed_trades[:100],
            "skipped": skipped[:100],
            "cache_used": use_cache,
            "cache_refreshed": refresh_cache,
            "cache_dir": str(cache.base_dir) if use_cache else None,
        }
    }
