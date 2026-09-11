import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.factor_engine.factor_backtest_scorer import resolve_roundtrip_cost, FactorBacktestScorer
print("固定档 env:", os.getenv("FACTOR_SCORER_COST"))
print("真实往返成本:", resolve_roundtrip_cost(0.0009))
os.environ["FACTOR_SCORER_COST_SOURCE"]="fixed"
print("回滚(fixed):", resolve_roundtrip_cost(0.0009))
os.environ.pop("FACTOR_SCORER_COST_SOURCE")
s = FactorBacktestScorer()
r = s.score_formula("diag_rev10_cost", "-1 * (close / delay(close, 10) - 1)", interval="4h", count_trial=False)
print(f"rev10 @ 真实成本: IC={r.ic_mean:+.4f} Sharpe={r.oos_sharpe:+.3f} net={r.oos_net_return:+.5f} trades={r.oos_trades}")
