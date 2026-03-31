"""Kalshi market access and normalization."""

from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from config import Settings
from data.weather import CITY_CONFIG


logger = logging.getLogger(__name__)

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
        with httpx.Client(timeout=20.0, headers=headers, trust_env=False) as client:
            response = client.get(f"{self.settings.kalshi_api_base_url}{path}", params=params)
            response.raise_for_status()
            return response.json()

    def post(self, path: str, json_payload: Dict[str, Any]) -> Dict[str, Any]:
        api_path = f"/trade-api/v2{path}"
        headers = self._headers("POST", api_path)
        with httpx.Client(timeout=20.0, headers=headers, trust_env=False) as client:
            response = client.post(f"{self.settings.kalshi_api_base_url}{path}", json=json_payload)
            response.raise_for_status()
            return response.json()

    def list_markets(self, series_ticker: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"series_ticker": series_ticker, "status": "open", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        return self.get("/markets", params=params)

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
