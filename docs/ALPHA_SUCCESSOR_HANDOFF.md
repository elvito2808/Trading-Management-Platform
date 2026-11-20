# Successor ALPHA Handoff — Trading Management Platform

**Document type:** Authoritative operational handoff for the next ALPHA manager  
**Prepared:** 2026-11-20  
**Repository:** https://github.com/elvito2808/Trading-Management-Platform  
**Handoff branch:** `docs/alpha-successor-handoff`  
**Handoff base / implementation snapshot:** `f22cbe6672604b63d6b603a8b5831eda1c0d5a89` on `feature/phase-2-bsc-pancakeswap-integration`  
**Purpose:** Transfer project ownership and decision context to a new ALPHA. This document does not authorize merging, live trading, signing, broadcasting, or financial activity.

> **Read this document first.** It reconciles older project documents that contain stale branch names, phase statuses, and test counts. For current Git state, always re-query GitHub; this document is a handoff snapshot, not a substitute for live verification.

---

## 1. Executive summary

The project is intended to become a modular platform for operating and managing one or more CEX/DEX arbitrage bots. It is not yet a complete arbitrage bot and is not production-ready.

The immediate configured use case is **BNB-USDT arbitrage between KuCoin and PancakeSwap on BNB Smart Chain**, evaluating both directions and both PancakeSwap V2 and V3. The system must obtain current market/pool data, calculate executable prices after trading fees and gas, compare venues, and prepare a simulation only when the configured net-profit condition is met. All trading remains **Dry-Run / simulation only**.

Current delivery:
- Phase 0 — repository audit and architecture: **ACCEPTED**.
- Phase 1 — KuCoin integration: **ACCEPTED WITH DEFERRED VALIDATION ITEMS**. The integration is on Draft PR #5 and has not been merged.
- Phase 2 — BSC/PancakeSwap V2/V3 integration: **PARTIALLY COMPLETED**. The implementation and ABI work are delivered on Draft PR #6; successful live quotes in both directions and a complete live Dry-Run remain unverified because public RPC calls failed/timed out.
- Phase 3 onward: not started. The existing sequence says not to begin Phase 3 until Phase 2 is accepted, unless the project owner explicitly changes that sequencing decision.

**Do not describe the current platform as capable of completed arbitrage execution.** KuCoin and DEX adapters have been delivered, but the arbitrage engine, strategy lifecycle, risk/recovery, integrated paper execution, and end-to-end validation remain outstanding.

## 2. Current repository and Git snapshot

Repository: `elvito2808/Trading-Management-Platform`

