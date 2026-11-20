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
from trading_platform.exchanges.kucoin import KuCoinClient, KuCoinConfig
from trading_platform.exchanges.models import FeeFallback, FeeInfo


def response(data=None, status=200, headers=None):
    payload = {"code": "200000", "data": data}
    return {
        "status": status,
        "headers": headers or {},
        "body": json.dumps(payload).encode(),
    }


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


def test_ticker_and_24h_parsing():
    transport = FakeTransport([response({"ticker": [{
        "symbol": "BNB-USDT", "last": "610.25", "buy": "610.20",
        "sell": "610.30", "high": "620", "low": "600",
        "changeRate": "0.012", "vol": "12345",
    }]})])
    client = KuCoinClient(transport=transport)
    ticker = client.get_ticker("bnb-usdt")
    assert ticker.price == Decimal("610.25")
    assert ticker.bid == Decimal("610.20")
    assert ticker.ask == Decimal("610.30")
    assert ticker.spread == Decimal("0.10")
    assert ticker.high_24h == Decimal("620")


def test_multiple_trading_pairs_symbols():
    transport = FakeTransport([response([SYMBOL, {**SYMBOL, "symbol": "BTC-USDT",
                                               "baseCurrency": "BTC"}])])
    symbols = KuCoinClient(transport=transport).get_symbols()
    assert {item.symbol for item in symbols} == {"BNB-USDT", "BTC-USDT"}
    assert "/api/v2/symbols" in transport.calls[0][1]


def test_symbol_detail_and_precision():
    transport = FakeTransport([response(SYMBOL)])
    metadata = KuCoinClient(transport=transport).get_symbol("BNB-USDT")
    assert metadata.base_min_size == Decimal("0.001")
    assert metadata.quantity_increment == Decimal("0.001")
    assert metadata.base_precision == 3
    assert metadata.min_funds == Decimal("10")
    assert "/api/v2/symbols/BNB-USDT" in transport.calls[0][1]


def test_quantity_increment_validation():
    transport = FakeTransport([response(SYMBOL), response(SYMBOL)])
    client = KuCoinClient(transport=transport)
    # Preparing a limit at an explicit reference price avoids a ticker request.
    valid = client.prepare_dry_run_order("BNB-USDT", "buy", "1.234", "limit", "610")
    assert valid.validation.valid
    invalid = client.prepare_dry_run_order("BNB-USDT", "buy", "1.2341", "limit", "610")
    assert not invalid.validation.valid
    assert any("baseIncrement" in error for error in invalid.validation.errors)


def test_minimum_quantity_rejection():
    transport = FakeTransport([response(SYMBOL)])
    order = KuCoinClient(transport=transport).prepare_dry_run_order(
        "BNB-USDT", "buy", "0.0001", "limit", "610"
    )
    assert not order.validation.valid
    assert any("baseMinSize" in error for error in order.validation.errors)


def test_unknown_symbol_is_rejected():
    transport = FakeTransport([response([])])
    with pytest.raises(ExchangeResponseError):
        KuCoinClient(transport=transport).get_symbol("")


def test_order_book_parsing():
    transport = FakeTransport([response({
        "sequence": 1234,
        "bids": [["610.1", "1.2"], ["610.0", "2"]],
        "asks": [["610.2", "0.5"]],
    })])
    book = KuCoinClient(transport=transport).get_order_book("BNB-USDT")
    assert book.sequence == 1234
    assert book.snapshot is True
    assert book.bids[0].price == Decimal("610.1")
    assert book.asks[0].quantity == Decimal("0.5")
    assert book.event_timestamp is None
    assert book.retrieved_at.tzinfo == timezone.utc


def test_order_book_exchange_event_timestamp_is_preserved():
    transport = FakeTransport([response({
        "time": 1700000000000,
        "sequence": 1234,
        "bids": [["610.1", "1.2"]],
        "asks": [["610.2", "0.5"]],
    })])
    book = KuCoinClient(transport=transport).get_order_book("BNB-USDT")
    assert book.event_timestamp == datetime.fromtimestamp(1700000000, timezone.utc)


def test_malformed_or_empty_order_book():
    transport = FakeTransport([response({"bids": None, "asks": []})])
    with pytest.raises(ExchangeResponseError):
        KuCoinClient(transport=transport).get_order_book("BNB-USDT")


def test_missing_ticker_data():
    transport = FakeTransport([response({"ticker": []})])
    with pytest.raises(ExchangeResponseError):
        KuCoinClient(transport=transport).get_ticker("BNB-USDT")


