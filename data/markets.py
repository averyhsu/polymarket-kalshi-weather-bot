"""Kalshi market access and normalization."""

from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from config import Settings
from data.weather import CITY_CONFIG


logger = logging.getLogger(__name__)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
EARLIEST_RECONSTRUCTABLE_MARKET_DATE = date(2025, 12, 23)

MONTH_ABBR = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


@dataclass(frozen=True)
class MarketQuote:
    """Normalized Kalshi market quote for a temperature bucket."""

    ticker: str
    series_ticker: str
    city_key: str
    city_name: str
    target_date: date
    bucket_low: float
    bucket_high: float
    strike_label: str
    title: str
    subtitle: str
    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float
    last_price: float
    volume: float
    open_interest: float
    updated_time: Optional[datetime]
    status: str

    @property
    def spread_cents(self) -> int:
        return int(round(max(0.0, self.yes_ask - self.yes_bid) * 100))

    @property
    def executable_yes_price(self) -> float:
        return self.yes_ask if self.yes_ask > 0.0 else max(self.last_price, self.yes_bid)

    @property
    def executable_no_price(self) -> float:
        return self.no_ask if self.no_ask > 0.0 else max(1.0 - self.last_price, self.no_bid)

    @property
    def mark_price(self) -> float:
        candidates = [price for price in (self.last_price, self.yes_bid, self.yes_ask) if price > 0.0]
        return sum(candidates) / len(candidates) if candidates else 0.5


@dataclass(frozen=True)
class HistoricalMarketDefinition:
    """Historical market metadata used by the backtest engine."""

    ticker: str
    series_ticker: str
    city_key: str
    city_name: str
    target_date: date
    bucket_low: float
    bucket_high: float
    strike_label: str
    title: str
    subtitle: str
    actual_high_f: float
    status: str
    open_time: Optional[datetime]
    close_time: Optional[datetime]
    settlement_time: Optional[datetime]
    volume: float
    open_interest: float
    use_historical_api: bool = False


def earliest_reconstructable_market_date() -> date:
    """Return the earliest verified target date with reconstructable Kalshi bucket history."""

    return EARLIEST_RECONSTRUCTABLE_MARKET_DATE


def _serialize_datetime(value: Optional[datetime]) -> Optional[str]:
    return None if value is None else value.isoformat()


def _deserialize_datetime(value: Optional[str]) -> Optional[datetime]:
    return None if value in (None, "") else datetime.fromisoformat(str(value))


def serialize_historical_market(market: HistoricalMarketDefinition) -> Dict[str, Any]:
    payload = asdict(market)
    payload["target_date"] = market.target_date.isoformat()
    payload["open_time"] = _serialize_datetime(market.open_time)
    payload["close_time"] = _serialize_datetime(market.close_time)
    payload["settlement_time"] = _serialize_datetime(market.settlement_time)
    return payload


def deserialize_historical_market(payload: Dict[str, Any]) -> HistoricalMarketDefinition:
    return HistoricalMarketDefinition(
        ticker=str(payload["ticker"]),
        series_ticker=str(payload["series_ticker"]),
        city_key=str(payload["city_key"]),
        city_name=str(payload["city_name"]),
        target_date=date.fromisoformat(str(payload["target_date"])),
        bucket_low=float(payload["bucket_low"]),
        bucket_high=float(payload["bucket_high"]),
        strike_label=str(payload["strike_label"]),
        title=str(payload["title"]),
        subtitle=str(payload["subtitle"]),
        actual_high_f=float(payload["actual_high_f"]),
        status=str(payload["status"]),
        open_time=_deserialize_datetime(payload.get("open_time")),
        close_time=_deserialize_datetime(payload.get("close_time")),
        settlement_time=_deserialize_datetime(payload.get("settlement_time")),
        volume=float(payload["volume"]),
        open_interest=float(payload["open_interest"]),
        use_historical_api=bool(payload.get("use_historical_api", False)),
    )


