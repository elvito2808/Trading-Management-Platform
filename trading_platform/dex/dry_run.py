"""Dry-run swap preparation for the fixed 0.1 BNB evaluation size.

Read-only by construction: it uses BscAdapter (allow-listed RPC methods only),
produces a report and a ``NonSendablePlan`` and exposes no signing, sending,
approval or wallet operation. There is no dynamic sizing: any amount other than
exactly 0.1 BNB is rejected.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from .bsc import BNB_DECIMALS, BscAdapter, require_valid
from .errors import DexError, InvalidInputError
from .gas import (GasEstimator, build_v2_swap_exact_eth_for_tokens_call,
                  build_v2_swap_tokens_for_exact_eth_calldata,
                  build_v3_exact_input_single_calldata, build_v3_exact_output_single_calldata)
from .models import (
    BlockInfo,
    DataValidity,
    Direction,
    GasEstimate,
    GasStatus,
    NonSendablePlan,
    QuoteMode,
    SwapQuote,
    TokenAmount,
    TokenInfo,
)
from .pancakeswap_v2 import PancakeSwapV2
from .pancakeswap_v3 import PancakeSwapV3, V3QuoteSet
from .registry import BSC_CHAIN_ID, BSC_NETWORK, DEFAULT_REGISTRY, ContractRegistry

MISSION_ID = "PHASE-2-BSC-PANCAKESWAP-INTEGRATION"
FIXED_BNB_AMOUNT_WEI = 10 ** 17  # exactly 0.1 BNB; deliberately not configurable
SAFETY_STATEMENT = {
    "mode": "READ-ONLY / DRY-RUN",
    "transactions_signed": 0,
    "transactions_broadcast": 0,
    "wallet_required": False,
    "private_key_or_seed_used": False,
    "approvals_transfers_swaps_executed": 0,
    "gas_figures_are_estimates_not_payments": True,
}


@dataclass(frozen=True)
class DirectionReport:
    direction: Direction
    fixed_size: TokenAmount
    v2_quote: Optional[SwapQuote]
    v2_error: Optional[str]
    v3_best: Optional[SwapQuote]
    v3_candidates: tuple[SwapQuote, ...]
    v3_failures: tuple[str, ...]
    v3_enabled_fee_tiers_ppm: tuple[int, ...]
    selected: Optional[SwapQuote]
    selection_rule: str
    gas: Optional[GasEstimate]
    net_output: Optional[TokenAmount]
    net_basis: str
    plan: Optional[NonSendablePlan]
    validation_errors: tuple[str, ...]
    status: str  # READY | READY_GAS_PARTIAL | READY_GAS_UNKNOWN | NOT_READY

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction.value,
            "fixed_size": self.fixed_size.to_dict(),
            "status": self.status,
            "validation_errors": list(self.validation_errors),
            "v2": {"quote": self.v2_quote.to_dict() if self.v2_quote else None, "error": self.v2_error},
            "v3": {"best": self.v3_best.to_dict() if self.v3_best else None,
                   "candidates": [q.to_dict() for q in self.v3_candidates],
                   "failures": list(self.v3_failures),
                   "enabled_fee_tiers_ppm": list(self.v3_enabled_fee_tiers_ppm)},
            "selected": self.selected.to_dict() if self.selected else None,
            "selection_rule": self.selection_rule,
            "gas": self.gas.to_dict() if self.gas else None,
            "net_output": self.net_output.to_dict() if self.net_output else None,
            "net_basis": self.net_basis,
            "plan": self.plan.to_dict() if self.plan else None,
        }


@dataclass(frozen=True)
class DryRunReport:
    generated_at: datetime
    endpoint: str
    block: Optional[BlockInfo]
    # True when the report was built from JSON-RPC answers. The report cannot tell a real
    # node from a mock transport; only the test harness knows which one it injected.
    rpc_responses_received: bool
    registry: list[dict[str, object]]
    token_issues: tuple[str, ...]
    directions: tuple[DirectionReport, ...]
    safety: dict[str, object] = field(default_factory=lambda: dict(SAFETY_STATEMENT))

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": MISSION_ID, "generated_at": self.generated_at.isoformat(),
            "network": {"name": BSC_NETWORK, "chain_id": BSC_CHAIN_ID}, "rpc_endpoint": self.endpoint,
            "block": None if self.block is None else {"number": self.block.number,
                                                      "timestamp": self.block.timestamp.isoformat()},
            "rpc_responses_received": self.rpc_responses_received, "safety": self.safety, "contract_registry": self.registry,
            "token_issues": list(self.token_issues),
            "directions": [d.to_dict() for d in self.directions],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


class DryRunPreparer:
    def __init__(self, adapter: BscAdapter, v2: Optional[PancakeSwapV2] = None, v3: Optional[PancakeSwapV3] = None,
                 gas: Optional[GasEstimator] = None, registry: ContractRegistry = DEFAULT_REGISTRY):
        self._bsc = adapter
        self._registry = registry
        self._v2 = v2 or PancakeSwapV2(adapter, registry)
        self._v3 = v3 or PancakeSwapV3(adapter, registry)
        self._gas = gas or GasEstimator(adapter)

    # ---- helpers ------------------------------------------------------------
    def _tokens(self, block: BlockInfo) -> tuple[TokenInfo, TokenInfo]:
        wbnb = require_valid(self._bsc.get_token_info(self._registry.address("wbnb"), expected_symbol="WBNB",
                                                      expected_decimals=BNB_DECIMALS, block=block.number))
        usdt = require_valid(self._bsc.get_token_info(self._registry.address("usdt"), expected_symbol="USDT",
                                                      block=block.number))
        return wbnb, usdt

    @staticmethod
    def _usdt_per_bnb(quote: SwapQuote) -> Decimal:
        spot = quote.spot_price_out_per_in
        return spot if quote.token_in.symbol == "WBNB" else Decimal(1) / spot

    @staticmethod
    def _ensure_fixed_amount(direction: Direction, amount_raw: Optional[int]) -> None:
        if amount_raw is not None and amount_raw != FIXED_BNB_AMOUNT_WEI:
            raise InvalidInputError("dynamic sizing is not allowed: the evaluation size is exactly 0.1 BNB")

    # ---- single direction ------------------------------------------------------
    def prepare_direction(self, direction: Direction, block: Optional[BlockInfo] = None,
                          amount_wei: Optional[int] = None) -> DirectionReport:
        self._ensure_fixed_amount(direction, amount_wei)
        block = block or self._bsc.snapshot_block()
        wbnb, usdt = self._tokens(block)
        fixed = TokenAmount(raw=FIXED_BNB_AMOUNT_WEI, decimals=BNB_DECIMALS, symbol="BNB")
        if direction is Direction.BNB_TO_USDT:
            token_in, token_out, mode = wbnb, usdt, QuoteMode.EXACT_IN
        else:  # USDT -> BNB: input is derived from buying exactly 0.1 BNB
            token_in, token_out, mode = usdt, wbnb, QuoteMode.EXACT_OUT

        v2_quote: Optional[SwapQuote] = None
        v2_error: Optional[str] = None
        try:
            v2_quote = self._v2.quote(direction, mode, FIXED_BNB_AMOUNT_WEI, token_in, token_out, block)
        except DexError as exc:
            v2_error = f"{type(exc).__name__}: {exc}"
        v3_set = V3QuoteSet(best=None, candidates=(), failures=(), enabled_fee_tiers_ppm=())
        try:
            v3_set = self._v3.quote(direction, mode, FIXED_BNB_AMOUNT_WEI, token_in, token_out, block)
        except DexError as exc:
            v3_set = V3QuoteSet(best=None, candidates=(), failures=(f"{type(exc).__name__}: {exc}",),
                                enabled_fee_tiers_ppm=())

        usable = [q for q in (v2_quote, v3_set.best) if q is not None and q.usable]
        selected: Optional[SwapQuote] = None
        if usable:
            selected = (max(usable, key=lambda q: q.amount_out.raw) if mode is QuoteMode.EXACT_IN
                        else min(usable, key=lambda q: q.amount_in.raw))
        rule = ("highest USDT output for exactly 0.1 BNB in" if mode is QuoteMode.EXACT_IN
                else "lowest USDT input to buy exactly 0.1 BNB") + " (quote only, before gas)"

        gas: Optional[GasEstimate] = None
        net: Optional[TokenAmount] = None
        net_basis = "no usable quote"
        if selected is not None:
            node_call = self._build_node_simulation_call(selected, direction, wbnb, usdt, block)
            gas = self._gas.estimate(
                self._usdt_per_bnb(selected),
                f"{selected.dex} pool {selected.pool_address} pre-trade mid price at block {block.number}",
                usdt, block, node_call=node_call, quoter_gas_hint=selected.gas_units_hint)
            net, net_basis = self._net(direction, selected, gas, usdt)

        errors = self._validate(direction, block, selected, v2_quote, v3_set.best, fixed)
        if selected is None or errors:
            status = "NOT_READY"
        elif gas is None or gas.status is GasStatus.UNKNOWN:
            status = "READY_GAS_UNKNOWN"
        elif gas.status is GasStatus.PARTIAL_POOL_ONLY:
            status = "READY_GAS_PARTIAL"
        else:
            status = "READY"
        plan = None if selected is None else NonSendablePlan(
            dex=selected.dex, pool_address=selected.pool_address, direction=direction, mode=mode,
            path_symbols=(token_in.symbol, token_out.symbol), amount_in=selected.amount_in,
            amount_out=selected.amount_out)
        return DirectionReport(
            direction=direction, fixed_size=fixed, v2_quote=v2_quote, v2_error=v2_error, v3_best=v3_set.best,
            v3_candidates=v3_set.candidates, v3_failures=v3_set.failures,
            v3_enabled_fee_tiers_ppm=v3_set.enabled_fee_tiers_ppm, selected=selected, selection_rule=rule,
            gas=gas, net_output=net, net_basis=net_basis, plan=plan, validation_errors=tuple(errors), status=status)

    def _build_node_simulation_call(self, quote: SwapQuote, direction: Direction,
                                    wbnb: TokenInfo, usdt: TokenInfo, block: BlockInfo) -> Optional[dict[str, str]]:
        """Build the selected Router call for eth_estimateGas; never sign or submit it."""
        sender = self._gas.simulation_sender
        if not sender:
            return None

        if quote.dex.endswith("V2"):
            if direction is Direction.BNB_TO_USDT:
                # Native BNB is wrapped by the V2 Router; sender needs 0.1 BNB plus gas.
                return build_v2_swap_exact_eth_for_tokens_call(
                    self._registry.address("v2_router"), wbnb.address, usdt.address,
                    FIXED_BNB_AMOUNT_WEI, sender, block)
            # Exact-output USDT -> WBNB; the sender needs USDT and a pre-existing
            # allowance to V2 Router. Native BNB is also needed to pay simulated gas.
            call = build_v2_swap_tokens_for_exact_eth_calldata(
                self._registry.address("v2_router"), FIXED_BNB_AMOUNT_WEI, quote.amount_in.raw,
                [usdt.address, wbnb.address], sender, block)
            call["from"] = sender
            return call

        if quote.dex.endswith("V3"):
            router = self._registry.address("v3_swap_router")
            if quote.mode is QuoteMode.EXACT_IN:
                call = build_v3_exact_input_single_calldata(
                    router, quote.token_in.address, quote.token_out.address, quote.pool_fee_ppm,
                    sender, quote.amount_in.raw, amount_out_minimum=0)
            else:
                call = build_v3_exact_output_single_calldata(
                    router, quote.token_in.address, quote.token_out.address, quote.pool_fee_ppm,
                    sender, quote.amount_out.raw, amount_in_maximum=quote.amount_in.raw)
            # V3 input is ERC-20: sender needs the input token, Router allowance,
            # and native BNB for simulated gas.
            call["from"] = sender
            return call

        return None

    @staticmethod
    def _net(direction: Direction, quote: SwapQuote, gas: GasEstimate, usdt: TokenInfo) -> tuple[Optional[TokenAmount], str]:
        if gas.cost_usdt is None or gas.status is GasStatus.UNKNOWN:
            return None, "gas unknown: net figure withheld rather than computed with an invented gas cost"
        if gas.status is GasStatus.PARTIAL_POOL_ONLY:
            return None, "gas partial: net figure withheld because pool-level gas excludes complete transaction overhead"
        if direction is Direction.BNB_TO_USDT:
            raw = quote.amount_out.raw - gas.cost_usdt.raw
            return usdt.amount(raw), "net USDT received = quoted USDT out - estimated complete gas in USDT"
        raw = quote.amount_in.raw + gas.cost_usdt.raw
        return usdt.amount(raw), "net USDT paid to obtain exactly 0.1 BNB = quoted USDT in + estimated complete gas in USDT"

    def _validate(self, direction: Direction, block: BlockInfo, selected: Optional[SwapQuote],
                  v2: Optional[SwapQuote], v3: Optional[SwapQuote], fixed: TokenAmount) -> list[str]:
        errors: list[str] = []
        if selected is None:
            errors.append("no usable (VALID) quote from PancakeSwap V2 or V3")
            return errors
        if not self._bsc.block_is_fresh(block):
            errors.append("pinned block is stale")
        if direction is Direction.BNB_TO_USDT:
            if selected.amount_in.raw != FIXED_BNB_AMOUNT_WEI or selected.token_in.symbol != "WBNB":
                errors.append("direction A input must be exactly 0.1 BNB (WBNB path)")
        else:
            if selected.amount_out.raw != FIXED_BNB_AMOUNT_WEI or selected.token_out.symbol != "WBNB":
                errors.append("direction B output must be exactly 0.1 BNB (WBNB path)")
        if selected.router_crosscheck == "MISMATCH":
            errors.append("V2 router cross-check mismatch")
        if selected.provenance.validity is not DataValidity.VALID:
            errors.append(f"selected quote validity is {selected.provenance.validity.value}")
        return errors

    # ---- both directions ------------------------------------------------------------
    def prepare_all(self) -> DryRunReport:
        health = self._bsc.health_check()
        now = self._bsc.now()
        if not health.ok:
            return DryRunReport(generated_at=now, endpoint=self._bsc.endpoint, block=None, rpc_responses_received=False,
                                registry=self._registry.to_dicts(),
                                token_issues=tuple(health.errors) or ("RPC health check failed",), directions=())
        block = self._bsc.snapshot_block()
        try:
            directions = tuple(self.prepare_direction(d, block) for d in (Direction.BNB_TO_USDT, Direction.USDT_TO_BNB))
        except DexError as exc:  # e.g. token validation failed: report it, never fake a result
            return DryRunReport(generated_at=now, endpoint=self._bsc.endpoint, block=block, rpc_responses_received=True,
                                registry=self._registry.to_dicts(),
                                token_issues=(f"{type(exc).__name__}: {exc}",), directions=())
        return DryRunReport(generated_at=now, endpoint=self._bsc.endpoint, block=block, rpc_responses_received=True,
                            registry=self._registry.to_dicts(), token_issues=(), directions=directions)