def test_malformed_ticker_item_is_reported():
    transport = FakeTransport([response({"ticker": [None]})])
    with pytest.raises(ExchangeResponseError):
        KuCoinClient(transport=transport).get_ticker("BNB-USDT")


def test_balance_parsing_available_hold_and_type():
    transport = FakeTransport([response([
        {"currency": "BNB", "balance": "2.500", "available": "2.0", "holds": "0.5", "type": "trade"},
        {"currency": "USDT", "balance": "100", "available": "100", "holds": "0", "type": "main"},
    ])])
    balances = KuCoinClient(
        transport=transport,
        api_key="dummy",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
    ).get_balances()
    assert balances[0].available == Decimal("2.0")
    assert balances[0].hold == Decimal("0.5")
    assert balances[0].account_type == "trade"


def test_empty_balances():
    transport = FakeTransport([response([])])
    balances = KuCoinClient(
        transport=transport,
        api_key="dummy",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
    ).get_balances()
    assert balances == []


def test_balance_authentication_failure():
    transport = FakeTransport([response(None, status=401)])
    with pytest.raises(ExchangeAuthenticationError):
        KuCoinClient(
            transport=transport,
            api_key="dummy",
            api_secret="dummy-secret",
            api_passphrase="dummy-pass",
        ).get_balances()


def test_live_pair_fee_maker_taker_and_exact_symbol_selection():
    transport = FakeTransport([response([
        {"symbol": "BTC-USDT", "makerFeeRate": "0.0008", "takerFeeRate": "0.001"},
        {"symbol": "BNB-USDT", "makerFeeRate": "0.001", "takerFeeRate": "0.0015"},
    ])])
    fee = KuCoinClient(
        transport=transport,
        api_key="dummy",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
    ).get_fees("BNB-USDT")
    assert fee.status == "live"
    assert fee.maker_rate == Decimal("0.001")
    assert fee.taker_rate == Decimal("0.0015")
    assert "GET" == transport.calls[0][0]
    assert "/api/v1/trade-fees?symbols=BNB-USDT" in transport.calls[0][1]


def test_configured_fee_fallback_is_explicit():
    transport = FakeTransport([response(None, status=401)])
    fallback = FeeFallback(
        exchange="kucoin", symbol="BNB-USDT", maker_rate=Decimal("0.001"),
        taker_rate=Decimal("0.001"), source="operator configured", verified=False,
    )
    client = KuCoinClient(
        config=KuCoinConfig(fee_fallback_enabled=True, fee_fallback=fallback),
        transport=transport,
        api_key="dummy",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
    )
    fee = client.get_fees("BNB-USDT")
    assert fee.status == "fallback"
    assert fee.maker_rate == Decimal("0.001")
    assert fee.warning
    assert "/api/v1/trade-fees" in transport.calls[0][1]


def test_fee_fallback_disabled_has_unavailable_status():
    transport = FakeTransport([response(None, status=401)])
    fee = KuCoinClient(
        transport=transport,
        api_key="dummy",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
    ).get_fees("BNB-USDT")
    assert fee.status == "unavailable"
    assert fee.maker_rate is None
    assert fee.taker_rate is None


def test_fee_fallback_is_used_for_network_failure_only_when_enabled():
    fallback = FeeFallback(
        exchange="kucoin", symbol="BNB-USDT", maker_rate=Decimal("0.001"),
        taker_rate=Decimal("0.002"), source="operator configured", verified=False,
    )
    enabled_transport = FakeTransport([ExchangeNetworkError("offline")] * 4)
    enabled_client = KuCoinClient(
        config=KuCoinConfig(max_retries=3, fee_fallback_enabled=True, fee_fallback=fallback),
        transport=enabled_transport,
        api_key="dummy",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
        sleeper=lambda _: None,
    )
    assert enabled_client.get_fees("BNB-USDT").status == "fallback"

    disabled_transport = FakeTransport([ExchangeTimeoutError("timeout")] * 4)
    disabled_client = KuCoinClient(
        config=KuCoinConfig(max_retries=3, fee_fallback=fallback),
        transport=disabled_transport,
        api_key="dummy",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
        sleeper=lambda _: None,
    )
    assert disabled_client.get_fees("BNB-USDT").status == "unavailable"


