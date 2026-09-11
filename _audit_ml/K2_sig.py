import os, sys, json, numpy as np
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.factor_engine.factor_backtest_scorer import FactorBacktestScorer, resolve_roundtrip_cost
sys.path.insert(0, os.path.join(os.getcwd(),'backend','scripts'))
from scan_holding_period import _wf_detail
s = FactorBacktestScorer(); cost = resolve_roundtrip_cost(0.0009)
syms = [x.strip() for x in os.getenv("FACTOR_SCORER_SYMBOLS","").split(",") if x.strip()][:20]
for name, f, fwd in (("rev10","-1 * (close / delay(close, 10) - 1)",84), ("rev10","-1 * (close / delay(close, 10) - 1)",168)):
    allrows=[]
    for sym in syms:
        kl = s._load_klines(sym, "4h", 2400)
        if not kl: continue
        arrays,_ = s._to_arrays(kl)
        if arrays is None: continue
        fv = s._eval_formula(f, arrays)
        if fv is None: continue
        allrows += _wf_detail(fv, arrays["close"], fwd, cost)
    g = np.array([r["gross"] for r in allrows]); n=len(g)
    mean=g.mean()*10000; se=g.std(ddof=1)/np.sqrt(n)*10000
    print(f"{name} fwd={fwd} ({fwd*4}h): n={n} 毛均值={mean:+.2f}bp 标准误={se:.2f}bp t={mean/se:+.2f} 胜率={(g>0).mean():.3f}")
