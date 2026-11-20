# KuCoin Read-Only Integration

## Scope and safety boundary

The `trading_platform.exchanges` package provides a provider-neutral Best Bid/Ask contract, a read-only KuCoin Spot adapter, a WebSocket-first quote feed, and local dry-run preparation. Market data is limited to Level 1 Best Bid/Ask; the adapter does not retrieve or retain Level 2 order books.

No order submission, cancel, amend, transfer, withdrawal, deposit, or other account-changing method is implemented. No real API key is needed for public quotes or local tests. Private balance reads require an API key with General (read-only) permission; do not enable trading or withdrawal permissions.

## Configuration and models

Public REST and public WebSocket market data need no API key. Private balance reads use local-only environment variables:

- `KUCOIN_API_KEY`
- `KUCOIN_API_SECRET`
- `KUCOIN_API_PASSPHRASE`

`.env` is ignored by Git and `.env.example` contains empty placeholders.

`BestBidAskQuote` records Decimal bid/ask prices and sizes, its source (`WEBSOCKET` or `REST`), optional exchange `event_timestamp`, local `received_at`, and freshness (`FRESH` or `STALE`). Retrieval time is never represented as exchange event time. The feed owns one public WebSocket connection for its configured symbols, reconnects with capped exponential backoff, and polls REST only when a configured symbol has no fresh WebSocket quote. REST requests are spaced and the initial feed gives the WebSocket a short opportunity to deliver before fallback.

REST and WebSocket quotes are rejected if any required price or size is missing, non-finite, non-positive, or crossed (`bestBid >= bestAsk`). If fallback fails and only a stale quote is cached, `get_quote()` returns it with `freshness=STALE`; callers must not use it as current market data.

## Official KuCoin endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v2/symbols` | Public | Spot symbols and trading constraints |
| GET | `/api/v2/symbols/{symbol}` | Public | Symbol-specific limits and increments |
| GET | `/api/v1/market/orderbook/level1?symbol=BNB-USDT` | Public | Best bid/ask prices and sizes; REST fallback only |
| POST | `/api/v1/bullet-public` | Public | Classic Spot/Margin WebSocket token and server/heartbeat metadata |
| GET | `/api/v1/accounts` | Private, General permission | Read-only account balances |

Documentation:

- [Get All Symbols](https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-all-symbols)
- [Get Symbol](https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-symbol)
- [Get Ticker — Level 1 Best Bid/Ask](https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-ticker)
- [Get Account List — Spot](https://www.kucoin.com/docs-new/rest/account-info/account-funding/get-account-list-spot)
- [Classic Spot/Margin WebSocket introduction](https://www.kucoin.com/docs-new/websocket-api/base-info/introduction)
- [Get Public Token — Classic Spot/Margin](https://www.kucoin.com/docs-new/websocket-api/base-info/get-public-token-spot-margin)
- [WebSocket API introduction and UTA distinction](https://www.kucoin.com/docs-new/websocket-api/introduction)

The WebSocket implementation uses the Classic Spot/Margin public protocol: obtain `/api/v1/bullet-public` token metadata, wait for the `{ "type": "welcome" }` message, subscribe to `/market/ticker:{symbol}`, and acknowledge `type=ack`. It sends `type=ping` at the server-provided interval and reconnects if a `pong` is not received before the server timeout. The UTA Pro WebSocket uses a different token and `action`/`channel` subscription protocol and is not mixed with this Classic connection.

The Level 1 REST endpoint documents `bestBid`, `bestBidSize`, `bestAsk`, `bestAskSize`, and `time`; `time` remains optional in the normalized model. A missing or malformed event time stays `None`.

## Request reliability

The REST client uses a 10-second timeout, at most three retries, and exponential backoff for network/timeouts and HTTP 5xx. HTTP 429 honors a valid numeric or HTTP-date `Retry-After`, capped at 60 seconds; malformed values use bounded backoff. HTTP/API errors, authentication failures, and malformed successful responses are surfaced as typed errors. Unexpected programming errors are not converted into successful or fallback-shaped results.

The market-data feed defaults to a 5-second maximum quote age, 2-second fallback polling interval, 250 ms minimum REST request spacing, and reconnect backoff from 0.5 to 10 seconds. These are local safety limits, not KuCoin rate-limit guarantees. The Level 1 REST endpoint has weight 2; public WebSocket is preferred to avoid repeated REST polling.

## Balances

The adapter parses `balance`, `available`, and `holds` as finite, non-negative Decimals and requires `balance == available + holds`. Each balance has a local retrieval timestamp only. Private connectivity has not been tested with a real account key.

## Fixed fee assumption and configured trade sizes

`FixedFeePolicy` centralizes the owner-configured `Decimal("0.001")` (0.1%) estimate. It is the same for every symbol, side, and order type and is explicitly labeled `fixed-assumption`; it is not represented as a live KuCoin rate. No private fee request is made. The pair-specific `/api/v1/trade-fees` and basic `/api/v1/base-fee` endpoints are not used.

An operator must provide a `FixedTradeSize` for each symbol; this repository invents no quantities and does not size from balances. `prepare_configured_dry_run_order()` accepts that exact quantity and a supplied Best Bid/Ask quote, selects ask for buys and bid for sells, and validates without rounding. A stale or wrong-symbol quote invalidates the dry-run. `prepare_dry_run_order()` requires an explicit reference price and performs no market-data or fee request.

Dry-run validation covers finite positive quantity and reference price, base increment, configured minimum/maximum quantity, limit price increment, `minFunds`, and `enableTrading`. Market orders use the taker assumption and cannot be labeled maker. A limit-order maker/taker label is only an explicit assumption; the fee amount remains the same fixed 0.1%.

## Validation boundary and limitations

- Tests use deterministic fake transports and fake WebSocket connections only; no live KuCoin connection or real account key was used.
- The public Classic Spot/Margin token, subscription, and heartbeat protocol is documented. The WebSocket quote parser accepts the ticker Best Bid/Ask fields and applies the same strict validation as REST.
- There are no preconfigured per-symbol trade quantities; a caller must supply them.
- `quoteMinSize` is exposed in symbol metadata but is not treated as a base-quantity or quote-notional threshold. `minFunds` is the documented quote-value minimum enforced by dry-run validation.
- This is a market-data and local dry-run integration only, not a trading system.
