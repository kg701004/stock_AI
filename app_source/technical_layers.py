"""Technical risk layers calculated only from locally stored daily bars."""
from __future__ import annotations
from dataclasses import dataclass
from statistics import mean

@dataclass(frozen=True, slots=True)
class Bar: close: float; high: float; low: float; volume: int
@dataclass(frozen=True, slots=True)
class TechnicalLayers:
    atr: float; ma20: float; ma60: float | None; support: float; resistance: float; relative_volume: float; regime: str

def calculate(bars: list[Bar]) -> TechnicalLayers:
    if len(bars) < 21: raise ValueError("technical layers need at least 21 daily bars")
    # Every statistic below only ever looks at the most recent 60 bars, but
    # the full `bars` list can be years long (e.g. a 10-year backfill) --
    # trimming to the tail first keeps this O(1) per call instead of O(n),
    # which used to make every technical_validation.validate() walk-forward
    # step (itself O(n) calls) recompute over the whole history each time.
    window = bars[-60:] if len(bars) >= 60 else bars
    closes=[x.close for x in window]; volumes=[x.volume for x in window]
    true_ranges=[]
    for previous,current in zip(window,window[1:]): true_ranges.append(max(current.high-current.low,abs(current.high-previous.close),abs(current.low-previous.close)))
    atr=mean(true_ranges[-14:]); ma20=mean(closes[-20:]); ma60=mean(closes[-60:]) if len(closes)>=60 else None
    support=min(x.low for x in window[-20:]); resistance=max(x.high for x in window[-20:])
    # 當日量能與前 20 日比較，避免把當日大量納入平均而稀釋訊號。
    volume_baseline=mean(volumes[-21:-1])
    relative=volumes[-1]/volume_baseline if volume_baseline else 0
    regime="多頭" if ma60 is not None and closes[-1] >= ma20 >= ma60 else "空頭" if ma60 is not None and closes[-1] < ma20 < ma60 else "盤整／資料不足"
    return TechnicalLayers(round(atr,4),round(ma20,2),None if ma60 is None else round(ma60,2),round(support,2),round(resistance,2),round(relative,2),regime)