def serialize_market_quote(quote: MarketQuote) -> Dict[str, Any]:
    payload = asdict(quote)
    payload["target_date"] = quote.target_date.isoformat()
    payload["updated_time"] = _serialize_datetime(quote.updated_time)
    return payload


def deserialize_market_quote(payload: Dict[str, Any]) -> MarketQuote:
    return MarketQuote(
        ticker=str(payload["ticker"]),
        series_ticker=str(payload["series_ticker"]),
        city_key=str(payload["city_key"]),
        city_name=str(payload["city_name"]),
        target_date=date.fromisoformat(str(payload["target_date"])),
        bucket_low=float(payload["bucket_low"]),
        bucket_high=float(payload["bucket_high"]),
        strike_label=str(payload["strike_label"]),
        title=str(payload["title"]),
        subtitle=str(payload["subtitle"]),
        yes_bid=float(payload["yes_bid"]),
        yes_ask=float(payload["yes_ask"]),
        no_bid=float(payload["no_bid"]),
        no_ask=float(payload["no_ask"]),
        last_price=float(payload["last_price"]),
        volume=float(payload["volume"]),
        open_interest=float(payload["open_interest"]),
        updated_time=_deserialize_datetime(payload.get("updated_time")),
        status=str(payload["status"]),
    )


