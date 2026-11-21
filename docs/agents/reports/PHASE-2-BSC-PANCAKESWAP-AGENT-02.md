# AGENT 02 REPORT - PHASE-2-BSC-PANCAKESWAP-INTEGRATION

## Current Closeout

**Current PR #6 HEAD:** `74d6f304bc0e0a5d159897068d7bdb159a0ac848`.
**Current PR #6 base SHA:** `fe6a1d2c6efe3db9ff7504f03a778b47d098fa8d`.
**PR #5 HEAD:** `fe6a1d2c6efe3db9ff7504f03a778b47d098fa8d`.
**Historical previous PR #6 HEAD:** `d66211e139b1df216ac00030091a14ced032978f` (superseded by the current documentation state).
**Historical alignment merge:** `3bdffb0921d29b4cdfcd6ca3cc2d3d7de1b3478d`.
**PR #6:** Open, Draft, and Unmerged. `main` and PR #5 were not modified.
**Canonical status:** Phase 0 `ACCEPTED`; Phase 1 `PENDING_ALPHA_REVIEW`; Phase 2 `PARTIALLY COMPLETED`; Phases 3–7 `NOT STARTED`.
**Historical references:** previous alignment HEAD `24842491e1cf6a55a5a4bb6c8a5ebdf73e36b80f`; closeout predecessor `84fb93aa95ceb2f63325bcededcdb5705935ea95`; gas-semantics correction commit `58932bd5c3b49352ed4646f36df88978c36ce908`.
**Evidence status:** Independent bounded validation in this closeout reached BSC Mainnet through `https://bsc.publicnode.com`; all seven live tests passed with a 180-second freshness bound. Gas remained `PARTIAL_POOL_ONLY`, so final/net profitability was withheld. Phase 2 remains `PARTIALLY COMPLETED` pending ALPHA acceptance of complete transaction-gas validation.

**Final status:** `PARTIALLY COMPLETED - LIVE QUOTES AND DRY-RUN VERIFIED; COMPLETE TRANSACTION GAS UNAVAILABLE`
Date: 2026-11-21 | Mode: READ-ONLY / DRY-RUN | Network: BSC Mainnet (chain 56)

### Final Phase 1 base alignment (historical, 2026-11-21)

The primary and supplementary instructions in comments `7053741167` and `7053863120` were reread. The supplementary Live Trading authorization remains conditional on independent ALPHA acceptance and does not authorize implementation or real execution here. PR #6's prior base was `aa7d65d281647596c43e4417020f5ae91687606b`; the requested Phase 1 head was `fe6a1d2c6efe3db9ff7504f03a778b47d098fa8d`. The normal alignment merge is historical; no rebase, reset, force-push, PR merge, main change, signing, broadcast, or financial action was authorized.

### ALPHA comment and branch-alignment closeout

Comments `7053741167` (primary Phase 2 scope) and `7053863120` (supplementary scope and conditional future Live Trading authorization) were read and incorporated. The latter does not authorize Live Trading implementation or any real execution in this task. PR #6 previously recorded base SHA `316829506217cb817c69f04af5eaa9614a29feff`; the current Phase 1 base branch was `aa7d65d281647596c43e4417020f5ae91687606b`. GitHub's prior `dirty` state was reproduced as content conflicts in `PROGRESS.md`, `docs/PROJECT_STATUS.md`, and `docs/agents/IMPLEMENTATION_STATE.md`. They were resolved with a standard non-rewriting merge commit `a54dfe94e55a5f44960929a202348ec0669a4faa`; Phase 1 simulator/runtime changes were retained. No main or PR #5 changes were made.

Post-alignment results: Phase 2 focused command `python -m unittest discover -s tests -p "test_dex_*.py" -v` — **58 passed, 7 skipped, 0 failed**. Phase 1 simulator direct unittest discovery found **0 tests** because it is pytest-based; the full command `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q` — **173 passed, 7 skipped, 0 failed**. The seven skips are the opt-in live class when no endpoint is supplied. The earlier independent Mainnet run remains recorded above: **7 live tests passed** against `https://bsc.publicnode.com`; gas stayed `PARTIAL_POOL_ONLY`, so no net/final price was published.

