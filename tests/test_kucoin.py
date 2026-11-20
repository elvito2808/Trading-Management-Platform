import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading_platform.exchanges.errors import (
    ExchangeAuthenticationError,
    ExchangeHTTPError,
    ExchangeNetworkError,
    ExchangeRateLimitError,
    ExchangeResponseError,
    ExchangeTimeoutError,
)
from trading_platform.exchanges.fee_policy import FixedFeePolicy, FIXED_TRADING_FEE_RATE
from trading_platform.exchanges.kucoin import KuCoinClient, KuCoinConfig
from trading_platform.exchanges.kucoin_market_data import (
    KuCoinMarketData,
    KuCoinMarketDataConfig,
)
from trading_platform.exchanges.models import (
    BestBidAskQuote,
    FixedTradeSize,
    QuoteFreshness,
    QuoteSource,
)


def response(data=None, status=200, headers=None, raw=None):
    body = raw if raw is not None else json.dumps({"code": "200000", "data": data}).encode()
    return {"status": status, "headers": headers or {}, "body": body}


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


SYMBOL = {
    "symbol": "BNB-USDT",
    "baseCurrency": "BNB",
    "quoteCurrency": "USDT",
    "baseMinSize": "0.001",
    "baseMaxSize": "100",
    "baseIncrement": "0.001",
    "quoteIncrement": "0.01",
    "priceIncrement": "0.1",
    "minFunds": "10",
    "enableTrading": True,
}

BBA = {
    "bestBid": "610.2",
    "bestBidSize": "1.25",
    "bestAsk": "610.3",
    "bestAskSize": "2.5",
    "time": 1700000000000,
}


def test_symbols_and_increment_metadata():
    transport = FakeTransport([response([SYMBOL, {**SYMBOL, "symbol": "BTC-USDT", "baseCurrency": "BTC"}])])
    symbols = KuCoinClient(transport=transport).get_symbols()
    assert {item.symbol for item in symbols} == {"BNB-USDT", "BTC-USDT"}
    assert "/api/v2/symbols" in transport.calls[0][1]

    detail = KuCoinClient(transport=FakeTransport([response(SYMBOL)])).get_symbol("bnb-usdt")
    assert detail.base_min_size == Decimal("0.001")
    assert detail.quantity_increment == Decimal("0.001")
    assert detail.base_precision == 3
    assert detail.min_funds == Decimal("10")
    assert detail.trading_enabled is True


def test_level1_endpoint_parses_quote_and_distinct_timestamps():
    transport = FakeTransport([response(BBA)])
    quote = KuCoinClient(transport=transport).get_best_bid_ask("bnb-usdt")
    assert quote.best_bid == Decimal("610.2")
    assert quote.bid_quantity == Decimal("1.25")
    assert quote.best_ask == Decimal("610.3")
    assert quote.ask_quantity == Decimal("2.5")
    assert quote.spread == Decimal("0.1")
    assert quote.source is QuoteSource.REST
    assert quote.event_timestamp == datetime.fromtimestamp(1700000000, timezone.utc)
    assert quote.received_at.tzinfo == timezone.utc
    assert "/api/v1/market/orderbook/level1?symbol=BNB-USDT" in transport.calls[0][1]
    assert all("level2" not in call[1] for call in transport.calls)


def test_exchange_event_timestamp_normalizes_seconds_milliseconds_and_nanoseconds():
    expected = datetime.fromtimestamp(1700000000, timezone.utc)
    for event_time in (1700000000, 1700000000000, 1700000000000000000):
        quote = KuCoinClient.parse_websocket_quote(
            "BNB-USDT", {**BBA, "time": event_time}
        )
        assert quote.event_timestamp == expected


