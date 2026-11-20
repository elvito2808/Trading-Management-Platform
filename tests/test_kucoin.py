import base64
import json
from decimal import Decimal
from urllib.error import URLError

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
from trading_platform.exchanges.models import FeeFallback


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


def test_symbol_detail_and_precision():
    transport = FakeTransport([response(SYMBOL)])
    metadata = KuCoinClient(transport=transport).get_symbol("BNB-USDT")
    assert metadata.base_min_size == Decimal("0.001")
    assert metadata.quantity_increment == Decimal("0.001")
    assert metadata.base_precision == 3


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


def test_malformed_or_empty_order_book():
    transport = FakeTransport([response({"bids": None, "asks": []})])
    with pytest.raises(ExchangeResponseError):
        KuCoinClient(transport=transport).get_order_book("BNB-USDT")


def test_missing_ticker_data():
    transport = FakeTransport([response({"ticker": []})])
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


def test_live_fee_maker_taker_distinction():
    transport = FakeTransport([response({"makerFeeRate": "0.001", "takerFeeRate": "0.0015"})])
    fee = KuCoinClient(
        transport=transport,
        api_key="dummy",
        api_secret="dummy-secret",
        api_passphrase="dummy-pass",
    ).get_fees("BNB-USDT")
    assert fee.status == "live"
    assert fee.maker_rate == Decimal("0.001")
    assert fee.taker_rate == Decimal("0.0015")


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