def test_invalid_live_fee_rate_is_not_accepted():
    transport = FakeTransport([response([{
        "symbol": "BNB-USDT", "makerFeeRate": "-0.001", "takerFeeRate": "0.001"
    }])])
    fee = KuCoinClient(
        transport=transport,
        api_key="dummy",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
    ).get_fees("BNB-USDT")
    assert fee.status == "unavailable"
    assert fee.maker_rate is None


def test_fee_fallback_for_different_symbol_is_not_applied():
    transport = FakeTransport([response(None, status=401)])
    fallback = FeeFallback(
        exchange="kucoin", symbol="BTC-USDT", maker_rate=Decimal("0.001"),
        taker_rate=Decimal("0.002"), source="BTC-only fallback", verified=False,
    )
    fee = KuCoinClient(
        config=KuCoinConfig(fee_fallback_enabled=True, fee_fallback=fallback),
        transport=transport,
        api_key="dummy",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
    ).get_fees("BNB-USDT")
    assert fee.status == "unavailable"
    assert fee.maker_rate is None
    assert "does not match" in fee.warning


def test_unexpected_programming_error_is_not_converted_to_fee_fallback():
    transport = FakeTransport([RuntimeError("programming fault")])
    fallback = FeeFallback(
        exchange="kucoin", symbol="BNB-USDT", maker_rate=Decimal("0.001"),
        taker_rate=Decimal("0.002"), source="configured", verified=False,
    )
    client = KuCoinClient(
        config=KuCoinConfig(fee_fallback_enabled=True, fee_fallback=fallback),
        transport=transport,
        api_key="dummy",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
    )
    with pytest.raises(RuntimeError, match="programming fault"):
        client.get_fees("BNB-USDT")


def test_malformed_symbol_increment_is_rejected():
    transport = FakeTransport([response({**SYMBOL, "baseIncrement": "NaN"})])
    with pytest.raises(ExchangeResponseError):
        KuCoinClient(transport=transport).get_symbol("BNB-USDT")


def test_dummy_signature_and_private_headers():
    transport = FakeTransport([response([])])
    client = KuCoinClient(
        transport=transport,
        api_key="dummy-key",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
        clock=lambda: 1700000000,
    )
    signature, passphrase = client.sign("1700000000000", "GET", "/api/v1/accounts", "")
    assert isinstance(signature, str)
    assert isinstance(passphrase, str)
    client.get_balances()
    headers = transport.calls[0][2]
    assert headers["KC-API-KEY"] == "dummy-key"
    assert "KC-API-SIGN" in headers
    assert "KC-API-PASSPHRASE" in headers


def test_private_request_without_credentials_does_not_call_transport():
    transport = FakeTransport([])
    client = KuCoinClient(transport=transport)
    with pytest.raises(ExchangeAuthenticationError):
        client.get_balances()
    assert transport.calls == []


def test_dry_run_is_explicit_and_submits_nothing():
    transport = FakeTransport([response(SYMBOL)])
    client = KuCoinClient(transport=transport)
    order = client.prepare_dry_run_order("BNB-USDT", "buy", "1.000", "limit", "610")
    assert order.dry_run is True
    assert order.estimated_notional == Decimal("610.000")
    assert all("/api/v1/orders" not in call[1] for call in transport.calls)


def make_fee_info(symbol="BNB-USDT", maker="0.001", taker="0.002", status="live"):
    return FeeInfo(
        exchange="kucoin",
        symbol=symbol,
        maker_rate=Decimal(maker) if maker is not None else None,
        taker_rate=Decimal(taker) if taker is not None else None,
        source="fixture",
        status=status,
        retrieved_at=datetime.now(timezone.utc),
    )


def test_dry_run_uses_supplied_fees_without_private_fee_request():
    transport = FakeTransport([response(SYMBOL)])
    order = KuCoinClient(transport=transport).prepare_dry_run_order(
        "BNB-USDT", "buy", "2", "limit", "610",
        fee_info=make_fee_info(), fee_assumption="maker",
    )
    assert order.validation.valid
    assert order.estimated_maker_fee == Decimal("1.220")
    assert order.estimated_taker_fee == Decimal("2.440")
    assert order.fee_assumption == "maker"
    assert order.fee_status == "live"
    assert all("/api/v1/trade-fees" not in call[1] for call in transport.calls)


def test_dry_run_market_order_uses_taker_assumption_only():
    transport = FakeTransport([response(SYMBOL)])
    order = KuCoinClient(transport=transport).prepare_dry_run_order(
        "BNB-USDT", "buy", "2", "market", "610",
        fee_info=make_fee_info(), fee_assumption="maker",
    )
    assert not order.validation.valid
    assert "market orders cannot use the maker fee assumption" in order.validation.errors
    assert order.fee_assumption == "taker"
    assert order.estimated_maker_fee is None