class KalshiClient:
    """Synchronous Kalshi client with RSA-PSS request signing."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._private_key = None

    def _load_private_key(self):
        if self._private_key is not None:
            return self._private_key
        if not self.settings.kalshi_private_key_path:
            raise ValueError("KALSHI_PRIVATE_KEY_PATH is not configured")
        pem_data = Path(self.settings.kalshi_private_key_path).expanduser().read_bytes()
        self._private_key = serialization.load_pem_private_key(pem_data, password=None)
        return self._private_key

    def _headers(self, method: str, path: str) -> Dict[str, str]:
        timestamp_ms = str(int(time.time() * 1000))
        message = f"{timestamp_ms}{method.upper()}{path}"
        private_key = self._load_private_key()
        signature = private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": str(self.settings.kalshi_api_key_id),
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "Content-Type": "application/json",
            "User-Agent": self.settings.user_agent,
        }

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers: Dict[str, str] = {"User-Agent": self.settings.user_agent}
        if self.settings.kalshi_credentials_present:
            api_path = f"/trade-api/v2{path}"
            headers.update(self._headers("GET", api_path))
        attempts = 4
        with httpx.Client(timeout=20.0, headers=headers, trust_env=False) as client:
            for attempt in range(1, attempts + 1):
                response = client.get(f"{self.settings.effective_kalshi_api_base_url}{path}", params=params)
                if response.status_code not in RETRYABLE_STATUS_CODES or attempt == attempts:
                    response.raise_for_status()
                    return response.json()
                sleep_seconds = min(8.0, 1.5 * attempt)
                logger.warning(
                    "Kalshi GET %s returned %s on attempt %d/%d; retrying in %.1fs",
                    path,
                    response.status_code,
                    attempt,
                    attempts,
                    sleep_seconds,
                )
                time.sleep(sleep_seconds)
        raise RuntimeError(f"unreachable GET retry loop for {path}")

    def post(self, path: str, json_payload: Dict[str, Any]) -> Dict[str, Any]:
        api_path = f"/trade-api/v2{path}"
        headers = self._headers("POST", api_path)
        with httpx.Client(timeout=20.0, headers=headers, trust_env=False) as client:
            response = client.post(f"{self.settings.effective_kalshi_api_base_url}{path}", json=json_payload)
            response.raise_for_status()
            return response.json()

    def preflight_authenticated(self) -> Dict[str, Any]:
        """Perform a harmless authenticated request to validate credentials and target environment."""

        return self.get("/portfolio/positions", params={"limit": 1})

    def list_markets(
        self,
        series_ticker: str,
        cursor: Optional[str] = None,
        *,
        status: str = "open",
        min_settled_ts: Optional[int] = None,
        max_settled_ts: Optional[int] = None,
        historical: bool = False,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"series_ticker": series_ticker, "status": status, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        if min_settled_ts is not None:
            params["min_settled_ts"] = int(min_settled_ts)
        if max_settled_ts is not None:
            params["max_settled_ts"] = int(max_settled_ts)
        path = "/historical/markets" if historical else "/markets"
        return self.get(path, params=params)

    def list_events(
        self,
        series_ticker: str,
        cursor: Optional[str] = None,
        *,
        status: str = "open",
        with_nested_markets: bool = False,
        limit: int = 200,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "series_ticker": series_ticker,
            "status": status,
            "limit": int(limit),
        }
        if cursor:
            params["cursor"] = cursor
        if with_nested_markets:
            params["with_nested_markets"] = "true"
        return self.get("/events", params=params)

    def get_historical_cutoff(self) -> Optional[datetime]:
        payload = self.get("/historical/cutoff")
        raw = payload.get("historical_cutoff_ts") or payload.get("market_settled_ts")
        if raw is None:
            return None
        try:
            if isinstance(raw, str) and "T" in raw:
                return _parse_datetime(raw)
            return datetime.fromtimestamp(int(raw), tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return _parse_datetime(str(raw))

    def get_market_candlesticks(
        self,
        *,
        series_ticker: str,
        ticker: str,
        start_ts: int,
        end_ts: int,
        period_interval: int = 60,
        historical: bool = False,
    ) -> Dict[str, Any]:
        params = {
            "start_ts": int(start_ts),
            "end_ts": int(end_ts),
            "period_interval": int(period_interval),
        }
        if historical:
            path = f"/historical/markets/{ticker}/candlesticks"
        else:
            path = f"/series/{series_ticker}/markets/{ticker}/candlesticks"
        return self.get(path, params=params)

    def create_order(
        self,
        *,
        ticker: str,
        side: str,
        action: str,
        contracts: int,
        price: float,
        client_order_id: str,
    ) -> Dict[str, Any]:
        cents = int(round(price * 100))
        payload = {
            "ticker": ticker,
            "side": side.lower(),
            "action": action.lower(),
            "type": "limit",
            "count": int(contracts),
            "client_order_id": client_order_id,
        }
        if side.upper() == "YES":
            payload["yes_price"] = cents
        else:
            payload["no_price"] = cents
        return self.post("/portfolio/orders", json_payload=payload)


def kalshi_credentials_present(settings: Settings) -> bool:
    """Check whether live Kalshi credentials are configured."""

    return settings.kalshi_credentials_present


def _parse_date_from_ticker(ticker: str) -> Optional[date]:
    match = re.search(r"-(\d{2})([A-Z]{3})(\d{2})-", ticker)
    if not match:
        return None
    year = 2000 + int(match.group(1))
    month = MONTH_ABBR.get(match.group(2))
    day = int(match.group(3))
    if month is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_datetime(raw_value: Optional[str]) -> Optional[datetime]:
    if not raw_value:
        return None
    normalized = raw_value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _convert_cents(raw_value: Any) -> float:
    if raw_value in (None, ""):
        return 0.0
    try:
        return float(raw_value) / 100.0
    except (TypeError, ValueError):
        return 0.0


def _convert_dollars(raw_value: Any) -> float:
    if raw_value in (None, ""):
        return 0.0
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return 0.0


def _parse_actual_high(raw_market: Dict[str, Any]) -> Optional[float]:
    for key in ("expiration_value", "settlement_value", "settlement_value_dollars"):
        raw_value = raw_market.get(key)
        if raw_value in (None, ""):
            continue
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            continue
    return None


def _parse_bucket_from_label(label: str) -> Optional[Tuple[float, float, str]]:
    cleaned = label.lower().replace("degrees", "").replace("degree", "").replace("°f", "").replace("°", "").strip()
    range_match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:to|-)\s*(-?\d+(?:\.\d+)?)", cleaned)
    if range_match:
        lower = float(range_match.group(1))
        upper = float(range_match.group(2))
        if lower > upper:
            lower, upper = upper, lower
        return lower - 0.5, upper + 0.5, f"{int(lower)} to {int(upper)}"

    below_match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:or below|and below|below|under)", cleaned)
    if below_match:
        threshold = float(below_match.group(1))
        return float("-inf"), threshold + 0.5, f"{int(threshold)} or below"

    above_match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:or above|and above|above|over)", cleaned)
    if above_match:
        threshold = float(above_match.group(1))
        return threshold - 0.5, float("inf"), f"{int(threshold)} or above"

    return None


def _fallback_bucket_from_ticker(ticker: str) -> Optional[Tuple[float, float, str]]:
    match = re.search(r"-([BT])(-?\d+(?:\.\d+)?)$", ticker)
    if not match:
        return None
    marker = match.group(1)
    threshold = float(match.group(2))
    if marker == "B":
        return threshold - 0.5, float("inf"), f"{int(threshold)} or above"
    return float("-inf"), threshold + 0.5, f"{int(threshold)} or below"


def _normalize_market(raw_market: Dict[str, Any], city_key: str, settings: Settings) -> Optional[MarketQuote]:
    ticker = str(raw_market.get("ticker", "")).strip()
    target_date = _parse_date_from_ticker(ticker)
    if target_date is None:
        return None

    title = str(raw_market.get("title") or "")
    subtitle = str(raw_market.get("subtitle") or raw_market.get("yes_sub_title") or "")
    strike_label = subtitle or title
    bucket = _parse_bucket_from_label(strike_label) or _fallback_bucket_from_ticker(ticker)
    if bucket is None:
        return None
    bucket_low, bucket_high, normalized_label = bucket

    yes_bid = _convert_dollars(raw_market.get("yes_bid_dollars")) or _convert_cents(raw_market.get("yes_bid"))
    yes_ask = _convert_dollars(raw_market.get("yes_ask_dollars")) or _convert_cents(raw_market.get("yes_ask"))
    no_bid = _convert_dollars(raw_market.get("no_bid_dollars")) or _convert_cents(raw_market.get("no_bid"))
    no_ask = _convert_dollars(raw_market.get("no_ask_dollars")) or _convert_cents(raw_market.get("no_ask"))
    last_price = _convert_dollars(raw_market.get("last_price_dollars")) or _convert_cents(raw_market.get("last_price"))
    volume = float(raw_market.get("volume_fp") or raw_market.get("volume") or 0.0)
    open_interest = float(raw_market.get("open_interest_fp") or raw_market.get("open_interest") or 0.0)
    updated_time = _parse_datetime(raw_market.get("updated_time") or raw_market.get("last_update_time"))
    city_name = str(CITY_CONFIG[city_key]["name"])

    quote = MarketQuote(
        ticker=ticker,
        series_ticker=str(raw_market.get("series_ticker") or CITY_CONFIG[city_key]["kalshi_series"]),
        city_key=city_key,
        city_name=city_name,
        target_date=target_date,
        bucket_low=bucket_low,
        bucket_high=bucket_high,
        strike_label=normalized_label,
        title=title,
        subtitle=subtitle,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
        last_price=last_price if last_price > 0 else max(yes_bid, yes_ask, 0.5),
        volume=volume,
        open_interest=open_interest,
        updated_time=updated_time,
        status=str(raw_market.get("status") or "active"),
    )

    if quote.status.lower() not in {"open", "active"}:
        return None
    if quote.executable_yes_price <= 0.0 and quote.executable_no_price <= 0.0:
        return None
    if quote.volume < settings.min_volume:
        return None
    if quote.spread_cents > settings.max_spread_cents:
        return None
    if settings.skip_same_day and quote.target_date <= date.today():
        return None
    if settings.day_ahead_only and quote.target_date <= date.today():
        return None
    if quote.city_key not in settings.tradable_cities:
        return None
    if quote.updated_time is not None and max(quote.yes_bid, quote.yes_ask, quote.no_bid, quote.no_ask) <= 0.0:
        age_minutes = (datetime.utcnow().replace(tzinfo=quote.updated_time.tzinfo) - quote.updated_time).total_seconds() / 60.0
        if age_minutes > settings.stale_market_minutes:
            return None
    return quote


def fetch_kxhigh_markets(settings: Settings) -> List[MarketQuote]:
    """Fetch and normalize open Kalshi KXHIGH contracts."""

    client = KalshiClient(settings)
    all_quotes: List[MarketQuote] = []
    for city_key in settings.tradable_cities:
        city = CITY_CONFIG.get(city_key)
        if city is None:
            continue
        series_ticker = str(city["kalshi_series"])
        cursor: Optional[str] = None
        while True:
            try:
                payload = client.list_markets(series_ticker, cursor=cursor)
            except httpx.HTTPError as exc:
                logger.warning("Kalshi market request failed for %s: %s", series_ticker, exc)
                break
            raw_markets = payload.get("markets", [])
            for raw_market in raw_markets:
                quote = _normalize_market(raw_market, city_key, settings)
                if quote is not None:
                    all_quotes.append(quote)
            cursor = payload.get("cursor")
            if not cursor or not raw_markets:
                break
    return all_quotes


def _normalize_historical_market(
    raw_market: Dict[str, Any],
    city_key: str,
    *,
    use_historical_api: bool,
) -> Optional[HistoricalMarketDefinition]:
    ticker = str(raw_market.get("ticker", "")).strip()
    target_date = _parse_date_from_ticker(ticker)
    if target_date is None:
        return None

    title = str(raw_market.get("title") or "")
    subtitle = str(raw_market.get("subtitle") or raw_market.get("yes_sub_title") or "")
    strike_label = subtitle or title
    bucket = _parse_bucket_from_label(strike_label) or _fallback_bucket_from_ticker(ticker)
    if bucket is None:
        return None
    bucket_low, bucket_high, normalized_label = bucket
    actual_high = _parse_actual_high(raw_market)
    if actual_high is None:
        return None

    return HistoricalMarketDefinition(
        ticker=ticker,
        series_ticker=str(raw_market.get("series_ticker") or CITY_CONFIG[city_key]["kalshi_series"]),
        city_key=city_key,
        city_name=str(CITY_CONFIG[city_key]["name"]),
        target_date=target_date,
        bucket_low=bucket_low,
        bucket_high=bucket_high,
        strike_label=normalized_label,
        title=title,
        subtitle=subtitle,
        actual_high_f=actual_high,
        status=str(raw_market.get("status") or "settled"),
        open_time=_parse_datetime(raw_market.get("open_time")),
        close_time=_parse_datetime(raw_market.get("close_time")),
        settlement_time=_parse_datetime(raw_market.get("settlement_ts")),
        volume=float(raw_market.get("volume_fp") or raw_market.get("volume") or 0.0),
        open_interest=float(raw_market.get("open_interest_fp") or raw_market.get("open_interest") or 0.0),
        use_historical_api=use_historical_api,
    )


def fetch_historical_market_definitions(
    settings: Settings,
    start_date: date,
    end_date: date,
) -> List[HistoricalMarketDefinition]:
    """Fetch settled KXHIGH markets for a target-date range."""

    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    client = KalshiClient(settings)
    all_markets: Dict[str, HistoricalMarketDefinition] = {}
    for city_key in settings.tradable_cities:
        city = CITY_CONFIG.get(city_key)
        if city is None:
            continue
        series_ticker = str(city["kalshi_series"])
        cursor: Optional[str] = None
        while True:
            try:
                payload = client.list_events(
                    series_ticker,
                    cursor=cursor,
                    status="settled",
                    with_nested_markets=True,
                )
            except httpx.HTTPError as exc:
                logger.warning("Kalshi settled event request failed for %s: %s", series_ticker, exc)
                break
            raw_events = payload.get("events", [])
            if not raw_events:
                break

            oldest_event_date: Optional[date] = None
            for raw_event in raw_events:
                event_date = None
                strike_date = raw_event.get("strike_date")
                if strike_date:
                    try:
                        event_date = date.fromisoformat(str(strike_date))
                    except ValueError:
                        event_date = None
                if event_date is None:
                    event_date = _parse_date_from_ticker(str(raw_event.get("event_ticker", "")))
                if event_date is not None:
                    oldest_event_date = event_date if oldest_event_date is None else min(oldest_event_date, event_date)

                for raw_market in raw_event.get("markets", []):
                    market = _normalize_historical_market(raw_market, city_key, use_historical_api=False)
                    if market is None:
                        continue
                    if not (start_date <= market.target_date <= end_date):
                        continue
                    all_markets[market.ticker] = market

            if oldest_event_date is not None and oldest_event_date < start_date:
                break
            cursor = payload.get("cursor")
            if not cursor:
                break
    return sorted(all_markets.values(), key=lambda item: (item.target_date, item.city_key, item.ticker))


def _candlestick_close(item: Dict[str, Any], key: str) -> float:
    values = item.get(key)
    if not isinstance(values, dict):
        return 0.0
    return _convert_dollars(values.get("close_dollars"))


def fetch_historical_market_quote(
    settings: Settings,
    market: HistoricalMarketDefinition,
    *,
    entry_time_utc: datetime,
    period_interval_minutes: int = 60,
    lookback_hours: int = 6,
) -> Optional[MarketQuote]:
    """Fetch the latest pre-entry historical quote for a settled market."""

    if entry_time_utc.tzinfo is None:
        entry_time_utc = entry_time_utc.replace(tzinfo=timezone.utc)
    else:
        entry_time_utc = entry_time_utc.astimezone(timezone.utc)

    client = KalshiClient(settings)

    start_ts = int((entry_time_utc - timedelta(hours=lookback_hours)).timestamp())
    end_ts = int(entry_time_utc.timestamp())
    try:
        payload = client.get_market_candlesticks(
            series_ticker=market.series_ticker,
            ticker=market.ticker,
            start_ts=start_ts,
            end_ts=end_ts,
            period_interval=period_interval_minutes,
            historical=market.use_historical_api,
        )
    except httpx.HTTPError as exc:
        logger.warning("Kalshi candlestick request failed for %s: %s", market.ticker, exc)
        return None

    candlesticks = payload.get("candlesticks", [])
    if not candlesticks:
        return None
    latest = max(candlesticks, key=lambda item: int(item.get("end_period_ts") or 0))
    yes_bid = _candlestick_close(latest, "yes_bid")
    yes_ask = _candlestick_close(latest, "yes_ask")
    last_price = _candlestick_close(latest, "price")
    if yes_bid <= 0.0 and yes_ask <= 0.0 and last_price <= 0.0:
        return None

    no_bid = max(0.0, min(1.0, 1.0 - yes_ask)) if yes_ask > 0.0 else max(0.0, 1.0 - last_price)
    no_ask = max(0.0, min(1.0, 1.0 - yes_bid)) if yes_bid > 0.0 else max(0.0, 1.0 - last_price)
    return MarketQuote(
        ticker=market.ticker,
        series_ticker=market.series_ticker,
        city_key=market.city_key,
        city_name=market.city_name,
        target_date=market.target_date,
        bucket_low=market.bucket_low,
        bucket_high=market.bucket_high,
        strike_label=market.strike_label,
        title=market.title,
        subtitle=market.subtitle,
        yes_bid=yes_bid,
        yes_ask=yes_ask if yes_ask > 0.0 else max(yes_bid, last_price),
        no_bid=no_bid,
        no_ask=no_ask,
        last_price=last_price if last_price > 0.0 else max(yes_bid, yes_ask, 0.5),
        volume=float(latest.get("volume_fp") or market.volume),
        open_interest=float(latest.get("open_interest_fp") or market.open_interest),
        updated_time=datetime.fromtimestamp(int(latest.get("end_period_ts") or end_ts), tz=timezone.utc),
        status="settled",
    )
