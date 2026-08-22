# -*- coding: utf-8 -*-
"""FACTOR-1 复评：用修正后的成本(9bps)/Sharpe(0.2)/净缓冲(2bp) 重打三个被误杀因子。"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.core.tenant import set_system_identity
from backend.services.factor_engine.factor_backtest_scorer import FactorBacktestScorer

set_system_identity()
CASES = [
    ("ai_a101s_bias30_rev_15m",
     "-1 * (close - ts_mean(close, 30)) / (ts_mean(close, 30) + 1e-9)",
     "15m", {"fwd": 2, "lookback": 720}),
    ("ai_gpu_rsi14_4h",
     "(ts_mean(np.maximum(delta(close, 1), 0), 14) - ts_mean(np.maximum(-delta(close, 1), 0), 14)) / (ts_mean(np.maximum(delta(close, 1), 0), 14) + ts_mean(np.maximum(-delta(close, 1), 0), 14) + 1e-9)",
     "4h", {"fwd": 6, "lookback": 1000}),
    ("ai_gpu_macd13_34_4h",
     "delta(ts_mean(close, 13), 1) - delta(ts_mean(close, 34), 1)",
     "4h", {"fwd": 3, "lookback": 1000}),
]
scorer = FactorBacktestScorer()
for fid, formula, interval, kw in CASES:
    print(f"\n===== {fid} @ {interval} =====")
    r = scorer.score_formula(
        fid, formula, interval=interval,
        dsr_required=True, count_trial=False,
        **kw,
    )
    print(f"  grade={r.grade} admitted={r.admitted}")
    print(f"  IC={r.ic_mean} ICIR={r.icir} OOS_sharpe={r.oos_sharpe} OOS_net={r.oos_net_return} win={r.oos_win_rate} trades={r.oos_trades}")
    print(f"  reason={r.reason}")
