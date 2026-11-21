"""Gas estimation for the dry-run flow.

Nothing here is a fixed or invented number. A gas figure is reported only when it
comes from the node (`eth_estimateGas` on a realistic swap call, simulated locally
with a *public* sender address) or, flagged as partial, from the V3 QuoterV2's own
pool-level estimate. Otherwise the status is UNKNOWN and no cost is computed.

An estimate is not a payment: dry-run pays nothing, and every result carries
``is_estimate_not_payment=True``.
"""
from __future__ import annotations

from datetime import datetime
from decimal import ROUND_UP, Decimal, localcontext
from typing import Optional

from . import abi
from .bsc import BNB_DECIMALS, BscAdapter
from .errors import DexError, InvalidInputError
from .models import BlockInfo, GasEstimate, GasStatus, TokenAmount, TokenInfo

ENV_SIMULATION_FROM = "BSC_GAS_SIMULATION_FROM"
_DEADLINE_WINDOW_SECONDS = 1200


def wei_to_bnb(wei: int) -> TokenAmount:
    return TokenAmount(raw=wei, decimals=BNB_DECIMALS, symbol="BNB")


def convert_bnb_cost_to_usdt(cost_wei: int, usdt_per_bnb: Decimal, usdt: TokenInfo) -> TokenAmount:
    """BNB wei -> USDT raw units, rounded UP (conservative for a cost)."""
    if usdt_per_bnb <= 0:
        raise InvalidInputError("USDT per BNB price must be positive")
    with localcontext() as ctx:
        ctx.prec = 80
        scaled = Decimal(cost_wei) / Decimal(10) ** BNB_DECIMALS * usdt_per_bnb * Decimal(10) ** usdt.decimals
        raw = int(scaled.to_integral_value(rounding=ROUND_UP))
    return usdt.amount(raw)


def build_v2_swap_exact_eth_for_tokens_call(router: str, wbnb: str, usdt: str, value_wei: int,
                                            sender: str, block: BlockInfo) -> dict[str, str]:
    """Calldata for a *local simulation only*: swapExactETHForTokens(0, [WBNB, USDT], sender, deadline).

    The dict is passed to ``eth_estimateGas``; it is never signed or sent, and
    ``amountOutMin=0`` is acceptable precisely because nothing is ever submitted.
    """
    deadline = int(block.timestamp.timestamp()) + _DEADLINE_WINDOW_SECONDS
    head = (abi.encode_uint(0) + abi.encode_uint(4 * abi.WORD) + abi.encode_address(sender) + abi.encode_uint(deadline))
    data = abi.function_selector("swapExactETHForTokens(uint256,address[],address,uint256)") + head \
        + abi.encode_address_array([wbnb, usdt])
    return {"from": abi.normalize_address(sender), "to": abi.normalize_address(router),
            "value": hex(value_wei), "data": abi.to_hex(data)}


def build_v2_swap_tokens_for_exact_eth_calldata(router: str, amount_out_wei: int, amount_in_max_wei: int,
                                                path: list[str], recipient: str, block: BlockInfo) -> dict[str, str]:
    """Build V2 exact-output calldata for local inspection/estimation only.

    The caller must still supply an allowance in any future execution context;
    this module has no approval or transaction-sending capability.
    """
    deadline = int(block.timestamp.timestamp()) + _DEADLINE_WINDOW_SECONDS
    head = (abi.encode_uint(amount_out_wei) + abi.encode_uint(amount_in_max_wei) +
            abi.encode_uint(5 * abi.WORD) + abi.encode_address(recipient) + abi.encode_uint(deadline))
    data = abi.function_selector(abi.ABI_SIGNATURES["v2_router_swap_tokens_for_exact_eth"]) + head
    return {"to": abi.normalize_address(router), "data": abi.to_hex(data + abi.encode_address_array(path))}


