#!/usr/bin/env python3
"""Idempotently append the Phase 2 / Agent 02 sections to the project management files.

Run from the repository root on the Phase 2 branch:  python scripts/apply_phase2_docs.py <COMMIT_SHA> <PR_URL_OR_PENDING>
Existing files are appended to (never recreated); missing files are reported, not invented.
"""
import pathlib
import sys

SHA = sys.argv[1] if len(sys.argv) > 1 else "PENDING"
PR = sys.argv[2] if len(sys.argv) > 2 else "PENDING"
MARK = "<!-- PHASE-2-BSC-PANCAKESWAP-INTEGRATION -->"

SECTION = f"""
{MARK}
## Phase 2 - BNB Smart Chain + PancakeSwap V2/V3 (Agent 02) - PARTIALLY COMPLETED

* Task ID: PHASE-2-BSC-PANCAKESWAP-INTEGRATION. Mode: READ-ONLY / DRY-RUN. Branch: `feature/phase-2-bsc-pancakeswap-integration`
  (based on `feature/phase-1-kucoin-integration`, PR #5 head). PR #5 is NOT merged. Commit: {SHA}. PR: {PR}.
* Implemented: `trading_platform/dex/` (BSC adapter, V2, V3, gas, registry, dry-run). KuCoin code untouched.
* Focused suite (Python 3.10.11): 48 passed, 7 skipped (live tests are opt-in). Full suite:
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q` — 146 passed, 7 skipped.
* Live read-only suite against `https://bsc-dataseed.binance.org`: 5 passed, 1 error and 1 failure after RPC
  TLS timeouts during quote/dry-run checks. Chain ID 56 was confirmed; full live quote validation did not pass.
  A second endpoint, `https://1rpc.io/bnb`, passed an initial chain-ID probe but timed out during live-suite setup
  before any tests ran; `https://bsc-rpc.publicnode.com` also timed out.
  The plain `python -m pytest -q` command is blocked by a global `web3.tools.pytest_ethereum` plugin import error
  (`eth_typing.ContractName`).
* Full report: `docs/agents/reports/PHASE-2-BSC-PANCAKESWAP-AGENT-02.md`. Usage: `docs/BSC_PANCAKESWAP_INTEGRATION.md`.
"""

ENV = """
# --- Phase 2: BNB Smart Chain read-only integration (no secrets; never commit real keys) ---
# Any BSC RPC endpoint (public or your own node). No paid endpoint is required or assumed.
BSC_RPC_URL=
# BSC_RPC_TIMEOUT_SECONDS=10
# BSC_RPC_MAX_RETRIES=2
# BSC_MAX_BLOCK_AGE_SECONDS=30
# Optional PUBLIC address that holds BNB; used only as `from` of a local eth_estimateGas simulation. Never a private key.
# BSC_GAS_SIMULATION_FROM=
"""

for name, text in (("PROGRESS.md", SECTION), ("docs/PROJECT_STATUS.md", SECTION),
                   ("docs/agents/IMPLEMENTATION_STATE.md", SECTION), (".env.example", ENV)):
    path = pathlib.Path(name)
    marker = "BSC_RPC_URL=" if name == ".env.example" else MARK
    if not path.exists():
        print(f"SKIP (missing): {name}")
        continue
    body = path.read_text()
    if marker in body:
        print(f"already applied: {name}")
        continue
    path.write_text(body.rstrip("\n") + "\n" + text)
    print(f"updated: {name}")
