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

KuCoin Classic Spot/Margin WebSocket ticker messages name their exchange event timestamp `Time` (capital T); the REST Level 1 response names it `time` (lowercase). The parser selects the field for the message source, converts seconds, milliseconds, or nanoseconds to UTC, and leaves `event_timestamp` as `None` when that source field is absent or invalid. Local receipt time is never substituted.

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

An operator must provide a `FixedTradeSize` for each symbol; this repository invents no quantities and does not size from balances. `prepare_configured_dry_run_order()` accepts that exact quantity and a supplied Best Bid/Ask quote, selects ask for buys and bid for sells, and validates without rounding. A stale or wrong-symbol quote invalidates the dry-run. Both preparation methods retrieve public symbol metadata for validation, but they make no Best Bid/Ask or fee request; `prepare_dry_run_order()` requires an explicit reference price.

Dry-run validation covers finite positive quantity and reference price, base increment, configured minimum/maximum quantity, limit price increment, `minFunds`, and `enableTrading`. Market orders use the taker assumption and cannot be labeled maker. A limit-order maker/taker label is only an explicit assumption; the fee amount remains the same fixed 0.1%.

## Validation boundary and limitations

- Tests use deterministic fake transports and fake WebSocket connections only; no live KuCoin connection or real account key was used.
- The public Classic Spot/Margin token, subscription, and heartbeat protocol is documented. The WebSocket quote parser accepts the ticker Best Bid/Ask fields and applies the same strict validation as REST.
- There are no preconfigured per-symbol trade quantities; a caller must supply them.
- `quoteMinSize` is exposed in symbol metadata but is not treated as a base-quantity or quote-notional threshold. `minFunds` is the documented quote-value minimum enforced by dry-run validation.
- This is a market-data and local dry-run integration only, not a trading system.


## Local BNB/USDT execution simulator

`trading_platform.execution.kucoin_dry_run.KuCoinBNBUSDTDryRunSimulator` is a stateful, local-only simulator, separate from `KuCoinClient`. It has no network transport, credentials, or order endpoint. Defaults are BNB=5 and USDT=4000; starting balances can be configured and `reset()` restores them.

It accepts only BNB-USDT BUY/SELL requests with matching enabled symbol metadata, finite positive quantity aligned to `baseIncrement` and within base size limits, and a fresh valid KuCoin Best Bid/Ask quote. It enforces `minFunds` when supplied and rejects quantities exceeding displayed top-of-book size. BUY uses Ask; SELL uses Bid. Rejections leave balances unchanged and return a structured `REJECTED` result and reason. Successful local fills return `FILLED`, before/after balances, gross notional, fee rate/amount/currency, debit/credit and effective unit price.

### Simulator fee accounting — explicit assumption

The fixed fee assumption is exactly 0.1% (`Decimal("0.001")`) on both sides. For deterministic local accounting only, the simulator treats fees as quote-currency USDT:

- BUY: gross = quantity × Ask; fee = gross × 0.001 USDT; USDT debit = gross + fee; BNB credit = quantity; effective price = debit ÷ quantity.
- SELL: gross = quantity × Bid; fee = gross × 0.001 USDT; USDT credit = gross − fee; BNB debit = quantity; effective price = net credit ÷ quantity.

This does **not** assert KuCoin will deduct actual fees in USDT. Actual fee currency/settlement depends on account settings, discounts and execution response. The simulator does not query actual fee rates or fee currency. This is a trading-fee estimate, not blockchain gas.

### Example

For Ask=601 USDT and Bid=600 USDT, a 0.1 BNB BUY produces gross 60.1 USDT, fee 0.0601 USDT, debit 60.1601 USDT, and balances BNB=5.1 / USDT=3939.8399. A separate 0.1 BNB SELL produces gross 60.0 USDT, fee 0.0600 USDT, net credit 59.9400 USDT, and balances BNB=4.9 / USDT=4059.9400. Each example starts from the defaults.

This is a full-fill simulation at one Level 1 price. It does not model queue position, partial fills, latency, market impact, deeper liquidity, or slippage; actual execution may differ or fail.
