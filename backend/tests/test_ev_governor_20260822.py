# -*- coding: utf-8 -*-
"""EV Governor 回归测试（PROFIT-3）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeAudit:
    def __init__(self, n, avg_net):
        self.n = n
        self.avg_net = avg_net


def test_ev_governor_decisions(monkeypatch):
    """正期望放大 / 负期望收缩 / 深负暂停。

    [2026-09-02] reduce/pause 乘数按 2026-08-23 用户指示上调为 0.75/0.5（×0.2 把单仓
    压到 ~5% 权益，手续费吞噬盈利、"基本就是刷手续费"）；原断言 0.5/0.2 过时。
    这里清掉 env 覆盖，验证代码默认值；再验证 env 可调。
    """
    from backend.services.ev_governor import _decide

    monkeypatch.delenv("EV_GOVERNOR_PAUSE_MULT", raising=False)
    monkeypatch.delenv("EV_GOVERNOR_REDUCE_MULT", raising=False)
    # n>=10 且 EV>0 → premium
    d = _decide(_FakeAudit(20, 0.05))
    assert d["decision"] == "premium" and d["mult"] == 1.2
    # 样本太少 → stable
    d = _decide(_FakeAudit(5, 0.05))
    assert d["decision"] == "stable" and d["mult"] == 1.0
    # n>=30 且 EV<0 → reduce（默认 0.75）
    d = _decide(_FakeAudit(40, -0.03))
    assert d["decision"] == "reduce" and d["mult"] == 0.75
    # n>=50 且 EV<0 → pause（默认 0.5）
    d = _decide(_FakeAudit(60, -0.03))
    assert d["decision"] == "pause" and d["mult"] == 0.5
    # env 可调
    monkeypatch.setenv("EV_GOVERNOR_REDUCE_MULT", "0.6")
    assert _decide(_FakeAudit(40, -0.03))["mult"] == 0.6
    # 负但样本不足 → stable（不冤枉）
    d = _decide(_FakeAudit(15, -0.03))
    assert d["decision"] == "stable" and d["mult"] == 1.0


def test_ev_governor_cluster_mult_failsafe(monkeypatch):
    """读取失败/无状态 → 1.0 默认，不阻塞开单。"""
    from backend.services import ev_governor as g
    _tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ev_tmp")
    os.makedirs(_tmp, exist_ok=True)
    monkeypatch.setattr(g, "_STATE_PATH", os.path.join(_tmp, "missing.json"))
    assert g.cluster_mult("scalp_ranging_mr") == 1.0
    assert g.cluster_mult("scalp_trend") == 1.0


def test_ev_governor_cluster_of():
    from backend.services.ev_governor import _cluster_of_strategy
    assert _cluster_of_strategy("scalp_mr_scalp_ab") == "scalp_ranging_mr"
    assert _cluster_of_strategy("scalp_lane_scalp_cd") == "scalp_trend"
    assert _cluster_of_strategy("tpl_mid_reversion_xx") == "swing"
    assert _cluster_of_strategy("tpl_long_swing_yy") == "trend_follow"
