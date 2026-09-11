import os, sys, numpy as np
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.factor_engine.factor_backtest_scorer import FactorBacktestScorer, resolve_roundtrip_cost
s = FactorBacktestScorer(); cost = resolve_roundtrip_cost(0.0009)
syms = [x.strip() for x in os.getenv("FACTOR_SCORER_SYMBOLS","").split(",") if x.strip()]
print("面板:", len(syms), "币 | 往返成本:", round(cost*10000,1), "bp")
forms = {
 "rev10": "-1 * (close / delay(close, 10) - 1)",
 "rev20": "-1 * (close / delay(close, 20) - 1)",
 "rev50": "-1 * (close / delay(close, 50) - 1)",
 "ts_rank_dev": "-1 * (ts_rank(close, 20) - 0.5)",
}
for tf, fwd in (("1d", 14), ("1d", 28), ("4h", 168)):
    print(f"\n=== 非重叠样本：{tf} fwd={fwd} ({fwd* (24 if tf=='1d' else 4)}h 持有) ===")
    for name, f in forms.items():
        rets=[]
        for sym in syms:
            kl = s._load_klines(sym, tf, 2400 if tf=="4h" else 1200)
            if not kl or len(kl) < fwd+60: continue
            arrays,_ = s._to_arrays(kl)
            if arrays is None: continue
            fv = s._eval_formula(f, arrays)
            if fv is None: continue
            c = arrays["close"]; n=len(c)
            # 非重叠：从最后往前每 fwd 根取一个观察
            idx = list(range(n-1-fwd, 60, -fwd))[::-1]
            for t in idx:
                if not np.isfinite(fv[t]): continue
                r = (c[t+fwd]-c[t])/c[t]
                rets.append((t, fv[t], r))
        if not rets: 
            print(f"  {name}: 无样本"); continue
        # 训练期定向：用前 50% 观察的 IC 定方向
        rets.sort()
        half = len(rets)//2
        fv1 = np.array([x[1] for x in rets[:half]]); r1 = np.array([x[2] for x in rets[:half]])
        ic = float(np.corrcoef(fv1, r1)[0,1]) if np.std(fv1)>0 and np.std(r1)>0 else 0.0
        orient = 1.0 if ic>=0 else -1.0
        # 测试期
        fv2 = np.array([x[1] for x in rets[half:]]); r2 = np.array([x[2] for x in rets[half:]])
        mu, sd = fv1.mean(), fv1.std()
        pos = np.sign((fv2-mu)/sd) * orient
        gross = pos*r2
        net = gross - cost*0.5  # 每次调仓按单边成本近似（非重叠=每次都换）
        n=len(net); m=net.mean()*10000; se=net.std(ddof=1)/np.sqrt(n)*10000 if n>1 else 0
        print(f"  {name:<12} 训练IC={ic:+.4f} 测试n={n} 净均值={m:+.2f}bp 标准误={se:.2f}bp t={m/se if se else 0:+.2f} 胜率={(net>0).mean():.3f}")
