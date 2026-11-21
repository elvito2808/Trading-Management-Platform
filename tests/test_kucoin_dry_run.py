from datetime import datetime,timedelta,timezone
from decimal import Decimal
import pytest
from trading_platform.exchanges.models import BestBidAskQuote,QuoteFreshness,QuoteSource,SymbolMetadata
from trading_platform.execution.kucoin_dry_run import KuCoinBNBUSDTDryRunSimulator
NOW=datetime(2026,11,21,tzinfo=timezone.utc)
META=SymbolMetadata("kucoin","BNB-USDT","BNB","USDT",True,Decimal(".001"),Decimal("100"),min_funds=Decimal("10"),quantity_increment=Decimal(".001"))
QUOTE=BestBidAskQuote("kucoin","BNB-USDT",Decimal("600"),Decimal("2"),Decimal("601"),Decimal("2"),QuoteSource.REST,None,NOW)
def sim(): return KuCoinBNBUSDTDryRunSimulator(clock=lambda:NOW)
def test_buy_arithmetic_and_reconciliation():
 s=sim(); r=s.execute("BNB-USDT","BUY",".1",META,QUOTE)
 assert (r.status,r.reference_price,r.gross_notional,r.fee_amount,r.fee_currency)==("FILLED",Decimal("601"),Decimal("60.1"),Decimal(".0601"),"USDT")
 assert r.debit_amount==Decimal("60.1601") and r.effective_unit_price==Decimal("601.601")
 assert r.balances_after==s.balances and s.balances==type(s.balances)(Decimal("5.1"),Decimal("3939.8399"))
def test_sell_arithmetic_and_reconciliation():
 s=sim(); r=s.execute("BNB-USDT","SELL",".1",META,QUOTE)
 assert r.status=="FILLED" and r.reference_price==Decimal("600")
 assert r.fee_amount==Decimal(".0600") and r.credit_amount==Decimal("59.9400")
 assert r.effective_unit_price==Decimal("599.400")
 assert s.balances==type(s.balances)(Decimal("4.9"),Decimal("4059.9400"))
@pytest.mark.parametrize("side,qty,reason",[("BUY","100","insufficient USDT"),("SELL","5.001","insufficient BNB")])
def test_insufficient_balance_no_mutation(side,qty,reason):
 s=sim(); before=s.balances; r=s.execute("BNB-USDT",side,qty,META,QUOTE)
 assert r.status=="REJECTED" and reason in r.rejection_reason and r.balances_after==before and s.balances==before
@pytest.mark.parametrize("symbol,side,qty",[("BTC-USDT","BUY",".1"),("BNB-USDT","HOLD",".1"),("BNB-USDT","BUY","0"),("BNB-USDT","BUY","-.1"),("BNB-USDT","BUY","NaN"),("BNB-USDT","BUY","Infinity"),("BNB-USDT","BUY",".0001"),("BNB-USDT","BUY",".1001")])
def test_invalid_symbol_side_quantity_no_mutation(symbol,side,qty):
 s=sim(); before=s.balances; r=s.execute(symbol,side,qty,META,QUOTE)
 assert r.status=="REJECTED" and r.balances_after==before and s.balances==before
@pytest.mark.parametrize("quote",[None,BestBidAskQuote("kucoin","BTC-USDT",Decimal(1),Decimal(1),Decimal(2),Decimal(1),QuoteSource.REST,None,NOW),BestBidAskQuote("kucoin","BNB-USDT",Decimal(600),Decimal(1),Decimal(600),Decimal(1),QuoteSource.REST,None,NOW),BestBidAskQuote("kucoin","BNB-USDT",Decimal(600),Decimal(1),Decimal(601),Decimal(1),QuoteSource.REST,None,NOW,QuoteFreshness.STALE),BestBidAskQuote("kucoin","BNB-USDT",Decimal(600),Decimal(1),Decimal(601),Decimal(1),QuoteSource.REST,None,NOW-timedelta(seconds=10)),BestBidAskQuote("kucoin","BNB-USDT",Decimal(600),Decimal(1),Decimal(601),Decimal(".05"),QuoteSource.REST,None,NOW)])
def test_invalid_quote_or_top_size_no_mutation(quote):
 s=sim(); before=s.balances; r=s.execute("BNB-USDT","BUY",".1",META,quote)
 assert r.status=="REJECTED" and r.balances_after==before and s.balances==before
def test_disabled_symbol_and_min_funds_rejected():
 s=sim(); off=SymbolMetadata(**{**META.__dict__,"trading_enabled":False})
 assert s.execute("BNB-USDT","BUY",".1",off,QUOTE).status=="REJECTED"
 low=SymbolMetadata(**{**META.__dict__,"min_funds":Decimal("100")})
 assert "minFunds" in s.execute("BNB-USDT","BUY",".1",low,QUOTE).rejection_reason
def test_reset_and_configurable_start_balances():
 s=sim(); s.execute("BNB-USDT","BUY",".1",META,QUOTE); assert s.reset().BNB==Decimal(5) and s.balances.USDT==Decimal(4000)
 assert KuCoinBNBUSDTDryRunSimulator("2","100",clock=lambda:NOW).balances.BNB==Decimal(2)
def test_fee_rate_fixed():
 with pytest.raises(ValueError): KuCoinBNBUSDTDryRunSimulator(fee_rate=Decimal(".002"))