## Current Phase 2 completion addendum (2026-11-21)

This section is the current validation record. Earlier sections retain historical results and are not rewritten.

### Registry and ABI audit

The registry addresses are unchanged and were checked with `eth_getCode` on Mainnet where the live suite reached them. The runtime identity checks passed for USDT metadata, V2 Router `factory()`/`WETH()`, V3 QuoterV2 `factory()`, dynamically discovered V2 pair, and dynamically discovered V3 pools. The exact deployed PancakeSwap ABI evidence remains the official PancakeSwap V3 source recorded below: QuoterV2 single-hop input/output uses the five-word tuple and returns four values; `slot0()` uses seven words with signed `int24 tick` and final `bool unlocked`. The implementation decodes `sqrtPriceX96After`, `initializedTicksCrossed`, `gasEstimate`, and `slot0` fields in that order. A centralized signature inventory now covers ERC-20 metadata/balance/allowance, V2 discovery/quotes/calldata, V3 discovery/QuoterV2, and local-only V3 SwapRouter calldata construction.

### Bounded endpoint preflight

At approximately 2026-11-21T00:59Z UTC, one bounded POST per method (`eth_chainId`, `eth_blockNumber`, 4-second timeout, no retry) was made to these free BSC Mainnet endpoints:

| Endpoint | Chain ID | Block result | Approx. elapsed | Result |
|---|---:|---|---:|---|
| `https://bsc-dataseed2.binance.org` | `0x38` (56) | `0x808d098` | 6720 ms | both methods returned |
| `https://bsc-dataseed3.binance.org` | `0x38` (56) | `0x808d09d` | 1982 ms | both methods returned |
| `https://bsc-dataseed4.binance.org` | `0x38` (56) | `0x808d0ac` | 6821 ms | both methods returned |
| `https://bsc-dataseed1.ninicoin.io` | `0x38` (56) | `0x808d0b2` | 2638 ms | both methods returned |
| `https://bsc.publicnode.com` | `0x38` (56) | `0x808d0b7` | 2156 ms | both methods returned; selected for suite |

The validation suite used `https://bsc.publicnode.com`, `BSC_RPC_TIMEOUT_SECONDS=5`, `BSC_RPC_MAX_RETRIES=0`, and then `BSC_MAX_BLOCK_AGE_SECONDS=180` solely to accommodate the endpoint's sequential read latency. The live snapshot was block `134796126`, timestamp `2026-11-21T01:05:14Z`.

### Exact live results

Command: `$env:RUN_LIVE_BSC_TESTS='1'; $env:BSC_RPC_URL='https://bsc.publicnode.com'; $env:BSC_RPC_TIMEOUT_SECONDS='5'; $env:BSC_RPC_MAX_RETRIES='0'; $env:BSC_MAX_BLOCK_AGE_SECONDS='180'; python -m unittest discover -s tests -p "test_dex_live_rpc.py" -v`

Result: **7 passed, 0 failed, 0 errors** in 187.524 seconds.

- Chain 56 and health: passed.
- USDT: `USDT`, `Tether USD`, 18 decimals; code present: passed.
- V2: Factory/Router code and identity checks passed; dynamically discovered pair `0x16b9a82891338f9bA80E2D6970FddA79D1eb0daE`; 0.1 BNB exact-input and exact-output quotes passed; Router cross-checks were `MATCH`; observed local fee `0.25%`.
- V3: Factory/QuoterV2 code and identity checks passed; enabled tiers `(100, 500, 2500, 10000)` ppm; dynamically discovered pools included `0x172fcD41E0913e95784454622d1c3724f546f849` (100 ppm), `0x36696169C63e42cd08ce11f5deeBbCeBae652050` (500 ppm), `0x1401ff943D08a7e098328C1d3a9d388923B115D2` (2500 ppm), and `0x6805E0E5333c5c3acCF2930Be4734E2b98f4Ce06` (10000 ppm). QuoterV2 exact-input and exact-output succeeded in both directions and returned all four values.
- Full Dry-Run: passed in both directions, remained non-sendable, and emitted no transaction.

