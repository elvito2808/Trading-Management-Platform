"""PancakeSwap V3 (concentrated liquidity) read-only module.

Quotes come from the on-chain QuoterV2 (``eth_call``), which simulates the real
swap across ticks. A single-tick or spot calculation is NOT used as a tradable
price; ``slot0`` is only read to compute the pre-trade mid price used as the
reference for price impact.
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
    PoolNotFoundError,
    QuoteError,
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
from .registry import DEFAULT_REGISTRY, V3_FEE_TIER_CANDIDATES_PPM, ContractRegistry

DEX_NAME = "PancakeSwap V3"
FEE_DENOMINATOR_PPM = 1_000_000
_Q96 = 2 ** 96

_SIG_QUOTE_IN = abi.ABI_SIGNATURES["v3_quoter_exact_input_single"]
_SIG_QUOTE_OUT = abi.ABI_SIGNATURES["v3_quoter_exact_output_single"]
_SEL_SLOT0 = abi.function_selector("slot0()")
_SEL_LIQUIDITY = abi.function_selector("liquidity()")
_SEL_FEE = abi.function_selector("fee()")
_SEL_TOKEN0 = abi.function_selector("token0()")
_SEL_TOKEN1 = abi.function_selector("token1()")
_SEL_FACTORY = abi.function_selector("factory()")


# ----------------------------- pure math ---------------------------------
def sqrt_price_x96_to_price_token1_per_token0(sqrt_price_x96: int, decimals0: int, decimals1: int) -> Decimal:
    """Human price of token0 in token1 units from slot0.sqrtPriceX96."""
    ratio = Fraction(sqrt_price_x96 * sqrt_price_x96, 2 ** 192)  # raw token1 per raw token0
    with localcontext() as ctx:
        ctx.prec = 60
        return Decimal(ratio.numerator) / Decimal(ratio.denominator) * Decimal(10) ** (decimals0 - decimals1)


def _fraction(value: Decimal) -> Fraction:
    return Fraction(value)  # exact: Decimal -> Fraction is lossless


def _to_decimal(fraction: Fraction) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 60
        return Decimal(fraction.numerator) / Decimal(fraction.denominator)


@dataclass(frozen=True)
class V3PoolState:
    pool: str
    fee_ppm: int
    token0: str
    token1: str
    sqrt_price_x96: int
    tick: int
    liquidity: int
    unlocked: Optional[bool]
    block: BlockInfo

    def spot_out_per_in(self, token_in: TokenInfo, token_out: TokenInfo) -> Decimal:
        """Pre-trade mid price in human units: token_out per token_in (no fee, no impact)."""
        if abi.same_address(token_in.address, self.token0):
            d0, d1 = token_in.decimals, token_out.decimals
            return sqrt_price_x96_to_price_token1_per_token0(self.sqrt_price_x96, d0, d1)
        d0, d1 = token_out.decimals, token_in.decimals
        price_token1_per_token0 = sqrt_price_x96_to_price_token1_per_token0(self.sqrt_price_x96, d0, d1)
        with localcontext() as ctx:
            ctx.prec = 60
            return Decimal(1) / price_token1_per_token0


@dataclass(frozen=True)
class V3QuoteSet:
    best: Optional[SwapQuote]
    candidates: tuple[SwapQuote, ...]
    failures: tuple[str, ...]
    enabled_fee_tiers_ppm: tuple[int, ...]


class PancakeSwapV3:
    def __init__(self, adapter: BscAdapter, registry: ContractRegistry = DEFAULT_REGISTRY,
                 fee_tier_candidates: tuple[int, ...] = V3_FEE_TIER_CANDIDATES_PPM):
        self._bsc = adapter
        self._registry = registry
        self.factory = registry.address("v3_factory")
        self.quoter = registry.address("v3_quoter_v2")
        self._fee_candidates = fee_tier_candidates

    # ---- deployment validation -------------------------------------------
    def verify_deployment(self, block: Optional[int] = None) -> tuple[str, ...]:
        issues: list[str] = []
        for key in ("v3_factory", "v3_quoter_v2"):
            if self._bsc.get_code_size(self._registry.address(key), block) == 0:
                issues.append(f"no contract code at registry {key} {self._registry.address(key)}")
        if issues:
            return tuple(issues)
        try:
            quoter_factory = abi.word_to_address(self._bsc.eth_call(self.quoter, _SEL_FACTORY, block))
            if not abi.same_address(quoter_factory, self.factory):
                issues.append(f"quoter.factory() {quoter_factory} != registry v3_factory {self.factory}")
        except DexError as exc:  # QuoterV2 exposes factory(); if it does not, record rather than assume
            issues.append(f"quoter.factory() unavailable: {exc}")
        return tuple(issues)

    # ---- fee tiers ----------------------------------------------------------
    def enabled_fee_tiers(self, block: Optional[int] = None) -> tuple[tuple[int, ...], tuple[str, ...]]:
        """Fee tiers the factory actually has enabled (tick spacing > 0)."""
        enabled: list[int] = []
        notes: list[str] = []
        for fee in self._fee_candidates:
            data = abi.encode_call("feeAmountTickSpacing(uint24)", abi.encode_uint(fee, 24))
            try:
                spacing = abi.word_to_int(self._bsc.eth_call(self.factory, data, block), 0, signed_bits=24)
            except DexError as exc:
                notes.append(f"fee tier {fee}: probe failed ({exc})")
                continue
            if spacing > 0:
                enabled.append(fee)
        return tuple(enabled), tuple(notes)

    # ---- pools ---------------------------------------------------------------
    def find_pool(self, token_a: str, token_b: str, fee_ppm: int, block: BlockInfo) -> Optional[V3PoolState]:
        data = abi.encode_call("getPool(address,address,uint24)", abi.encode_address(token_a),
                               abi.encode_address(token_b), abi.encode_uint(fee_ppm, 24))
        pool = abi.word_to_address(self._bsc.eth_call(self.factory, data, block.number))
        if int(pool, 16) == 0:
            return None
        n = block.number
        pool_factory = abi.word_to_address(self._bsc.eth_call(pool, _SEL_FACTORY, n))
        if not abi.same_address(pool_factory, self.factory):
            raise ContractCallError(f"pool {pool} reports factory {pool_factory}, not the registry V3 factory")
        pool_fee = abi.decode_uint(self._bsc.eth_call(pool, _SEL_FEE, n))
        if pool_fee != fee_ppm:
            raise ContractCallError(f"pool {pool} fee {pool_fee} != requested tier {fee_ppm}")
        t0 = abi.word_to_address(self._bsc.eth_call(pool, _SEL_TOKEN0, n))
        t1 = abi.word_to_address(self._bsc.eth_call(pool, _SEL_TOKEN1, n))
        if {t0.lower(), t1.lower()} != {token_a.lower(), token_b.lower()}:
            raise ContractCallError(f"pool {pool} tokens do not match the requested tokens")
        slot0 = self._bsc.eth_call(pool, _SEL_SLOT0, n)
        sqrt_price = abi.word_to_int(slot0, 0)
        tick = abi.word_to_int(slot0, 1, signed_bits=24)
        unlocked = bool(abi.word_to_int(slot0, 6)) if len(slot0) >= 7 * abi.WORD else None
        liquidity = abi.decode_uint(self._bsc.eth_call(pool, _SEL_LIQUIDITY, n))
        return V3PoolState(pool=pool, fee_ppm=fee_ppm, token0=t0, token1=t1, sqrt_price_x96=sqrt_price,
                           tick=tick, liquidity=liquidity, unlocked=unlocked, block=block)

    # ---- quotes ----------------------------------------------------------------
    def _quoter_call(self, mode: QuoteMode, token_in: str, token_out: str, amount: int, fee_ppm: int,
                     block: int) -> tuple[int, int, int, int]:
        signature = _SIG_QUOTE_IN if mode is QuoteMode.EXACT_IN else _SIG_QUOTE_OUT
        data = abi.encode_call(signature, abi.encode_address(token_in), abi.encode_address(token_out),
                               abi.encode_uint(amount), abi.encode_uint(fee_ppm, 24), abi.encode_uint(0, 160))
        out = self._bsc.eth_call(self.quoter, data, block)
        return (abi.word_to_int(out, 0), abi.word_to_int(out, 1), abi.word_to_int(out, 2), abi.word_to_int(out, 3))

    def quote_pool(self, state: V3PoolState, direction: Direction, mode: QuoteMode, amount_raw: int,
                   token_in: TokenInfo, token_out: TokenInfo, deployment_issues: tuple[str, ...] = ()) -> SwapQuote:
        block = state.block
        if state.liquidity <= 0:
            raise InsufficientLiquidityError(f"pool {state.pool} has zero in-range liquidity")
        issues = list(deployment_issues)
        if state.unlocked is False:
            issues.append("pool slot0.unlocked is false (re-entrancy lock engaged)")
        result, sqrt_after, ticks_crossed, gas_hint = self._quoter_call(
            mode, token_in.address, token_out.address, amount_raw, state.fee_ppm, block.number)
        if result <= 0:
            raise QuoteError(f"QuoterV2 returned zero for pool {state.pool}")
        if mode is QuoteMode.EXACT_IN:
            amount_in, amount_out = amount_raw, result
        else:
            amount_in, amount_out = result, amount_raw

        spot = state.spot_out_per_in(token_in, token_out)
        fee_fraction = Fraction(state.fee_ppm, FEE_DENOMINATOR_PPM)
        in_amt, out_amt = token_in.amount(amount_in), token_out.amount(amount_out)
        if mode is QuoteMode.EXACT_IN:
            ideal_out = _fraction(in_amt.amount) * (1 - fee_fraction) * _fraction(spot)
            impact = _to_decimal(1 - _fraction(out_amt.amount) / ideal_out)
        else:
            ideal_in = _fraction(out_amt.amount) / _fraction(spot)
            impact = _to_decimal(_fraction(in_amt.amount) * (1 - fee_fraction) / ideal_in - 1)

        if not self._bsc.block_is_fresh(block):
            issues.append(f"block {block.number} is {self._bsc.block_age_seconds(block)}s old "
                          f"(limit {self._bsc.config.max_block_age_seconds}s)")
            validity = DataValidity.STALE
        else:
            validity = DataValidity.VALID
        if deployment_issues:
            validity = DataValidity.INVALID
        if impact < Decimal("-0.000001"):
            issues.append("negative price impact: quote better than mid price; data inconsistent")
            validity = DataValidity.INVALID
        if impact > Decimal("0.05"):
            issues.append(f"price impact {(impact * 100).quantize(Decimal('0.0001'))}% exceeds 5%")

        fee = TokenAmount(raw=-(-amount_in * state.fee_ppm // FEE_DENOMINATOR_PPM),
                          decimals=token_in.decimals, symbol=token_in.symbol)
        exec_price = out_amt.amount / in_amt.amount
        return SwapQuote(
            dex=DEX_NAME, pool_address=state.pool, pool_fee_ppm=state.fee_ppm, direction=direction, mode=mode,
            token_in=token_in, token_out=token_out, amount_in=in_amt, amount_out=out_amt, protocol_fee=fee,
            spot_price_out_per_in=spot, execution_price_out_per_in=exec_price, price_impact=impact,
            liquidity_note=(f"in-range liquidity={state.liquidity}, current tick={state.tick}, "
                            f"initialized ticks crossed by swap={ticks_crossed}, sqrtPriceX96 after={sqrt_after}"),
            provenance=DataProvenance(
                source=f"{self._bsc.endpoint} :: PancakeSwap V3 QuoterV2 {self.quoter} via eth_call, pool {state.pool}",
                fetched_at=self._bsc.now(), block_number=block.number, block_timestamp=block.timestamp,
                validity=validity, issues=tuple(issues)),
            router_crosscheck="NOT_APPLICABLE",
            gas_units_hint=gas_hint if gas_hint > 0 else None,
            extra={"initialized_ticks_crossed": ticks_crossed, "sqrt_price_x96_after": str(sqrt_after),
                   "fee_tier_ppm": state.fee_ppm, "fee_rounding": "protocol fee rounded up to raw unit"},
        )

    def quote(self, direction: Direction, mode: QuoteMode, amount_raw: int, token_in: TokenInfo,
              token_out: TokenInfo, block: Optional[BlockInfo] = None) -> V3QuoteSet:
        """Quote every enabled fee tier that has a pool; select the best usable result."""
        block = block or self._bsc.snapshot_block()
        deployment_issues = self.verify_deployment(block.number)
        tiers, tier_notes = self.enabled_fee_tiers(block.number)
        failures: list[str] = list(tier_notes)
        candidates: list[SwapQuote] = []
        for fee in tiers:
            try:
                state = self.find_pool(token_in.address, token_out.address, fee, block)
                if state is None:
                    failures.append(f"fee tier {fee}: no pool")
                    continue
                candidates.append(self.quote_pool(state, direction, mode, amount_raw, token_in, token_out,
                                                  deployment_issues))
            except DexError as exc:
                failures.append(f"fee tier {fee}: {type(exc).__name__}: {exc}")
        usable = [q for q in candidates if q.usable]
        if not usable:
            best = None
        elif mode is QuoteMode.EXACT_IN:
            best = max(usable, key=lambda q: q.amount_out.raw)
        else:
            best = min(usable, key=lambda q: q.amount_in.raw)
        if not candidates and not failures:
            failures.append("no enabled fee tiers")
        if best is None and not candidates:
            raise PoolNotFoundError("PancakeSwap V3: no quotable pool; " + "; ".join(failures))
        return V3QuoteSet(best=best, candidates=tuple(candidates), failures=tuple(failures),
                          enabled_fee_tiers_ppm=tiers)
