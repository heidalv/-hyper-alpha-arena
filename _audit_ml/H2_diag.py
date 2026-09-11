import os, sys, json
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.factor_engine.factor_backtest_scorer import FactorBacktestScorer
s = FactorBacktestScorer()
tests = [
    ("diag_rev10", "-1 * (close / delay(close, 10) - 1)", "4h"),
    ("diag_rev20", "-1 * (close / delay(close, 20) - 1)", "4h"),
    ("diag_zero", "close * 0.0", "4h"),
    ("diag_mom10", "(close / delay(close, 10) - 1)", "4h"),
]
for fid, f, tf in tests:
    try:
        r = s.score_formula(fid, f, interval=tf, count_trial=False)
        print(f"{fid:<12} {tf} IC={r.ic_mean:+.4f} ICIR={r.icir:+.3f} Sharpe={r.oos_sharpe:+.3f} "
              f"net={r.oos_net_return:+.5f} trades={r.oos_trades} win={r.oos_win_rate:.3f} grade={r.grade} admitted={r.admitted}")
        print(f"             reason={str(r.reason)[:220]}")
    except Exception as e:
        print(fid, "ERR", str(e)[:150])
