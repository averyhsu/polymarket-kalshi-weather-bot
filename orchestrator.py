"""End-to-end orchestration for the Kalshi weather bot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from analytics.calibration import summarize_calibration
from analytics.pnl import summarize_pnl
from analytics.replay import replay_from_database, run_historical_backtest
from config import Settings
from core.decision import choose_trade
from core.exits import evaluate_exit
from core.probability import ProbabilityResult, estimate_bucket_probability
from core.risk import RiskAssessment, assess_entry_risk
from core.sizing import calculate_kelly_size
from data.climatology import bucket_probability_from_climatology
from data.markets import MarketQuote, fetch_kxhigh_markets
from data.weather import ForecastSnapshot, fetch_forecast_snapshot, fetch_observed_high
from db.models import Database, PositionRecord
from execution.live import LiveBroker
from execution.paper import PaperBroker


@dataclass
class CycleSummary:
    """Summary of a single bot cycle."""

    scanned_markets: int = 0
    forecasts_loaded: int = 0
    entries_attempted: int = 0
    entries_executed: int = 0
    exits_executed: int = 0
    settlements: int = 0
    skipped: List[str] = field(default_factory=list)


class WeatherTradingOrchestrator:
    """Single-run orchestrator for paper and guarded live trading."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.database = Database(settings.db_path)
        self.paper = PaperBroker(settings, self.database)
        self.live = LiveBroker(settings, self.database)
        self._forecast_cache: Dict[Tuple[str, date], ForecastSnapshot] = {}

    def _load_forecast(self, city_key: str, target_date: date) -> Optional[ForecastSnapshot]:
        key = (city_key, target_date)
        if key not in self._forecast_cache:
            snapshot = fetch_forecast_snapshot(self.settings, city_key, target_date)
            if snapshot is not None:
                self._forecast_cache[key] = snapshot
        return self._forecast_cache.get(key)

    def _position_mode(self) -> str:
        return "paper" if self.settings.mode == "paper" else "live"

    def _bankroll_reference(self) -> float:
        if self.settings.mode == "paper":
            summary = self.paper.summary()
            return max(summary["starting_balance"], self.settings.initial_balance)
        return max(self.settings.initial_balance, 0.0)

    def _available_balance(self) -> float:
        if self.settings.mode == "paper":
            return max(self.paper.summary()["cash"], 0.0)
        return max(self.settings.initial_balance, 0.0)

    def _probability_and_risk(
        self,
        market: MarketQuote,
        forecast: ForecastSnapshot,
        open_positions: int,
    ) -> Tuple[ProbabilityResult, RiskAssessment]:
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
            pseudo_count=self.settings.pseudo_count,
            boundary_buffer_f=self.settings.boundary_buffer_f,
            min_sigma_f=self.settings.min_sigma_f,
        )
        realized_today = self.database.aggregate_realized_pnl_today(date.today(), mode=self._position_mode())
        risk = assess_entry_risk(
            settings=self.settings,
            boundary_mass=probability.boundary_mass,
            disagreement=max(probability.disagreement, forecast.disagreement),
            spread_cents=market.spread_cents,
            source_health=forecast.source_health,
            open_positions=open_positions,
            realized_pnl_today=realized_today,
            bankroll_reference=self._bankroll_reference(),
        )
        return probability, risk

    def _record_market_context(self, market: MarketQuote, forecast: ForecastSnapshot) -> None:
        self.database.record_snapshot(
            ticker=market.ticker,
            city_key=market.city_key,
            target_date=market.target_date.isoformat(),
            yes_bid=market.yes_bid,
            yes_ask=market.yes_ask,
            no_bid=market.no_bid,
            no_ask=market.no_ask,
            spread_cents=market.spread_cents,
            volume=market.volume,
            metadata={
                "bucket_low": market.bucket_low,
                "bucket_high": market.bucket_high,
                "strike_label": market.strike_label,
                "series_ticker": market.series_ticker,
                "city_name": market.city_name,
                "title": market.title,
                "subtitle": market.subtitle,
                "last_price": market.last_price,
                "open_interest": market.open_interest,
                "source_health_score": forecast.source_health.score,
                "source_health_status": forecast.source_health.status,
            },
        )
        self.database.record_forecast(
            city_key=forecast.city_key,
            target_date=forecast.target_date.isoformat(),
            provider=forecast.provider,
            mean_temp=forecast.mean_temp_f,
            sigma_temp=forecast.sigma_temp_f,
            member_count=forecast.member_count,
            member_spread=forecast.member_spread_f,
            disagreement=forecast.disagreement,
            source_health=forecast.source_health.score,
            payload={
                "members": forecast.member_highs,
                "fetched_at": forecast.fetched_at.isoformat(),
            },
        )

    def settle_matured_positions(self) -> int:
        """Settle paper positions that are safely past the target day."""

        settled = 0
        now = datetime.utcnow()
        for position in self.database.fetch_open_positions(mode="paper"):
            target_date = date.fromisoformat(position.target_date)
            settle_after = datetime.combine(target_date, datetime.min.time()) + timedelta(
                days=1,
                hours=self.settings.settlement_buffer_hours,
            )
            if now < settle_after:
                continue
            observed_high = fetch_observed_high(self.settings, position.city_key, target_date)
            if observed_high is None:
                continue
            result = self.paper.settle_position(position, observed_high)
            if result.executed:
                settled += 1
        return settled

    def manage_open_positions(self, markets: Dict[str, MarketQuote], summary: CycleSummary) -> None:
        """Apply exit rules to open paper positions."""

        if self.settings.mode != "paper":
            return
        open_positions = self.database.fetch_open_positions(mode="paper")
        for position in open_positions:
            market = markets.get(position.ticker)
            if market is None:
                continue
            forecast = self._load_forecast(position.city_key, market.target_date)
            if forecast is None:
                continue
            probability, _ = self._probability_and_risk(market, forecast, len(open_positions))
            self.paper.mark_position(position, market, probability.p_bucket_yes)
            entry_probability_yes = float(position.metadata.get("entry_probability_yes", 0.5))
            exit_decision = evaluate_exit(
                position=position,
                market=market,
                entry_probability_yes=entry_probability_yes,
                current_probability_yes=probability.p_bucket_yes,
                settings=self.settings,
            )
            if exit_decision.action == "EXIT":
                result = self.paper.exit_position(
                    position=position,
                    market=market,
                    reason=exit_decision.reason,
                    current_probability_yes=probability.p_bucket_yes,
                )
                if result.executed:
                    summary.exits_executed += 1

    def run_cycle(self) -> CycleSummary:
        """Run one scan/decision/execution cycle."""

        summary = CycleSummary()
        if self.settings.mode == "paper":
            summary.settlements = self.settle_matured_positions()

        markets = fetch_kxhigh_markets(self.settings)
        summary.scanned_markets = len(markets)
        if not markets:
            summary.skipped.append("no markets available")
            return summary

        market_map = {market.ticker: market for market in markets}
        self.manage_open_positions(market_map, summary)

        open_tickers = {position.ticker for position in self.database.fetch_open_positions(mode=self._position_mode())}
        for market in markets:
            if market.ticker in open_tickers:
                continue
            forecast = self._load_forecast(market.city_key, market.target_date)
            if forecast is None:
                summary.skipped.append(f"forecast unavailable for {market.ticker}")
                continue
            summary.forecasts_loaded += 1
            self._record_market_context(market, forecast)
            probability, risk = self._probability_and_risk(
                market,
                forecast,
                len(self.database.fetch_open_positions(mode=self._position_mode())),
            )
            decision = choose_trade(market=market, probability=probability, risk=risk, settings=self.settings)
            self.database.record_calibration(
                ticker=market.ticker,
                city_key=market.city_key,
                target_date=market.target_date.isoformat(),
                side=decision.side if decision.approved else "NONE",
                predicted_probability=probability.p_bucket_yes,
                settled_value=None,
                bucket_low=market.bucket_low,
                bucket_high=market.bucket_high,
                metadata={"decision": decision.rationale},
            )
            if not decision.approved:
                summary.skipped.append(f"{market.ticker}: {'; '.join(decision.rationale)}")
                continue

            summary.entries_attempted += 1
            sizing = calculate_kelly_size(
                balance=self._available_balance(),
                probability=decision.win_probability,
                cost=decision.price,
                fee=self.settings.fee_per_contract,
                uncertainty_mult=risk.size_mult,
                source_health_mult=risk.source_health_mult,
                settings=self.settings,
            )
            if sizing.contracts < 1:
                summary.skipped.append(f"{market.ticker}: Kelly size below 1 contract")
                continue

            if self.settings.mode == "paper":
                result = self.paper.place_entry_order(
                    decision=decision,
                    sizing=sizing,
                    market=market,
                    probability_yes=probability.p_bucket_yes,
                )
                if result.executed:
                    summary.entries_executed += 1
            else:
                result = self.live.place_entry_order(decision=decision, sizing=sizing, market=market)
                if result.submitted:
                    summary.entries_executed += 1

        account = self.paper.summary()
        self.database.record_daily_pnl(
            date.today(),
            realized_pnl=self.database.aggregate_realized_pnl_today(date.today()),
            unrealized_pnl=account["unrealized_pnl"],
            total_fees=self.database.aggregate_fees(),
        )
        return summary

    def stats(self) -> Dict[str, object]:
        """Return combined performance and calibration statistics."""

        return {
            "pnl": summarize_pnl(self.database, mode="paper"),
            "calibration": summarize_calibration(self.database),
        }

    def replay(self) -> Dict[str, object]:
        """Replay the strategy using stored forecasts and snapshots."""

        return replay_from_database(self.settings, self.database)

    def backtest(
        self,
        *,
        start_date: date,
        end_date: date,
        entry_hour_utc: int = 20,
        entry_minute_utc: int = 0,
    ) -> Dict[str, object]:
        """Run a historical day-ahead backtest."""

        return run_historical_backtest(
            self.settings,
            start_date=start_date,
            end_date=end_date,
            entry_hour_utc=entry_hour_utc,
            entry_minute_utc=entry_minute_utc,
        )

    def positions(self) -> List[PositionRecord]:
        """Return open positions."""

        return self.database.fetch_open_positions(mode=self._position_mode())

    def close_all_paper(self) -> int:
        """Close every open paper position at the stored mark price."""

        if self.settings.mode != "paper":
            return 0
        count = 0
        for position in self.database.fetch_open_positions(mode="paper"):
            fill_price = max(position.current_mark, 0.01)
            dummy_market = MarketQuote(
                ticker=position.ticker,
                series_ticker="",
                city_key=position.city_key,
                city_name=str(position.metadata.get("city_name", position.city_key)),
                target_date=date.fromisoformat(position.target_date),
                bucket_low=float(position.metadata.get("bucket_low", float("-inf"))),
                bucket_high=float(position.metadata.get("bucket_high", float("inf"))),
                strike_label=str(position.metadata.get("strike_label", "")),
                title=position.ticker,
                subtitle="manual close",
                yes_bid=fill_price if position.side == "YES" else 0.5,
                yes_ask=fill_price,
                no_bid=fill_price if position.side == "NO" else 0.5,
                no_ask=fill_price,
                last_price=fill_price,
                volume=0.0,
                open_interest=0.0,
                updated_time=None,
                status="open",
            )
            result = self.paper.exit_position(
                position=position,
                market=dummy_market,
                reason="manual close all",
                current_probability_yes=float(position.metadata.get("entry_probability_yes", 0.5)),
            )
            if result.executed:
                count += 1
        return count
