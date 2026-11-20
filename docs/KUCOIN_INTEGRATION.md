# KuCoin Read-Only Integration

## Scope

The `trading_platform.exchanges` package provides provider-neutral exchange contracts and a read-only KuCoin Spot REST adapter. It retrieves public symbol, ticker, and order-book data and, with credentials, private account balances and pair-specific actual fee rates.

The implementation has no place-order, cancel, amend, withdraw, deposit, transfer, or other account-changing method. `prepare_dry_run_order()` only validates and represents a hypothetical order locally.

## Provider-neutral contracts

`ExchangeAdapter` in `trading_platform/exchanges/contracts.py` defines the reusable public interface for future exchanges. Typed models are in `trading_platform/exchanges/models.py` and use `Decimal` for prices, quantities, balances, and fees.

## Configuration

Public endpoints need no credentials. Private read-only endpoints read:

- `KUCOIN_API_KEY`
- `KUCOIN_API_SECRET`
- `KUCOIN_API_PASSPHRASE`

These values must be local-only environment variables. `.env` is ignored by Git and `.env.example` contains empty placeholders only. Do not grant Spot trading or Withdrawal permission.

`KuCoinConfig` supports `base_url`, `timeout`, bounded `max_retries`, and explicit fee-fallback configuration.

## REST endpoints

| Method | Path | Auth | Permission / purpose | Parameters |
|---|---|---|---|---|
| GET | `/api/v2/symbols` | Public | Spot symbol list, increments, and `minFunds` | Optional `market` |
| GET | `/api/v2/symbols/{symbol}` | Public | Symbol detail and increments | Symbol path |
| GET | `/api/v1/market/allTickers` | Public | 24-hour ticker summary | None |
| GET | `/api/v1/market/orderbook/level2_20` | Public | REST order-book snapshot, 20 levels | `symbol` |
| GET | `/api/v1/accounts` | Private | Read-only balances | Optional `currency` |
| GET | `/api/v1/trade-fees` | Private | Actual pair-specific maker/taker rates | `symbols` query |

Official endpoint documentation:

- [Get All Symbols](https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-all-symbols)
- [Get Symbol](https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-symbol)
- [Get All Tickers](https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-all-tickers)
- [Get Actual Fee - Spot/Margin](https://www.kucoin.com/docs-new/rest/account-info/trade-fee/get-actual-fee-spot-margin)
- [Get Basic Fee - Spot/Margin](https://www.kucoin.com/docs-new/api-3470149)
- API documentation root: https://www.kucoin.com/docs

Private account and fee endpoints require KuCoin API-key authentication and General (read-only) permission. Never use an API key with trading or withdrawal permission. The actual-fee endpoint returns a list of symbol/maker/taker records, accepts up to 10 symbols per request, and the adapter currently requests one pair at a time.

The pair-specific actual-fee endpoint is `/api/v1/trade-fees?symbols=BNB-USDT`. The distinct `/api/v1/base-fee` endpoint returns the account's basic Spot/Margin fee rate, takes `currencyType`, and is not used by this adapter for pair-specific fees. KuCoin documents rate-limit weight 3 for the actual-fee endpoint.

The KuCoin REST signing procedure is timestamped HMAC-SHA256: `timestamp + method + endpoint + body`, Base64 of the HMAC digest, API passphrase HMAC-SHA256 using the API secret and Base64 encoding, and the `KC-API-KEY-VERSION: 2` header. Public requests require no signing.

## Rate limits and failures

The client uses a 10-second timeout by default, at most three retries, exponential backoff, and respects valid non-negative `Retry-After` values for HTTP 429. Malformed retry guidance falls back to exponential backoff. Authentication and validation errors are not retried indefinitely. HTTP 429 is retried only within the bounded retry count. HTTP 5xx and network/timeout failures are retried; HTTP 4xx failures are surfaced as typed errors.

Malformed JSON and unexpected payload shapes raise `ExchangeResponseError`. Secrets are never included in exception text or logs.

## Fee fallback

Live fee retrieval reports `live`, `fallback`, or `unavailable`. A fallback is used only when `fee_fallback_enabled=True` and an explicit `FeeFallback` has been configured. This applies to authentication, HTTP/API response, rate-limit, network, and timeout failures. The fallback identifies its source and whether it is verified. It is never silently substituted after a live failure. Unexpected programming errors are not caught or converted into fallback results.

## Order-book semantics

Level 2 REST retrieval is a snapshot, not a continuously synchronized order book. A WebSocket implementation would require a separate future enhancement.

## Dry-run boundary

Ticker and order-book models distinguish optional exchange `event_timestamp` from local `retrieved_at`. If KuCoin does not return an event time, `event_timestamp` is `None`; local retrieval time is never represented as exchange time. Balance and fee results have local retrieval timestamps only.

`prepare_dry_run_order()` validates symbol, side, type, finite positive quantity and price, minimum/maximum quantity, base quantity increment, limit price increment, `minFunds`, and trading-enabled status. It computes notional locally and uses `dry_run=True`. Previously fetched `FeeInfo` may be supplied; preparation does not fetch private fee data. It calculates maker and taker estimates using `Decimal`, with an explicit assumption recorded on the result. A market order always uses the taker assumption and rejects an explicit maker assumption. Missing fee data remains `fee_status="unavailable"` with no estimates. It never makes an HTTP request to a trading endpoint. The implementation contains no method for placing or changing orders.