Representative selected quotes at the pinned block were V3 100 ppm: sell 0.1 WBNB -> `53.959547329333241127 USDT`; buy exactly 0.1 WBNB -> `53.970476482221386392 USDT` required. V2 Router cross-checks matched the reserve formula. Exact quote values are intentionally block-specific and are not final profitability claims.

### Gas and safety closeout

The live dry-run reported `PARTIAL_POOL_ONLY` from QuoterV2 (`92,211`/`92,136` units in the selected directions) and explicitly withheld `net_output`; this is not complete Router transaction gas and cannot produce `READY` or a final net price. `ESTIMATED_NODE` remains reserved for `eth_estimateGas` on a realistic router call with a public simulation sender. `UNKNOWN` produces no fabricated units, cost, or net result. No sender, allowance, approval, key, signature, broadcast, transfer, withdrawal, or swap was used.

The implementation commit is `8a9e28c9d416d686a5be77b177e2bb7672040639`; it adds the documented ABI inventory, bounded RPC failover, ERC-20 balance/allowance reads, pure V2/V3 calldata builders, and focused regression coverage. Phase 2 remains `PARTIALLY COMPLETED` because complete transaction-gas estimates and final net prices were not available.

## 1. Summary
A new package `trading_platform/dex/` provides a BSC adapter, independent PancakeSwap V2 and V3 modules, gas
estimation, a contract registry and a fixed-size (0.1 BNB) dry-run in both directions. No KuCoin file was modified.
Nothing can sign, broadcast, approve, transfer or hold keys; this is enforced by an RPC method allow-list and tested.

## 2. Files and delivery
`trading_platform/dex/{__init__,__main__,abi,errors,models,registry,rpc,bsc,pancakeswap_v2,pancakeswap_v3,gas,dry_run}.py`;
`tests/{dex_fake_chain,test_dex_abi_registry,test_dex_rpc_bsc,test_dex_pancakeswap,test_dex_gas_dry_run,test_dex_safety_static,test_dex_live_rpc}.py`;
`docs/BSC_PANCAKESWAP_INTEGRATION.md`; this report; `scripts/apply_phase2_docs.py` (appends to PROGRESS.md,
docs/PROJECT_STATUS.md, docs/agents/IMPLEMENTATION_STATE.md, .env.example). The implementation commit added these 22 files;
the post-PR documentation commit updated the listed status files and this report. No KuCoin source was changed.

- Phase 1 base / PR #5 head: `316829506217cb817c69f04af5eaa9614a29feff`.
- Implementation commit: `258e5a69b676d99f7069eda4c3ceb2fbc5112794`.
- Branch: `feature/phase-2-bsc-pancakeswap-integration`.
- Draft PR #6: https://github.com/elvito2808/Trading-Management-Platform/pull/6 (target: `feature/phase-1-kucoin-integration`).
- PR #5 remains Open, Draft, and Unmerged. `main` and PR #5 were not modified.

No new runtime dependency was added (stdlib JSON-RPC; the existing `eth_hash` dependency is used when present, with a pure-Python keccak fallback).

## 3. Contract registry (`registry.py`)
| Contract | Address | Source | Status |
|---|---|---|---|
| USDT | 0x55d398326f99059fF775485246999027B3197955 | mission brief (owner) | USER_SPECIFIED; symbol/decimals read on-chain |
| WBNB | 0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c | third-party sources only | SECONDARY_SOURCES_ONLY; runtime check `== router.WETH()` mandatory |
| V2 Factory | 0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73 | developer.pancakeswap.finance v2 addresses + docs factory-v2 | DOCS_VERIFIED |
| V2 Router | 0x10ED43C718714eb63d5aA57B78B54704E256024E | developer.pancakeswap.finance v2 addresses | DOCS_VERIFIED |
| V3 Factory | 0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865 | developer.pancakeswap.finance v3 addresses | DOCS_VERIFIED |
| V3 QuoterV2 | 0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997 | same | DOCS_VERIFIED |
| V3 SwapRouter | 0x1b81D678ffb9C0263b24A97847620C99d213eB14 | same | DOCS_VERIFIED (informational, never called) |
DOCS_VERIFIED = read from official docs on 2026-11-20; **not** checked on-chain. All addresses pass EIP-55.

