# -*- coding: utf-8 -*-
"""[h664 2026-09-30] 方向分数融合(设计:研究结论/方向判定最优算法与策略 §3)。

D_side = w0×微价 + w1×流(OFI) + w2×趋势 − 毒性(乘法否决)。
D ∈ [−1,1]:正=看涨(做多信心),负=看跌。影子期只算只记
(limits.dir_score_fusion=0),分桶验证(含尾部桶)通过后才启用:
启用后 D 决定挂单侧与加仓量(q_size_mult ∝ |D|)。

各分量已带实测 t 值:微价 t=18.1(h365)、流 t=11~35(h351)、趋势(h324)。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[3]
HISTORY = ROOT / "data" / "dir_score_history.jsonl"
HISTORY_MAX_LINES = 4000

# [h690 2026-10-01 实测重标定] 权重来自**样本外可泛化**的 OLS 拟合
# (scripts/h690_direction_study.py,--grid 15000 --horizons 5,15,30,7572 样本):
#   r30 权重 mp 0.128 / ofi 0.107 / trend 0.237 ⇒ 归一化 ≈ 0.27/0.23/0.50。
# 旧权重(0.25/0.40/0.35)把**最弱的流因子给了最大权重** —— 实测 OFI 的 IC 仅
# 0.01 且分桶非单调(Q2 最差,t=−3.3)⇒ 只适合做尾部否决,不适合线性加权。
#   IC(样本外):r5 +0.037 / r15 +0.051 / r30 +0.051(30-120s 尺度为负 ⇒ 只在秒级有效)。
W_MP = 0.25
W_OFI = 0.15
W_TREND = 0.35
W_TREND900 = 0.25     # [h694] 15 分钟 fade 项(负号在公式里):r30 样本外 IC
                      # 0.042 → 0.060;印证 L4"15min fade"(h328)。

MP_SAT_BP = 2.0     # 微价偏移饱和尺度(bp)
TREND_SAT_BP = 20.0  # 趋势饱和尺度(bp,trend300)
TREND900_SAT_BP = 40.0  # 15 分钟趋势饱和尺度(bp,尺度更大)


def direction_score(mp_skew_bp: float, ofi: float, trend_bp: float,
                    trend900_bp: float = 0.0,
                    toxicity: float = 0.0) -> float:
    """D ∈ [−1,1](纯函数)。各分量夹紧到 [−1,1] 加权;毒性乘法否决(1=完全否决)。

    [h694] trend900 = 近 15 分钟中价净移动(bp),符号取**负**(fade):
    涨太多 ⇒ 看跌、跌太多 ⇒ 看涨 —— 与 trend300(延续)方向相反,均有样本外证据。
    """
    def _c(x: float, sat: float) -> float:
        return max(-1.0, min(1.0, float(x or 0.0) / sat))

    mp = _c(mp_skew_bp, MP_SAT_BP)
    of = max(-1.0, min(1.0, float(ofi or 0.0)))
    tr = _c(trend_bp, TREND_SAT_BP)
    tr9 = _c(trend900_bp, TREND900_SAT_BP)
    d = W_MP * mp + W_OFI * of + W_TREND * tr - W_TREND900 * tr9
    d = max(-1.0, min(1.0, d))
    tox = max(0.0, min(1.0, float(toxicity or 0.0)))
    return round(d * (1.0 - tox), 4)


def append_history(entry: Mapping[str, Any]) -> bool:
    """影子快照落盘(供分桶验证)。失败不抛。"""
    try:
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(entry), ensure_ascii=False, default=str) + "\n")
        lines = HISTORY.read_text(encoding="utf-8").splitlines()
        if len(lines) > HISTORY_MAX_LINES:
            HISTORY.write_text("\n".join(lines[-HISTORY_MAX_LINES:]) + "\n",
                               encoding="utf-8")
        return True
    except Exception:
        return False
