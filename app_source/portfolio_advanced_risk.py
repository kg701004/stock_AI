"""Optional historical portfolio risk metrics. Missing data remains visible."""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from portfolio import Position, calculate_metrics
from portfolio_risk import SecurityMetadata, pearson_correlation, stress_correlation

@dataclass(frozen=True, slots=True)
class AdvancedRiskReport:
    window_days:int; dynamic_beta:float|None; annualized_volatility_pct:float|None; max_drawdown_pct:float|None; var_pct:float|None; expected_shortfall_pct:float|None; correlations:tuple[tuple[str,str,float],...]; stress_losses:tuple[tuple[str,float],...]; rebalance_amounts:tuple[tuple[str,float],...]; warnings:tuple[str,...]

def _series(database:Path,symbol:str,window:int)->list[tuple[str,float]]:
    if not database.exists(): return []
    c=sqlite3.connect(database)
    try: rows=c.execute("SELECT trading_date,close_micros FROM daily_bars WHERE symbol=? ORDER BY trading_date DESC LIMIT ?",(symbol,window+1)).fetchall()
    except sqlite3.Error: return []
    finally: c.close()
    return [(d,p/1_000_000) for d,p in reversed(rows)]

def _returns(rows:list[tuple[str,float]])->dict[str,float]: return {d:p/prev-1 for (_,prev),(d,p) in zip(rows,rows[1:]) if prev>0}
def _quantile(values:list[float],q:float)->float: return sorted(values)[max(0,min(len(values)-1,int((len(values)-1)*q)))]


def compute_symbol_beta(database:Path, symbol:str, benchmark_symbol:str="0050", window:int=250, minimum_aligned_days:int=30) -> float|None:
    """Real per-symbol Beta vs a benchmark, from local daily-bar return
    series -- the same regression already used for portfolio-level dynamic
    Beta above, applied to a single symbol instead of a weighted portfolio.
    Returns None (never a fabricated number) when there isn't enough
    locally archived, date-aligned history for both symbol and benchmark;
    the caller is expected to fall back to a neutral default in that case."""
    symbol_returns=_returns(_series(database,symbol,window)); benchmark_returns=_returns(_series(database,benchmark_symbol,window))
    dates=sorted(set(symbol_returns)&set(benchmark_returns))
    if len(dates)<minimum_aligned_days: return None
    x=[benchmark_returns[d] for d in dates]; y=[symbol_returns[d] for d in dates]
    variance=mean([(v-mean(x))**2 for v in x])
    if not variance: return None
    return round(mean([(a-mean(x))*(b-mean(y)) for a,b in zip(x,y)])/variance,3)

def assess(database:Path, positions:list[Position], metadata:dict[str,SecurityMetadata], settings:dict[str,object])->AdvancedRiskReport:
    window=int(settings["window_days"]); values={p.symbol:calculate_metrics(p).market_value for p in positions}; total=sum(values.values()); warnings=[]
    if total<=0: raise ValueError("portfolio value must be positive")
    returns={s:_returns(_series(database,s,window)) for s in values}; common=set.intersection(*(set(x) for x in returns.values())) if returns else set()
    portfolio=[sum(values[s]/total*returns[s][d] for s in values) for d in sorted(common)]
    if len(portfolio)<30: warnings.append("可對齊歷史日線不足 30 日；波動、VaR、ES 與相關性不顯示。")
    correlations=[]
    if settings["enable_correlation"] and len(portfolio)>=30:
        symbols=list(values)
        for i,left in enumerate(symbols):
            for right in symbols[i+1:]:
                dates=sorted(set(returns[left])&set(returns[right]));
                if len(dates)>=30: correlations.append((left,right,pearson_correlation([returns[left][d] for d in dates],[returns[right][d] for d in dates])))
        threshold=float(settings.get("high_correlation_threshold",0.75))
        for left,right,correlation in correlations:
            if correlation>=threshold: warnings.append(f"{left} 與 {right} 的歷史報酬相關性 {correlation:.2f}，偏高，分散效果有限。")
        if settings.get("enable_correlation_stress"):
            shock=float(settings.get("correlation_stress_shock_pct",50))/100
            for left,right,correlation in correlations:
                stressed=stress_correlation(correlation,shock)
                if stressed>=threshold>correlation:
                    warnings.append(f"壓力情境假設（相關性以 {shock*100:.0f}% 幅度收斂至 1，非歷史實測，僅供情境參考）：{left} 與 {right} 的相關性可能由 {correlation:.2f} 上升至 {stressed:.2f}，屆時分散效果可能大幅減弱。")
    benchmark=_returns(_series(database,str(settings["benchmark_symbol"]),window)); dates=sorted(common&set(benchmark)); beta=None
    if settings["enable_dynamic_beta"]:
        if len(dates)<30: warnings.append("基準 0050 或組合歷史資料不足；動態 Beta 不顯示。")
        else:
            x=[benchmark[d] for d in dates]; y=[sum(values[s]/total*returns[s][d] for s in values) for d in dates]; variance=mean([(v-mean(x))**2 for v in x]); beta=round(mean([(a-mean(x))*(b-mean(y)) for a,b in zip(x,y)])/variance,3) if variance else None
    vol=round((mean([(x-mean(portfolio))**2 for x in portfolio])**.5)*(252**.5)*100,2) if len(portfolio)>=30 else None
    equity=peak=1.; draw=0.
    for r in portfolio: equity*=1+r; peak=max(peak,equity); draw=min(draw,equity/peak-1)
    confidence=float(settings["var_confidence_pct"])/100; q=_quantile(portfolio,1-confidence) if len(portfolio)>=30 and settings["enable_var_es"] else None; tail=[x for x in portfolio if q is not None and x<=q]
    stress=[]
    if settings["enable_stress_test"]:
        exposure=sum(values[s]/total*(metadata[s].beta if s in metadata else 1.0) for s in values); stress=[("大盤 -5%",round(-5*exposure,2)),("大盤 -10%",round(-10*exposure,2))]
    max_weight=float(settings["maximum_position_weight_pct"])/100; rebalance=tuple((s,round(max(0,v-total*max_weight),2)) for s,v in values.items() if v/total>max_weight) if settings["enable_rebalance"] else ()
    return AdvancedRiskReport(window,beta,vol,round(draw*100,2) if len(portfolio)>=30 else None,round(-q*100,2) if q is not None else None,round(-mean(tail)*100,2) if tail else None,tuple(correlations),tuple(stress),rebalance,tuple(warnings))
