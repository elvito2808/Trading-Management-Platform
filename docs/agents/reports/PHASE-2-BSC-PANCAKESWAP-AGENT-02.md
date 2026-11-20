# AGENT 02 REPORT - PHASE-2-BSC-PANCAKESWAP-INTEGRATION

**Final status: `PARTIALLY COMPLETED`** (code + mock tests done; live verification and Git delivery BLOCKED, see section 6)
Date: 2026-11-20 | Mode: READ-ONLY / DRY-RUN | Network: BSC Mainnet (chain 56)

## 1. Summary
A new package `trading_platform/dex/` provides a BSC adapter, independent PancakeSwap V2 and V3 modules, gas
estimation, a contract registry and a fixed-size (0.1 BNB) dry-run in both directions. No KuCoin file was modified.
Nothing can sign, broadcast, approve, transfer or hold keys; this is enforced by an RPC method allow-list and tested.

## 2. Files (all new; nothing existing was edited except by the idempotent docs script)
`trading_platform/dex/{__init__,__main__,abi,errors,models,registry,rpc,bsc,pancakeswap_v2,pancakeswap_v3,gas,dry_run}.py`;
`tests/{dex_fake_chain,test_dex_abi_registry,test_dex_rpc_bsc,test_dex_pancakeswap,test_dex_gas_dry_run,test_dex_safety_static,test_dex_live_rpc}.py`;
`docs/BSC_PANCAKESWAP_INTEGRATION.md`; this report; `scripts/apply_phase2_docs.py` (appends to PROGRESS.md,
docs/PROJECT_STATUS.md, docs/agents/IMPLEMENTATION_STATE.md, .env.example). No new runtime dependency (stdlib JSON-RPC;
`eth_hash` is used when present, with a pure-Python keccak fallback).

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
* **Mock/unit (fake chain, no network): 48 passed, 0 failed** (`python -m unittest discover -s tests -p "test_dex_*.py"`, Python 3.12).
  Covers: keccak/selectors/EIP-55, read-only allow-list (no I/O for send/sign), URL redaction, retry bounds, chain-id mismatch,
  unreachable/stale RPC, token validation, V2 math and Router cross-check (match / mismatch / unavailable), V3 tier/pool/quoter handling,
  gas (node / partial / unknown), dry-run for both directions, no-dynamic-sizing, JSON units, static scans (no float, no key/signing code, no secrets).
* Two initial failures during development were wrong test expectations (impact magnitude; exact-out impact definition), not code defects; tests were corrected.
* **Live: 7 tests written, 0 executed (skipped).** The sandbox has no network (`Could not resolve host`).
  **No claim of real BSC/PancakeSwap connectivity is made.** Mock success is not live success.
* eth_hash code path was not exercised here (library absent); the fallback was validated against known keccak vectors and the
  selectors below. CI (which installs eth_hash) will exercise it.

## 5. Limitations / things to confirm in the first live run
1. Function selectors were verified against independent well-known values (Uniswap-derived). Pancake V3 `QuoterV2`/`slot0()` layout
   is assumed identical to Uniswap V3 (struct params, 7-word `slot0`); the live test `test_06`/`test_05` will confirm.
2. V2 fee 0.25% (9975/10000) is hard-coded in the local formula; the Router cross-check turns any disagreement into `INVALID`.
3. WBNB address lacks a primary official page; mismatch with `router.WETH()` invalidates quotes.
4. Gas: node simulation exists only for V2 BNB->USDT and needs `BSC_GAS_SIMULATION_FROM` (public address with BNB). Other paths use the
   V3 quoter's pool-level estimate (`PARTIAL_POOL_ONLY`, understates real gas, flagged) or are `UNKNOWN` (no net figure). BSC is treated as legacy gas price; EIP-1559 is not assumed.
5. V3: only single-hop pools on the four probed fee tiers (100/500/2500/10000 ppm, enabled ones detected on-chain); no multi-hop routing.
6. The ledger in `docs/agents/IMPLEMENTATION_STATE.md` lists a Phase 1 head SHA (cd07970...) different from the actual PR #5 head
   (31682950...). Not changed by this work; flagged for ALPHA.

## 6. Blockers
* **Git delivery blocked:** the GitHub integration returned `403 Resource not accessible by integration` on branch creation, so no branch,
  commit, push or PR could be created. Commit SHA: **none**. PR: **none opened**. PR #5 untouched, `main` untouched.
  Intended: branch `feature/phase-2-bsc-pancakeswap-integration` from `feature/phase-1-kucoin-integration` (PR #5 head), stacked PR targeting that branch.
* **Live tests blocked:** no network in the execution sandbox.

## 7. Next steps for the owner
Apply the delivered files onto the branch, run `python scripts/apply_phase2_docs.py <SHA> <PR_URL>`, run the live tests, open the PR.
