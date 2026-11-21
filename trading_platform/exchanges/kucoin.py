"""KuCoin Spot read-only REST adapter and local dry-run preparation."""
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
from email.utils import parsedate_to_datetime
from typing import Callable, Optional

from .errors import (
    ExchangeAuthenticationError,
    ExchangeHTTPError,
    ExchangeNetworkError,
    ExchangeRateLimitError,
    ExchangeResponseError,
    ExchangeTimeoutError,
)
from .fee_policy import FixedFeePolicy
from .models import (
    Balance,
    BestBidAskQuote,
    DryRunOrder,
    FixedTradeSize,
    QuoteFreshness,
    QuoteSource,
    SymbolMetadata,
    ValidationResult,
)


@dataclass(frozen=True)
class KuCoinConfig:
    base_url: str = "https://api.kucoin.com"
    timeout: float = 10.0
    max_retries: int = 3
    retry_after_max_seconds: float = 60.0

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.timeout)
            or not math.isfinite(self.retry_after_max_seconds)
            or self.timeout <= 0
            or self.max_retries < 0
            or self.retry_after_max_seconds <= 0
        ):
            raise ValueError("timeout, retry count, and retry-after cap must be valid")


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


def _input_decimal(value, field: str) -> Decimal:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise ExchangeResponseError(f"{field} must be a valid Decimal") from exc


