# -*- coding: utf-8 -*-
"""轮110 中线止盈档位重标回归测试（2026-09-19）。

## 依据（159 笔中线已平仓的真实 MFE/MAE 网格，已扣 0.04% 往返费）

    现行 SL1.5% / 首档 TP 0.8% = **−0.175%/笔**   ← 0.8% 那一列在 7×8 网格里**全场最差**
    保留的 mlto 臂：TP2.5% = +0.237 / TP4.0% = +0.386 / TP6.0% = +0.399
    mlto 臂 MFE 分位：P50=1.41%  P75=2.46%  P85=3.67%  P90=3.85%  P95=4.52%
    放宽止损无效：SL 1.5/2.0/3.0/4.0 → +0.237 / +0.245 / +0.158 / +0.058

⇒ 首档从 0.8%（≈ MFE 中位数）抬到 **2.5%（= MFE P75）**，二档 4.0%（≈P90），
三档 6.0%（P95 之外，让尾部奔跑）；**SL 保持 1.5%** ⇒ 每笔风险不变、无需重算仓位乘子。

旧口径的病：0.8% 首档 = "在赢单的 MFE 中位数附近就止盈"，而亏损单仍按 −1.7% 走。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 实测标定值（改这两个数必须同步改本文件的说明与 reports 报告）
_NEW_STAGES = (2.5, 4.0, 6.0)
_OLD_STAGES = (0.8, 1.6, 3.0)


def test_runtime_exit_policy_reads_new_stages():
    from backend.services.exit.exit_policy import ExitPolicy
    assert ExitPolicy.for_lane("mid").tp_stages == _NEW_STAGES


def test_lane_policy_design_matches_execution():
    """设计意图（lane_policy.RECOMMENDED）必须与运行时（ExitPolicy）一致 ——
    轮100 立下的"车道口径只有一处定义"，这里再钉一次。"""
    from backend.config.lane_policy import LANE_MID, RECOMMENDED
    assert tuple(RECOMMENDED[LANE_MID]["tp_stages"]) == _NEW_STAGES


def test_first_stage_clears_the_mfe_median_ratchet():
    """棘轮：首档必须 ≥ 2.0%。旧的 0.8% 贴着 MFE 中位数(1.41%)，是网格里最差的一列。"""
    from backend.services.exit.exit_policy import ExitPolicy
    first = min(ExitPolicy.for_lane("mid").tp_stages)
    assert first >= 2.0, first
    assert first > min(_OLD_STAGES) * 2.5


def test_stages_are_increasing_and_reach_past_p90():
    st = tuple(sorted(_NEW_STAGES))
    assert st == _NEW_STAGES, "档位必须递增"
    assert st[-1] >= 6.0, "末档要放到 MFE P95(4.52%) 之外，让尾部奔跑"


def test_lanes_stay_distinct_after_retune():
    """中线与长线的档位量级必须仍然分开（用户核心口径：中/长是两个概念）。"""
    from backend.config.lane_policy import LANE_LONG, LANE_MID, RECOMMENDED
    mid_first = min(RECOMMENDED[LANE_MID]["tp_stages"])
    long_first = min(RECOMMENDED[LANE_LONG]["tp_stages"])
    assert long_first >= 5.0 and mid_first < long_first, (mid_first, long_first)


def test_env_override_present():
    env = open(os.path.join(_ROOT, ".env"), encoding="utf-8", errors="replace").read()
    assert "EXIT_POLICY_MID_TP_STAGES=2.5,4.0,6.0" in env


def test_stop_distance_untouched_by_this_round():
    """本轮只动止盈：放宽止损在网格里更差（3.0% → +0.158 vs 1.5% → +0.237），
    所以 `MIDLONG_MAX_SL_PCT_MID` 必须仍是 1.5% —— 否则每笔风险变了、
    仓位乘子也要重算（这轮明确**不做**）。"""
    from backend.config.settings import MIDLONG_MAX_SL_PCT_MID as cap
    assert cap == pytest.approx(0.015), cap


def test_grid_arithmetic_selfcheck():
    """算式自证：把促使这次改动的那几个实测数写死在测试里。"""
    # 现行配置（SL1.5/TP0.8）的期望 ≈ −0.175%/笔；抬到 2.5% 后 ≈ +0.237%/笔
    _cur, _new = -0.175, 0.237
    assert _cur < 0 < _new
    _gain = (_new - _cur) / 100.0                      # 百分比 → 小数
    _notional = 1484.9                                  # 实测笔均名义
    assert _gain * _notional == pytest.approx(6.12, abs=0.05)   # ≈ +$6.1/笔
    # mlto 臂 30 天 38 笔 → 约 +$230/月（量级自证）
    assert _gain * _notional * 38 == pytest.approx(232.6, abs=5.0)


def test_exit_policy_documents_the_evidence():
    src = open(os.path.join(_ROOT, "backend/services/exit/exit_policy.py"), encoding="utf-8").read()
    i = src.index('[轮110 2026-09-19 用 30 天真实样本重标]')
    window = src[i: i + 900]
    assert "2.5/4.0/6.0" in window and "P75" in window and "不需要重算仓位乘子" in window