@pytest.mark.parametrize(
    "field,value",
    [
        ("bestBid", "NaN"),
        ("bestBid", "Infinity"),
        ("bestBidSize", "0"),
        ("bestAsk", "-1"),
        ("bestAskSize", "Infinity"),
        ("bestAsk", "610.2"),
    ],
)
def test_invalid_level1_quote_values_are_rejected(field, value):
    data = {**BBA, field: value}
    with pytest.raises(ExchangeResponseError):
        KuCoinClient(transport=FakeTransport([response(data)])).get_best_bid_ask("BNB-USDT")


def test_missing_level1_fields_and_invalid_websocket_quote_are_rejected():
    with pytest.raises(ExchangeResponseError):
        KuCoinClient(transport=FakeTransport([response({"bestBid": "1"})])).get_best_bid_ask("BNB-USDT")
    with pytest.raises(ExchangeResponseError):
        KuCoinClient.parse_websocket_quote("BNB-USDT", {"bestBid": "NaN"})


def test_balance_fields_are_finite_nonnegative_and_consistent():
    transport = FakeTransport([response([
        {"currency": "BNB", "balance": "2.5", "available": "2", "holds": "0.5", "type": "trade"},
        {"currency": "USDT", "balance": "100", "available": "100", "holds": "0", "type": "main"},
    ])])
    balances = KuCoinClient(
        transport=transport,
        api_key="dummy",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
    ).get_balances()
    assert balances[0].available == Decimal("2")
    assert balances[0].hold == Decimal("0.5")
    assert balances[0].total == Decimal("2.5")
    assert balances[0].retrieved_at.tzinfo == timezone.utc


@pytest.mark.parametrize(
    "balance",
    [
        {"currency": "BNB", "balance": "1", "available": "0.5", "holds": "0.4", "type": "trade"},
        {"currency": "BNB", "balance": "NaN", "available": "1", "holds": "0", "type": "trade"},
        {"currency": "BNB", "balance": "-1", "available": "-1", "holds": "0", "type": "trade"},
        {"currency": "BNB", "available": "1", "holds": "0", "type": "trade"},
    ],
)
def test_inconsistent_or_invalid_balances_are_rejected(balance):
    client = KuCoinClient(
        transport=FakeTransport([response([balance])]),
        api_key="dummy",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
    )
    with pytest.raises(ExchangeResponseError):
        client.get_balances()


def test_empty_balances_and_read_only_authentication():
    client = KuCoinClient(
        transport=FakeTransport([response([])]),
        api_key="dummy",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
    )
    assert client.get_balances() == []
    with pytest.raises(ExchangeAuthenticationError):
        KuCoinClient(transport=FakeTransport([])).get_balances()


@pytest.mark.parametrize(("side", "order_type", "assumption"), [
    ("buy", "market", None),
    ("sell", "limit", "maker"),
])
def test_fixed_fee_is_decimal_and_applied_once_for_each_direction(side, order_type, assumption):
    transport = FakeTransport([response(SYMBOL)])
    client = KuCoinClient(transport=transport)
    order = client.prepare_dry_run_order(
        "BNB-USDT", side, "2", order_type, "610", fee_assumption=assumption
    )
    assert order.validation.valid
    assert order.estimated_notional == Decimal("1220")
    assert order.fee_rate == Decimal("0.001")
    assert order.estimated_fee == Decimal("1.220")
    assert order.fee_status == "fixed-assumption"
    assert order.fee_assumption == ("taker" if order_type == "market" else "maker")
    assert all("trade-fees" not in call[1] for call in transport.calls)


def test_fixed_fee_policy_is_shared_and_rejects_bad_rates():
    assert FIXED_TRADING_FEE_RATE == Decimal("0.001")
    assert FixedFeePolicy().estimate(Decimal("100")) == Decimal("0.100")
    for rate in (Decimal("NaN"), Decimal("Infinity"), Decimal("-0.001")):
        with pytest.raises(ValueError):
            FixedFeePolicy(rate)


def test_market_order_cannot_use_maker_assumption():
    order = KuCoinClient(transport=FakeTransport([response(SYMBOL)])).prepare_dry_run_order(
        "BNB-USDT", "buy", "2", "market", "610", fee_assumption="maker"
    )
    assert not order.validation.valid
    assert "market orders cannot use the maker fee assumption" in order.validation.errors
    assert order.fee_assumption == "taker"


