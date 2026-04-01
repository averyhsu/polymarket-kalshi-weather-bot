"""Runtime configuration for the Kalshi weather trading bot."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Dict, List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = PROJECT_ROOT / "kalshi_weather_bot.sqlite3"
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".cache"
DEFAULT_HISTORICAL_DATA_DIR = PROJECT_ROOT / "historical_data" / "backtests"
DEFAULT_USER_AGENT = "kalshi-weather-bot/1.0 (https://github.com/averyhsu/polymarket-kalshi-weather-bot)"


def _parse_csv(value: object) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [part.strip().lower() for part in text.split(",") if part.strip()]


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"cannot parse boolean value from {value!r}")


def _parse_float_mapping(value: object) -> Dict[str, float]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(key).strip().lower(): float(item) for key, item in value.items()}
    text = str(value).strip()
    if not text:
        return {}
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("mapping config must decode to an object")
    return {str(key).strip().lower(): float(item) for key, item in parsed.items()}


def _parse_bool_mapping(value: object) -> Dict[str, bool]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(key).strip().lower(): _parse_bool(item) for key, item in value.items()}
    text = str(value).strip()
    if not text:
        return {}
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("mapping config must decode to an object")
    return {str(key).strip().lower(): _parse_bool(item) for key, item in parsed.items()}


@dataclass(frozen=True)
class ProfilePreset:
    """Risk profile tuning knobs."""

    name: str
    kelly_fraction_mult: float
    max_position_mult: float
    min_ev_buffer_mult: float
    dampening: float


PROFILE_PRESETS: Dict[str, ProfilePreset] = {
    "conservative": ProfilePreset(
        name="conservative",
        kelly_fraction_mult=0.7,
        max_position_mult=0.75,
        min_ev_buffer_mult=1.2,
        dampening=0.75,
    ),
    "balanced": ProfilePreset(
        name="balanced",
        kelly_fraction_mult=1.0,
        max_position_mult=1.0,
        min_ev_buffer_mult=1.0,
        dampening=0.9,
    ),
    "aggressive": ProfilePreset(
        name="aggressive",
        kelly_fraction_mult=1.2,
        max_position_mult=1.25,
        min_ev_buffer_mult=0.85,
        dampening=1.0,
    ),
}


class Settings(BaseSettings):
    """Application settings loaded from environment variables and CLI overrides."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    mode: str = Field(default="paper", alias="BOT_MODE")
    profile: str = Field(default="balanced", alias="BOT_PROFILE")
    dry_run: bool = Field(default=False, alias="DRY_RUN")
    verbose: bool = Field(default=False, alias="VERBOSE")
    db_path: Path = Field(default=DEFAULT_DB_PATH, alias="DB_PATH")
    cache_dir: Path = Field(default=DEFAULT_CACHE_DIR, alias="CACHE_DIR")
    historical_data_dir: Path = Field(default=DEFAULT_HISTORICAL_DATA_DIR, alias="HISTORICAL_DATA_DIR")
    user_agent: str = Field(default=DEFAULT_USER_AGENT, alias="USER_AGENT")

    kalshi_api_key_id: Optional[str] = Field(default=None, alias="KALSHI_API_KEY_ID")
    kalshi_private_key_path: Optional[Path] = Field(default=None, alias="KALSHI_PRIVATE_KEY_PATH")
    kalshi_api_base_url: str = Field(
        default="https://api.elections.kalshi.com/trade-api/v2",
        alias="KALSHI_API_BASE_URL",
    )

    enabled_cities: Annotated[List[str], NoDecode] = Field(
        default_factory=lambda: ["nyc", "chicago", "los_angeles", "denver"],
        alias="ENABLED_CITIES",
    )
    blacklisted_cities: Annotated[List[str], NoDecode] = Field(
        default_factory=lambda: ["miami"],
        alias="BLACKLISTED_CITIES",
    )
    no_only: bool = Field(default=False, alias="NO_ONLY")
    yes_enabled: bool = Field(default=True, alias="YES_ENABLED")
    skip_same_day: bool = Field(default=True, alias="SKIP_SAME_DAY")
    day_ahead_only: bool = Field(default=True, alias="DAY_AHEAD_ONLY")

    boundary_threshold: float = Field(default=0.25, alias="BOUNDARY_THRESHOLD")
    disagreement_threshold: float = Field(default=0.85, alias="DISAGREEMENT_THRESHOLD")
    boundary_buffer_f: float = Field(default=1.0, alias="BOUNDARY_BUFFER_F")
    pseudo_count: float = Field(default=8.0, alias="PSEUDO_COUNT")
    min_sigma_f: float = Field(default=0.5, alias="MIN_SIGMA_F")

    kelly_fraction: float = Field(default=0.25, alias="KELLY_FRACTION")
    max_position_pct: float = Field(default=0.02, alias="MAX_POSITION_PCT")
    max_position_usd: float = Field(default=10.0, alias="MAX_POSITION_USD")
    fee_per_contract: float = Field(default=0.01, alias="FEE_PER_CONTRACT")
    slippage_per_contract: float = Field(default=0.005, alias="SLIPPAGE_PER_CONTRACT")

    base_min_ev: float = Field(default=0.04, alias="BASE_MIN_EV")
    yes_min_ev: float = Field(default=0.08, alias="YES_MIN_EV")
    no_min_ev: float = Field(default=0.04, alias="NO_MIN_EV")
    max_spread_cents: int = Field(default=5, alias="MAX_SPREAD_CENTS")
    min_volume: float = Field(default=25.0, alias="MIN_VOLUME")
    min_price_cents: int = Field(default=2, alias="MIN_PRICE_CENTS")
    max_price_cents: int = Field(default=98, alias="MAX_PRICE_CENTS")
    yes_min_price_cents: int = Field(default=10, alias="YES_MIN_PRICE_CENTS")
    yes_kelly_fraction_mult: float = Field(default=0.35, alias="YES_KELLY_FRACTION_MULT")
    max_positions_per_city_day: int = Field(default=3, alias="MAX_POSITIONS_PER_CITY_DAY")
    city_ev_buffer_overrides: Annotated[Dict[str, float], NoDecode] = Field(
        default_factory=lambda: {"chicago": 0.02},
        alias="CITY_EV_BUFFER_OVERRIDES",
    )
    city_enabled_overrides: Annotated[Dict[str, bool], NoDecode] = Field(
        default_factory=dict,
        alias="CITY_ENABLED_OVERRIDES",
    )
    calibration_enabled: bool = Field(default=True, alias="CALIBRATION_ENABLED")
    calibration_pseudo_count: float = Field(default=12.0, alias="CALIBRATION_PSEUDO_COUNT")
    tail_yes_price_cents: int = Field(default=12, alias="TAIL_YES_PRICE_CENTS")
    tail_yes_probability_threshold: float = Field(default=0.58, alias="TAIL_YES_PROBABILITY_THRESHOLD")
    tail_uncertainty_threshold: float = Field(default=0.35, alias="TAIL_UNCERTAINTY_THRESHOLD")
    tail_risk_penalty: float = Field(default=0.03, alias="TAIL_RISK_PENALTY")
    ranking_spread_weight: float = Field(default=0.002, alias="RANKING_SPREAD_WEIGHT")

    take_profit_cents: int = Field(default=10, alias="TAKE_PROFIT_CENTS")
    stop_loss_cents: int = Field(default=15, alias="STOP_LOSS_CENTS")
    closeout_hours_before: float = Field(default=2.0, alias="CLOSEOUT_HOURS_BEFORE")
    probability_drift_threshold: float = Field(default=0.12, alias="PROBABILITY_DRIFT_THRESHOLD")
    hold_min_ev: float = Field(default=0.01, alias="HOLD_MIN_EV")

    daily_max_loss_pct: float = Field(default=0.10, alias="DAILY_MAX_LOSS_PCT")
    max_open_positions: int = Field(default=10, alias="MAX_OPEN_POSITIONS")
    initial_balance: float = Field(default=100.0, alias="INITIAL_BALANCE")
    survival_balance_floor: float = Field(default=50.0, alias="SURVIVAL_BALANCE_FLOOR")
    survival_kelly_fraction: float = Field(default=0.10, alias="SURVIVAL_KELLY_FRACTION")

    forecast_cache_ttl_minutes: int = Field(default=20, alias="FORECAST_CACHE_TTL_MINUTES")
    stale_market_minutes: int = Field(default=20, alias="STALE_MARKET_MINUTES")
    settlement_buffer_hours: float = Field(default=8.0, alias="SETTLEMENT_BUFFER_HOURS")
    source_health_trade_threshold: float = Field(default=0.5, alias="SOURCE_HEALTH_TRADE_THRESHOLD")
    source_health_degraded_threshold: float = Field(default=0.75, alias="SOURCE_HEALTH_DEGRADED_THRESHOLD")

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"paper", "live"}:
            raise ValueError("mode must be 'paper' or 'live'")
        return normalized

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in PROFILE_PRESETS:
            raise ValueError(f"profile must be one of {', '.join(PROFILE_PRESETS)}")
        return normalized

    @field_validator("enabled_cities", "blacklisted_cities", mode="before")
    @classmethod
    def parse_city_lists(cls, value: object) -> List[str]:
        return _parse_csv(value)

    @field_validator("city_ev_buffer_overrides", mode="before")
    @classmethod
    def parse_city_ev_mapping(cls, value: object) -> Dict[str, float]:
        return _parse_float_mapping(value)

    @field_validator("city_enabled_overrides", mode="before")
    @classmethod
    def parse_city_enabled_mapping(cls, value: object) -> Dict[str, bool]:
        return _parse_bool_mapping(value)

    @property
    def active_profile(self) -> ProfilePreset:
        return PROFILE_PRESETS[self.profile]

    @property
    def tradable_cities(self) -> List[str]:
        blacklist = set(self.blacklisted_cities)
        return [
            city
            for city in self.enabled_cities
            if city not in blacklist and self.city_enabled_overrides.get(city, True)
        ]

    @property
    def live_enabled(self) -> bool:
        return self.mode == "live"

    @property
    def kalshi_credentials_present(self) -> bool:
        return bool(self.kalshi_api_key_id and self.kalshi_private_key_path)

    def city_ev_buffer(self, city_key: str) -> float:
        return float(self.city_ev_buffer_overrides.get(city_key.lower(), 0.0))


def load_settings(overrides: Optional[Dict[str, object]] = None) -> Settings:
    """Load settings and apply optional runtime overrides."""

    settings = Settings()
    if not overrides:
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        settings.historical_data_dir.mkdir(parents=True, exist_ok=True)
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        return settings

    merged = settings.model_dump()
    merged.update({key: value for key, value in overrides.items() if value is not None})
    updated = Settings(**merged)
    updated.cache_dir.mkdir(parents=True, exist_ok=True)
    updated.historical_data_dir.mkdir(parents=True, exist_ok=True)
    updated.db_path.parent.mkdir(parents=True, exist_ok=True)
    return updated
