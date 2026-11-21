"""Local-only deterministic KuCoin BNB/USDT Dry-Run simulator."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, DecimalException
from typing import Optional
from trading_platform.exchanges.fee_policy import FIXED_TRADING_FEE_RATE
from trading_platform.exchanges.models import BestBidAskQuote, QuoteFreshness, SymbolMetadata

ZERO=Decimal("0")
def dec(value, field):
    try: result=value if isinstance(value,Decimal) else Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError) as exc: raise ValueError(f"{field} must be a valid Decimal") from exc
    if not result.is_finite(): raise ValueError(f"{field} must be finite")
    return result

@dataclass(frozen=True)
class SimulatedBalances:
    BNB: Decimal
    USDT: Decimal

@dataclass(frozen=True)
class DryRunFillResult:
    symbol:str; side:str; quantity:Decimal; status:str; rejection_reason:Optional[str]
    balances_before:SimulatedBalances; balances_after:SimulatedBalances
    reference_price:Optional[Decimal]; gross_notional:Decimal
    fee_rate:Decimal; fee_amount:Decimal; fee_currency:str
    debit_currency:Optional[str]; debit_amount:Decimal
    credit_currency:Optional[str]; credit_amount:Decimal
    effective_unit_price:Optional[Decimal]; quote_source:Optional[str]; simulated_at:datetime
    @property
    def filled(self): return self.status=="FILLED"

class KuCoinBNBUSDTDryRunSimulator:
    """Full-fill top-of-book simulation. Fee currency USDT is a simulator assumption."""
    SYMBOL="BNB-USDT"
    def __init__(self,bnb_balance="5",usdt_balance="4000",fee_rate=FIXED_TRADING_FEE_RATE,max_quote_age_seconds="5",clock=None):
        self.starting_balances=SimulatedBalances(dec(bnb_balance,"BNB balance"),dec(usdt_balance,"USDT balance"))
        if min(self.starting_balances.BNB,self.starting_balances.USDT)<ZERO: raise ValueError("starting balances cannot be negative")
        self.fee_rate=dec(fee_rate,"fee rate")
        if self.fee_rate!=FIXED_TRADING_FEE_RATE: raise ValueError("fee rate is fixed at 0.001")
        self.max_quote_age_seconds=dec(max_quote_age_seconds,"max quote age")
        if self.max_quote_age_seconds<=ZERO: raise ValueError("max quote age must be positive")
        self._clock=clock or (lambda:datetime.now(timezone.utc))
        self.reset()
    @property
    def balances(self): return self._balances
    def reset(self):
        self._balances=self.starting_balances
        return self._balances
    def execute(self,symbol,side,quantity,metadata:SymbolMetadata,quote:BestBidAskQuote):
        before=self._balances
        sym=symbol.strip().upper() if isinstance(symbol,str) else ""
        sd=side.strip().upper() if isinstance(side,str) else ""
        try: qty=dec(quantity,"quantity"); qerr=None
        except ValueError as exc: qty=ZERO; qerr=str(exc)
        reason=None; price=None; size=None
        if sym!=self.SYMBOL: reason="unsupported symbol; expected BNB-USDT"
        elif sd not in ("BUY","SELL"): reason="side must be BUY or SELL"
        elif qerr: reason=qerr
        elif qty<=ZERO: reason="quantity must be positive"
        elif not isinstance(metadata,SymbolMetadata) or metadata.symbol.upper()!=self.SYMBOL or metadata.base_currency.upper()!="BNB" or metadata.quote_currency.upper()!="USDT": reason="matching BNB-USDT symbol metadata is required"
        elif metadata.trading_enabled is not True: reason="symbol is not enabled for trading"
        elif metadata.quantity_increment is None or metadata.quantity_increment<=ZERO: reason="valid baseIncrement metadata is required"
        elif metadata.base_min_size is not None and qty<metadata.base_min_size: reason="quantity is below baseMinSize"
        elif metadata.base_max_size is not None and qty>metadata.base_max_size: reason="quantity exceeds baseMaxSize"
        else:
            try:
                if qty%metadata.quantity_increment!=ZERO: reason="quantity does not match baseIncrement"
            except DecimalException: reason="quantity cannot be validated against baseIncrement"
        if reason is None:
            if not isinstance(quote,BestBidAskQuote): reason="a Best Bid/Ask quote is required"
            elif quote.symbol.upper()!=self.SYMBOL or quote.exchange.casefold()!="kucoin": reason="quote must be for KuCoin BNB-USDT"
            elif quote.freshness is not QuoteFreshness.FRESH: reason="quote is stale"
            elif any(not isinstance(v,Decimal) or not v.is_finite() or v<=ZERO for v in (quote.best_bid,quote.bid_quantity,quote.best_ask,quote.ask_quantity)): reason="quote prices and displayed sizes must be finite and positive"
            elif quote.best_bid>=quote.best_ask: reason="Best Bid must be lower than Best Ask"
            elif not isinstance(quote.received_at,datetime) or quote.received_at.tzinfo is None: reason="quote receipt timestamp must be timezone-aware"
            else:
                age=dec((self._clock()-quote.received_at).total_seconds(),"quote age")
                if age<ZERO or age>self.max_quote_age_seconds: reason="quote is expired or from the future"
                else:
                    price=quote.best_ask if sd=="BUY" else quote.best_bid
                    size=quote.ask_quantity if sd=="BUY" else quote.bid_quantity
                    if qty>size: reason="quantity exceeds displayed top-of-book size"
        gross=fee=debit=credit=ZERO; debit_cur=credit_cur=None; effective=None; after=before
        if reason is None:
            try:
                gross=qty*price; fee=gross*self.fee_rate
                if not gross.is_finite() or not fee.is_finite(): reason="notional or fee is not finite"
                elif metadata.min_funds is not None and gross<metadata.min_funds: reason="gross notional is below minFunds"
            except DecimalException: reason="notional or fee calculation failed"
        if reason is None and sd=="BUY":
            debit=gross+fee; debit_cur="USDT"; credit=qty; credit_cur="BNB"; effective=debit/qty
            if debit>before.USDT: reason="insufficient USDT balance including fee"
        elif reason is None and sd=="SELL":
            debit=qty; debit_cur="BNB"; credit=gross-fee; credit_cur="USDT"; effective=credit/qty
            if qty>before.BNB: reason="insufficient BNB balance"
            elif credit<ZERO: reason="net USDT credit cannot be negative"
        if reason is None:
            after=SimulatedBalances(before.BNB+qty,before.USDT-debit) if sd=="BUY" else SimulatedBalances(before.BNB-qty,before.USDT+credit)
            self._balances=after; status="FILLED"
        else:
            debit=credit=fee=gross=ZERO; debit_cur=credit_cur=None; effective=price=None; status="REJECTED"
        return DryRunFillResult(sym,sd,qty,status,reason,before,after,price,gross,self.fee_rate,fee,"USDT",debit_cur,debit,credit_cur,credit,effective,quote.source.value if price is not None else None,self._clock())