def test_invalid_fee_assumption_is_not_silently_accepted():
    order = KuCoinClient(transport=FakeTransport([response(SYMBOL)])).prepare_dry_run_order(
        "BNB-USDT", "buy", "2", "market", "610", fee_assumption="unknown"
    )
    assert not order.validation.valid
    assert "fee assumption must be maker or taker" in order.validation.errors
    assert order.fee_assumption == "taker"


@pytest.mark.parametrize("quantity", ["NaN", "Infinity", "-Infinity"])
def test_dry_run_rejects_non_finite_quantities_before_calculation(quantity):
    order = KuCoinClient(transport=FakeTransport([response(SYMBOL)])).prepare_dry_run_order(
        "BNB-USDT", "buy", quantity, "limit", "610"
    )
    assert not order.validation.valid
    assert "quantity must be finite" in order.validation.errors
    assert order.estimated_notional is None


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-Infinity"])
def test_dry_run_rejects_non_finite_prices_before_calculation(price):
    order = KuCoinClient(transport=FakeTransport([response(SYMBOL)])).prepare_dry_run_order(
        "BNB-USDT", "buy", "1", "limit", price
    )
    assert not order.validation.valid
    assert "reference price must be finite" in order.validation.errors
    assert order.estimated_notional is None


@pytest.mark.parametrize(("quantity", "price", "expected_error"), [
    ("0", "610", "quantity must be positive"),
    ("-1", "610", "quantity must be positive"),
    ("1", "0", "reference price must be positive"),
    ("1", "-1", "reference price must be positive"),
])
def test_dry_run_rejects_non_positive_quantity_or_price(quantity, price, expected_error):
    order = KuCoinClient(transport=FakeTransport([response(SYMBOL)])).prepare_dry_run_order(
        "BNB-USDT", "buy", quantity, "limit", price
    )
    assert expected_error in order.validation.errors
    assert order.estimated_notional is None


def test_quantity_increment_minimum_maximum_price_increment_and_min_funds():
    client = KuCoinClient(transport=FakeTransport([response(SYMBOL)] * 5))
    increment = client.prepare_dry_run_order("BNB-USDT", "buy", "1.0001", "limit", "610")
    minimum = client.prepare_dry_run_order("BNB-USDT", "buy", "0.0001", "limit", "610")
    maximum = client.prepare_dry_run_order("BNB-USDT", "buy", "100.001", "limit", "610")
    price = client.prepare_dry_run_order("BNB-USDT", "buy", "1", "limit", "610.01")
    funds = client.prepare_dry_run_order("BNB-USDT", "buy", "0.01", "limit", "610")
    assert "quantity does not match baseIncrement" in increment.validation.errors
    assert "quantity is below baseMinSize" in minimum.validation.errors
    assert "quantity exceeds baseMaxSize" in maximum.validation.errors
    assert "reference price does not match priceIncrement" in price.validation.errors
    assert "notional is below minFunds" in funds.validation.errors


def test_dry_run_rejects_disabled_or_unknown_trading_status():
    disabled = KuCoinClient(transport=FakeTransport([
        response({**SYMBOL, "enableTrading": False}),
        response({**SYMBOL, "enableTrading": None}),
    ]))
    for _ in range(2):
        order = disabled.prepare_dry_run_order("BNB-USDT", "buy", "1", "limit", "610")
        assert "symbol is not enabled for trading" in order.validation.errors


def test_order_constraint_boundaries_are_inclusive():
    client = KuCoinClient(transport=FakeTransport([response(SYMBOL)] * 2))
    at_minimum = client.prepare_dry_run_order("BNB-USDT", "buy", "0.1", "limit", "100")
    at_maximum = client.prepare_dry_run_order("BNB-USDT", "sell", "100", "limit", "0.1")
    assert at_minimum.validation.valid
    assert at_maximum.validation.valid


