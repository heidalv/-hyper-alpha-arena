import os, sys, numpy as np
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.factor_engine.factor_backtest_scorer import FactorBacktestScorer, resolve_roundtrip_cost
s = FactorBacktestScorer(); cost = resolve_roundtrip_cost(0.0009)
syms = [x.strip() for x in os.getenv("FACTOR_SCORER_SYMBOLS","").split(",") if x.strip()]
forms = {
 "rev10": "-1 * (close / delay(close, 10) - 1)",
 "rev20": "-1 * (close / delay(close, 20) - 1)",
 "ts_rank_dev": "-1 * (ts_rank(close, 20) - 0.5)",
}
for tf, fwd, holdh in (("1d",14,336), ("1d",28,672)):
    print(f"\n=== 严格逐币时序切分：{tf} fwd={fwd} ({holdh}h) 前50%训练/后50%测试，非重叠 ===")
    for name, f in forms.items():
        allnet=[]
        for sym in syms:
            kl = s._load_klines(sym, tf, 1200)
            if not kl or len(kl) < fwd*4+80: continue
            arrays,_ = s._to_arrays(kl)
            if arrays is None: continue
            fv = s._eval_formula(f, arrays)
            if fv is None: continue
            c = arrays["close"]; n=len(c)
            split = n//2
            # 非重叠观察点
            tr_idx = [t for t in range(60, split-fwd, fwd) if np.isfinite(fv[t])]
            te_idx = [t for t in range(split, n-1-fwd, fwd) if np.isfinite(fv[t])]
            if len(tr_idx) < 8 or len(te_idx) < 4: continue
            fv1=np.array([fv[t] for t in tr_idx]); r1=np.array([(c[t+fwd]-c[t])/c[t] for t in tr_idx])
            if np.std(fv1)<1e-12 or np.std(r1)<1e-12: continue
            ic=float(np.corrcoef(fv1,r1)[0,1]); orient=1.0 if ic>=0 else -1.0
            mu,sd=fv1.mean(),fv1.std()
            for t in te_idx:
                pos=np.sign((fv[t]-mu)/sd)*orient
                r=(c[t+fwd]-c[t])/c[t]
                allnet.append(pos*r - cost*0.5)
        if not allnet: 
            print(f"  {name}: 无样本"); continue
        a=np.array(allnet); n=len(a); m=a.mean()*10000; se=a.std(ddof=1)/np.sqrt(n)*10000 if n>1 else 0
        print(f"  {name:<12} n={n:>5} 净均值={m:>+8.2f}bp 标准误={se:>6.2f}bp t={m/se if se else 0:>+5.2f} 胜率={(a>0).mean():.3f}")
