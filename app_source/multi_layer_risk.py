"""Priority-ordered multi-layer take-profit / stop-loss decision engine."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class LayeredInputs:
    price: float; reference_price: float; base_target: float; base_stop: float
    atr_stop: float | None = None; support: float | None = None; moving_average: float | None = None
    peak_price: float | None = None; event_risk: float = 0; portfolio_weight_pct: float = 0; technical_score: float | None = None

@dataclass(frozen=True, slots=True)
class LayeredDecision:
    action: str; effective_stop: float; triggers: tuple[str,...]; warnings: tuple[str,...]

def evaluate(value: LayeredInputs, trailing_drawdown_pct: float = 8, max_position_weight_pct: float = 20) -> LayeredDecision:
    if min(value.price,value.reference_price,value.base_target,value.base_stop) <= 0: raise ValueError("prices must be positive")
    stops=[value.base_stop]; triggers=[]; warnings=[]
    for label, level in (("ATR 波動停損",value.atr_stop),("支撐位",value.support),("均線",value.moving_average)):
        if level is None: warnings.append(f"缺少{label}資料，未啟用該層。")
        elif level > 0: stops.append(level)
    effective=max(stops)
    if value.event_risk >= 80: return LayeredDecision("事件風險減碼",effective,(f"事件風險 {value.event_risk:.0f} ≥ 80",),tuple(warnings))
    if value.price <= effective:
        if value.price >= value.reference_price:
            # Price broke the effective stop (often the moving-average layer
            # in a pullback), but is still at or above the original
            # reference price -- a real unrealized gain eroding, not a loss
            # to cut. Calling this "停損" (stop-LOSS) mislabels a still-
            # profitable position as a loss; Taiwan convention calls
            # protecting a weakening gain "停利", not "停損".
            return LayeredDecision("停利（轉弱）",effective,(f"現價跌破有效停損 {effective:.2f}，但仍高於參考價 {value.reference_price:.2f}（趨勢轉弱，非虧損）",),tuple(warnings))
        return LayeredDecision("停損",effective,(f"現價跌破有效停損 {effective:.2f}",),tuple(warnings))
    if value.peak_price and value.peak_price > value.reference_price and value.price <= value.peak_price*(1-trailing_drawdown_pct/100): return LayeredDecision("移動停利",effective,(f"高點回落 {trailing_drawdown_pct:.1f}%",),tuple(warnings))
    if value.price >= value.base_target: return LayeredDecision("停利",effective,(f"現價到達目標 {value.base_target:.2f}",),tuple(warnings))
    if value.portfolio_weight_pct > max_position_weight_pct: return LayeredDecision("組合減碼",effective,(f"持股權重 {value.portfolio_weight_pct:.1f}% 超過 {max_position_weight_pct:.1f}%",),tuple(warnings))
    if value.technical_score is not None and value.technical_score < 35:
        return LayeredDecision("技術轉弱減碼",effective,(f"技術確認分數 {value.technical_score:.1f} 低於 35",),tuple(warnings))
    return LayeredDecision("續抱／觀察",effective,("未觸發任何停利、停損或組合風險門檻",),tuple(warnings))