## 4. Test results
* **Focused suite (Python 3.10.11): 48 passed, 7 skipped**:
  `python -m unittest discover -s tests -p 'test_dex_*.py' -v`. The seven skipped cases are opt-in live tests, not passes.
  Covers: keccak/selectors/EIP-55, read-only allow-list (no I/O for send/sign), URL redaction, retry bounds, chain-id mismatch,
  unreachable/stale RPC, token validation, V2 math and Router cross-check (match / mismatch / unavailable), V3 tier/pool/quoter handling,
  gas (node / partial / unknown), dry-run for both directions, no-dynamic-sizing, JSON units, static scans (no float, no key/signing code, no secrets).
* **Full repository suite:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q` — **146 passed, 7 skipped**.
  Plain `python -m pytest -q` fails during plugin loading because globally installed `web3.tools.pytest_ethereum`
  imports missing `eth_typing.ContractName`; this is an environment/plugin issue.
* `eth_hash` was installed and exercised in this environment; the pure-Python fallback was also tested against known keccak vectors.
* **Live suite command:** with `BSC_RPC_URL=https://bsc-dataseed.binance.org` and `RUN_LIVE_BSC_TESTS=1`,
  `python -m unittest discover -s tests -p 'test_dex_live_rpc.py' -v` — **5 passed, 1 error, 1 failure**.
  The first eth_chainId probe returned chain 56; tests 1–5 passed (including token reads, V2 deployment/pair,
  V3 deployment and enabled fee-tier checks). During test 6 a TLS handshake timeout exhausted retries; test 7 then
  failed because the full dry-run returned no directions after an RPC timeout. Thus no successful live quote or
  complete dry-run was verified. An unauthenticated `https://1rpc.io/bnb` chain-ID probe returned chain 56,
  but the live suite against it timed out during `setUpClass` while fetching the latest block, before any tests ran.
  The `https://bsc-rpc.publicnode.com` chain-ID probe also timed out.

## 5. Limitations / things to confirm in the first live run
1. Function selectors were verified against independent well-known values (Uniswap-derived). Pancake V3 `QuoterV2`/`slot0()` layout
   is assumed identical to Uniswap V3 (struct params, 7-word `slot0`); the live test `test_06`/`test_05` will confirm.
2. V2 fee 0.25% (9975/10000) is hard-coded in the local formula; the Router cross-check turns any disagreement into `INVALID`.
3. WBNB address lacks a primary official page; mismatch with `router.WETH()` invalidates quotes.
4. Gas: node simulation exists only for V2 BNB->USDT and needs `BSC_GAS_SIMULATION_FROM` (public address with BNB). Other paths use the
   V3 quoter's pool-level estimate (`PARTIAL_POOL_ONLY`, understates real gas, flagged) or are `UNKNOWN` (no net figure). BSC is treated as legacy gas price; EIP-1559 is not assumed.
5. V3: only single-hop pools on the four probed fee tiers (100/500/2500/10000 ppm, enabled ones detected on-chain); no multi-hop routing.
6. Live PancakeSwap V2/V3 quote comparison and the full dry-run remain unverified because the public RPC endpoint timed out.
   The WBNB/`router.WETH()` runtime check passed in live test 04; USDT symbol/decimals and V3 factory/tier checks passed.

## 6. Remaining validation
* Git delivery succeeded: commit `258e5a69b676d99f7069eda4c3ceb2fbc5112794` is on the existing Phase 2 branch;
  Draft PR #6 is Open and targets `feature/phase-1-kucoin-integration`.
* Public RPC connectivity is intermittent. The exact live result and endpoint probes are recorded in section 4.
* No signing, transaction submission, approvals, transfers, or real trading was performed or added.

## 7. Next steps
ALPHA independently reviews Draft PR #6. Keep it Open and Unmerged; rerun read-only quote validation when a stable free public BSC RPC endpoint is available. Do not use paid infrastructure, private keys, signing, transactions, approvals, or transfers.

