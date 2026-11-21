"""Centrally configured, local-only fee assumptions."""
from dataclasses import dataclass
from decimal import Decimal


FIXED_TRADING_FEE_RATE = Decimal("0.001")


@dataclass(frozen=True)
class FixedFeePolicy:
    rate: Decimal = FIXED_TRADING_FEE_RATE

    def __post_init__(self) -> None:
        if not isinstance(self.rate, Decimal) or self.rate != FIXED_TRADING_FEE_RATE:
            raise ValueError("fee rate is fixed at Decimal('0.001')")

    def estimate(self, notional: Decimal) -> Decimal:
        if not isinstance(notional, Decimal) or not notional.is_finite() or notional < 0:
            raise ValueError("notional must be finite and non-negative")
        fee = notional * self.rate
        if not fee.is_finite():
            raise ValueError("fee estimate must be finite")
        return fee
