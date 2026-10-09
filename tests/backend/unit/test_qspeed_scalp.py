# -*- coding: utf-8 -*-
"""[h898 2026-10-07] scalp 口径 Q 速控(compute_q_scalp)单测。

策略已从做市转为趋势概率快进快出,Q 分指标随之改为:
成交率(0.40) + 实时 edge(0.35) + 市场流(0.25),尾部风暴硬钳保留。
"""
from __future__ import annotations

import pytest

from backend.services.market_maker import qspeed as Q


def test_scalp_full_marks():
    # 全满分:10腿/h + edge +5bp + 30笔/30min ⇒ Q=1.0
    q = Q.compute_q_scalp(fill_rate_per_h=10.0, edge_1h_bp=5.0, flow_30m=30.0)
    assert q == pytest.approx(1.0, abs=1e-9)


def test_scalp_all_zero():
    q = Q.compute_q_scalp(fill_rate_per_h=0.0, edge_1h_bp=-5.0, flow_30m=0.0)
    assert q == 0.0


def test_scalp_weights():
    # 只有成交率满分 ⇒ 0.40
    q = Q.compute_q_scalp(fill_rate_per_h=10.0, edge_1h_bp=-5.0, flow_30m=0.0)
    assert q == pytest.approx(0.40, abs=1e-9)
    # 只有 edge 满分 ⇒ 0.35
    q = Q.compute_q_scalp(fill_rate_per_h=0.0, edge_1h_bp=5.0, flow_30m=0.0)
    assert q == pytest.approx(0.35, abs=1e-9)
    # 只有流满分 ⇒ 0.25
    q = Q.compute_q_scalp(fill_rate_per_h=0.0, edge_1h_bp=-5.0, flow_30m=30.0)
    assert q == pytest.approx(0.25, abs=1e-9)


def test_scalp_edge_mapping():
    # edge −5bp ⇒ 0;0bp ⇒ 0.5;+5bp ⇒ 1(线性)
    assert Q.compute_q_scalp(fill_rate_per_h=0, edge_1h_bp=-5.0, flow_30m=0) == 0.0
    q_mid = Q.compute_q_scalp(fill_rate_per_h=0, edge_1h_bp=0.0, flow_30m=0)
    assert q_mid == pytest.approx(0.35 * 0.5, abs=1e-9)


def test_scalp_stop_storm_cap():
    # 尾部风暴:近 30 分钟止损腿 ≥2 ⇒ Q 压到 0.39 以下(触发停加仓)
    q = Q.compute_q_scalp(fill_rate_per_h=10.0, edge_1h_bp=5.0, flow_30m=30.0,
                          stop_rate_30m=2.0)
    assert q <= Q.STOP_STORM_Q_CAP
    assert q < Q.Q_HALF


def test_scalp_clamps_over_range():
    # 超范围输入被夹紧(不爆)
    q = Q.compute_q_scalp(fill_rate_per_h=999.0, edge_1h_bp=999.0, flow_30m=999.0)
    assert q == pytest.approx(1.0, abs=1e-9)
    q0 = Q.compute_q_scalp(fill_rate_per_h=-5, edge_1h_bp=-999.0, flow_30m=-1)
    assert q0 == 0.0
