"""Typed models for the BSC / PancakeSwap read-only integration.

Rules enforced here:
* contract-level quantities are integers in the token's smallest unit (``*_raw`` / ``*_wei``);
* human quantities are ``Decimal`` and always travel with a unit symbol (``TokenAmount``);
* nothing is ever converted through ``float``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, localcontext
from enum import Enum
from typing import Any, Optional


class DataValidity(str, Enum):
    VALID = "VALID"
    STALE = "STALE"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class Direction(str, Enum):
    BNB_TO_USDT = "BNB_TO_USDT"  # Direction A: sell exactly 0.1 BNB
    USDT_TO_BNB = "USDT_TO_BNB"  # Direction B: buy exactly 0.1 BNB


class QuoteMode(str, Enum):
    EXACT_IN = "EXACT_IN"
    EXACT_OUT = "EXACT_OUT"


class GasStatus(str, Enum):
    ESTIMATED_NODE = "ESTIMATED_NODE"        # eth_estimateGas on the real swap call
    PARTIAL_POOL_ONLY = "PARTIAL_POOL_ONLY"  # V3 quoter pool-level estimate; excludes router overhead
    UNKNOWN = "UNKNOWN"                      # no valid estimate available


def raw_to_decimal(raw: int, decimals: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 80
        return Decimal(raw) / (Decimal(10) ** decimals)


def decimal_to_raw(value: Decimal, decimals: int) -> int:
    """Exact conversion; refuses to round silently."""
    with localcontext() as ctx:
        ctx.prec = 80
        scaled = value * (Decimal(10) ** decimals)
        if scaled != scaled.to_integral_value():
            raise ValueError("amount has more precision than the token supports")
        return int(scaled)


@dataclass(frozen=True)
class TokenAmount:
    """An integer amount in smallest units plus the information to read it."""
    raw: int
    decimals: int
    symbol: str

    @property
    def amount(self) -> Decimal:
        return raw_to_decimal(self.raw, self.decimals)

    def __str__(self) -> str:
        return f"{self.amount.normalize():f} {self.symbol}"

    def to_dict(self) -> dict[str, Any]:
        return {"raw": str(self.raw), "decimals": self.decimals, "symbol": self.symbol,
                "amount": format(self.amount.normalize(), "f")}


@dataclass(frozen=True)
class DataProvenance:
    """Where a piece of data came from, when, and whether it can be trusted."""
    source: str                       # redacted RPC description + contract/method
    fetched_at: datetime
    block_number: Optional[int]
    block_timestamp: Optional[datetime]
    validity: DataValidity
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "fetched_at": self.fetched_at.isoformat(),
            "block_number": self.block_number,
            "block_timestamp": self.block_timestamp.isoformat() if self.block_timestamp else None,
            "validity": self.validity.value,
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class BlockInfo:
    number: int
    timestamp: datetime
    block_hash: Optional[str]
    gas_used: Optional[int]
    gas_limit: Optional[int]
    base_fee_per_gas_wei: Optional[int]


@dataclass(frozen=True)
class RpcHealth:
    ok: bool
    chain_id: Optional[int]
    block_number: Optional[int]
    block_age_seconds: Optional[Decimal]
    latency_ms: Optional[Decimal]
    errors: tuple[str, ...]
    provenance: DataProvenance


@dataclass(frozen=True)
class TokenInfo:
    address: str
    symbol: str
    name: Optional[str]
    decimals: int
    total_supply_raw: Optional[int]
    has_code: bool
    provenance: DataProvenance

    def amount(self, raw: int) -> TokenAmount:
        return TokenAmount(raw=raw, decimals=self.decimals, symbol=self.symbol)

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address, "symbol": self.symbol, "name": self.name,
            "decimals": self.decimals,
            "total_supply_raw": None if self.total_supply_raw is None else str(self.total_supply_raw),
            "has_code": self.has_code, "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class SwapQuote:
    """A single venue/pool quote for the fixed-size evaluation."""
    dex: str                          # "PancakeSwap V2" | "PancakeSwap V3"
    pool_address: str
    pool_fee_ppm: int                 # parts per million (V2: 2500 = 0.25%)
    direction: Direction
    mode: QuoteMode
    token_in: TokenInfo
    token_out: TokenInfo
    amount_in: TokenAmount
    amount_out: TokenAmount
    protocol_fee: TokenAmount         # charged in the input token
    spot_price_out_per_in: Decimal    # pre-trade pool mid price (no fee, no impact)
    execution_price_out_per_in: Decimal
    price_impact: Decimal             # fraction (0.01 == 1%), excludes the protocol fee
    liquidity_note: str
    provenance: DataProvenance
    router_crosscheck: str            # MATCH | MISMATCH | UNAVAILABLE | NOT_APPLICABLE
    gas_units_hint: Optional[int] = None   # V3 quoter pool-level estimate, if provided
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.provenance.validity is DataValidity.VALID

    def to_dict(self) -> dict[str, Any]:
        return {
            "dex": self.dex, "pool_address": self.pool_address,
            "pool_fee_percent": format((Decimal(self.pool_fee_ppm) / Decimal(10_000)).normalize(), "f"),
            "direction": self.direction.value, "mode": self.mode.value,
            "token_in": self.token_in.symbol, "token_out": self.token_out.symbol,
            "amount_in": self.amount_in.to_dict(), "amount_out": self.amount_out.to_dict(),
            "protocol_fee": self.protocol_fee.to_dict(),
            "spot_price": {"value": format(self.spot_price_out_per_in, "f"),
                           "unit": f"{self.token_out.symbol}/{self.token_in.symbol}"},
            "execution_price": {"value": format(self.execution_price_out_per_in, "f"),
                                "unit": f"{self.token_out.symbol}/{self.token_in.symbol}"},
            "price_impact_percent": format((self.price_impact * 100), "f"),
            "liquidity_note": self.liquidity_note,
            "router_crosscheck": self.router_crosscheck,
            "gas_units_hint": self.gas_units_hint,
            "provenance": self.provenance.to_dict(),
            "extra": self.extra,
        }


@dataclass(frozen=True)
class GasEstimate:
    status: GasStatus
    method: str
    gas_units: Optional[int]
    gas_price_wei: Optional[int]
    cost: Optional[TokenAmount]            # in BNB
    cost_usdt: Optional[TokenAmount]       # converted with a recorded, valid price
    price_source: Optional[str]            # what BNB/USDT price was used for conversion
    gas_price_source: Optional[str]
    estimated_at: datetime
    block_number: Optional[int]
    notes: tuple[str, ...] = ()
    # An estimate is never an amount actually paid. A dry-run pays nothing.
    is_estimate_not_payment: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value, "method": self.method,
            "gas_units": self.gas_units, "gas_price_wei": None if self.gas_price_wei is None else str(self.gas_price_wei),
            "estimated_cost_bnb": self.cost.to_dict() if self.cost else None,
            "estimated_cost_usdt": self.cost_usdt.to_dict() if self.cost_usdt else None,
            "usdt_conversion_price_source": self.price_source,
            "gas_price_source": self.gas_price_source,
            "estimated_at": self.estimated_at.isoformat(), "block_number": self.block_number,
            "notes": list(self.notes),
            "is_estimate_not_payment": self.is_estimate_not_payment,
        }


@dataclass(frozen=True)
class NonSendablePlan:
    """Local description of a swap. It has no sender, nonce, signature or calldata
    that could be broadcast, and the type offers no way to sign or send it."""
    dex: str
    pool_address: str
    direction: Direction
    mode: QuoteMode
    path_symbols: tuple[str, ...]
    amount_in: TokenAmount
    amount_out: TokenAmount
    sendable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"dex": self.dex, "pool_address": self.pool_address, "direction": self.direction.value,
                "mode": self.mode.value, "path": list(self.path_symbols),
                "amount_in": self.amount_in.to_dict(), "amount_out": self.amount_out.to_dict(),
                "sendable": self.sendable}