## 8. Agent 04 final live-validation addendum (2026-11-20)

This addendum preserves the historical results above and records the final bounded validation attempt. The isolated clone was switched to `feature/phase-2-bsc-pancakeswap-integration` at `db7f5f3300fb1e63804c8865129e768a052bfee7` before testing. No main branch, PR #5, or PR #6 was changed.

### ABI verification

The authoritative PancakeSwap source was fetched read-only from the official repository `pancakeswap/pancake-v3-contracts`, commit `986847948755cba528324d41be19480731c36c2a`:

- `projects/v3-periphery/contracts/interfaces/IQuoterV2.sol`, `quoteExactInputSingle(QuoteExactInputSingleParams)` and `quoteExactOutputSingle(QuoteExactOutputSingleParams)` each use the five-word tuple `(address tokenIn, address tokenOut, uint256 amount[In/Out], uint24 fee, uint160 sqrtPriceLimitX96)` and return exactly four values: `uint256 amount[Out/In]`, `uint160 sqrtPriceX96After`, `uint32 initializedTicksCrossed`, `uint256 gasEstimate`.
- `projects/v3-core/contracts/interfaces/pool/IPancakeV3PoolState.sol` defines `slot0()` as seven ABI words: `uint160 sqrtPriceX96`, signed `int24 tick`, `uint16 observationIndex`, `uint16 observationCardinality`, `uint16 observationCardinalityNext`, `uint32 feeProtocol`, and `bool unlocked`.
- The implementation matches this layout: it encodes the five static tuple words, decodes all four QuoterV2 words, decodes `slot0` word 0 as unsigned, word 1 as signed 24-bit, and word 6 as the boolean. The added regression assertions explicitly cover `sqrtPriceX96After`, `initializedTicksCrossed`, and the four-word order.

Source URLs: https://github.com/pancakeswap/pancake-v3-contracts/blob/986847948755cba528324d41be19480731c36c2a/projects/v3-periphery/contracts/interfaces/IQuoterV2.sol and https://github.com/pancakeswap/pancake-v3-contracts/blob/986847948755cba528324d41be19480731c36c2a/projects/v3-core/contracts/interfaces/pool/IPancakeV3PoolState.sol.

### Exact test results

- Mock/unit: `python -m unittest discover -s tests -p "test_dex_*.py" -v` — **55 passed, 7 skipped**. The skips are the opt-in live class; they are not passes.
- Full suite: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q` — **146 passed, 7 skipped**. The skips are the same opt-in live tests.
- Live endpoint `https://bsc-dataseed.binance.org`, with `RUN_LIVE_BSC_TESTS=1`, `BSC_RPC_TIMEOUT_SECONDS=5`, and `BSC_RPC_MAX_RETRIES=1`: **5 passed, 1 error, 1 failure** in 164.578s. Chain ID 56, health, wrong-chain detection, USDT metadata, V2 deployment/pair, V3 deployment, and enabled tiers `(100, 500, 2500, 10000)` succeeded. The V2/V3 quote test errored on `missing trie node` while probing the V3 QuoterV2; full dry-run returned zero directions and failed its two-direction assertion. No quote succeeded in this run.
- Live endpoint `https://bsc-dataseed1.binance.org`, with the same bounded settings: **2 passed, 4 errors, 1 failure** in 192.508s. The endpoint intermittently timed out during TLS/RPC calls and also returned `missing trie node`; no quote or complete dry-run was obtained.

Therefore both V2 and V3 live quotes did **not** succeed in both directions, and full live Dry-Run did **not** succeed. These outcomes are endpoint/network/state-availability blockers, not evidence of an ABI defect. No paid RPC, private key, signing, broadcast, approval, transfer, withdrawal, or swap was used.

### Gas semantics

The mock/unit cases verified all required states: `ESTIMATED_NODE` is sourced only from local `eth_estimateGas`; `PARTIAL_POOL_ONLY` is explicitly labeled as the V3 QuoterV2 pool-level hint and excludes router/transfer/intrinsic gas; and `UNKNOWN` has no fabricated units, cost, or net result. Live gas could not be validated because no live quote completed. The live dry-run correctly withheld directions when prerequisite RPC data was unavailable.