| Item | Snapshot at handoff |
|---|---|
| Default branch | `main` |
| Main SHA observed in the project status history | `6cb4d2d2d9e6ec00310c672cf822ee46db5e5c22` |
| Phase 1 branch | `feature/phase-1-kucoin-integration` |
| Phase 1 PR | [#5](https://github.com/elvito2808/Trading-Management-Platform/pull/5) — Open, Draft, Unmerged |
| Phase 1 submitted HEAD | `316829506217cb817c69f04af5eaa9614a29feff` |
| Phase 2 branch | `feature/phase-2-bsc-pancakeswap-integration` |
| Phase 2 PR | [#6](https://github.com/elvito2808/Trading-Management-Platform/pull/6) — Open, Draft, Unmerged |
| Phase 2 latest HEAD | `f22cbe6672604b63d6b603a8b5831eda1c0d5a89` |
| Phase 2 base | Phase 1 branch at `316829506217cb817c69f04af5eaa9614a29feff` |
| Handoff branch | `docs/alpha-successor-handoff`, created from the Phase 2 HEAD above |

At the time of the latest direct PR #6 inspection:
- PR #6 was Open, Draft, and Unmerged; its base was `feature/phase-1-kucoin-integration`.
- It contained 27 changed files and 5 commits.
- No combined CI status evidence was available. Do not claim CI passed.
- PR #5 was also Open, Draft, and Unmerged.
- Neither PR has merge authorization. No merge was performed by this handoff.

The new ALPHA must re-check both PRs, branch heads, base SHAs, main, rulesets, CI/status checks, changed files, and PR discussion before taking further action. Never assume a SHA or PR state remains current.

## 3. Project purpose and target architecture

### 3.1 Product objective

Build an extensible trading-management platform that can:
1. Connect to multiple centralized and decentralized exchanges through isolated adapters.
2. Read live or near-live market/pool data and metadata.
3. Normalize prices, quantities, timestamps, provenance, fees, and gas into common contracts.
4. Evaluate multiple trading pairs and arbitrage strategies independently.
5. Calculate net executable outcomes after applicable trading fees, pool fees, price impact, gas, and other explicitly modeled costs.
6. Generate deterministic Dry-Run / Paper-Trade simulations and auditable reports.
7. Provide strategy lifecycle, portfolio/risk controls, recovery, persistence, monitoring, and eventually operational readiness.

The platform must remain modular: adding a CEX, DEX, network, pair, or strategy must not require invasive changes to existing adapters or cause interference between independently configured pairs/strategies.

### 3.2 Architecture principles

- Retain the existing Python **Modular Monolith** unless an accepted architecture decision explicitly changes it.
- Use provider-neutral interfaces / Protocols and adapter boundaries.
- Separate concerns: domain, application services, market data, exchange/DEX adapters, strategy, risk, execution/simulation, portfolio, persistence, and operational interfaces.
- Keep strategy logic independent of bot lifecycle and execution implementation.
- Keep domain/application logic independent of concrete persistence technology; preserve the existing repository Protocol and SQLite adapter.
- Prefer configuration-driven behavior and explicit typed contracts.
- Use `Decimal` for all financial arithmetic; do not use binary floating point for prices, quantities, fees, profit, or gas-cost calculations.
- Use integer raw token units for EVM contract amounts and explicit decimal conversion at boundaries.
- Carry source, timestamp, freshness/validity, and (for chain data) block provenance with quotes.
- Fail closed: stale, invalid, mismatched, incomplete, or unknown inputs must not produce a usable arbitrage decision or fabricated net profit.
- Keep multiple pairs and strategies isolated; no shared mutable per-pair state unless deliberately designed and tested.

### 3.3 What the existing baseline already provides

The repository baseline includes a Python 3.10+ Modular Monolith, CLI and central Command Registry, Readiness Checker, TOML configuration, logging, metadata domain/services, SQLite persistence and tests. The metadata/persistence layer includes typed Network, Token, Contract, ABI, Asset, TradingPair, TradingVenue, Market and FeeMetadata concepts, relationships, validation, CRUD and schema versioning. The README and existing historical project records describe this baseline.

The metadata layer is not itself a market-data adapter, arbitrage engine, wallet, or execution system.

## 4. User-approved arbitrage scenario and fixed business rules

These are explicit project decisions and must not be silently changed:

| Setting | Owner-defined value / rule |
|---|---|
| Pair | `BNB-USDT` |
| CEX | KuCoin |
| DEX | PancakeSwap on BNB Smart Chain Mainnet |
| Chain ID | `56` |
| PancakeSwap versions | Both V2 and V3 |
| Directions | Both directions |
| Evaluation size | Exactly `0.1 BNB` equivalent |
| Profit basis | Net, after modeled fees and gas |
| Trigger threshold | Net difference must be **strictly greater than USD 0.10** |
| USDT contract | `0x55d398326f99059fF775485246999027B3197955` |
| KuCoin taker-fee assumption | Exactly `Decimal("0.001")` (0.1%) |
| Execution mode | Dry-Run / simulation only |

Directional semantics:
- **BNB → USDT:** evaluate selling exactly 0.1 BNB; use the CEX Best Bid when selling base on KuCoin and the DEX exact-input quote for 0.1 BNB.
- **USDT → BNB:** calculate the USDT required to obtain exactly 0.1 BNB; do not substitute an arbitrary USDT input or change the target output size.
- Compare the actual executable side of each venue, not a midpoint/spot price as if it were executable.
- Deduct KuCoin fees, PancakeSwap pool/protocol fees, price impact/slippage as modeled, and gas costs. A net result must not be emitted if a required cost is `UNKNOWN` or materially incomplete.

Important qualifications:
- The user-specified USDT address must be preserved exactly. Do not replace it with a different bridged or canonical token without explicit owner approval.
- The user has not finalized a DEX funding/settlement model (pre-funded balances versus transfers). Do not invent one or implement transfers.
- Earlier suggested balances of 0.5 BNB for costs and 4.5 WBNB for execution were not finalized. Do not treat them as funded or approved balances.
- BNB is the native gas asset; PancakeSwap pool interactions use WBNB. WBNB validation against the V2 Router's `WETH()` is required at runtime before using the registered WBNB address.
- No wallet is required for the current stage. No actual wallet balance is to be consumed.

## 5. Phase-by-phase status and evidence

### Phase 0 — Repository audit and architecture

**Status: ACCEPTED.**

Scope completed:
- Identified the existing Python Modular Monolith, package structure, CLI/registry/readiness/configuration, metadata and persistence baseline.
- Established modular adapter-based direction and phased development plan.
- Confirmed this repository is infrastructure for a future bot, not an already-running arbitrage system.

No implementation change was required as part of the audit. The acceptance is the project owner's/ALPHA's prior phase decision; do not confuse it with a CI run.

### Phase 1 — KuCoin integration

**Status: ACCEPTED WITH DEFERRED VALIDATION ITEMS.**

Delivery:
- Branch: `feature/phase-1-kucoin-integration`
- PR #5: https://github.com/elvito2808/Trading-Management-Platform/pull/5
- Submitted HEAD: `316829506217cb817c69f04af5eaa9614a29feff`
- PR #5 is Open, Draft, Unmerged.

Implemented package and behavior:
- Provider-neutral exchange contracts / adapter and typed Decimal models.
- Public market-data integration designed WebSocket-first for configured symbols.
- Best Bid / Best Ask only; REST Level 1 fallback when WebSocket is disconnected, subscription fails, quote is stale, or an error occurs.
- No Level 2 depth, order-book VWAP, or depth-based sizing.
- After reconnect, WebSocket is considered recovered only after a fresh valid WebSocket quote.
- Explicit fixed quantity supplied by caller; no invented quantity, balance sizing, dynamic scaling, or silent rounding.
- Quote source, exchange/local timestamps, freshness and connection/provenance information.
- Bounded retries/backoff and rate-limit-aware fallback behavior.
- Read-only balance parsing was included in the delivered adapter, but live private account/balance connectivity has not been validated.
- Fee policy is a fixed project assumption of `Decimal("0.001")`; do not call it the user's actual KuCoin account fee.
- No order submission or account-changing methods are authorized.

Test evidence (agent-reported; accepted for sequencing, not independently rerun by the accepting ALPHA):
- Focused KuCoin suite: **55 passed**.
- Full suite at Phase 1 submission: **98 passed**.
- Runs used pytest plugin autoload disabled because a globally installed `web3.tools.pytest_ethereum` plugin fails importing `eth_typing.ContractName`.
- No CI green result was established for the submitted head.

Deferred validation:
- No live KuCoin public WebSocket connectivity was demonstrated.
- No live KuCoin REST market-data connectivity was demonstrated.
- No live authenticated private balance/account connectivity was demonstrated.
- No complete end-to-end Paper Trading lifecycle was validated.
- The fixed 0.1% is a configured assumption, not an account-specific fee.

Acceptance decision:
- The prior ALPHA explicitly accepted Phase 1 **with deferred validation items** so the project could proceed to the DEX integration sequence.
- PR #5 remains Draft/Open/Unmerged. Acceptance did not authorize merge.

Primary detailed source: `docs/agents/reports/PHASE-1-KUCOIN-AGENT-01.md` on the Phase 1 branch. Treat old historical sections inside it as chronology; use its latest continuation plus this handoff for the current accepted scope.

### Phase 2 — BNB Smart Chain + PancakeSwap V2/V3

**Status: PARTIALLY COMPLETED — not accepted as fully complete.**

Delivery:
- Branch: `feature/phase-2-bsc-pancakeswap-integration`
- PR #6: https://github.com/elvito2808/Trading-Management-Platform/pull/6
- Base: Phase 1 submitted HEAD `316829506217cb817c69f04af5eaa9614a29feff`
- Latest HEAD at handoff: `f22cbe6672604b63d6b603a8b5831eda1c0d5a89`
- PR #6 is Open, Draft, Unmerged.
- 27 changed files at latest direct PR metadata inspection.
- KuCoin source files were not modified by Phase 2.

Implemented:
- `trading_platform/dex/`: ABI helpers, RPC client, BSC adapter, contract registry, PancakeSwap V2 and V3 modules, gas handling, typed models/errors, Dry-Run plan/report.
- BSC Chain ID 56 check and read-only RPC allow-list. Signing/sending methods are rejected before network I/O.
- PancakeSwap V2 pair discovery/reserves, exact-input and exact-output math, 0.25% fee assumption, price impact and Router cross-check.
- PancakeSwap V3 enabled fee-tier discovery, single-hop pool discovery/validation, QuoterV2 exact-input and exact-output quote support, price impact and `slot0()` reference-price decoding.
- Fixed-size Dry-Run for exactly 0.1 BNB, both directions; reverse direction derives USDT input for exact 0.1 BNB output.
- Contract registry and explicit source/verification statuses.
- Gas result states: `ESTIMATED_NODE`, `PARTIAL_POOL_ONLY`, `UNKNOWN`.
- Quote provenance, block pinning and validity handling.
- No signing, broadcasting, approval, transfer, withdrawal, wallet key, or real swap.

Important contract registry facts:
- USDT `0x55d398326f99059fF775485246999027B3197955`: owner-specified; metadata read on-chain.
- WBNB `0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c`: secondary-source provenance only; runtime equality with `router.WETH()` is mandatory.
- V2 Factory `0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73`.
- V2 Router `0x10ED43C718714eb63d5aA57B78B54704E256024E`.
- V3 Factory `0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865`.
- V3 QuoterV2 `0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997`.
- V3 SwapRouter `0x1b81D678ffb9C0263b24A97847620C99d213eB14` is informational only and is not called.
- Official PancakeSwap address docs were consulted for documented addresses; WBNB source provenance remains secondary.

ABI validation addendum:
- Agent 04 checked official PancakeSwap source repository `pancakeswap/pancake-v3-contracts`, commit `986847948755cba528324d41be19480731c36c2a`.
- QuoterV2 single-hop calls use a five-word static tuple: tokenIn, tokenOut, amountIn/amountOut, fee, sqrtPriceLimitX96.
- The return data has exactly four values, in order: amountOut/amountIn, sqrtPriceX96After, initializedTicksCrossed, gasEstimate.
- Pancake V3 `slot0()` has seven ABI words; decoding of sqrtPriceX96, signed int24 tick and unlocked boolean was checked.
- Agent added regression assertions for return word order/values and slot0-related interpretation.
- This validates the ABI layout against source; it does **not** prove successful calls against the live deployed contract.

Test evidence after Agent 04 (agent-reported):
- Mock/unit: **55 passed, 7 skipped**. Seven skips are opt-in live tests, not passes.
- Full suite: **146 passed, 7 skipped**.
- Plain pytest plugin autoload has the known external `web3.tools.pytest_ethereum` / `eth_typing.ContractName` import issue; successful full suite used `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`.
- No CI green result was established. These are local agent-reported results, not CI evidence.

Live RPC results after Agent 04:
- `https://bsc-dataseed.binance.org`: **5 passed, 1 error, 1 failure**. Chain ID 56, health/wrong-chain checks, USDT metadata, V2 deployment/pair, V3 deployment and enabled fee tiers succeeded. Quote validation errored with `missing trie node`; full Dry-Run had zero directions and failed its two-direction assertion.
- `https://bsc-dataseed1.binance.org`: **2 passed, 4 errors, 1 failure**. Intermittent TLS/RPC timeouts and `missing trie node`; no successful quote or complete Dry-Run.
- Therefore V2/V3 live quotes did not complete in both directions; full live Dry-Run did not complete.
- These failures are RPC/state-availability blockers in the reported runs, not proof of an ABI defect. Do not represent them as successful live validation.

Gas semantics:
- `ESTIMATED_NODE`: local `eth_estimateGas` result for a realistic supported call; still an estimate, not paid gas.
- `PARTIAL_POOL_ONLY`: V3 QuoterV2 pool-level hint only; excludes router, transfer and intrinsic gas and can understate the real total.
- `UNKNOWN`: no gas units/cost/net result may be fabricated. A net profitability result must be withheld when required gas is unknown.
- Node simulation is only implemented for V2 BNB→USDT and requires an optional public address with BNB as the `from` of a local `eth_estimateGas`; it does not require a private key or sign/broadcast.
- Other routes may return partial hints or `UNKNOWN`.

Remaining Phase 2 acceptance criteria:
1. Obtain stable, free public BSC RPC access (no paid service unless separately approved).
2. Successfully perform live read-only V2 quotes for both directions and cross-check Router values.
3. Successfully perform live read-only V3 QuoterV2 quotes for both directions; validate decoded values.
4. Complete full live Dry-Run for both directions with honest gas-state handling.
5. Preserve exact RPC endpoint, timestamp/block, commands, responses, and pass/fail evidence.
6. New ALPHA independently reviews source/tests/report and decides whether Phase 2 can be accepted. Do not assume endpoint failure means code failure, but do not waive required live validation silently.

Primary sources: `docs/agents/reports/PHASE-2-BSC-PANCAKESWAP-AGENT-02.md`, `docs/BSC_PANCAKESWAP_INTEGRATION.md`, and PR #6.

### Phase 3 — Arbitrage engine

**Status: NOT STARTED; sequence-gated by Phase 2 acceptance unless owner explicitly changes order.**

Expected scope:
- Normalize KuCoin and PancakeSwap quote contracts.
- Evaluate both venues and both directions for BNB-USDT.
- Compare executable prices, not spot/midpoint values.
- Apply fixed 0.1 BNB evaluation size.
- Deduct all modeled CEX fees, DEX pool fees, price impact and gas.
- Enforce strict net threshold (> USD\ 0.10).
- Withhold decision when data is stale/invalid or required costs are unknown/incomplete.
- Generate Dry-Run only; no real execution.
- Keep pair/venue/strategy configuration isolated and extensible.
- Define deterministic rounding and precision rules; never silently resize.

Do not invent settlement, inventory, pre-funding, transfer or balance assumptions. The owner has not finalized DEX funding/settlement behavior.

### Phase 4 — Multi-strategy management

**Status: NOT STARTED.**

Design and implement strategy definitions, configuration, lifecycle, scheduling/activation/deactivation, independent pair/strategy state, and orchestration. Keep strategy logic separate from execution and bot lifecycle. Support multiple pairs without cross-contamination.

### Phase 5 — Risk management and recovery

**Status: NOT STARTED.**

Define deterministic risk controls, exposure/position limits, stale-data protections, quote validity, circuit breakers, cooldowns, idempotency, error/retry policy, recovery/reconciliation and persistent state. Any future live capability requires a separate owner authorization; this phase does not authorize it.

### Phase 6 — Testing and validation

**Status: NOT STARTED as a project-wide integrated phase.**

Includes independent unit/contract/integration tests, multi-pair isolation, failure injection, deterministic simulation, fee/gas accounting, persistence/recovery, supported Python matrix and security review. Existing adapter unit tests do not replace end-to-end platform validation.

### Phase 7 — Live-trading readiness

**Status: NOT STARTED; readiness assessment only.**

Review deployment, monitoring, key custody, permissions, operational controls, incident handling, reconciliation, security and rollback. Completing this phase does not authorize live trading. Real trading remains prohibited unless the owner separately and explicitly authorizes a dedicated scope.

## 6. Complete project rules and operating constraints

These rules are binding unless the owner explicitly changes them.

### 6.1 ALPHA manager workflow

- The owner communicates with one manager: ALPHA.
- ALPHA reviews requests, breaks work into bounded tasks, delegates to execution agents, reviews their reports/diffs/evidence, decides acceptance, and gives the owner the next exact agent prompt or revision prompt.
- Execution agents receive instructions from ALPHA and report back to ALPHA; the owner is a relay and should not have to coordinate technical agents directly.
- Agents must not wait for owner confirmation for coding/file creation that is already within the assigned scope.
- Do not ask the owner to provision routine environments. Agents should create their own isolated clone/workspace and install dependencies as needed.
- The user should not be asked to repeat already documented decisions. Ask only when a genuinely necessary owner decision is missing.
- Keep tasks bounded and avoid unnecessary agent count, repeated research, and redundant full test loops.
- Do not use endless tests as the main response. If blocked, report the exact command, file/location, error, cause, attempts, and the specific capability/documentation needed.
- Every agent must persist progress and handoff information in repository documentation so a successor can resume without repeating work.
- ALPHA must independently verify agent claims against GitHub state, code, diffs, tests and source evidence. An agent report is evidence, not proof.
- Never claim tests, CI, live connectivity, Git actions, merge, or code changes that were not actually performed and verified.

### 6.2 Repository and Git safety

- Never modify `main` directly.
- Work from an isolated clone/worktree/copy, on a dedicated feature or documentation branch.
- Preserve the original repository and existing branch histories; do not reset, overwrite, rebase destructively, or force-push.
- Do not merge PR #5, PR #6, or any future PR without explicit owner authorization.
- Keep PRs Draft/Open/Unmerged unless the owner explicitly instructs otherwise.
- Do not close, delete, or rewrite historical branches/PRs as housekeeping without explicit authorization.
- Before work, verify repository, branch, base SHA, current HEAD, working tree, and relevant PR. Before delivery, record exact changed files, commits, tests, and limitations.
- Do not assume the latest branch/PR SHA in this document remains current; verify it.
- Do not claim CI green without an actual successful status/check/run for the relevant HEAD.
- Prefer one focused PR per bounded phase; do not create duplicate PRs for an existing workstream.
- Documentation-only handoff changes must also use an isolated branch and remain unmerged pending owner approval.

### 6.3 Financial and blockchain safety — absolute current restriction

All project execution is Dry-Run / simulation only.

Prohibited unless the owner gives a separate explicit authorization for a future scope:
- Real orders or swaps.
- Wallet creation/import/use for signing.
- Private keys, seed phrases, signing, transaction broadcast or submission.
- Token approvals, transfers, deposits, withdrawals, bridge operations or account-changing requests.
- Any real consumption or movement of user funds.
- Paid RPC/API/cloud infrastructure.
- Any hidden live execution path or feature flag that can accidentally submit transactions.

Allowed within current read-only scope:
- Public unauthenticated market/pool reads and public RPC calls.
- Read-only account/balance access only if a separately approved secure credential path exists; never request or expose credentials in the current task.
- Local `eth_estimateGas` simulation without signing or broadcasting.
- Mock/fake-chain tests and deterministic Dry-Run plan generation.

No wallet or real funds are needed for current development. A public address used solely as an `eth_estimateGas` simulation `from` value is not a private key and must never be used to sign or send.

### 6.4 Secrets and infrastructure

- Never commit API keys, secrets, passwords, access tokens, seed phrases, private keys, or credential-bearing RPC URLs.
- Never print secrets in logs, exceptions, test output, progress reports or PR bodies.
- Keep placeholders in `.env.example`; real local environment files must remain ignored.
- `rpc_config_ref` is a future secure configuration reference, not a place to store a raw credential-bearing URL.
- No paid infrastructure without explicit owner approval. Prefer bounded use of free public endpoints; report endpoint limits honestly.

### 6.5 Technical correctness and financial arithmetic

- Use `Decimal` for all financial calculations; no `float` in production pricing/fee/profit paths.
- Use raw integer units for token contract amounts; explicit conversion with token decimals.
- Never round or resize the owner's fixed 0.1 BNB trade size silently.
- Use KuCoin Best Ask to buy base and Best Bid to sell base.
- Do not use depth/VWAP for the current KuCoin requirement.
- Include source, timestamp, freshness, validity and chain block provenance.
- After WebSocket reconnect, do not trust the feed until a fresh valid WS quote arrives.
- REST fallback must be bounded and rate-limit-aware; do not poll REST continuously while WS is healthy.
- Do not calculate net arbitrage profit from stale/invalid quotes, unknown gas, incomplete fee inputs or unmodeled required costs.
- A gas hint must be labeled accurately. `PARTIAL_POOL_ONLY` is not full transaction gas; `UNKNOWN` means no net result.
- Preserve user-specified token addresses and explicit configuration. Never silently substitute token/network/DEX.
- Adding a new adapter must not require editing the existing KuCoin adapter or break other pairs.

### 6.6 Agent execution, reporting and handoff

Every task prompt should include:
- Task ID and phase.
- Exact repository and branch/base requirements.
- Objective and narrow scope.
- Explicit prohibited operations.
- Acceptance criteria and required tests.
- Documentation/progress files to update.
- Error-handling/reporting expectations.
- Commit/PR rules and final report format.

Every agent final report must state:
- Exact repository, branch, base and final HEAD SHA.
- Files created/modified.
- Commits and PR URL/state.
- Commands actually run and exact test results (separate mock/unit/live/CI).
- What was and was not validated.
- Known limitations, blockers, exact errors and next action.
- Safety confirmations (no signing/sending/real trading).
- Updated progress/handoff documentation path.

Agents must not claim completion because tests pass if acceptance gates or live validations remain incomplete.

## 7. Canonical project decisions and unresolved owner choices

### Fixed decisions — do not reopen without reason
- BNB-USDT; KuCoin + BSC PancakeSwap V2/V3; both directions.
- Chain ID 56.
- Exact user-specified USDT address listed above.
- 0.1 BNB fixed evaluation size.
- Net profit basis and strict threshold greater than USD 0.10.
- KuCoin fee assumption `Decimal("0.001")`.
- Dry-Run only.
- Modular adapter architecture, multiple pairs/strategies isolated.
- No direct main writes, no merge without owner approval, no secrets, no paid infrastructure without approval.

### Explicitly unresolved
- DEX inventory/funding/settlement design (pre-funded inventory vs transfer-based); no transfers are authorized.
- Whether the earlier suggested 0.5 BNB cost reserve and 4.5 WBNB execution reserve should ever be adopted; they are not approved balances.
- Future pair list, additional exchanges, networks, DEXs and runtime schedules.
- Any move from Dry-Run to real execution; this requires separate explicit owner authorization and is currently prohibited.

Do not block implementation of unrelated read-only components by repeatedly asking about unresolved future funding. Keep the interfaces explicit and do not assume an answer.

## 8. Immediate instructions to the successor ALPHA

1. Read this handoff and the linked Phase 1/Phase 2 technical reports before assigning work.
2. Independently inspect the live GitHub repository, main, PR #5, PR #6, branch heads, diffs, CI and comments. Reconcile stale headers in `PROGRESS.md`, `docs/PROJECT_STATUS.md` and `docs/agents/IMPLEMENTATION_STATE.md` against the current verified state.
3. Preserve the current acceptance decisions: Phase 0 accepted; Phase 1 accepted with deferred validation; Phase 2 partially completed. Do not upgrade Phase 2 to complete based on ABI and unit tests alone.
4. Decide the next bounded action. The conservative path is to keep Phase 2 partially completed and defer only its live RPC acceptance gate until a reliable free endpoint can perform both directions and full Dry-Run. Do not run repeated identical RPC tests without a concrete change in endpoint conditions or test strategy.
5. Do not begin Phase 3 unless Phase 2 is accepted or the owner explicitly changes the sequence. If the owner later authorizes a sequencing exception, document it clearly and preserve the outstanding Phase 2 validation gate.
6. Continue to use a feature branch and Draft PR. Never merge without owner approval.
7. Maintain a single authoritative status and append dated progress/handoff updates after every accepted or materially changed task.
8. At every handoff, leave exact next action, current SHA, outstanding acceptance criteria and known blockers.

### Suggested Phase 2 validation completion criteria

When a materially better free public RPC option is available:
- Confirm Chain ID 56 and latest block.
- Read/validate registered contracts and WBNB == Router `WETH()`.
- Complete V2 exact-input BNB→USDT and exact-output USDT→BNB; compare Router outputs.
- Complete V3 QuoterV2 exact-input and exact-output for both directions and validate all decoded return fields.
- Complete Dry-Run in both directions, selecting/recording V2/V3 results and gas status.
- Confirm no result is emitted where required gas is `UNKNOWN` or only partial gas is incorrectly represented as complete.
- Preserve exact endpoint, block, commands, response/error, timestamps and result.
- Independent ALPHA review before acceptance.

If public RPC remains unavailable after a bounded, justified attempt, report the exact blocker and defer; do not fabricate results or buy a paid endpoint.

## 9. Canonical source files and reports

- `README.md` — project description, setup and scope exclusions.
- `PROGRESS.md` — chronological project progress and prior handoffs (contains historical/stale entries; reconcile).
- `docs/PROJECT_STATUS.md` — project phase/status record (header and Phase 1 fields are stale relative to later acceptance; reconcile).
- `docs/agents/IMPLEMENTATION_STATE.md` — living implementation/agent ledger (contains stale Phase 1 current-state claims; reconcile).
- `docs/KUCOIN_INTEGRATION.md` — KuCoin adapter usage and semantics.
- `docs/BSC_PANCAKESWAP_INTEGRATION.md` — BSC/PancakeSwap usage, quotes, gas and safety.
- `docs/agents/reports/PHASE-1-KUCOIN-AGENT-01.md` — full KuCoin implementation chronology and limitations.
- `docs/agents/reports/PHASE-2-BSC-PANCAKESWAP-AGENT-02.md` — Phase 2 implementation, Agent 04 ABI verification, latest test and RPC results.
- [PR #5](https://github.com/elvito2808/Trading-Management-Platform/pull/5) and [PR #6](https://github.com/elvito2808/Trading-Management-Platform/pull/6) — authoritative live review threads and diffs.

**Conflict rule:** This handoff is the reconciled statement of owner-approved decisions and the known acceptance state as of the snapshot date. If live GitHub evidence shows a later change, verify whether it was authorized, then update this handoff and the status ledger. Do not silently overwrite history.

---

## 10. Handoff completion statement

The outgoing ALPHA's task ends after preparing this handoff package. The successor ALPHA is responsible for all further repository inspection, technical decisions within the owner's constraints, agent delegation, independent review, progress maintenance and owner-facing next-step prompts.

No source implementation was changed by this handoff document. The handoff branch is documentation-only and must remain unmerged until the owner authorizes the relevant PR.
