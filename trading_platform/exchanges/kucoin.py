"""KuCoin Spot read-only REST adapter.

This module intentionally exposes no order, cancel, transfer, withdrawal,
deposit, or account-changing operation.
"""
import base64
import hashlib
import hmac
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, DecimalException, InvalidOperation
from typing import Callable, Optional

from .errors import (
    ExchangeAuthenticationError,
    ExchangeHTTPError,
    ExchangeNetworkError,
    ExchangeRateLimitError,
    ExchangeResponseError,
    ExchangeTimeoutError,
)
from .models import (
    Balance,
    DryRunOrder,
    FeeFallback,
    FeeInfo,
    OrderBook,
    OrderBookLevel,
    SymbolMetadata,
    Ticker,
    ValidationResult,
)


@dataclass(frozen=True)
class KuCoinConfig:
    base_url: str = "https://api.kucoin.com"
    timeout: float = 10.0
    max_retries: int = 3
    fee_fallback_enabled: bool = False
    fee_fallback: FeeFallback | None = None


def _decimal(value, field: str) -> Decimal:
    if value is None:
        raise ExchangeResponseError(f"Missing required Decimal field: {field}")
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise ExchangeResponseError(f"Invalid Decimal field: {field}") from exc
    if not result.is_finite():
        raise ExchangeResponseError(f"Invalid Decimal field: {field}")
    return result


def _optional_decimal(value) -> Optional[Decimal]:
    if value is None or str(value).strip() == "":
        return None
    return _decimal(value, "optional numeric field")