class KuCoinClient:
    """Read-only KuCoin REST client; private access is limited to account reads."""

    def __init__(
        self,
        config: KuCoinConfig | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_passphrase: str | None = None,
        transport: Callable[..., object] | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        fee_policy: FixedFeePolicy | None = None,
    ):
        self.config = config or KuCoinConfig()
        self.api_key = api_key if api_key is not None else os.getenv("KUCOIN_API_KEY")
        self.api_secret = api_secret if api_secret is not None else os.getenv("KUCOIN_API_SECRET")
        self.api_passphrase = api_passphrase if api_passphrase is not None else os.getenv("KUCOIN_API_PASSPHRASE")
        if fee_policy is not None and fee_policy.rate != FixedFeePolicy().rate:
            raise ValueError("KuCoin fee rate is fixed at Decimal('0.001')")
        self.fee_policy = FixedFeePolicy()
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

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, object] | None = None,
        private: bool = False,
    ):
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
            if status == 429:
                retry_after = next(
                    (value for key, value in raw_headers.items() if key.lower() == "retry-after"),
                    None,
                )
                seconds = self._retry_after_seconds(retry_after)
                if seconds is not None:
                    seconds = min(seconds, self.config.retry_after_max_seconds)
                if attempt < retries:
                    self._sleeper(seconds if seconds is not None else 0.5 * (2 ** attempt))
                    continue
                raise ExchangeRateLimitError("KuCoin rate limit exceeded", seconds)
            if status >= 500:
                if attempt < retries:
                    self._sleeper(0.5 * (2 ** attempt))
                    continue
                raise ExchangeHTTPError(f"KuCoin service error (HTTP {status})")
            if status in (401, 403):
                raise ExchangeAuthenticationError("KuCoin authentication failed")
            if status >= 400:
                raise ExchangeHTTPError(f"KuCoin HTTP error {status}")
            try:
                raw_body = response["body"]
                if isinstance(raw_body, bytes):
                    raw_body = raw_body.decode("utf-8")
                payload = json.loads(raw_body)
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError, KeyError) as exc:
                raise ExchangeResponseError("KuCoin returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise ExchangeResponseError("Unexpected KuCoin response structure")
            code = payload.get("code")
            if code not in (None, "200000"):
                raise ExchangeResponseError(f"KuCoin API returned error code {code}")
            return payload.get("data", payload)
        raise ExchangeNetworkError("KuCoin request retry limit reached")

    def get_public_ws_token(self) -> dict:
        """Return the public WebSocket token and endpoint metadata from KuCoin."""
        data = self._request("POST", "/api/v1/bullet-public")
        if not isinstance(data, dict) or not isinstance(data.get("token"), str):
            raise ExchangeResponseError("Malformed public WebSocket token response")
        servers = data.get("instanceServers")
        if not isinstance(servers, list):
            raise ExchangeResponseError("Malformed public WebSocket server list")
        server = next(
            (
                item for item in servers
                if isinstance(item, dict)
                and isinstance(item.get("endpoint"), str)
                and item["endpoint"].startswith("wss://")
            ),
            None,
        )
        if server is None:
            raise ExchangeResponseError("No secure public WebSocket endpoint was returned")
        try:
            ping_interval = float(server["pingInterval"]) / 1000
            ping_timeout = float(server["pingTimeout"]) / 1000
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ExchangeResponseError("Invalid public WebSocket heartbeat metadata") from exc
        if not math.isfinite(ping_interval) or not math.isfinite(ping_timeout) or ping_interval <= 0 or ping_timeout <= 0:
            raise ExchangeResponseError("Invalid public WebSocket heartbeat metadata")
        return {
            "token": data["token"],
            "endpoint": server["endpoint"],
            "ping_interval": ping_interval,
            "ping_timeout": ping_timeout,
        }

    @staticmethod
    def _retry_after_seconds(value) -> float | None:
        if value is None:
            return None
        try:
            seconds = float(value)
            if math.isfinite(seconds) and seconds >= 0:
                return seconds
            return None
        except (TypeError, ValueError, OverflowError):
            pass
        try:
            retry_at = parsedate_to_datetime(str(value))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, retry_at.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    def get_symbols(self) -> list[SymbolMetadata]:
        data = self._request("GET", "/api/v2/symbols")
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ExchangeResponseError("Unexpected symbol-list response")
        return [self._parse_symbol(item) for item in data]

    def get_symbol(self, symbol: str) -> SymbolMetadata:
        symbol = self._validate_symbol_name(symbol)
        data = self._request("GET", f"/api/v2/symbols/{urllib.parse.quote(symbol, safe='-')}")
        if not isinstance(data, dict):
            raise ExchangeResponseError("Unexpected symbol-detail response")
        return self._parse_symbol(data)

    def _parse_symbol(self, item: dict) -> SymbolMetadata:
        symbol = item.get("symbol")
        base_currency = item.get("baseCurrency")
        quote_currency = item.get("quoteCurrency")
        enabled = item.get("enableTrading")
        if not isinstance(symbol, str) or not symbol:
            raise ExchangeResponseError("Symbol metadata has no symbol")
        if not isinstance(base_currency, str) or not base_currency:
            raise ExchangeResponseError("Symbol metadata has no base currency")
        if not isinstance(quote_currency, str) or not quote_currency:
            raise ExchangeResponseError("Symbol metadata has no quote currency")
        if enabled is not None and not isinstance(enabled, bool):
            raise ExchangeResponseError("Symbol trading status must be a Boolean")
        values = {
            "base_min_size": _optional_decimal(item.get("baseMinSize")),
            "base_max_size": _optional_decimal(item.get("baseMaxSize")),
            "quote_min_size": _optional_decimal(item.get("quoteMinSize")),
            "min_funds": _optional_decimal(item.get("minFunds")),
            "price_increment": _optional_decimal(item.get("priceIncrement")),
            "quantity_increment": _optional_decimal(item.get("baseIncrement")),
        }
        if any(value is not None and value <= 0 for value in values.values()):
            raise ExchangeResponseError("Symbol minimums and increments must be positive")
        if (
            values["base_min_size"] is not None
            and values["base_max_size"] is not None
            and values["base_min_size"] > values["base_max_size"]
        ):
            raise ExchangeResponseError("Symbol minimum quantity exceeds maximum quantity")
        return SymbolMetadata(
            exchange="kucoin",
            symbol=symbol,
            base_currency=base_currency,
            quote_currency=quote_currency,
            trading_enabled=enabled,
            base_precision=self._precision(item.get("baseIncrement")),
            quote_precision=self._precision(item.get("quoteIncrement")),
            **values,
        )

    @staticmethod
    def _precision(value) -> Optional[int]:
        decimal = _optional_decimal(value)
        if decimal is None or decimal <= 0:
            return None
        text = format(decimal, "f")
        return max(0, len(text.split(".")[1])) if "." in text else 0

    def get_best_bid_ask(self, symbol: str) -> BestBidAskQuote:
        symbol = self._validate_symbol_name(symbol)
        data = self._request("GET", "/api/v1/market/orderbook/level1", {"symbol": symbol})
        if not isinstance(data, dict):
            raise ExchangeResponseError("Unexpected Best Bid/Ask response")
        return self._parse_quote(symbol, data, QuoteSource.REST)

    @classmethod
    def parse_websocket_quote(cls, symbol: str, data: object) -> BestBidAskQuote:
        if not isinstance(data, dict):
            raise ExchangeResponseError("Malformed WebSocket Best Bid/Ask data")
        return cls._parse_quote(symbol, data, QuoteSource.WEBSOCKET)

    @staticmethod
    def _parse_quote(symbol: str, data: dict, source: QuoteSource) -> BestBidAskQuote:
        try:
            bid = _decimal(data.get("bestBid"), "bestBid")
            bid_size = _decimal(data.get("bestBidSize"), "bestBidSize")
            ask = _decimal(data.get("bestAsk"), "bestAsk")
            ask_size = _decimal(data.get("bestAskSize"), "bestAskSize")
        except ExchangeResponseError:
            raise
        if bid <= 0 or ask <= 0 or bid_size <= 0 or ask_size <= 0:
            raise ExchangeResponseError("Best Bid/Ask values and sizes must be positive")
        if bid >= ask:
            raise ExchangeResponseError("Best Bid must be lower than Best Ask")
        timestamp_field = "Time" if source is QuoteSource.WEBSOCKET else "time"
        return BestBidAskQuote(
            exchange="kucoin",
            symbol=symbol,
            best_bid=bid,
            bid_quantity=bid_size,
            best_ask=ask,
            ask_quantity=ask_size,
            source=source,
            event_timestamp=KuCoinClient._exchange_timestamp(data.get(timestamp_field)),
            received_at=datetime.now(timezone.utc),
            freshness=QuoteFreshness.FRESH,
        )

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
            currency_code = item.get("currency")
            account_type = item.get("type")
            if not isinstance(currency_code, str) or not currency_code:
                raise ExchangeResponseError("Account balance has no currency")
            if not isinstance(account_type, str) or not account_type:
                raise ExchangeResponseError("Account balance has no account type")
            total = _decimal(item.get("balance"), "balance")
            available = _decimal(item.get("available"), "available")
            hold = _decimal(item.get("holds"), "holds")
            if min(total, available, hold) < 0:
                raise ExchangeResponseError("Account balance fields cannot be negative")
            if total != available + hold:
                raise ExchangeResponseError("Account balance total does not equal available plus holds")
            balances.append(Balance(
                currency=currency_code,
                total=total,
                available=available,
                hold=hold,
                account_type=account_type,
                retrieved_at=retrieved,
            ))
        return balances

    def prepare_dry_run_order(
        self,
        symbol: str,
        side: str,
        quantity: object,
        order_type: str = "limit",
        reference_price: object | None = None,
        fee_assumption: str | None = None,
        quote: BestBidAskQuote | None = None,
    ) -> DryRunOrder:
        """Validate a hypothetical order locally; no private or trading request is made."""
        symbol = self._validate_symbol_name(symbol)
        side = str(side).lower()
        order_type = str(order_type).lower()
        if reference_price is None:
            raise ExchangeResponseError(
                "An explicit reference price or a Best Bid/Ask quote is required"
            )
        quantity_decimal = _input_decimal(quantity, "Quantity")
        reference = _input_decimal(reference_price, "Reference price")
        metadata = self.get_symbol(symbol)
        errors: list[str] = []
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
        if metadata.trading_enabled is not True:
            errors.append("symbol is not enabled for trading")

        normalized_fee_assumption = (
            fee_assumption.lower()
            if isinstance(fee_assumption, str)
            else fee_assumption
        )
        if (
            normalized_fee_assumption is not None
            and normalized_fee_assumption not in ("maker", "taker")
        ):
            errors.append("fee assumption must be maker or taker")
            normalized_fee_assumption = None
        if order_type == "market":
            if normalized_fee_assumption == "maker":
                errors.append("market orders cannot use the maker fee assumption")
            selected_assumption = "taker"
        else:
            selected_assumption = normalized_fee_assumption or "taker"

        if quantity_decimal.is_finite() and quantity_decimal > 0:
            if metadata.base_min_size is not None and quantity_decimal < metadata.base_min_size:
                errors.append("quantity is below baseMinSize")
            if metadata.base_max_size is not None and quantity_decimal > metadata.base_max_size:
                errors.append("quantity exceeds baseMaxSize")
        increment = metadata.quantity_increment
        if increment is None:
            errors.append("baseIncrement is unavailable")
        elif increment <= 0:
            errors.append("baseIncrement must be positive")
        elif quantity_decimal.is_finite():
            try:
                if quantity_decimal % increment != 0:
                    errors.append("quantity does not match baseIncrement")
            except DecimalException:
                errors.append("quantity cannot be validated against baseIncrement")
        if order_type == "limit":
            price_increment = metadata.price_increment
            if price_increment is None:
                errors.append("priceIncrement is unavailable")
            elif price_increment <= 0:
                errors.append("priceIncrement must be positive")
            elif reference.is_finite():
                try:
                    if reference % price_increment != 0:
                        errors.append("reference price does not match priceIncrement")
                except DecimalException:
                    errors.append("reference price cannot be validated against priceIncrement")

        notional = None
        if (
            quantity_decimal.is_finite()
            and quantity_decimal > 0
            and reference.is_finite()
            and reference > 0
        ):
            try:
                notional = quantity_decimal * reference
                if not notional.is_finite():
                    notional = None
                    errors.append("notional must be finite")
                elif metadata.min_funds is not None and notional < metadata.min_funds:
                    errors.append("notional is below minFunds")
            except DecimalException:
                notional = None
                errors.append("notional cannot be calculated")
        estimated_fee = None
        if notional is not None:
            try:
                estimated_fee = self.fee_policy.estimate(notional)
            except (ValueError, DecimalException):
                errors.append("fee estimate cannot be calculated")

        if quote is not None:
            if quote.symbol != symbol:
                errors.append("quote symbol does not match order symbol")
            if not isinstance(quote.exchange, str) or quote.exchange.casefold() != "kucoin":
                errors.append("quote exchange does not match order exchange")
            quote_values = (
                quote.best_bid,
                quote.bid_quantity,
                quote.best_ask,
                quote.ask_quantity,
            )
            quote_values_finite = all(
                isinstance(value, Decimal) and value.is_finite()
                for value in quote_values
            )
            if not quote_values_finite:
                errors.append("Best Bid/Ask quote values must be finite Decimals")
            elif any(value <= 0 for value in quote_values):
                errors.append("Best Bid/Ask quote values and sizes must be positive")
            elif quote.best_bid >= quote.best_ask:
                errors.append("Best Bid must be lower than Best Ask")
            if quote.freshness is not QuoteFreshness.FRESH:
                errors.append("Best Bid/Ask quote is stale")
        return DryRunOrder(
            exchange="kucoin",
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity_decimal,
            reference_price=reference,
            estimated_notional=notional,
            fee_rate=self.fee_policy.rate,
            estimated_fee=estimated_fee,
            fee_status="fixed-assumption",
            fee_assumption=selected_assumption,
            quote_source=quote.source if quote is not None else None,
            quote_received_at=quote.received_at if quote is not None else None,
            quote_event_timestamp=quote.event_timestamp if quote is not None else None,
            validation=ValidationResult(valid=not errors, errors=tuple(errors)),
            dry_run=True,
            prepared_at=datetime.now(timezone.utc),
        )

    def prepare_configured_dry_run_order(
        self,
        trade_size: FixedTradeSize,
        side: str,
        quote: BestBidAskQuote,
        order_type: str = "market",
        fee_assumption: str | None = None,
    ) -> DryRunOrder:
        """Use an explicitly configured quantity and a supplied, fresh BBA quote."""
        symbol = self._validate_symbol_name(trade_size.symbol)
        if not isinstance(quote, BestBidAskQuote):
            raise ExchangeResponseError("A Best Bid/Ask quote is required")
        try:
            price = quote.execution_price(side)
        except ValueError as exc:
            raise ExchangeResponseError(str(exc)) from exc
        return self.prepare_dry_run_order(
            symbol=symbol,
            side=side,
            quantity=trade_size.quantity,
            order_type=order_type,
            reference_price=price,
            fee_assumption=fee_assumption,
            quote=quote,
        )

    @staticmethod
    def _exchange_timestamp(value) -> datetime | None:
        if value is None:
            return None
        try:
            numeric = Decimal(str(value))
            if not numeric.is_finite():
                return None
            magnitude = abs(numeric)
            if magnitude >= Decimal("1000000000000000000"):
                seconds = numeric / Decimal("1000000000")
            elif magnitude >= Decimal("1000000000000000"):
                seconds = numeric / Decimal("1000000")
            elif magnitude >= Decimal("100000000000"):
                seconds = numeric / Decimal("1000")
            else:
                seconds = numeric
            timestamp = float(seconds)
            return datetime.fromtimestamp(timestamp, timezone.utc)
        except (InvalidOperation, OverflowError, OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _validate_symbol_name(symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ExchangeResponseError("A non-empty trading symbol is required")
        return symbol.strip().upper()
