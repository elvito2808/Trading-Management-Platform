"""Typed, Decimal-based models shared by exchange adapters."""
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from enum import Enum
from typing import Optional


class QuoteSource(str, Enum):
    WEBSOCKET = "WEBSOCKET"
    REST = "REST"


class QuoteFreshness(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"


@dataclass(frozen=True)
class SymbolMetadata:
    exchange: str
    symbol: str
    base_currency: str
    quote_currency: str
    trading_enabled: Optional[bool] = None
    base_min_size: Optional[Decimal] = None
    base_max_size: Optional[Decimal] = None
    quote_min_size: Optional[Decimal] = None
    min_funds: Optional[Decimal] = None
    price_increment: Optional[Decimal] = None
    quantity_increment: Optional[Decimal] = None
    base_precision: Optional[int] = None
    quote_precision: Optional[int] = None


@dataclass(frozen=True)
class BestBidAskQuote:
    exchange: str
    symbol: str
    best_bid: Decimal
    bid_quantity: Decimal
    best_ask: Decimal
    ask_quantity: Decimal
    source: QuoteSource
    event_timestamp: Optional[datetime]
    received_at: datetime
    freshness: QuoteFreshness = QuoteFreshness.FRESH

    @property
    def spread(self) -> Decimal:
        return self.best_ask - self.best_bid

    def execution_price(self, side: str) -> Decimal:
        if not isinstance(side, str):
            raise ValueError("side must be buy or sell")
        normalized_side = side.lower()
        if normalized_side == "buy":
            return self.best_ask
        if normalized_side == "sell":
            return self.best_bid
        raise ValueError("side must be buy or sell")


@dataclass(frozen=True)
class Balance:
    currency: str
    total: Decimal
    available: Decimal
    hold: Decimal
    account_type: str
    retrieved_at: datetime


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
    estimated_notional: Optional[Decimal]
    fee_rate: Decimal
    estimated_fee: Optional[Decimal]
    fee_status: str
    fee_assumption: str
    quote_source: Optional[QuoteSource]
    quote_received_at: Optional[datetime]
    quote_event_timestamp: Optional[datetime]
    validation: ValidationResult
    dry_run: bool
    prepared_at: datetime


@dataclass(frozen=True)
class FixedTradeSize:
    symbol: str
    quantity: Decimal


@dataclass(frozen=True)
class MarketDataConnectionStatus:
    websocket_connected: bool
    connecting: bool
    reconnecting: bool
    subscribed_symbols: tuple[str, ...]
    last_message_received_at: Optional[datetime] = None
    last_error: Optional[str] = None
