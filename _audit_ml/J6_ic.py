import os, sys, numpy as np
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.factor_engine.factor_backtest_scorer import FactorBacktestScorer
s = FactorBacktestScorer()
syms = [x.strip() for x in os.getenv("FACTOR_SCORER_SYMBOLS","").split(",") if x.strip()][:12]
formula = "-1 * (ts_rank(close, 20) - 0.5)"
fwd = 6
raw_ic=[]; neu_ic=[]
for sym in syms:
    kl = s._load_klines(sym, "4h", 2400)
    if not kl: continue
    arrays, ts = s._to_arrays(kl)
    if arrays is None: continue
    f = s._eval_formula(formula, arrays)
    if f is None: continue
    c = arrays["close"]
    n=len(c)
    fr = np.full(n, np.nan); fr[:n-fwd] = (c[fwd:]-c[:n-fwd])/c[:n-fwd]
    m = np.isfinite(f) & np.isfinite(fr)
    if m.sum() < 50: continue
    ic = float(np.corrcoef(f[m], fr[m])[0,1])
    raw_ic.append((sym, ic))
print("per-symbol raw IC (4h, fwd=6):")
for sym, ic in raw_ic: print(f"   {sym:<6} {ic:+.4f}")
arr=np.array([x[1] for x in raw_ic])
print(f"mean={arr.mean():+.4f} pos={int((arr>0).sum())}/{len(arr)} std={arr.std():.4f}")
