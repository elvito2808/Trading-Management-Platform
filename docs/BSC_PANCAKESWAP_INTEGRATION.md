# BNB Smart Chain + PancakeSwap V2/V3 integration (Phase 2, read-only)

Package: `trading_platform/dex/`. Independent of `trading_platform/exchanges/` (KuCoin): nothing in the
KuCoin code was changed, and adding another DEX needs no KuCoin change.

| Module | Role |
|---|---|
| `abi.py` | selectors (keccak256), EIP-55, ABI encode/decode. No `float`. |
| `rpc.py` | JSON-RPC client. **Allow-list of read-only methods**; `eth_send*`, `eth_sign*`, `personal_*`, ... are rejected before any I/O. URL credentials redacted. Bounded retry. |
| `bsc.py` | `BscAdapter`: chain-id verification (56), health, blocks, token info, `eth_call`, gas price. |
| `registry.py` | Contract registry: name, version, address, network, source, verification status, runtime checks. |
| `pancakeswap_v2.py` | Pair discovery/validation, reserves, exact V2 math (0.25% fee), price impact, Router cross-check. |
| `pancakeswap_v3.py` | Enabled fee tiers (probed on-chain), pool discovery/validation, **QuoterV2 quotes**, price impact. |
| `gas.py` | Real `eth_gasPrice`; node `eth_estimateGas` on a realistic call (V2 BNB->USDT) or the V3 quoter's partial hint; else `UNKNOWN`. |
| `dry_run.py` | Fixed **0.1 BNB**, both directions, V2 vs V3 selection, net figure, `NonSendablePlan`, JSON report. |

## Use
```bash
export BSC_RPC_URL=<any BSC RPC; public or your own node>      # required, no default, nothing paid is assumed
export BSC_GAS_SIMULATION_FROM=<PUBLIC address holding >=0.1 BNB>   # optional; only the `from` of a local eth_estimateGas
python -m trading_platform.dex            # prints the dry-run report as JSON
python -m unittest discover -s tests -p "test_dex_*.py"     # mock/unit (live tests skip)
RUN_LIVE_BSC_TESTS=1 BSC_RPC_URL=... python -m unittest tests/test_dex_live_rpc.py -v   # real, read-only
```
Direction A = sell exactly 0.1 BNB for USDT (`EXACT_IN`). Direction B = buy exactly 0.1 BNB, the USDT input is
**derived** (`EXACT_OUT`: V2 `getAmountIn`, V3 `quoteExactOutputSingle`). Any other size is rejected.

## Semantics worth knowing
* Price impact excludes the protocol fee; the fee is reported separately (in the input token). `execution_price` includes both.
* A quote is usable only if `validity == VALID`. Stale block, V2 Router mismatch, registry mismatch (e.g. `router.WETH()` != registry WBNB) make it `STALE`/`INVALID`.
* Gas: `ESTIMATED_NODE` (real call simulated), `PARTIAL_POOL_ONLY` (V3 quoter pool-level number; **understates** real gas, flagged in the net figure), `UNKNOWN` (no number, no cost, no net figure). Gas is converted to USDT with the selected pool's pre-trade mid price (recorded) and rounded up. It is an estimate, never a payment.
* Everything is pinned to one block; every datum carries source, fetch time, block and validity.
* Direction B on V2 and every V3 path cannot be node-simulated without an allowance, so those rely on the quoter hint or stay `UNKNOWN`.

## Safety guarantees (and how they are tested)
No signing, broadcasting, approvals, transfers, wallets or keys exist in the code. `test_signing_and_broadcast_methods_blocked_before_any_io`,
`test_nothing_is_signed_or_sent` (every RPC method used is in the allow-list), and static scans for key/signing code, `float`, and secrets.