def _build_v3_single_calldata(signature_name: str, router: str, token_in: str, token_out: str,
                              fee_ppm: int, recipient: str, amount: int, limit: int,
                              sqrt_price_limit_x96: int = 0) -> dict[str, str]:
    words = (abi.encode_address(token_in), abi.encode_address(token_out), abi.encode_uint(fee_ppm, 24),
             abi.encode_address(recipient), abi.encode_uint(amount), abi.encode_uint(limit),
             abi.encode_uint(sqrt_price_limit_x96, 160))
    data = abi.encode_call(abi.ABI_SIGNATURES[signature_name], *words)
    return {"to": abi.normalize_address(router), "data": abi.to_hex(data)}


def build_v3_exact_input_single_calldata(router: str, token_in: str, token_out: str, fee_ppm: int,
                                         recipient: str, amount_in: int, amount_out_minimum: int = 0,
                                         sqrt_price_limit_x96: int = 0) -> dict[str, str]:
    return _build_v3_single_calldata("v3_router_exact_input_single", router, token_in, token_out,
                                     fee_ppm, recipient, amount_in, amount_out_minimum,
                                     sqrt_price_limit_x96)


def build_v3_exact_output_single_calldata(router: str, token_in: str, token_out: str, fee_ppm: int,
                                          recipient: str, amount_out: int, amount_in_maximum: int = 0,
                                          sqrt_price_limit_x96: int = 0) -> dict[str, str]:
    return _build_v3_single_calldata("v3_router_exact_output_single", router, token_in, token_out,
                                     fee_ppm, recipient, amount_out, amount_in_maximum,
                                     sqrt_price_limit_x96)


class GasEstimator:
    def __init__(self, adapter: BscAdapter, simulation_sender: Optional[str] = None):
        self._bsc = adapter
        self._sender = abi.normalize_address(simulation_sender) if simulation_sender else None

    @property
    def simulation_sender(self) -> Optional[str]:
        return self._sender

    def gas_price(self) -> tuple[int, str]:
        price = self._bsc.get_gas_price_wei()
        return price, f"{self._bsc.endpoint} :: eth_gasPrice (legacy gas price; no EIP-1559 assumption)"

    def estimate(self, usdt_per_bnb: Decimal, price_source: str, usdt: TokenInfo, block: BlockInfo,
                 node_call: Optional[dict[str, str]] = None, quoter_gas_hint: Optional[int] = None) -> GasEstimate:
        """Return a GasEstimate; status UNKNOWN when no valid estimate is available."""
        now: datetime = self._bsc.now()
        notes: list[str] = []
        try:
            gas_price, gas_price_source = self.gas_price()
        except DexError as exc:
            return GasEstimate(GasStatus.UNKNOWN, "none", None, None, None, None, None, None, now, block.number,
                               (f"gas price unavailable: {exc}",))
        units: Optional[int] = None
        status, method = GasStatus.UNKNOWN, "none"
        if node_call is not None:
            try:
                units = self._bsc.estimate_gas(node_call, block=block.number)
                status, method = GasStatus.ESTIMATED_NODE, "eth_estimateGas on the real swap call (local simulation, public sender)"
            except DexError as exc:
                notes.append(f"node gas estimate failed: {exc}")
        else:
            notes.append(f"no node simulation call available (set {ENV_SIMULATION_FROM} to a public address holding BNB "
                         "for the BNB->USDT V2 path); other paths cannot be simulated without allowances")
        if units is None and quoter_gas_hint:
            units = quoter_gas_hint
            status, method = GasStatus.PARTIAL_POOL_ONLY, "V3 QuoterV2 gasEstimate (pool-level only)"
            notes.append("quoter estimate excludes router/transfer overhead and intrinsic gas: understates real gas")
        if units is None:
            return GasEstimate(GasStatus.UNKNOWN, method, None, gas_price, None, None, None, gas_price_source,
                               now, block.number, tuple(notes))
        cost_wei = units * gas_price
        return GasEstimate(
            status=status, method=method, gas_units=units, gas_price_wei=gas_price,
            cost=wei_to_bnb(cost_wei), cost_usdt=convert_bnb_cost_to_usdt(cost_wei, usdt_per_bnb, usdt),
            price_source=price_source, gas_price_source=gas_price_source, estimated_at=now,
            block_number=block.number, notes=tuple(notes))