class KuCoinClient:
    """Read-only KuCoin REST client. Private methods query account data only."""

    def __init__(
        self,
        config: KuCoinConfig | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_passphrase: str | None = None,
        transport: Callable[..., object] | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ):
        self.config = config or KuCoinConfig()
        self.api_key = api_key if api_key is not None else os.getenv("KUCOIN_API_KEY")
        self.api_secret = api_secret if api_secret is not None else os.getenv("KUCOIN_API_SECRET")
        self.api_passphrase = api_passphrase if api_passphrase is not None else os.getenv("KUCOIN_API_PASSPHRASE")
        self._transport = transport or self._urllib_transport
        self._clock = clock or time.time
        self._sleeper = sleeper or time.sleep

    def _urllib_transport(self, method: str, url: str, headers: dict[str, str], body: bytes) -> object:
        request = urllib.request.Request(url, data=body if body else None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                return {
                    "status": response.status,
                    "headers": dict(response.headers.items()),
                    "body": response.read(),
                }
        except urllib.error.HTTPError as exc:
            return {
                "status": exc.code,
                "headers": dict(exc.headers.items()) if exc.headers else {},
                "body": exc.read(),
            }
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise ExchangeTimeoutError("KuCoin request timed out") from exc
            raise ExchangeNetworkError("KuCoin network request failed") from exc
        except TimeoutError as exc:
            raise ExchangeTimeoutError("KuCoin request timed out") from exc

    def sign(self, timestamp: str, method: str, endpoint: str, body: str) -> tuple[str, str]:
        if not self.api_key or not self.api_secret or not self.api_passphrase:
            raise ExchangeAuthenticationError("KuCoin API credentials are not configured")
        prehash = timestamp + method.upper() + endpoint + body
        signature = base64.b64encode(
            hmac.new(self.api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
        ).decode()
        passphrase = base64.b64encode(
            hmac.new(self.api_secret.encode(), self.api_passphrase.encode(), hashlib.sha256).digest()
        ).decode()
        return signature, passphrase

    def _request(self, method: str, path: str, params: dict[str, object] | None = None, private: bool = False) -> dict:
        query = urllib.parse.urlencode(params or {}, doseq=True)
        endpoint = path + ("?" + query if query else "")
        url = self.config.base_url.rstrip("/") + endpoint
        body = ""
        headers = {"Accept": "application/json", "User-Agent": "Trading-Management-Platform/1.0"}
        if private:
            timestamp = str(int(self._clock() * 1000))
            signature, passphrase = self.sign(timestamp, method, endpoint, body)
            headers.update({
                "KC-API-KEY": self.api_key,
                "KC-API-SIGN": signature,
                "KC-API-TIMESTAMP": timestamp,
                "KC-API-PASSPHRASE": passphrase,
                "KC-API-KEY-VERSION": "2",
            })

        retries = max(0, self.config.max_retries)
        for attempt in range(retries + 1):
            try:
                response = self._transport(method, url, headers, body.encode())
            except (ExchangeTimeoutError, ExchangeNetworkError):
                if attempt < retries:
                    self._sleeper(0.5 * (2 ** attempt))
                    continue
                raise

            status = int(response["status"])
            raw_headers = response["headers"]
            raw_body = response["body"]
            try:
                if isinstance(raw_body, bytes):
                    raw_body = raw_body.decode("utf-8")
                payload = json.loads(raw_body)
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as exc:
                raise ExchangeResponseError("KuCoin returned invalid JSON") from exc
            if status == 429:
                retry = next(
                    (value for key, value in raw_headers.items() if key.lower() == "retry-after"),
                    None,
                )
                try:
                    seconds = float(retry) if retry is not None else None
                    if seconds is not None and (not math.isfinite(seconds) or seconds < 0):
                        seconds = None
                except (TypeError, ValueError):
                    seconds = None
                if attempt < retries:
                    self._sleeper(seconds if seconds is not None else 0.5 * (2 ** attempt))
                    continue
                raise ExchangeRateLimitError("KuCoin rate limit exceeded", seconds)
            if status in (401, 403):
                raise ExchangeAuthenticationError("KuCoin authentication failed")
            if status >= 500:
                if attempt < retries:
                    self._sleeper(0.5 * (2 ** attempt))
                    continue
                raise ExchangeHTTPError(f"KuCoin service error (HTTP {status})")
            if status >= 400:
                code = payload.get("code") if isinstance(payload, dict) else None
                raise ExchangeHTTPError(f"KuCoin HTTP error {status}" + (f": {code}" if code else ""))
            if not isinstance(payload, dict):
                raise ExchangeResponseError("Unexpected KuCoin response structure")
            code = payload.get("code")
            if code not in (None, "200000"):
                raise ExchangeResponseError(f"KuCoin API returned error code {code}")
            if "data" not in payload:
                return payload
            return payload["data"]

        raise ExchangeNetworkError("KuCoin request retry limit reached")

    def get_symbols(self) -> list[SymbolMetadata]:
        data = self._request("GET", "/api/v2/symbols")
        if not isinstance(data, list):
            raise ExchangeResponseError("Unexpected symbol-list response")
        if any(not isinstance(item, dict) for item in data):
            raise ExchangeResponseError("Malformed symbol-list item")
        return [self._parse_symbol(item) for item in data]

    def get_symbol(self, symbol: str) -> SymbolMetadata:
        symbol = self._validate_symbol_name(symbol)
        data = self._request("GET", f"/api/v2/symbols/{urllib.parse.quote(symbol, safe='-')}")
        if not isinstance(data, dict):
            raise ExchangeResponseError("Unexpected symbol-detail response")
        return self._parse_symbol(data)

    def _parse_symbol(self, item: dict) -> SymbolMetadata:
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ExchangeResponseError("Symbol metadata has no symbol")
        return SymbolMetadata(
            exchange="kucoin",
            symbol=symbol,
            base_currency=str(item.get("baseCurrency") or ""),
            quote_currency=str(item.get("quoteCurrency") or ""),
            base_min_size=_optional_decimal(item.get("baseMinSize")),
            base_max_size=_optional_decimal(item.get("baseMaxSize")),
            quote_min_size=_optional_decimal(item.get("quoteMinSize")),
            min_funds=_optional_decimal(item.get("minFunds")),
            price_increment=_optional_decimal(item.get("priceIncrement")),
            quantity_increment=_optional_decimal(item.get("baseIncrement")),
            base_precision=self._precision(item.get("baseIncrement")),
            quote_precision=self._precision(item.get("quoteIncrement")),
            trading_status=str(item.get("enableTrading", "unknown")).lower(),
        )

    @staticmethod
    def _precision(value) -> Optional[int]:
        decimal = _optional_decimal(value)
        if decimal is None or decimal <= 0:
            return None
        text = format(decimal, "f")
        return max(0, len(text.split(".")[1])) if "." in text else 0

    def get_ticker(self, symbol: str) -> Ticker:
        symbol = self._validate_symbol_name(symbol)
        data = self._request("GET", "/api/v1/market/allTickers")
        if not isinstance(data, dict) or not isinstance(data.get("ticker"), list):
            raise ExchangeResponseError("Unexpected ticker response")
        if any(not isinstance(item, dict) for item in data["ticker"]):
            raise ExchangeResponseError("Malformed ticker item")
        match = next((item for item in data["ticker"] if item.get("symbol") == symbol), None)
        if match is None:
            raise ExchangeResponseError(f"No ticker data returned for {symbol}")
        retrieved_at = datetime.now(timezone.utc)
        return Ticker(
            exchange="kucoin",
            symbol=symbol,
            price=_optional_decimal(match.get("last")),
            bid=_optional_decimal(match.get("buy")),
            ask=_optional_decimal(match.get("sell")),
            retrieved_at=retrieved_at,
            event_timestamp=self._exchange_timestamp(data.get("time")),
            high_24h=_optional_decimal(match.get("high")),
            low_24h=_optional_decimal(match.get("low")),
            change_24h=_optional_decimal(match.get("changeRate")),
            volume_24h=_optional_decimal(match.get("vol")),
        )

    def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        symbol = self._validate_symbol_name(symbol)
        if depth != 20:
            raise ValueError("KuCoin REST snapshot supports depth 20")
        data = self._request("GET", "/api/v1/market/orderbook/level2_20", {"symbol": symbol})
        if not isinstance(data, dict):
            raise ExchangeResponseError("Unexpected order-book response")
        bids = data.get("bids")
        asks = data.get("asks")
        if not isinstance(bids, list) or not isinstance(asks, list):
            raise ExchangeResponseError("Malformed order-book response")
        sequence = data.get("sequence")
        try:
            sequence = int(sequence) if sequence is not None else None
        except (TypeError, ValueError):
            sequence = None
        return OrderBook(
            exchange="kucoin",
            symbol=symbol,
            retrieved_at=datetime.now(timezone.utc),
            event_timestamp=self._exchange_timestamp(data.get("time")),
            sequence=sequence,
            bids=tuple(self._parse_level(row) for row in bids),
            asks=tuple(self._parse_level(row) for row in asks),
            snapshot=True,
        )

    @staticmethod
    def _parse_level(row) -> OrderBookLevel:
        if not isinstance(row, list) or len(row) < 2:
            raise ExchangeResponseError("Malformed order-book level")
        return OrderBookLevel(price=_decimal(row[0], "book price"), quantity=_decimal(row[1], "book quantity"))

    def get_balances(self, currency: str | None = None) -> list[Balance]:
        params = {"currency": currency} if currency else None
        data = self._request("GET", "/api/v1/accounts", params, private=True)
        if not isinstance(data, list):
            raise ExchangeResponseError("Unexpected account-balance response")
        retrieved = datetime.now(timezone.utc)
        balances = []
        for item in data:
            if not isinstance(item, dict):
                raise ExchangeResponseError("Malformed account-balance item")
            available = _optional_decimal(item.get("available")) or Decimal("0")
            hold = _optional_decimal(item.get("holds")) or Decimal("0")
            total = _optional_decimal(item.get("balance"))
            if total is None:
                total = available + hold
            balances.append(Balance(
                currency=str(item.get("currency") or ""),
                total=total,
                available=available,
                hold=hold,
                account_type=str(item.get("type") or "trade"),
                retrieved_at=retrieved,
            ))
        return balances

    def get_fees(
        self,
        symbol: str,
        fallback: FeeFallback | None = None,
        fallback_enabled: bool | None = None,
    ) -> FeeInfo:
        symbol = self._validate_symbol_name(symbol)
        fallback_enabled = (
            self.config.fee_fallback_enabled if fallback_enabled is None else fallback_enabled
        )
        fallback = fallback or self.config.fee_fallback
        try:
            data = self._request(
                "GET", "/api/v1/trade-fees", {"symbols": symbol}, private=True
            )
            if not isinstance(data, list):
                raise ExchangeResponseError("Unexpected fee response")
            item = next(
                (
                    value for value in data
                    if isinstance(value, dict) and value.get("symbol") == symbol
                ),
                None,
            )
            if item is None:
                raise ExchangeResponseError(f"No fee data returned for {symbol}")
            maker = self._fee_rate(item.get("makerFeeRate"), "makerFeeRate")
            taker = self._fee_rate(item.get("takerFeeRate"), "takerFeeRate")
            return FeeInfo(
                exchange="kucoin",
                symbol=symbol,
                maker_rate=maker,
                taker_rate=taker,
                source="official KuCoin pair-specific trade-fees endpoint",
                status="live",
                retrieved_at=datetime.now(timezone.utc),
                verified=True,
            )
        except (
            ExchangeAuthenticationError,
            ExchangeHTTPError,
            ExchangeNetworkError,
            ExchangeRateLimitError,
            ExchangeResponseError,
        ) as exc:
            warning = f"Live fee request unavailable: {type(exc).__name__}"
            if (
                fallback_enabled
                and fallback is not None
                and fallback.exchange.casefold() == "kucoin"
                and fallback.symbol in (None, symbol)
            ):
                maker = self._fee_rate(fallback.maker_rate, "fallback maker rate")
                taker = self._fee_rate(fallback.taker_rate, "fallback taker rate")
                return FeeInfo(
                    exchange="kucoin",
                    symbol=symbol,
                    maker_rate=maker,
                    taker_rate=taker,
                    source=fallback.source,
                    status="fallback",
                    retrieved_at=datetime.now(timezone.utc),
                    verified=fallback.verified,
                    warning=warning,
                )
            if fallback_enabled and fallback is not None:
                warning += "; configured fallback exchange or symbol does not match the request"
            return FeeInfo(
                exchange="kucoin",
                symbol=symbol,
                maker_rate=None,
                taker_rate=None,
                source="live-request",
                status="unavailable",
                retrieved_at=datetime.now(timezone.utc),
                verified=None,
                warning=warning,
            )

    def prepare_dry_run_order(
        self,
        symbol: str,
        side: str,
        quantity: object,
        order_type: str = "limit",
        reference_price: object | None = None,
        fee_info: FeeInfo | None = None,
        fee_assumption: str | None = None,
    ) -> DryRunOrder:
        """Prepare locally only. This method never creates HTTP trading requests."""
        symbol = self._validate_symbol_name(symbol)
        side = str(side).lower()
        order_type = str(order_type).lower()
        try:
            quantity_decimal = Decimal(str(quantity).strip())
        except (InvalidOperation, AttributeError, ValueError) as exc:
            raise ExchangeResponseError("Quantity must be a valid Decimal") from exc
        if reference_price is None:
            ticker = self.get_ticker(symbol)
            if ticker.price is None:
                raise ExchangeResponseError("No reference price is available")
            reference = ticker.price
        else:
            try:
                reference = Decimal(str(reference_price).strip())
            except (InvalidOperation, AttributeError, ValueError) as exc:
                raise ExchangeResponseError("Reference price must be a valid Decimal") from exc
        metadata = self.get_symbol(symbol)
        errors = []
        if not quantity_decimal.is_finite():
            errors.append("quantity must be finite")
        elif quantity_decimal <= 0:
            errors.append("quantity must be positive")
        if not reference.is_finite():
            errors.append("reference price must be finite")
        elif reference <= 0:
            errors.append("reference price must be positive")
        if side not in ("buy", "sell"):
            errors.append("side must be buy or sell")
        if order_type not in ("market", "limit"):
            errors.append("order type must be market or limit")
        if metadata.trading_status.lower() not in ("true", "enabled", "active"):
            errors.append("symbol is not enabled for trading")
        if order_type == "market":
            if fee_assumption == "maker":
                errors.append("market orders cannot use the maker fee assumption")
            selected_fee_assumption = "taker"
        else:
            selected_fee_assumption = fee_assumption or "taker"
            if selected_fee_assumption not in ("maker", "taker"):
                errors.append("fee assumption must be maker or taker")
        if quantity_decimal.is_finite():
            if metadata.base_min_size is not None and quantity_decimal < metadata.base_min_size:
                errors.append("quantity is below baseMinSize")
            if metadata.base_max_size is not None and quantity_decimal > metadata.base_max_size:
                errors.append("quantity exceeds baseMaxSize")
        if metadata.quantity_increment is not None:
            increment = metadata.quantity_increment
            if increment > 0 and quantity_decimal.is_finite():
                try:
                    if quantity_decimal % increment != 0:
                        errors.append("quantity does not match baseIncrement")
                except DecimalException:
                    errors.append("quantity cannot be validated against baseIncrement")
        if (
            order_type == "limit"
            and metadata.price_increment is not None
            and metadata.price_increment > 0
            and reference.is_finite()
        ):
            try:
                if reference % metadata.price_increment != 0:
                    errors.append("reference price does not match priceIncrement")
            except DecimalException:
                errors.append("reference price cannot be validated against priceIncrement")

        notional = None
        if quantity_decimal.is_finite() and reference.is_finite():
            try:
                notional = quantity_decimal * reference
                if not notional.is_finite():
                    notional = None
                    errors.append("notional must be finite")
                if (
                    notional is not None
                    and metadata.min_funds is not None
                    and notional < metadata.min_funds
                ):
                    errors.append("notional is below minFunds")
            except DecimalException:
                notional = None
                errors.append("notional cannot be calculated")

        maker_fee = None
        taker_fee = None
        fee_status = "unavailable"
        if fee_info is not None and fee_info.status in ("live", "fallback"):
            if fee_info.exchange.casefold() != "kucoin":
                errors.append("fee data exchange does not match the order exchange")
                fee_status = "invalid"
            elif fee_info.symbol is not None and fee_info.symbol != symbol:
                errors.append("fee data symbol does not match order symbol")
                fee_status = "invalid"
            elif notional is not None:
                try:
                    if order_type != "market" and fee_info.maker_rate is not None:
                        maker_rate = self._fee_rate(fee_info.maker_rate, "maker rate")
                        maker_fee = notional * maker_rate
                    if fee_info.taker_rate is not None:
                        taker_rate = self._fee_rate(fee_info.taker_rate, "taker rate")
                        taker_fee = notional * taker_rate
                    if (
                        (maker_fee is not None and not maker_fee.is_finite())
                        or (taker_fee is not None and not taker_fee.is_finite())
                    ):
                        raise DecimalException("fee estimate is not finite")
                    selected_rate = (
                        fee_info.taker_rate if selected_fee_assumption == "taker"
                        else fee_info.maker_rate
                    )
                    fee_status = fee_info.status if selected_rate is not None else "unavailable"
                except ExchangeResponseError:
                    errors.append("fee data contains an invalid rate")
                    fee_status = "invalid"
                except DecimalException:
                    errors.append("fee estimate cannot be calculated")
                    fee_status = "invalid"
        return DryRunOrder(
            exchange="kucoin",
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity_decimal,
            reference_price=reference,
            estimated_notional=notional,
            estimated_maker_fee=maker_fee,
            estimated_taker_fee=taker_fee,
            fee_assumption=selected_fee_assumption,
            fee_status=fee_status,
            validation=ValidationResult(valid=not errors, errors=tuple(errors)),
            dry_run=True,
            prepared_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _fee_rate(value, field: str) -> Decimal:
        rate = _decimal(value, field)
        if rate < 0:
            raise ExchangeResponseError(f"Invalid fee rate: {field}")
        return rate

    @staticmethod
    def _exchange_timestamp(value) -> datetime | None:
        if value is None:
            return None
        try:
            numeric = Decimal(str(value))
            if not numeric.is_finite():
                return None
            timestamp = float(numeric / Decimal("1000") if abs(numeric) >= Decimal("100000000000") else numeric)
            return datetime.fromtimestamp(timestamp, timezone.utc)
        except (InvalidOperation, OverflowError, OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _validate_symbol_name(symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ExchangeResponseError("A non-empty trading symbol is required")
        return symbol.strip().upper()
