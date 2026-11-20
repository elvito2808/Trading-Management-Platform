"""Typed, Decimal-based models shared by exchange adapters."""
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Ticker:
    exchange: str
    symbol: str
    price: Optional[Decimal]
    bid: Optional[Decimal]
    ask: Optional[Decimal]
    timestamp: datetime
    high_24h: Optional[Decimal] = None
    low_24h: Optional[Decimal] = None
    change_24h: Optional[Decimal] = None
    volume_24h: Optional[Decimal] = None

    @property
    def spread(self) -> Optional[Decimal]:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid


@dataclass(frozen=True)
class SymbolMetadata:
    exchange: str
    symbol: str
    base_currency: str
    quote_currency: str
    base_min_size: Optional[Decimal] = None
    base_max_size: Optional[Decimal] = None
    quote_min_size: Optional[Decimal] = None
    price_increment: Optional[Decimal] = None
    quantity_increment: Optional[Decimal] = None
    base_precision: Optional[int] = None
    quote_precision: Optional[int] = None
    trading_status: str = "unknown"


@dataclass(frozen=True)
class OrderBookLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class OrderBook:
    exchange: str
    symbol: str
    timestamp: datetime
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    sequence: Optional[int] = None
    snapshot: bool = True


@dataclass(frozen=True)
class Balance:
    currency: str
    total: Optional[Decimal]
    available: Decimal
    hold: Decimal
    account_type: str
    retrieved_at: datetime


@dataclass(frozen=True)
class FeeFallback:
    exchange: str
    symbol: Optional[str]
    maker_rate: Decimal
    taker_rate: Decimal
    source: str
    verified: bool


@dataclass(frozen=True)
class FeeInfo:
    exchange: str
    symbol: Optional[str]
    maker_rate: Optional[Decimal]
    taker_rate: Optional[Decimal]
    source: str
    status: str
    retrieved_at: datetime
    warning: Optional[str] = None


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class DryRunOrder:
    exchange: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    reference_price: Decimal
    estimated_notional: Decimal
    estimated_maker_fee: Optional[Decimal]
    estimated_taker_fee: Optional[Decimal]
    validation: ValidationResult
    dry_run: bool
    prepared_at: datetime