def test_dry_run_market_order_estimates_taker_fee():
    transport = FakeTransport([response(SYMBOL)])
    order = KuCoinClient(transport=transport).prepare_dry_run_order(
        "BNB-USDT", "buy", "2", "market", "610", fee_info=make_fee_info()
    )
    assert order.validation.valid
    assert order.fee_assumption == "taker"
    assert order.estimated_maker_fee is None
    assert order.estimated_taker_fee == Decimal("2.440")


def test_dry_run_fee_data_unavailable_is_explicit():
    transport = FakeTransport([response(SYMBOL)])
    order = KuCoinClient(transport=transport).prepare_dry_run_order(
        "BNB-USDT", "buy", "2", "market", "610",
        fee_info=make_fee_info(maker=None, taker=None, status="unavailable"),
    )
    assert order.fee_status == "unavailable"
    assert order.estimated_maker_fee is None
    assert order.estimated_taker_fee is None


@pytest.mark.parametrize("quantity", ["NaN", "Infinity", "-Infinity"])
def test_dry_run_rejects_non_finite_quantities(quantity):
    transport = FakeTransport([response(SYMBOL)])
    order = KuCoinClient(transport=transport).prepare_dry_run_order(
        "BNB-USDT", "buy", quantity, "limit", "610"
    )
    assert not order.validation.valid
    assert "quantity must be finite" in order.validation.errors
    assert order.estimated_notional is None


@pytest.mark.parametrize("quantity", ["0", "-1"])
def test_dry_run_rejects_non_positive_quantities(quantity):
    transport = FakeTransport([response(SYMBOL)])
    order = KuCoinClient(transport=transport).prepare_dry_run_order(
        "BNB-USDT", "buy", quantity, "limit", "610"
    )
    assert not order.validation.valid
    assert "quantity must be positive" in order.validation.errors


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-Infinity"])
def test_dry_run_rejects_non_finite_reference_prices(price):
    transport = FakeTransport([response(SYMBOL)])
    order = KuCoinClient(transport=transport).prepare_dry_run_order(
        "BNB-USDT", "buy", "1", "limit", price
    )
    assert not order.validation.valid
    assert "reference price must be finite" in order.validation.errors
    assert order.estimated_notional is None


@pytest.mark.parametrize("price", ["0", "-1"])
def test_dry_run_rejects_non_positive_reference_prices(price):
    transport = FakeTransport([response(SYMBOL)])
    order = KuCoinClient(transport=transport).prepare_dry_run_order(
        "BNB-USDT", "buy", "1", "limit", price
    )
    assert not order.validation.valid
    assert "reference price must be positive" in order.validation.errors


def test_dry_run_rejects_quantity_above_maximum():
    transport = FakeTransport([response(SYMBOL)])
    order = KuCoinClient(transport=transport).prepare_dry_run_order(
        "BNB-USDT", "buy", "100.001", "limit", "610"
    )
    assert not order.validation.valid
    assert "quantity exceeds baseMaxSize" in order.validation.errors


def test_dry_run_rejects_price_increment_and_minimum_funds():
    transport = FakeTransport([response(SYMBOL), response(SYMBOL)])
    client = KuCoinClient(transport=transport)
    bad_price = client.prepare_dry_run_order("BNB-USDT", "buy", "1", "limit", "610.01")
    assert not bad_price.validation.valid
    assert "reference price does not match priceIncrement" in bad_price.validation.errors
    low_notional = client.prepare_dry_run_order("BNB-USDT", "buy", "0.01", "limit", "610")
    assert not low_notional.validation.valid
    assert "notional is below minFunds" in low_notional.validation.errors


def test_dry_run_rejects_trading_disabled_symbol():
    transport = FakeTransport([response({**SYMBOL, "enableTrading": False})])
    order = KuCoinClient(transport=transport).prepare_dry_run_order(
        "BNB-USDT", "buy", "1", "limit", "610"
    )
    assert not order.validation.valid
    assert "symbol is not enabled for trading" in order.validation.errors


def test_dry_run_quantity_and_price_boundaries_are_inclusive():
    transport = FakeTransport([response(SYMBOL), response(SYMBOL)])
    client = KuCoinClient(transport=transport)
    at_minimum = client.prepare_dry_run_order("BNB-USDT", "buy", "0.1", "limit", "100")
    at_maximum = client.prepare_dry_run_order("BNB-USDT", "sell", "100", "limit", "0.1")
    assert at_minimum.validation.valid
    assert at_maximum.validation.valid


