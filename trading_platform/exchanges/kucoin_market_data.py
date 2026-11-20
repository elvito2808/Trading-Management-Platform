"""WebSocket-first KuCoin Best Bid/Ask feed with bounded REST fallback."""
import asyncio
import json
import math
import time
import urllib.parse
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Sequence

import websockets

from .errors import ExchangeError, ExchangeResponseError, ExchangeTimeoutError
from .kucoin import KuCoinClient
from .models import (
    BestBidAskQuote,
    MarketDataConnectionStatus,
    QuoteFreshness,
    QuoteSource,
)


@dataclass(frozen=True)
class KuCoinMarketDataConfig:
    max_quote_age_seconds: float = 5.0
    websocket_initial_wait_seconds: float = 1.0
    rest_poll_interval_seconds: float = 2.0
    rest_min_request_interval_seconds: float = 0.25
    reconnect_initial_seconds: float = 0.5
    reconnect_max_seconds: float = 10.0
    connect_timeout_seconds: float = 10.0
    subscription_timeout_seconds: float = 10.0
    close_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        values = (
            self.max_quote_age_seconds,
            self.websocket_initial_wait_seconds,
            self.rest_poll_interval_seconds,
            self.rest_min_request_interval_seconds,
            self.reconnect_initial_seconds,
            self.reconnect_max_seconds,
            self.connect_timeout_seconds,
            self.subscription_timeout_seconds,
            self.close_timeout_seconds,
        )
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("market-data timing values must be positive")
        if self.reconnect_initial_seconds > self.reconnect_max_seconds:
            raise ValueError("initial reconnect delay cannot exceed the maximum")


class _WebSocketProtocolError(ExchangeError):
    pass


