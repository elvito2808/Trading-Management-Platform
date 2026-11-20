"""``python -m trading_platform.dex`` - print the read-only dry-run report as JSON.

Requires BSC_RPC_URL. Optional: BSC_GAS_SIMULATION_FROM (a PUBLIC address that holds
BNB; used only as the ``from`` of a local eth_estimateGas simulation).
No private key, wallet or signing capability exists in this program.
"""
from __future__ import annotations

import os
import sys

from .bsc import BscAdapter, BscAdapterConfig
from .dry_run import DryRunPreparer
from .errors import DexError
from .gas import ENV_SIMULATION_FROM, GasEstimator


def main() -> int:
    try:
        config = BscAdapterConfig.from_env()
        adapter = BscAdapter(config)
        sender = os.environ.get(ENV_SIMULATION_FROM, "").strip() or None
        report = DryRunPreparer(adapter, gas=GasEstimator(adapter, simulation_sender=sender)).prepare_all()
    except DexError as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(report.to_json())
    return 0 if report.directions and all(d.status != "NOT_READY" for d in report.directions) else 1


if __name__ == "__main__":
    raise SystemExit(main())
