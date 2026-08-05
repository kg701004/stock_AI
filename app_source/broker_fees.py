"""KGI public Taiwan-equity fee estimates, with an optional account commission discount.

Only the broker's own commission (`fee`) is ever discounted in practice --
the government transaction tax (`tax`) is fixed by law and no broker discount
applies to it, so `discount` never touches the tax calculation.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

KGI_FEE_RATE=0.001425
@dataclass(frozen=True,slots=True)
class FeeEstimate: fee:float; tax:float; total:float; description:str
def _round_half_up(value:float)->int:
    # Taiwan brokers round fees/tax half-up to the nearest dollar; Python's
    # builtin round() is banker's rounding (round-half-to-even) and silently
    # understates fees on exact .5 amounts.
    return int(Decimal(str(value)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
def estimate(price:float,shares:int,side:str,is_etf:bool=False,is_day_trade:bool=False,discount:float=1.0)->FeeEstimate:
    if price<=0 or shares<=0 or side not in {'BUY','SELL'}: raise ValueError('invalid trade for fee estimate')
    if not 0 < discount <= 1: raise ValueError('discount must be from 0 (exclusive) to 1 (full rate)')
    value=price*shares; fee=max(20,_round_half_up(value*KGI_FEE_RATE*discount)); tax=0.0
    if side=='SELL': tax=_round_half_up(value*(0.001 if is_etf else 0.0015 if is_day_trade else 0.003))
    description = '凱基公開牌告估算；未含其他代收費用' if discount == 1.0 else f'凱基公開牌告估算，手續費套用 {discount*10:g} 折；未含其他代收費用'
    return FeeEstimate(fee,tax,round(fee+tax,2),description)
