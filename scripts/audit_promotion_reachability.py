# -*- coding: utf-8 -*-
"""[F338 2026-09-18] 「通道可达性」测试：晋升门在**理想候选**下能否开一次？

背景（报告 §12 P1′）：线上实测 `[PromotionScan] 候选=2 晋升=0`（连续 4 tick），
门槛为 `MIN_TRADES=50`(env) / `WIN_RATE≥0.48` / `DD≤0.15` / **`MIN_DSR=0.55`** / `ΔPnL≥0.005`。
若连一个**明显优秀**的候选都过不了，那"晋升=0"就不是数据量问题，而是**门本身打不开**。

本测试用纯函数 `evaluate_promotion(metrics)`（无副作用、不写库）做三档探测：
  A 理想候选（100 笔 / 胜率 0.62 / 回撤 0.05 / 正收益序列 / n_trials=20）
  B 极优候选（300 笔 / 胜率 0.70 / 回撤 0.03 / 强正收益 / n_trials=60）
  C 当前现实候选（25 笔 —— 实测系统量级）
打印每档的 `approved / reason / dsr`，**不断言"必须通过"**（门槛是治理策略，不属测试裁判范围），
而是把"门到底能不能开"变成**可复现的读数**；若 A/B 亦被拒，报告里"不可达"的推断即被证实。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.promotion_gate_service import (  # noqa: E402
    PromotionMetrics, evaluate_promotion,
)


def _rets(n: int, mean: float, amp: float):
    """构造 n 期收益序列（确定性，避免随机导致 flaky）。"""
    out = []
    for i in range(n):
        out.append(mean + (amp if i % 2 == 0 else -amp * 0.6))
    return out


CASES = [
    ("A 理想候选", dict(trade_count=100, win_rate=0.62, max_drawdown=0.05,
                     sharpe=2.0, n_trials=20, returns=_rets(60, 0.004, 0.003),
                     canary_pnl_delta=0.02)),
    ("B 极优候选", dict(trade_count=300, win_rate=0.70, max_drawdown=0.03,
                     sharpe=3.0, n_trials=60, returns=_rets(120, 0.006, 0.002),
                     canary_pnl_delta=0.05)),
    ("C 当前现实", dict(trade_count=25, win_rate=0.52, max_drawdown=0.08,
                     sharpe=1.0, n_trials=200, returns=_rets(25, 0.002, 0.004),
                     canary_pnl_delta=0.001)),
]


def main() -> int:
    print("晋升门门槛（.env 实测）: MIN_TRADES=%s MIN_WIN_RATE=%s MAX_DD=%s MIN_DSR=%s "
          "CANARY_DELTA=%s" % (
              os.getenv("PROMOTION_MIN_TRADES", "50(默认20)"),
              os.getenv("PROMOTION_MIN_WIN_RATE", "0.48"),
              os.getenv("PROMOTION_MAX_DRAWDOWN", "0.15"),
              os.getenv("PROMOTION_MIN_DSR", "0.55"),
              os.getenv("PROMOTION_CANARY_MIN_PNL_DELTA", "0.005")))
    for name, kw in CASES:
        m = PromotionMetrics(candidate_id=f"probe_{name}", **kw)
        d = evaluate_promotion(m)
        print(f"{name:<10} approved={str(d.approved):<5} dsr={d.dsr} "
              f"stage={d.from_stage.value}->{d.to_stage.value} reason={d.reason[:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
