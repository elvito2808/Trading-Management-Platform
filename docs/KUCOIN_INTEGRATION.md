# KuCoin Read-Only Integration

## Scope

The `trading_platform.exchanges` package provides provider-neutral exchange contracts and a read-only KuCoin Spot REST adapter. It retrieves public symbol, ticker, and order-book data and, with credentials, private account balances and the official base-fee endpoint.

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
| GET | `/api/v1/symbols` | Public | Spot symbol list | None |
| GET | `/api/v1/symbols/{symbol}` | Public | Symbol detail and increments | Symbol path |
| GET | `/api/v1/market/allTickers` | Public | 24-hour ticker summary | None |
| GET | `/api/v1/market/orderbook/level2_20` | Public | REST order-book snapshot, 20 levels | `symbol` |
| GET | `/api/v1/accounts` | Private | Read-only balances | Optional `currency` |
| GET | `/api/v1/base-fee` | Private | Base maker/taker fee rates | Optional `symbols` in this implementation's request helper |

Official authority: https://www.kucoin.com/docs

Private account and fee endpoints require KuCoin API-key authentication. Use only General/read-only key permissions; never an API key with trading or withdrawal permission.

The KuCoin REST signing procedure is timestamped HMAC-SHA256: `timestamp + method + endpoint + body`, Base64 of the HMAC digest, API passphrase HMAC-SHA256 using the API secret and Base64 encoding, and the `KC-API-KEY-VERSION: 2` header. Public requests require no signing.

## Rate limits and failures

The client uses a 10-second timeout by default, at most three retries, exponential backoff, and respects `Retry-After` for HTTP 429. Authentication and validation errors are not retried indefinitely. HTTP 429 is retried only within the bounded retry count. HTTP 5xx and network/timeout failures are retried; HTTP 4xx failures are surfaced as typed errors.

Malformed JSON and unexpected payload shapes raise `ExchangeResponseError`. Secrets are never included in exception text or logs.

## Fee fallback

Live fee retrieval reports `live`, `fallback`, or `unavailable`. A fallback is used only when `fee_fallback_enabled=True` and an explicit `FeeFallback` has been configured. The fallback identifies its source and whether it is verified. It is never silently substituted after a live failure.

## Order-book semantics

Level 2 REST retrieval is a snapshot, not a continuously synchronized order book. A WebSocket implementation would require a separate future enhancement.

## Dry-run boundary

`prepare_dry_run_order()` validates symbol, side, order type, positive values, minimum/maximum quantity, and configured quantity increments. It computes notional locally and uses `dry_run=True`. It never makes an HTTP request to a trading endpoint. The implementation contains no method for placing or changing orders.
