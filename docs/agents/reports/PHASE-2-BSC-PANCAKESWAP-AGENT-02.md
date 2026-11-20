# AGENT 02 REPORT - PHASE-2-BSC-PANCAKESWAP-INTEGRATION

**Final status: `DELIVERED - PENDING ALPHA REVIEW`** (source and documentation pushed; live quote validation remains incomplete)
Date: 2026-11-20 | Mode: READ-ONLY / DRY-RUN | Network: BSC Mainnet (chain 56)

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