def test_configured_fixed_size_uses_bba_side_price_without_rounding():
    transport = FakeTransport([response(SYMBOL), response(SYMBOL)])
    client = KuCoinClient(transport=transport)
    quote = KuCoinClient.parse_websocket_quote("BNB-USDT", BBA)
    buy = client.prepare_configured_dry_run_order(
        FixedTradeSize("BNB-USDT", Decimal("1.2375")), "buy", quote
    )
    sell = client.prepare_configured_dry_run_order(
        FixedTradeSize("BNB-USDT", Decimal("1.2375")), "sell", quote
    )
    assert buy.quantity == Decimal("1.2375")
    assert buy.reference_price == Decimal("610.3")
    assert sell.reference_price == Decimal("610.2")
    assert buy.quote_source is QuoteSource.WEBSOCKET
    assert buy.quote_event_timestamp == quote.event_timestamp
    assert "quantity does not match baseIncrement" in buy.validation.errors
    assert len(transport.calls) == 2
    assert all("trade-fees" not in call[1] for call in transport.calls)


def test_invalid_symbol_minimum_and_maximum_metadata_is_rejected():
    malformed = {**SYMBOL, "baseMinSize": "101", "baseMaxSize": "100"}
    with pytest.raises(ExchangeResponseError, match="minimum quantity exceeds maximum"):
        KuCoinClient(transport=FakeTransport([response(malformed)])).get_symbol("BNB-USDT")


def test_dry_run_requires_explicit_price_and_never_fetches_quote_or_fee():
    transport = FakeTransport([])
    with pytest.raises(ExchangeResponseError, match="explicit reference price"):
        KuCoinClient(transport=transport).prepare_dry_run_order("BNB-USDT", "buy", "1")
    assert transport.calls == []


def test_stale_or_wrong_symbol_quote_invalidates_configured_dry_run():
    client = KuCoinClient(transport=FakeTransport([response(SYMBOL), response(SYMBOL)]))
    quote = KuCoinClient.parse_websocket_quote("BNB-USDT", BBA)
    stale = BestBidAskQuote(
        **{**quote.__dict__, "freshness": QuoteFreshness.STALE}
    )
    stale_order = client.prepare_configured_dry_run_order(
        FixedTradeSize("BNB-USDT", Decimal("1")), "buy", stale
    )
    wrong = KuCoinClient.parse_websocket_quote("BTC-USDT", BBA)
    wrong_order = client.prepare_configured_dry_run_order(
        FixedTradeSize("BNB-USDT", Decimal("1")), "buy", wrong
    )
    assert "Best Bid/Ask quote is stale" in stale_order.validation.errors
    assert "quote symbol does not match order symbol" in wrong_order.validation.errors


def test_dry_run_rejects_quote_from_another_exchange_or_with_invalid_book():
    client = KuCoinClient(transport=FakeTransport([response(SYMBOL), response(SYMBOL)]))
    quote = KuCoinClient.parse_websocket_quote("BNB-USDT", BBA)
    wrong_exchange = BestBidAskQuote(
        **{**quote.__dict__, "exchange": "other"}
    )
    crossed = BestBidAskQuote(
        **{**quote.__dict__, "best_ask": Decimal("610.2")}
    )
    wrong_order = client.prepare_configured_dry_run_order(
        FixedTradeSize("BNB-USDT", Decimal("1")), "buy", wrong_exchange
    )
    crossed_order = client.prepare_configured_dry_run_order(
        FixedTradeSize("BNB-USDT", Decimal("1")), "buy", crossed
    )
    assert "quote exchange does not match order exchange" in wrong_order.validation.errors
    assert "Best Bid must be lower than Best Ask" in crossed_order.validation.errors


