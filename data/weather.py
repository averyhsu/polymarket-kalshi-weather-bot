"""Weather forecast and observation adapters."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from statistics import mean, pstdev
from typing import Dict, List, Optional

import httpx

from config import Settings


logger = logging.getLogger(__name__)

ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
SINGLE_RUNS_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
NWS_OBSERVATION_URL = "https://api.weather.gov/stations/{station}/observations"
EXPECTED_ENSEMBLE_MEMBERS = 31
EXPECTED_HISTORICAL_RUNS = 4


CITY_CONFIG: Dict[str, Dict[str, object]] = {
    "nyc": {
        "name": "New York City",
        "kalshi_series": "KXHIGHNY",
        "lat": 40.7128,
        "lon": -74.0060,
        "nws_station": "KNYC",
    },
    "chicago": {
        "name": "Chicago",
        "kalshi_series": "KXHIGHCHI",
        "lat": 41.8781,
        "lon": -87.6298,
        "nws_station": "KORD",
    },
    "miami": {
        "name": "Miami",
        "kalshi_series": "KXHIGHMIA",
        "lat": 25.7617,
        "lon": -80.1918,
        "nws_station": "KMIA",
    },
    "los_angeles": {
        "name": "Los Angeles",
        "kalshi_series": "KXHIGHLAX",
        "lat": 34.0522,
        "lon": -118.2437,
        "nws_station": "KLAX",
    },
    "denver": {
        "name": "Denver",
        "kalshi_series": "KXHIGHDEN",
        "lat": 39.7392,
        "lon": -104.9903,
        "nws_station": "KDEN",
    },
}


@dataclass(frozen=True)
class SourceHealth:
    """Source-health score used by risk and sizing layers."""

    score: float
    status: str
    success: float
    freshness: float
    completeness: float
    consistency: float


@dataclass(frozen=True)
class ForecastSnapshot:
    """Normalized ensemble forecast for a single city/date."""

    city_key: str
    city_name: str
    target_date: date
    fetched_at: datetime
    provider: str
    member_highs: List[float]
    mean_temp_f: float
    sigma_temp_f: float
    member_spread_f: float
    disagreement: float
    source_health: SourceHealth

    @property
    def member_count(self) -> int:
        return len(self.member_highs)


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _cache_file(settings: Settings, city_key: str, target_date: date) -> Path:
    return settings.cache_dir / "weather" / f"{city_key}_{target_date.isoformat()}.json"


def serialize_forecast_snapshot(snapshot: ForecastSnapshot) -> Dict[str, object]:
    payload = asdict(snapshot)
    payload["target_date"] = snapshot.target_date.isoformat()
    payload["fetched_at"] = snapshot.fetched_at.isoformat()
    return payload


def deserialize_forecast_snapshot(payload: Dict[str, object]) -> ForecastSnapshot:
    source_payload = dict(payload["source_health"])
    source_health = SourceHealth(**source_payload)
    return ForecastSnapshot(
        city_key=str(payload["city_key"]),
        city_name=str(payload["city_name"]),
        target_date=date.fromisoformat(str(payload["target_date"])),
        fetched_at=datetime.fromisoformat(str(payload["fetched_at"])),
        provider=str(payload["provider"]),
        member_highs=[float(value) for value in payload["member_highs"]],
        mean_temp_f=float(payload["mean_temp_f"]),
        sigma_temp_f=float(payload["sigma_temp_f"]),
        member_spread_f=float(payload["member_spread_f"]),
        disagreement=float(payload["disagreement"]),
        source_health=source_health,
    )


def _load_cached_snapshot(settings: Settings, city_key: str, target_date: date) -> Optional[ForecastSnapshot]:
    cache_path = _cache_file(settings, city_key, target_date)
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return deserialize_forecast_snapshot(payload)
    except (OSError, ValueError, TypeError):
        return None


def _persist_snapshot(settings: Settings, snapshot: ForecastSnapshot) -> None:
    cache_path = _cache_file(settings, snapshot.city_key, snapshot.target_date)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(serialize_forecast_snapshot(snapshot), indent=2), encoding="utf-8")


def _extract_member_highs(payload: Dict[str, object]) -> List[float]:
    daily = payload.get("daily", {})
    if not isinstance(daily, dict):
        return []
    values: List[float] = []
    for key, item in daily.items():
        if "temperature_2m_max" not in str(key):
            continue
        if not isinstance(item, list) or not item:
            continue
        raw = item[0]
        if raw is None:
            continue
        try:
            temp = float(raw)
        except (TypeError, ValueError):
            continue
        if isfinite(temp):
            values.append(temp)
    return values


def _compute_disagreement(member_highs: List[float]) -> float:
    if len(member_highs) < 2:
        return 1.0
    center = mean(member_highs)
    above = sum(1 for value in member_highs if value >= center)
    fraction = above / len(member_highs)
    return _clamp(4.0 * fraction * (1.0 - fraction))


def _compute_source_health(
    *,
    fetched_at: datetime,
    member_count: int,
    sigma_temp_f: float,
    member_spread_f: float,
    settings: Settings,
    expected_member_count: int = EXPECTED_ENSEMBLE_MEMBERS,
    reference_time: Optional[datetime] = None,
) -> SourceHealth:
    reference = reference_time or datetime.utcnow()
    age_minutes = max(0.0, (reference - fetched_at).total_seconds() / 60.0)
    success = 1.0 if member_count > 0 else 0.0
    freshness = _clamp(1.0 - age_minutes / max(settings.forecast_cache_ttl_minutes * 2, 1))
    completeness = _clamp(member_count / max(expected_member_count, 1))
    spread_scale = max(6.0, sigma_temp_f * 4.0)
    consistency = 1.0 - _clamp(member_spread_f / spread_scale)
    score = _clamp(0.45 * success + 0.25 * freshness + 0.20 * completeness + 0.10 * consistency)
    if score >= settings.source_health_degraded_threshold:
        status = "HEALTHY"
    elif score >= settings.source_health_trade_threshold:
        status = "DEGRADED"
    else:
        status = "BROKEN"
    return SourceHealth(
        score=score,
        status=status,
        success=success,
        freshness=freshness,
        completeness=completeness,
        consistency=consistency,
    )


def _build_snapshot(
    *,
    city_key: str,
    target_date: date,
    provider: str,
    member_highs: List[float],
    fetched_at: datetime,
    settings: Settings,
    expected_member_count: int,
    reference_time: Optional[datetime] = None,
) -> ForecastSnapshot:
    mean_temp_f = mean(member_highs)
    sigma_temp_f = pstdev(member_highs) if len(member_highs) > 1 else 0.0
    member_spread_f = max(member_highs) - min(member_highs) if member_highs else 0.0
    disagreement = _compute_disagreement(member_highs)
    source_health = _compute_source_health(
        fetched_at=fetched_at,
        member_count=len(member_highs),
        sigma_temp_f=sigma_temp_f,
        member_spread_f=member_spread_f,
        settings=settings,
        expected_member_count=expected_member_count,
        reference_time=reference_time,
    )
    return ForecastSnapshot(
        city_key=city_key,
        city_name=str(CITY_CONFIG[city_key]["name"]),
        target_date=target_date,
        fetched_at=fetched_at,
        provider=provider,
        member_highs=member_highs,
        mean_temp_f=mean_temp_f,
        sigma_temp_f=sigma_temp_f,
        member_spread_f=member_spread_f,
        disagreement=disagreement,
        source_health=source_health,
    )


def fetch_forecast_snapshot(settings: Settings, city_key: str, target_date: date) -> Optional[ForecastSnapshot]:
    """Fetch and normalize an ensemble forecast snapshot."""

    city = CITY_CONFIG.get(city_key)
    if city is None:
        raise ValueError(f"Unsupported city '{city_key}'")

    cached = _load_cached_snapshot(settings, city_key, target_date)
    if cached is not None:
        age = datetime.utcnow() - cached.fetched_at
        if age <= timedelta(minutes=settings.forecast_cache_ttl_minutes):
            return cached

    params = {
        "latitude": city["lat"],
        "longitude": city["lon"],
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
        "models": "gfs_seamless",
    }
    headers = {"User-Agent": settings.user_agent}

    try:
        with httpx.Client(timeout=20.0, headers=headers, trust_env=False) as client:
            response = client.get(ENSEMBLE_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Open-Meteo request failed for %s %s: %s", city_key, target_date, exc)
        if cached is not None:
            return cached
        return None

    member_highs = _extract_member_highs(payload)
    if not member_highs:
        logger.warning("No ensemble members returned for %s %s", city_key, target_date)
        return cached

    fetched_at = datetime.utcnow()
    snapshot = _build_snapshot(
        city_key=city_key,
        target_date=target_date,
        provider="open-meteo-gfs-ensemble",
        member_highs=member_highs,
        fetched_at=fetched_at,
        settings=settings,
        expected_member_count=EXPECTED_ENSEMBLE_MEMBERS,
    )
    _persist_snapshot(settings, snapshot)
    return snapshot


def _fetch_single_run_high(
    settings: Settings,
    city_key: str,
    target_date: date,
    run_time: datetime,
) -> Optional[float]:
    city = CITY_CONFIG.get(city_key)
    if city is None:
        raise ValueError(f"Unsupported city '{city_key}'")

    params = {
        "latitude": city["lat"],
        "longitude": city["lon"],
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "models": "gfs_seamless",
        "run": run_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
        "forecast_days": max((target_date - run_time.date()).days + 1, 1),
        "timezone": "UTC",
    }
    headers = {"User-Agent": settings.user_agent}
    try:
        with httpx.Client(timeout=20.0, headers=headers, trust_env=False) as client:
            response = client.get(SINGLE_RUNS_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Open-Meteo single-run request failed for %s %s @ %s: %s", city_key, target_date, run_time, exc)
        return None

    daily = payload.get("daily", {})
    if not isinstance(daily, dict):
        return None
    times = daily.get("time", [])
    values = daily.get("temperature_2m_max", [])
    if not isinstance(times, list) or not isinstance(values, list):
        return None
    target_iso = target_date.isoformat()
    for raw_day, raw_value in zip(times, values):
        if str(raw_day) != target_iso or raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return None
        return value if isfinite(value) else None
    return None


def fetch_historical_forecast_snapshot(
    settings: Settings,
    city_key: str,
    target_date: date,
    *,
    entry_time_utc: datetime,
    run_hours_utc: Optional[List[int]] = None,
) -> Optional[ForecastSnapshot]:
    """Approximate a historical day-ahead forecast using archived deterministic runs.

    Open-Meteo does not expose archived historical ensemble members in the same shape as the
    live ensemble endpoint used by the trading cycle. For backtests we approximate uncertainty
    by sampling the deterministic GFS run at multiple issuance times on the entry day and using
    those run-to-run forecasts as pseudo-members.
    """

    if entry_time_utc.tzinfo is None:
        entry_time_utc = entry_time_utc.replace(tzinfo=timezone.utc)
    else:
        entry_time_utc = entry_time_utc.astimezone(timezone.utc)

    candidate_hours = run_hours_utc or [0, 6, 12, 18]
    member_highs: List[float] = []
    latest_run = entry_time_utc
    for hour in candidate_hours:
        run_time = datetime.combine(entry_time_utc.date(), datetime.min.time(), tzinfo=timezone.utc).replace(hour=hour)
        if run_time > entry_time_utc:
            continue
        value = _fetch_single_run_high(settings, city_key, target_date, run_time)
        if value is None:
            continue
        member_highs.append(value)
        latest_run = max(latest_run, run_time)

    if not member_highs:
        return None

    return _build_snapshot(
        city_key=city_key,
        target_date=target_date,
        provider="open-meteo-gfs-single-runs",
        member_highs=member_highs,
        fetched_at=latest_run,
        settings=settings,
        expected_member_count=EXPECTED_HISTORICAL_RUNS,
        reference_time=entry_time_utc,
    )


def fetch_observed_high(settings: Settings, city_key: str, target_date: date) -> Optional[float]:
    """Fetch NWS observations for settlement verification."""

    city = CITY_CONFIG.get(city_key)
    if city is None:
        raise ValueError(f"Unsupported city '{city_key}'")

    station = str(city["nws_station"])
    url = NWS_OBSERVATION_URL.format(station=station)
    start = datetime.combine(target_date, datetime.min.time()).isoformat() + "Z"
    end = datetime.combine(target_date + timedelta(days=1), datetime.min.time()).isoformat() + "Z"
    headers = {"User-Agent": settings.user_agent, "Accept": "application/geo+json"}
    try:
        with httpx.Client(timeout=20.0, headers=headers, trust_env=False) as client:
            response = client.get(url, params={"start": start, "end": end})
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("NWS observation request failed for %s %s: %s", city_key, target_date, exc)
        return None

    features = payload.get("features", [])
    observations: List[float] = []
    for feature in features:
        properties = feature.get("properties", {})
        temperature = properties.get("temperature", {})
        raw_value = temperature.get("value")
        if raw_value is None:
            continue
        try:
            celsius = float(raw_value)
        except (TypeError, ValueError):
            continue
        observations.append(celsius * 9.0 / 5.0 + 32.0)

    if not observations:
        return None
    return max(observations)
