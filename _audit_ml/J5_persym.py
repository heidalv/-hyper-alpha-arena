import os, sys, json
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.factor_engine.factor_backtest_scorer import FactorBacktestScorer, resolve_roundtrip_cost
s = FactorBacktestScorer()
print("真实往返成本:", resolve_roundtrip_cost(0.0009))
r = s.score_formula("diag_tsrank", "-1 * (ts_rank(close, 20) - 0.5)", interval="4h", count_trial=False)
print(f"ts_rank_dev: IC={r.ic_mean:+.4f} ICIR={r.icir:+.3f} Sharpe={r.oos_sharpe:+.3f} net={r.oos_net_return:+.5f} trades={r.oos_trades}")
print("per_symbol:")
ps = r.per_symbol or {}
for k,v in list(ps.items())[:12]:
    if isinstance(v, dict):
        print(f"   {k}: ic={v.get('ic')} net={v.get('net_return')} sharpe={v.get('sharpe')} trades={v.get('trades')}")
    else:
        print(f"   {k}: {v}")