def test_http_timeout_then_success():
    transport = FakeTransport([
        ExchangeTimeoutError("timeout"),
        response(SYMBOL),
    ])
    client = KuCoinClient(transport=transport, sleeper=lambda _: None)
    assert client.get_symbol("BNB-USDT").symbol == "BNB-USDT"


def test_network_failure_is_structured():
    transport = FakeTransport([ExchangeNetworkError("network")] * 4)
    client = KuCoinClient(
        config=KuCoinConfig(max_retries=3),
        transport=transport,
        sleeper=lambda _: None,
    )
    with pytest.raises(ExchangeNetworkError):
        client.get_symbol("BNB-USDT")


def test_http429_retries_and_has_retry_guidance():
    transport = FakeTransport([
        response(None, status=429, headers={"Retry-After": "2"}),
        response(SYMBOL),
    ])
    client = KuCoinClient(transport=transport, sleeper=lambda _: None)
    assert client.get_symbol("BNB-USDT").symbol == "BNB-USDT"


def test_malformed_retry_after_uses_backoff_without_crashing():
    sleeps = []
    transport = FakeTransport([
        response(None, status=429, headers={"Retry-After": "not-a-number"}),
        response(SYMBOL),
    ])
    client = KuCoinClient(transport=transport, sleeper=sleeps.append)
    assert client.get_symbol("BNB-USDT").symbol == "BNB-USDT"
    assert sleeps == [0.5]


def test_ticker_timestamps_distinguish_exchange_time_from_retrieval_time():
    transport = FakeTransport([response({
        "time": 1700000000000,
        "ticker": [{"symbol": "BNB-USDT", "last": "610", "buy": "609", "sell": "611"}],
    })])
    ticker = KuCoinClient(transport=transport).get_ticker("BNB-USDT")
    assert ticker.event_timestamp == datetime.fromtimestamp(1700000000, timezone.utc)
    assert ticker.retrieved_at.tzinfo == timezone.utc


def test_missing_exchange_timestamp_is_not_fabricated():
    transport = FakeTransport([response({
        "ticker": [{"symbol": "BNB-USDT", "last": "610", "buy": "609", "sell": "611"}],
    })])
    ticker = KuCoinClient(transport=transport).get_ticker("BNB-USDT")
    assert ticker.event_timestamp is None
    assert ticker.retrieved_at is not None


def test_dry_run_rejects_fee_data_for_another_exchange():
    transport = FakeTransport([response(SYMBOL)])
    fee_info = FeeInfo(
        exchange="other-exchange",
        symbol="BNB-USDT",
        maker_rate=Decimal("0.001"),
        taker_rate=Decimal("0.002"),
        source="fixture",
        status="live",
        retrieved_at=datetime.now(timezone.utc),
    )
    order = KuCoinClient(transport=transport).prepare_dry_run_order(
        "BNB-USDT", "buy", "2", "limit", "610", fee_info=fee_info
    )
    assert not order.validation.valid
    assert "fee data exchange does not match the order exchange" in order.validation.errors


def test_http429_exhausted():
    transport = FakeTransport([response(None, status=429)] * 4)
    client = KuCoinClient(
        config=KuCoinConfig(max_retries=3),
        transport=transport,
        sleeper=lambda _: None,
    )
    with pytest.raises(ExchangeRateLimitError):
        client.get_symbol("BNB-USDT")


def test_http500_retries():
    transport = FakeTransport([response(None, status=500), response(SYMBOL)])
    client = KuCoinClient(transport=transport, sleeper=lambda _: None)
    assert client.get_symbol("BNB-USDT").symbol == "BNB-USDT"


def test_non_retryable_http_error():
    transport = FakeTransport([response(None, status=400)])
    with pytest.raises(ExchangeHTTPError):
        KuCoinClient(transport=transport, sleeper=lambda _: None).get_symbol("BNB-USDT")


def test_malformed_json():
    transport = FakeTransport([{"status": 200, "headers": {}, "body": b"{bad"}])
    with pytest.raises(ExchangeResponseError):
        KuCoinClient(transport=transport).get_symbol("BNB-USDT")


def test_unexpected_symbol_response():
    transport = FakeTransport([response({"unexpected": True})])
    with pytest.raises(ExchangeResponseError):
        KuCoinClient(transport=transport).get_symbols()
