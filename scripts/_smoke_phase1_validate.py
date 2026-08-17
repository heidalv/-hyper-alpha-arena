"""阶段1冒烟 v2：直接对已拒绝的 alpha101 因子跑 validate_and_promote，
验证 DSR 闸门修复后不再出现机械性 pbo=0.500 拒绝。"""
import os, sys, json, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")

from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer

fids = ["ai_a101_rev_z_4h", "ai_a101_mom_4h", "ai_a101_argmax_rev_1d"]
t0 = time.time()
for fid in fids:
    r = factor_backtest_scorer.validate_and_promote(fid)
    print("-", fid,
          "| grade:", r.grade,
          "| admitted:", r.admitted,
          "| ic:", r.ic_mean,
          "| icir:", r.icir,
          "| oos_sharpe:", r.oos_sharpe,
          "| reason:", r.reason[:160])
print(f"elapsed={time.time()-t0:.1f}s")