def test_public_websocket_token_response_is_validated():
    token_data = {
        "token": "test-token",
        "instanceServers": [{
            "endpoint": "wss://example.test/endpoint",
            "pingInterval": 18000,
            "pingTimeout": 10000,
        }],
    }
    token = KuCoinClient(transport=FakeTransport([response(token_data)])).get_public_ws_token()
    assert token == {
        "token": "test-token",
        "endpoint": "wss://example.test/endpoint",
        "ping_interval": 18.0,
        "ping_timeout": 10.0,
    }
    malformed = {"token": "token", "instanceServers": [{"endpoint": "ws://insecure"}]}
    with pytest.raises(ExchangeResponseError):
        KuCoinClient(transport=FakeTransport([response(malformed)])).get_public_ws_token()


def test_retry_after_malformed_and_non_json_rate_limit_are_safe():
    sleeps = []
    transport = FakeTransport([
        response(status=429, headers={"Retry-After": "not-a-number"}, raw=b"not json"),
        response(SYMBOL),
    ])
    client = KuCoinClient(transport=transport, sleeper=sleeps.append)
    assert client.get_symbol("BNB-USDT").symbol == "BNB-USDT"
    assert sleeps == [0.5]


def test_retry_after_http_date_is_supported():
    sleeps = []
    transport = FakeTransport([
        response(status=429, headers={"Retry-After": "Wed, 21 Oct 2037 07:28:00 GMT"}),
        response(SYMBOL),
    ])
    client = KuCoinClient(transport=transport, sleeper=sleeps.append)
    client.get_symbol("BNB-USDT")
    assert len(sleeps) == 1
    assert sleeps[0] > 0


def test_retry_after_is_capped_to_configured_bound():
    sleeps = []
    client = KuCoinClient(
        config=KuCoinConfig(max_retries=1, retry_after_max_seconds=3),
        transport=FakeTransport([
            response(status=429, headers={"Retry-After": "1000"}),
            response(SYMBOL),
        ]),
        sleeper=sleeps.append,
    )
    client.get_symbol("BNB-USDT")
    assert sleeps == [3]


def test_timeout_and_network_failures_use_bounded_retries():
    client = KuCoinClient(
        config=KuCoinConfig(max_retries=1),
        transport=FakeTransport([ExchangeTimeoutError("timeout")] * 2),
        sleeper=lambda _: None,
    )
    with pytest.raises(ExchangeTimeoutError):
        client.get_symbol("BNB-USDT")
    client = KuCoinClient(
        config=KuCoinConfig(max_retries=1),
        transport=FakeTransport([ExchangeNetworkError("offline")] * 2),
        sleeper=lambda _: None,
    )
    with pytest.raises(ExchangeNetworkError):
        client.get_symbol("BNB-USDT")


def test_unexpected_programming_errors_are_not_hidden():
    client = KuCoinClient(transport=FakeTransport([RuntimeError("programming fault")]))
    with pytest.raises(RuntimeError, match="programming fault"):
        client.get_symbol("BNB-USDT")


def test_private_balance_access_requires_credentials_and_signs_read_request():
    transport = FakeTransport([response([])])
    client = KuCoinClient(
        transport=transport,
        api_key="dummy-key",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
        clock=lambda: 1700000000,
    )
    signature, passphrase = client.sign("1700000000000", "GET", "/api/v1/accounts", "")
    assert signature and passphrase
    client.get_balances()
    headers = transport.calls[0][2]
    assert headers["KC-API-KEY"] == "dummy-key"
    assert "KC-API-SIGN" in headers
    assert "KC-API-PASSPHRASE" in headers
    assert "/api/v1/accounts" in transport.calls[0][1]


class FakeWebSocket:
    def __init__(self, active_counts, ack_error=False):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.active_counts = active_counts
        self.ack_error = ack_error
        self.closed = False

    async def __aenter__(self):
        self.active_counts["active"] += 1
        self.active_counts["maximum"] = max(
            self.active_counts["maximum"], self.active_counts["active"]
        )
        await self.incoming.put(json.dumps({"type": "welcome"}))
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.active_counts["active"] -= 1
        self.closed = True

    async def recv(self):
        value = await self.incoming.get()
        if isinstance(value, Exception):
            raise value
        return value

    async def send(self, raw):
        message = json.loads(raw)
        self.sent.append(message)
        if message["type"] == "subscribe":
            await self.incoming.put(json.dumps({
                "type": "error" if self.ack_error else "ack",
                "id": message["id"],
            }))
        elif message["type"] == "ping":
            await self.incoming.put(json.dumps({"type": "pong", "id": message["id"]}))

    async def close(self):
        self.closed = True

    async def publish(self, message):
        await self.incoming.put(json.dumps(message))


