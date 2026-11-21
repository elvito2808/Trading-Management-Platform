"""PancakeSwap V2 (constant-product) read-only module.

Prices use the real V2 swap formula including the 0.25% protocol fee, NOT the
simple spot price. The local result is cross-checked against the on-chain Router
(`getAmountsOut` / `getAmountsIn`); a disagreement invalidates the quote.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Optional

from . import abi
from .bsc import BscAdapter
from .errors import (
    ContractCallError,
    DexError,
    InsufficientLiquidityError,
    InvalidInputError,
    PoolNotFoundError,
)
from .models import (
    BlockInfo,
    DataProvenance,
    DataValidity,
    Direction,
    QuoteMode,
    SwapQuote,
    TokenAmount,
    TokenInfo,
)
from .registry import DEFAULT_REGISTRY, ContractRegistry

DEX_NAME = "PancakeSwap V2"
FEE_BPS = 25                 # 0.25% total swap fee on PancakeSwap V2 (BSC)
BPS_DENOMINATOR = 10_000
POOL_FEE_PPM = FEE_BPS * 100  # 2500 ppm == 0.25%

_SEL_GET_PAIR = abi.function_selector("getPair(address,address)")
_SEL_RESERVES = abi.function_selector("getReserves()")
_SEL_TOKEN0 = abi.function_selector("token0()")
_SEL_TOKEN1 = abi.function_selector("token1()")
_SEL_FACTORY = abi.function_selector("factory()")
_SEL_WETH = abi.function_selector("WETH()")


# ----------------------------- pure math ---------------------------------
def get_amount_out(amount_in: int, reserve_in: int, reserve_out: int) -> int:
    """Uniswap-V2 formula with PancakeSwap's 0.25% fee, integer arithmetic."""
    if amount_in <= 0:
        raise InvalidInputError("amount_in must be positive")
    if reserve_in <= 0 or reserve_out <= 0:
        raise InsufficientLiquidityError("pool has an empty reserve")
    amount_in_with_fee = amount_in * (BPS_DENOMINATOR - FEE_BPS)
    return (amount_in_with_fee * reserve_out) // (reserve_in * BPS_DENOMINATOR + amount_in_with_fee)


def get_amount_in(amount_out: int, reserve_in: int, reserve_out: int) -> int:
    """Input needed to receive exactly ``amount_out`` (rounded up, like the Router)."""
    if amount_out <= 0:
        raise InvalidInputError("amount_out must be positive")
    if reserve_in <= 0 or reserve_out <= 0:
        raise InsufficientLiquidityError("pool has an empty reserve")
    if amount_out >= reserve_out:
        raise InsufficientLiquidityError("requested output is not smaller than the output reserve")
    numerator = reserve_in * amount_out * BPS_DENOMINATOR
    denominator = (reserve_out - amount_out) * (BPS_DENOMINATOR - FEE_BPS)
    return numerator // denominator + 1


def _to_decimal(fraction: Fraction) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 60
        return Decimal(fraction.numerator) / Decimal(fraction.denominator)


def price_impact_exact_in(amount_in: int, amount_out: int, reserve_in: int, reserve_out: int) -> Decimal:
    """1 - actual_out / ideal_out, where ideal_out is the fee-adjusted spot output (fee excluded from impact)."""
    ideal = Fraction(amount_in * (BPS_DENOMINATOR - FEE_BPS) * reserve_out, BPS_DENOMINATOR * reserve_in)
    return _to_decimal(1 - Fraction(amount_out) / ideal)


def price_impact_exact_out(amount_in: int, amount_out: int, reserve_in: int, reserve_out: int) -> Decimal:
    """actual_in_after_fee / ideal_in - 1 for an exact-output trade."""
    ideal_in = Fraction(amount_out * reserve_in, reserve_out)
    actual_in = Fraction(amount_in * (BPS_DENOMINATOR - FEE_BPS), BPS_DENOMINATOR)
    return _to_decimal(actual_in / ideal_in - 1)


def spot_price_out_per_in(reserve_in: int, reserve_out: int, decimals_in: int, decimals_out: int) -> Decimal:
    """Pool mid price in human units (token_out per token_in); no fee, no impact."""
    with localcontext() as ctx:
        ctx.prec = 60
        return (Decimal(reserve_out) / Decimal(10) ** decimals_out) / (Decimal(reserve_in) / Decimal(10) ** decimals_in)