class KuCoinMarketData:
    """Owns one public connection and one bounded REST fallback worker."""

    def __init__(
        self,
        client: KuCoinClient,
        symbols: Sequence[str],
        config: KuCoinMarketDataConfig | None = None,
        websocket_connect: Callable | None = None,
        token_provider: Callable | None = None,
        monotonic: Callable[[], float] | None = None,
        utcnow: Callable[[], datetime] | None = None,
    ):
        normalized = tuple(sorted({client._validate_symbol_name(symbol) for symbol in symbols}))
        if not normalized:
            raise ValueError("at least one symbol is required")
        self._client = client
        self._symbols = normalized
        self.config = config or KuCoinMarketDataConfig()
        self._websocket_connect = websocket_connect or websockets.connect
        self._token_provider = token_provider or client.get_public_ws_token
        self._monotonic = monotonic or time.monotonic
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._stop_event: asyncio.Event | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._fallback_task: asyncio.Task | None = None
        self._socket = None
        self._rest_lock: asyncio.Lock | None = None
        self._next_rest_request_at = 0.0
        self._quotes: dict[str, tuple[BestBidAskQuote, float]] = {}
        self._status = MarketDataConnectionStatus(
            websocket_connected=False,
            connecting=False,
            reconnecting=False,
            subscribed_symbols=self._symbols,
        )

    @property
    def connection_status(self) -> MarketDataConnectionStatus:
        return self._status

    async def start(self) -> None:
        if self._supervisor_task is not None:
            return
        self._stop_event = asyncio.Event()
        self._rest_lock = asyncio.Lock()
        self._fallback_task = asyncio.create_task(
            self._rest_fallback_loop(), name="kucoin-rest-bba-fallback"
        )
        self._supervisor_task = asyncio.create_task(
            self._connection_supervisor(), name="kucoin-bba-websocket"
        )

    async def close(self) -> None:
        if self._stop_event is None:
            return
        self._stop_event.set()
        socket = self._socket
        close_error = None
        try:
            if socket is not None:
                await socket.close()
        except (websockets.exceptions.WebSocketException, OSError) as exc:
            close_error = exc
        finally:
            tasks = [
                task for task in (self._supervisor_task, self._fallback_task)
                if task is not None
            ]
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            self._supervisor_task = None
            self._fallback_task = None
            self._socket = None
            self._status = replace(
                self._status,
                websocket_connected=False,
                connecting=False,
                reconnecting=False,
            )
        unexpected = next(
            (
                result for result in results
                if isinstance(result, BaseException)
                and not isinstance(result, asyncio.CancelledError)
            ),
            None,
        )
        if unexpected is not None:
            raise unexpected
        if close_error is not None:
            raise close_error

    async def get_quote(self, symbol: str) -> BestBidAskQuote:
        symbol = self._client._validate_symbol_name(symbol)
        if symbol not in self._symbols:
            raise ExchangeResponseError(f"{symbol} is not configured for this market-data feed")
        cached = self._current_quote(symbol)
        if cached is not None and cached.freshness is QuoteFreshness.FRESH:
            return cached
        try:
            quote = await self._refresh_rest_quote(symbol)
        except ExchangeError:
            if cached is not None:
                return cached
            raise
        return quote

    def _current_quote(self, symbol: str) -> BestBidAskQuote | None:
        stored = self._quotes.get(symbol)
        if stored is None:
            return None
        quote, received_monotonic = stored
        age = self._monotonic() - received_monotonic
        freshness = (
            QuoteFreshness.FRESH
            if 0 <= age <= self.config.max_quote_age_seconds
            else QuoteFreshness.STALE
        )
        return replace(quote, freshness=freshness)

    def _has_fresh_websocket_quote(self, symbol: str) -> bool:
        quote = self._current_quote(symbol)
        return (
            quote is not None
            and quote.source is QuoteSource.WEBSOCKET
            and quote.freshness is QuoteFreshness.FRESH
        )

    async def _refresh_rest_quote(self, symbol: str) -> BestBidAskQuote:
        if self._rest_lock is None:
            self._rest_lock = asyncio.Lock()
        async with self._rest_lock:
            wait = self._next_rest_request_at - self._monotonic()
            if wait > 0:
                await self._wait_or_stop(wait)
                if self._stop_event is not None and self._stop_event.is_set():
                    raise ExchangeTimeoutError("Market-data feed is closing")
            quote = await asyncio.to_thread(self._client.get_best_bid_ask, symbol)
            received_monotonic = self._monotonic()
            self._next_rest_request_at = (
                received_monotonic + self.config.rest_min_request_interval_seconds
            )
            existing = self._current_quote(symbol)
            if (
                existing is None
                or existing.source is not QuoteSource.WEBSOCKET
                or existing.freshness is QuoteFreshness.STALE
            ):
                self._quotes[symbol] = (quote, received_monotonic)
            self._status = replace(self._status, last_error=None)
            return self._current_quote(symbol) or quote

    async def _rest_fallback_loop(self) -> None:
        if self._stop_event is None:
            return
        if await self._wait_or_stop(self.config.websocket_initial_wait_seconds):
            return
        while not self._stop_event.is_set():
            for symbol in self._symbols:
                if self._stop_event.is_set():
                    return
                if not self._has_fresh_websocket_quote(symbol):
                    try:
                        await self._refresh_rest_quote(symbol)
                    except ExchangeError as exc:
                        self._set_last_error(exc)
                if await self._wait_or_stop(self.config.rest_poll_interval_seconds):
                    return

    async def _connection_supervisor(self) -> None:
        if self._stop_event is None:
            return
        attempt = 0
        while not self._stop_event.is_set():
            self._status = replace(
                self._status,
                websocket_connected=False,
                connecting=True,
                reconnecting=attempt > 0,
            )
            try:
                token = await asyncio.to_thread(self._token_provider)
                url = self._websocket_url(token)
                async with self._websocket_connect(
                    url,
                    ping_interval=None,
                    open_timeout=self.config.connect_timeout_seconds,
                    close_timeout=self.config.close_timeout_seconds,
                    max_size=1_048_576,
                ) as socket:
                    self._socket = socket
                    await self._subscribe(socket, token)
                    self._status = replace(
                        self._status,
                        websocket_connected=True,
                        connecting=False,
                        reconnecting=False,
                        last_error=None,
                    )
                    await self._read_messages(socket, token)
            except asyncio.CancelledError:
                raise
            except (
                ExchangeError,
                asyncio.TimeoutError,
                TimeoutError,
                OSError,
                websockets.exceptions.WebSocketException,
            ) as exc:
                self._set_last_error(exc)
            finally:
                self._socket = None
                self._status = replace(
                    self._status,
                    websocket_connected=False,
                    connecting=False,
                )
            if self._stop_event.is_set():
                return
            delay = min(
                self.config.reconnect_initial_seconds * (2 ** min(attempt, 20)),
                self.config.reconnect_max_seconds,
            )
            attempt += 1
            self._status = replace(self._status, reconnecting=True)
            if await self._wait_or_stop(delay):
                return

    @staticmethod
    def _websocket_url(token: dict) -> str:
        endpoint = token["endpoint"]
        separator = "&" if "?" in endpoint else "?"
        query = urllib.parse.urlencode({
            "token": token["token"],
            "connectId": uuid.uuid4().hex,
        })
        return endpoint + separator + query

    async def _subscribe(self, socket, token: dict) -> None:
        welcome = await asyncio.wait_for(
            socket.recv(), timeout=self.config.subscription_timeout_seconds
        )
        message = self._decode_message(welcome)
        if message.get("type") != "welcome":
            raise ExchangeResponseError("KuCoin WebSocket did not send a welcome message")
        pending: dict[str, str] = {}
        for symbol in self._symbols:
            request_id = uuid.uuid4().hex
            pending[request_id] = symbol
            await socket.send(json.dumps({
                "id": request_id,
                "type": "subscribe",
                "topic": f"/market/ticker:{symbol}",
                "privateChannel": False,
                "response": True,
            }))
        deadline = self._monotonic() + self.config.subscription_timeout_seconds
        while pending:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise ExchangeTimeoutError("KuCoin WebSocket subscription acknowledgement timed out")
            raw = await asyncio.wait_for(socket.recv(), timeout=remaining)
            try:
                response = self._decode_message(raw)
                if response.get("type") == "error":
                    raise _WebSocketProtocolError("KuCoin rejected a WebSocket subscription")
                if response.get("type") == "ack":
                    pending.pop(str(response.get("id")), None)
                elif response.get("type") == "message":
                    self._process_quote_message(response)
            except ExchangeResponseError as exc:
                self._set_last_error(exc)
            except _WebSocketProtocolError:
                raise
    async def _read_messages(self, socket, token: dict) -> None:
        interval = token["ping_interval"]
        heartbeat_timeout = token["ping_timeout"]
        next_ping = self._monotonic() + interval
        ping_deadline: float | None = None
        ping_id: str | None = None
        while self._stop_event is not None and not self._stop_event.is_set():
            now = self._monotonic()
            next_deadline = ping_deadline if ping_deadline is not None else next_ping
            timeout = max(0.001, next_deadline - now)
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                now = self._monotonic()
                if ping_deadline is not None and now >= ping_deadline:
                    raise ExchangeTimeoutError("KuCoin WebSocket heartbeat timed out")
                if ping_deadline is None and now >= next_ping:
                    ping_id = uuid.uuid4().hex
                    await socket.send(json.dumps({"id": ping_id, "type": "ping"}))
                    ping_deadline = now + heartbeat_timeout
                continue
            try:
                message = self._decode_message(raw)
                if message.get("type") == "error":
                    raise _WebSocketProtocolError("KuCoin WebSocket reported a connection error")
                if message.get("type") == "pong":
                    if ping_id is None or str(message.get("id")) == ping_id:
                        ping_id = None
                        ping_deadline = None
                        next_ping = self._monotonic() + interval
                elif message.get("type") == "message":
                    self._process_quote_message(message)
            except ExchangeResponseError as exc:
                self._set_last_error(exc)
            except _WebSocketProtocolError:
                raise

    @staticmethod
    def _decode_message(raw) -> dict:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            message = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise ExchangeResponseError("KuCoin WebSocket returned malformed JSON") from exc
        if not isinstance(message, dict):
            raise ExchangeResponseError("KuCoin WebSocket returned a malformed message")
        return message

    def _process_quote_message(self, message: dict) -> None:
        topic = message.get("topic")
        data = message.get("data")
        if not isinstance(topic, str) or not topic.startswith("/market/ticker:"):
            return
        symbol = topic.partition(":")[2]
        if symbol not in self._symbols:
            return
        quote = self._client.parse_websocket_quote(symbol, data)
        self._quotes[symbol] = (quote, self._monotonic())
        self._status = replace(
            self._status,
            last_message_received_at=self._utcnow(),
            last_error=None,
        )

    def _set_last_error(self, error: BaseException) -> None:
        self._status = replace(self._status, last_error=type(error).__name__)

    async def _wait_or_stop(self, seconds: float) -> bool:
        if self._stop_event is None:
            return True
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False