### Delivery

Confirmed source change: one bounded regression test assertion update in `tests/test_dex_pancakeswap.py`; no runtime implementation correction was required. The initial validation commit is `33415a9`; the report-SHA finalization commit is recorded by the delivery agent. Keep PR #6 Open, Draft, and Unmerged.

## 9. Alpha closeout preflight (2026-11-20)

No ABI research or previously attempted endpoint was repeated. The following previously untested free public endpoints received one bounded `eth_chainId` POST preflight at approximately `2026-11-20T22:00Z`, PowerShell `Invoke-RestMethod`, `-TimeoutSec 6`: `https://bsc-dataseed2.binance.org` -> chain 56 in 4429 ms; `https://bsc-dataseed3.binance.org` -> chain 56 in 3113 ms; `https://bsc-dataseed4.binance.org` -> chain 56 in 5263 ms; `https://bsc-dataseed1.defibit.io` -> timeout at 6026 ms; `https://bsc-dataseed1.ninicoin.io` -> chain 56 in 3038 ms; `https://bsc.publicnode.com` -> chain 56 in 3779 ms.

The complete live command was run against `https://bsc-dataseed3.binance.org` with `RUN_LIVE_BSC_TESTS=1 BSC_RPC_TIMEOUT_SECONDS=5 BSC_RPC_MAX_RETRIES=0`: `python -m unittest discover -s tests -p "test_dex_live_rpc.py" -v`. It reached chain/health, token metadata, V2 deployment/pair, then failed V3 tier discovery (`enabled fee tiers: ()`), hit `missing trie node` during V2 quote setup, and the dry-run returned zero directions. Result: **4 passed, 2 failures, 1 error** in 144.438s; block `134770716`, block timestamp `2026-11-20T21:54:38Z`.

The complete live command was also attempted against `https://bsc.publicnode.com` with the same bounded settings. It passed setup through V3 pool/tier discovery and did not complete the quote/dry-run phase within the bounded per-call behavior; the process was stopped after prolonged RPC timeouts. A single targeted quote command, `python -m unittest tests.test_dex_live_rpc.LiveBscTests.test_06_quotes_both_dexes_both_directions -v`, with `BSC_RPC_TIMEOUT_SECONDS=3 BSC_RPC_MAX_RETRIES=0`, then failed in `setUpClass` while `eth_getBlockByNumber` timed out; **0 tests ran**.

No new endpoint provided a complete, stable quote run. V2 exact-input/exact-output, V2 Router cross-checks, V3 exact-input/exact-output, and complete live Dry-Run therefore remain unverified. Phase 2 remains **PARTIALLY COMPLETED**. The existing gas audit remains valid: `PARTIAL_POOL_ONLY` is not complete transaction gas and cannot claim complete profitability or `READY`; `UNKNOWN` produces no fabricated gas cost or net result. No code defect was confirmed and no runtime code was changed.

## 10. Alpha closeout correction

The closeout audit identified and corrected one bounded gas-semantics defect: `PARTIAL_POOL_ONLY` previously produced a net figure and status `READY` with only an understatement qualifier. It now retains the explicit pool-level gas estimate but withholds `net_output` and reports `READY_GAS_PARTIAL`; only a complete node estimate can report `READY`. `UNKNOWN` behavior remains unchanged and withholds both cost and net output. Focused and full suites remained **55 passed, 7 skipped** and **146 passed, 7 skipped**, respectively, after the correction. The correction is covered by `tests/test_dex_gas_dry_run.py`.

Closeout implementation and test correction are committed as `58932bd5c3b49352ed4646f36df88978c36ce908` from starting HEAD `f22cbe6672604b63d6b603a8b5831eda1c0d5a89`; the pre-alignment closeout HEAD `84fb93aa95ceb2f63325bcededcdb5705935ea95` is historical. The authoritative current HEAD is recorded in **Current Closeout** above. Keep PR #6 Open, Draft, and Unmerged.