class FakeWebSocketContext:
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return await self.websocket.__aenter__()

    async def __aexit__(self, exc_type, exc, traceback):
        return await self.websocket.__aexit__(exc_type, exc, traceback)


async def wait_until(predicate, timeout=1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


def run_async(awaitable):
    return asyncio.run(awaitable)


def test_websocket_first_feed_subscribes_once_and_publishes_bba():
    async def scenario():
        client = KuCoinClient(transport=FakeTransport([response(BBA)]))
        counts = {"active": 0, "maximum": 0, "connects": 0}
        sockets = []

        def connector(url, **kwargs):
            assert "token=test-token" in url
            counts["connects"] += 1
            socket = FakeWebSocket(counts)
            sockets.append(socket)
            return FakeWebSocketContext(socket)

        feed = KuCoinMarketData(
            client,
            ["BNB-USDT", "BNB-USDT"],
            token_provider=lambda: {
                "token": "test-token",
                "endpoint": "wss://example.test/endpoint",
                "ping_interval": 0.02,
                "ping_timeout": 0.05,
            },
            websocket_connect=connector,
            config=KuCoinMarketDataConfig(rest_poll_interval_seconds=100),
        )
        await feed.start()
        await feed.start()
        await wait_until(lambda: feed.connection_status.websocket_connected)
        assert counts["connects"] == 1
        assert counts["maximum"] == 1
        assert len(sockets[0].sent) == 1
        assert sockets[0].sent[0]["topic"] == "/market/ticker:BNB-USDT"
        await sockets[0].publish({
            "type": "message",
            "topic": "/market/ticker:BNB-USDT",
            "data": BBA,
        })
        await wait_until(lambda: feed.connection_status.last_message_received_at is not None)
        await wait_until(lambda: any(message["type"] == "ping" for message in sockets[0].sent))
        quote = await feed.get_quote("BNB-USDT")
        assert quote.source is QuoteSource.WEBSOCKET
        assert quote.freshness is QuoteFreshness.FRESH
        await feed.close()
        assert counts["active"] == 0
        assert not feed.connection_status.websocket_connected

    run_async(scenario())


def test_stale_websocket_quote_falls_back_to_rest_then_recovers():
    async def scenario():
        clock = [10.0]
        client = KuCoinClient(transport=FakeTransport([response(BBA)]))
        counts = {"active": 0, "maximum": 0}
        sockets = []

        def connector(url, **kwargs):
            socket = FakeWebSocket(counts)
            sockets.append(socket)
            return FakeWebSocketContext(socket)

        feed = KuCoinMarketData(
            client,
            ["BNB-USDT"],
            token_provider=lambda: {
                "token": "test-token",
                "endpoint": "wss://example.test/endpoint",
                "ping_interval": 10,
                "ping_timeout": 5,
            },
            websocket_connect=connector,
            monotonic=lambda: clock[0],
            config=KuCoinMarketDataConfig(
                max_quote_age_seconds=0.5,
                websocket_initial_wait_seconds=100,
                rest_poll_interval_seconds=100,
            ),
        )
        await feed.start()
        await wait_until(lambda: feed.connection_status.websocket_connected)
        await sockets[0].publish({
            "type": "message",
            "topic": "/market/ticker:BNB-USDT",
            "data": BBA,
        })
        await wait_until(lambda: feed.connection_status.last_message_received_at is not None)
        websocket_quote = await feed.get_quote("BNB-USDT")
        assert websocket_quote.source is QuoteSource.WEBSOCKET
        clock[0] += 1
        rest_quote = await feed.get_quote("BNB-USDT")
        assert rest_quote.source is QuoteSource.REST
        assert rest_quote.freshness is QuoteFreshness.FRESH
        await sockets[0].publish({
            "type": "message",
            "topic": "/market/ticker:BNB-USDT",
            "data": BBA,
        })
        await wait_until(lambda: feed._current_quote("BNB-USDT").source is QuoteSource.WEBSOCKET)
        await feed.close()

    run_async(scenario())


def test_rest_quote_fallback_and_stale_status_are_explicit():
    async def scenario():
        clock = [10.0]
        client = KuCoinClient(transport=FakeTransport([response(BBA), response(BBA)]))
        feed = KuCoinMarketData(
            client,
            ["BNB-USDT"],
            monotonic=lambda: clock[0],
            config=KuCoinMarketDataConfig(
                max_quote_age_seconds=1,
                rest_min_request_interval_seconds=0.01,
            ),
        )
        quote = await feed.get_quote("BNB-USDT")
        assert quote.source is QuoteSource.REST
        assert quote.freshness is QuoteFreshness.FRESH
        clock[0] += 2
        assert feed._current_quote("BNB-USDT").freshness is QuoteFreshness.STALE
        fresh_again = await feed.get_quote("BNB-USDT")
        assert fresh_again.freshness is QuoteFreshness.FRESH

    run_async(scenario())


def test_concurrent_quote_requests_share_fresh_rest_fallback():
    async def scenario():
        transport = FakeTransport([response(BBA)])
        feed = KuCoinMarketData(
            KuCoinClient(transport=transport),
            ["BNB-USDT"],
            config=KuCoinMarketDataConfig(rest_min_request_interval_seconds=0.01),
        )
        first, second = await asyncio.gather(
            feed.get_quote("BNB-USDT"),
            feed.get_quote("BNB-USDT"),
        )
        assert first == second
        assert len(transport.calls) == 1

    run_async(scenario())


def test_websocket_subscription_failure_reconnects_with_single_active_connection():
    async def scenario():
        client = KuCoinClient(transport=FakeTransport([response(BBA)]))
        counts = {"active": 0, "maximum": 0, "connects": 0}

        def connector(url, **kwargs):
            counts["connects"] += 1
            return FakeWebSocketContext(FakeWebSocket(
                counts, ack_error=counts["connects"] == 1
            ))

        feed = KuCoinMarketData(
            client,
            ["BNB-USDT"],
            token_provider=lambda: {
                "token": "test-token",
                "endpoint": "wss://example.test/endpoint",
                "ping_interval": 10,
                "ping_timeout": 5,
            },
            websocket_connect=connector,
            config=KuCoinMarketDataConfig(
                reconnect_initial_seconds=0.01,
                reconnect_max_seconds=0.02,
                rest_poll_interval_seconds=100,
                rest_min_request_interval_seconds=0.01,
            ),
        )
        await feed.start()
        await wait_until(lambda: feed.connection_status.websocket_connected)
        assert counts["connects"] >= 2
        assert counts["maximum"] == 1
        await feed.close()

    run_async(scenario())


def test_websocket_feed_rejects_unconfigured_symbol():
    async def scenario():
        feed = KuCoinMarketData(KuCoinClient(transport=FakeTransport([])), ["BNB-USDT"])
        with pytest.raises(ExchangeResponseError, match="not configured"):
            await feed.get_quote("BTC-USDT")

    run_async(scenario())


def test_http_errors_and_exhausted_rate_limit_are_explicit():
    with pytest.raises(ExchangeHTTPError):
        KuCoinClient(transport=FakeTransport([response(status=400)])).get_symbols()
    with pytest.raises(ExchangeRateLimitError):
        KuCoinClient(
            config=KuCoinConfig(max_retries=0),
            transport=FakeTransport([response(status=429, headers={"Retry-After": "NaN"})]),
        ).get_symbols()