# ----------------------------- on-chain state -----------------------------
@dataclass(frozen=True)
class V2PoolState:
    pair: str
    token0: str
    token1: str
    reserve0: int
    reserve1: int
    reserve_timestamp_last: int
    block: BlockInfo

    def reserve_of(self, token_address: str) -> int:
        if abi.same_address(token_address, self.token0):
            return self.reserve0
        if abi.same_address(token_address, self.token1):
            return self.reserve1
        raise InvalidInputError("token is not part of this pair")


class PancakeSwapV2:
    def __init__(self, adapter: BscAdapter, registry: ContractRegistry = DEFAULT_REGISTRY):
        self._bsc = adapter
        self._registry = registry
        self.factory = registry.address("v2_factory")
        self.router = registry.address("v2_router")
        self._deployment_cache: dict[int, tuple[str, ...]] = {}

    # ---- registry validation ---------------------------------------------
    def verify_deployment(self, block: Optional[int] = None) -> tuple[str, ...]:
        """On-chain cross-checks of the registry entries this module relies on."""
        issues: list[str] = []
        for key in ("v2_factory", "v2_router"):
            if self._bsc.get_code_size(self._registry.address(key), block) == 0:
                issues.append(f"no contract code at registry {key} {self._registry.address(key)}")
        if issues:
            return tuple(issues)
        router_factory = abi.word_to_address(self._bsc.eth_call(self.router, _SEL_FACTORY, block))
        if not abi.same_address(router_factory, self.factory):
            issues.append(f"router.factory() {router_factory} != registry v2_factory {self.factory}")
        router_weth = abi.word_to_address(self._bsc.eth_call(self.router, _SEL_WETH, block))
        wbnb = self._registry.address("wbnb")
        if not abi.same_address(router_weth, wbnb):
            issues.append(f"router.WETH() {router_weth} != registry wbnb {wbnb}")
        return tuple(issues)

    # ---- pair discovery ----------------------------------------------------
    def resolve_pair(self, token_a: str, token_b: str, block: Optional[int] = None) -> str:
        data = abi.encode_call("getPair(address,address)", abi.encode_address(token_a), abi.encode_address(token_b))
        pair = abi.word_to_address(self._bsc.eth_call(self.factory, data, block))
        if pair == abi.ZERO_ADDRESS or int(pair, 16) == 0:
            raise PoolNotFoundError(f"PancakeSwap V2 has no pair for {token_a} / {token_b}")
        # validate that the pair really belongs to this factory and holds exactly these tokens
        pair_factory = abi.word_to_address(self._bsc.eth_call(pair, _SEL_FACTORY, block))
        if not abi.same_address(pair_factory, self.factory):
            raise ContractCallError(f"pair {pair} reports factory {pair_factory}, not the registry V2 factory")
        t0 = abi.word_to_address(self._bsc.eth_call(pair, _SEL_TOKEN0, block))
        t1 = abi.word_to_address(self._bsc.eth_call(pair, _SEL_TOKEN1, block))
        if {t0.lower(), t1.lower()} != {token_a.lower(), token_b.lower()}:
            raise ContractCallError(f"pair {pair} tokens ({t0}, {t1}) do not match the requested tokens")
        return pair

    def get_pool_state(self, pair: str, block: BlockInfo) -> V2PoolState:
        t0 = abi.word_to_address(self._bsc.eth_call(pair, _SEL_TOKEN0, block.number))
        t1 = abi.word_to_address(self._bsc.eth_call(pair, _SEL_TOKEN1, block.number))
        raw = self._bsc.eth_call(pair, _SEL_RESERVES, block.number)
        r0, r1, ts = abi.word_to_int(raw, 0), abi.word_to_int(raw, 1), abi.word_to_int(raw, 2)
        return V2PoolState(pair=pair, token0=t0, token1=t1, reserve0=r0, reserve1=r1,
                           reserve_timestamp_last=ts, block=block)

    # ---- router cross-check -----------------------------------------------
    def _router_amounts(self, signature: str, amount: int, path: list[str], block: int) -> list[int]:
        data = abi.encode_call_uint_address_array(signature, amount, path)
        return abi.decode_uint_array(self._bsc.eth_call(self.router, data, block))

    # ---- quotes ----------------------------------------------------------------
    def quote(self, direction: Direction, mode: QuoteMode, amount_raw: int,
              token_in: TokenInfo, token_out: TokenInfo, block: Optional[BlockInfo] = None) -> SwapQuote:
        """Quote one fixed amount. EXACT_IN: ``amount_raw`` is the input; EXACT_OUT: the output."""
        block = block or self._bsc.snapshot_block()
        deployment_issues = self._bsc_issues(block)
        issues: list[str] = list(deployment_issues)
        pair = self.resolve_pair(token_in.address, token_out.address, block.number)
        state = self.get_pool_state(pair, block)
        reserve_in, reserve_out = state.reserve_of(token_in.address), state.reserve_of(token_out.address)
        if reserve_in <= 0 or reserve_out <= 0:
            raise InsufficientLiquidityError(f"pair {pair} has an empty reserve")

        if mode is QuoteMode.EXACT_IN:
            amount_in, amount_out = amount_raw, get_amount_out(amount_raw, reserve_in, reserve_out)
            if amount_out <= 0:
                raise InsufficientLiquidityError("quoted output is zero")
            impact = price_impact_exact_in(amount_in, amount_out, reserve_in, reserve_out)
            router_signature, router_index, router_expected = "getAmountsOut(uint256,address[])", 1, amount_out
        else:
            amount_out, amount_in = amount_raw, get_amount_in(amount_raw, reserve_in, reserve_out)
            impact = price_impact_exact_out(amount_in, amount_out, reserve_in, reserve_out)
            router_signature, router_index, router_expected = "getAmountsIn(uint256,address[])", 0, amount_in

        crosscheck = self._crosscheck(router_signature, amount_raw, router_index, router_expected,
                                      [token_in.address, token_out.address], block.number, issues)
        if not self._bsc.block_is_fresh(block):
            issues.append(f"block {block.number} is {self._bsc.block_age_seconds(block)}s old "
                          f"(limit {self._bsc.config.max_block_age_seconds}s)")
            validity = DataValidity.STALE
        else:
            validity = DataValidity.VALID
        if crosscheck == "MISMATCH" or deployment_issues:
            validity = DataValidity.INVALID
        if impact > Decimal("0.05"):
            issues.append(f"price impact {(impact * 100).quantize(Decimal('0.0001'))}% exceeds 5%")
        spot = spot_price_out_per_in(reserve_in, reserve_out, token_in.decimals, token_out.decimals)
        in_amt, out_amt = token_in.amount(amount_in), token_out.amount(amount_out)
        exec_price = out_amt.amount / in_amt.amount
        fee = TokenAmount(raw=amount_in * FEE_BPS // BPS_DENOMINATOR, decimals=token_in.decimals, symbol=token_in.symbol)
        share = Decimal(amount_in) * 100 / Decimal(reserve_in)
        return SwapQuote(
            dex=DEX_NAME, pool_address=pair, pool_fee_ppm=POOL_FEE_PPM, direction=direction, mode=mode,
            token_in=token_in, token_out=token_out, amount_in=in_amt, amount_out=out_amt, protocol_fee=fee,
            spot_price_out_per_in=spot, execution_price_out_per_in=exec_price, price_impact=impact,
            liquidity_note=(f"reserve_in={token_in.amount(reserve_in)}, reserve_out={token_out.amount(reserve_out)}, "
                            f"trade is {share.quantize(Decimal('0.0001'))}% of input reserve; "
                            f"reserves last changed at unix {state.reserve_timestamp_last}"),
            provenance=DataProvenance(
                source=f"{self._bsc.endpoint} :: PancakeSwap V2 pair {pair} getReserves + local formula",
                fetched_at=self._bsc.now(), block_number=block.number, block_timestamp=block.timestamp,
                validity=validity, issues=tuple(issues)),
            router_crosscheck=crosscheck,
            extra={"fee_bps": FEE_BPS, "formula": "amountIn*9975*reserveOut/(reserveIn*10000+amountIn*9975)",
                   "protocol_fee_is_floored_raw_units": True},
        )

    def _crosscheck(self, signature: str, amount: int, index: int, expected: int, path: list[str],
                    block: int, issues: list[str]) -> str:
        try:
            amounts = self._router_amounts(signature, amount, path, block)
        except DexError as exc:
            issues.append(f"router cross-check unavailable: {exc}")
            return "UNAVAILABLE"
        if len(amounts) != 2 or amounts[index] != expected:
            issues.append(f"router cross-check MISMATCH: router={amounts}, local={expected}")
            return "MISMATCH"
        return "MATCH"

    def _bsc_issues(self, block: BlockInfo) -> tuple[str, ...]:
        if block.number not in self._deployment_cache:
            self._deployment_cache[block.number] = self.verify_deployment(block.number)
        return self._deployment_cache[block.number]
