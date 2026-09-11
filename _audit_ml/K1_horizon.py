import os, sys, numpy as np
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.factor_engine.factor_backtest_scorer import FactorBacktestScorer, resolve_roundtrip_cost
s = FactorBacktestScorer()
cost = resolve_roundtrip_cost(0.0009)
print("往返成本:", round(cost,5), f"({cost*10000:.1f}bp)")
syms = [x.strip() for x in os.getenv("FACTOR_SCORER_SYMBOLS","").split(",") if x.strip()][:15]
formulas = {
  "ts_rank_dev": "-1 * (ts_rank(close, 20) - 0.5)",
  "mom_skip_rev": "-1 * (close / delay(close, 5) - 1)",
  "vwap_dev_rev": "-1 * (close - vwap) / close",
}
print(f"\n{'因子':<14}{'fwd':>5}{'每笔毛(bp)':>12}{'每笔净(bp)':>12}{'trades':>8}{'win':>7}")
for name, f in formulas.items():
    for fwd in (6, 12, 24, 42, 84):
        nets=[]; trades=0; wins=[]; gross=[]
        for sym in syms:
            kl = s._load_klines(sym, "4h", 2400)
            if not kl: continue
            arrays, ts = s._to_arrays(kl)
            if arrays is None: continue
            fv = s._eval_formula(f, arrays)
            if fv is None: continue
            bt = s._walk_forward_backtest(fv, arrays["close"], fwd, cost, funding_per_hold=0.0, bars_per_year=6*365)
            if bt["trades"]>0:
                nets.append(bt["net_return"]); trades += bt["trades"]; wins.append(bt["win_rate"])
                gross.append(bt["net_return"] + cost*0.5*bt["trades"])  # 近似回加成本
        if trades:
            per_net = sum(nets)/trades*10000
            per_gross = sum(gross)/trades*10000
            print(f"{name:<14}{fwd:>5}{per_gross:>12.2f}{per_net:>12.2f}{trades:>8}{sum(wins)/len(wins):>7.3f}")
