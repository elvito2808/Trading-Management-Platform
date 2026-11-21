# BNB Smart Chain + PancakeSwap V2/V3 integration (Phase 2, read-only)

Package: `trading_platform/dex/`. Independent of `trading_platform/exchanges/` (KuCoin): nothing in the
KuCoin code was changed, and adding another DEX needs no KuCoin change.

| Module | Role |
|---|---|
| `abi.py` | Named canonical ABI signature inventory, selectors (keccak256), EIP-55, ABI encode/decode. No `float`. |
| `rpc.py` | JSON-RPC client and bounded endpoint failover. **Allow-list of read-only methods**; `eth_send*`, `eth_sign*`, `personal_*`, ... are rejected before any I/O. URL credentials redacted. |
| `bsc.py` | `BscAdapter`: chain-id verification (56), health, blocks, token metadata, ERC-20 balance/allowance reads, `eth_call`, gas price. |
| `registry.py` | Contract registry: name, version, address, network, source, verification status, runtime checks. |
| `pancakeswap_v2.py` | Pair discovery/validation, reserves, exact V2 math (0.25% fee), price impact, Router cross-check. |
| `pancakeswap_v3.py` | Enabled fee tiers (probed on-chain), pool discovery/validation, **QuoterV2 quotes**, price impact. |
| `gas.py` | Real `eth_gasPrice`; `eth_estimateGas` on the selected V2 or V3 Router calldata in either direction when a public simulation sender has required balances/allowances. V3 QuoterV2 remains a clearly partial fallback only. |
| `dry_run.py` | Fixed **0.1 BNB**, both directions, V2 vs V3 selection, net figure, `NonSendablePlan`, JSON report. |

## Use
```bash
export BSC_RPC_URL=<any BSC RPC; public or your own node>      # required, no default, nothing paid is assumed
export BSC_RPC_URLS=<comma-separated free BSC Mainnet endpoints> # optional bounded failover
export BSC_GAS_SIMULATION_FROM=<PUBLIC address>   # optional; only the `from` of local eth_estimateGas Router simulation
python -m trading_platform.dex            # prints the dry-run report as JSON
python -m unittest discover -s tests -p "test_dex_*.py"     # mock/unit (live tests skip)
RUN_LIVE_BSC_TESTS=1 BSC_RPC_URL=... python -m unittest tests/test_dex_live_rpc.py -v   # real, read-only
```
Direction A = sell exactly 0.1 BNB for USDT (`EXACT_IN`). Direction B = buy exactly 0.1 BNB, the USDT input is
**derived** (`EXACT_OUT`: V2 `getAmountIn`, V3 `quoteExactOutputSingle`). Any other size is rejected.

## Semantics worth knowing
* Price impact excludes the protocol fee; the fee is reported separately (in the input token). `execution_price` includes both.
* A quote is usable only if `validity == VALID`. Stale block, V2 Router mismatch, registry mismatch (e.g. `router.WETH()` != registry WBNB) make it `STALE`/`INVALID`.
* Gas: `ESTIMATED_NODE` means the selected Router calldata was simulated by the node with `eth_estimateGas`; `PARTIAL_POOL_ONLY` is only the V3 QuoterV2 pool-level hint (**incomplete**, so no net figure and no `READY` status); `UNKNOWN` means no defensible estimate. Configure `BSC_GAS_SIMULATION_FROM` with a public address holding native BNB for gas and the route's input token; ERC-20 input routes also need a pre-existing allowance to the selected Router. This software never creates approvals. If the account state is insufficient, the node reverts and the report remains partial/unknown. Gas is converted to USDT with the selected pool's pre-trade mid price (recorded) and rounded up. It is an estimate, never a payment.
* Everything is pinned to one block; every datum carries source, fetch time, block and validity.
* Live validation uses BSC Mainnet only. Endpoint failover is bounded and records the endpoint/method infrastructure error; it does not convert chain-ID success into quote readiness.
* All four route shapes (V2/V3, both directions) now have a matching Router calldata builder for node estimation. ERC-20 input routes require a pre-existing allowance and sufficient input-token balance at the simulation sender; all routes require native BNB for gas. The program never creates allowances.

## Safety guarantees (and how they are tested)
No signing, broadcasting, approvals, transfers, wallets or keys exist in the code. `test_signing_and_broadcast_methods_blocked_before_any_io`,
`test_nothing_is_signed_or_sent` (every RPC method used is in the allow-list), and static scans for key/signing code, `float`, and secrets.
