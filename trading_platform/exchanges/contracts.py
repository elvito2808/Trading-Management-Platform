"""Provider-neutral exchange and market-data contracts."""
from typing import Protocol, Sequence

from .models import (
    Balance,
    BestBidAskQuote,
    DryRunOrder,
    FixedTradeSize,
    MarketDataConnectionStatus,
    SymbolMetadata,
)


class ExchangeAdapter(Protocol):
    def get_best_bid_ask(self, symbol: str) -> BestBidAskQuote: ...
    def get_symbols(self) -> Sequence[SymbolMetadata]: ...
    def get_symbol(self, symbol: str) -> SymbolMetadata: ...
    def get_balances(self, currency: str | None = None) -> Sequence[Balance]: ...
    def prepare_configured_dry_run_order(
        self,
        trade_size: FixedTradeSize,
        side: str,
        quote: BestBidAskQuote,
        order_type: str = "market",
    ) -> DryRunOrder: ...


class BestBidAskProvider(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def get_quote(self, symbol: str) -> BestBidAskQuote: ...

    @property
    def connection_status(self) -> MarketDataConnectionStatus: ...
