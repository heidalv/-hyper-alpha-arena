# -*- coding: utf-8 -*-
"""[F251 2026-09-16] 注册表 meta 热更新契约（堵 F79 的"重建才生效"缺口）：

现场：09-15 22:27 锚定脚本重写 `replay_baseline.vol_baseline_bp`，但当时在跑的
runner 直到次日 09:04 后端重启才读到 ⇒ 快盘窗口按**旧基线**漏保护（2h −$24.5）。

契约：
  1. 60s 节流：间隔内的非强制检查不读注册表；
  2. 指纹变化（params / limits / vol_baseline 任一）⇒ 热采用，无需重建；
  3. 指纹不变 ⇒ 不动作（幂等）；
  4. 注册表缺某币 baseline ⇒ 该币沿用旧值（与 get_runner F96 语义一致）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.runner import ShadowRunner  # noqa: E402


def _make_runner() -> ShadowRunner:
    r = ShadowRunner(
        lane_id="t_f251", venue="x", symbols=["BTC", "XRP"], equity=300.0,
        params=QuoteParams(w_base_bp=8.0),
        limits=LaneRiskLimits(vol_pause_sigma=0.5, trend_pause_bp=0.0,
                              stop_loss_bp=0.0, max_one_side_seconds=3600.0,
                              ofi_block_threshold=0.0,
                              max_net_directional_ratio=0.3,
                              max_net_exposure_ratio=0.6,
                              max_gross_notional_ratio=1.0))
    r.states["BTC"].vol_baseline_bp = 1.0
    r.states["XRP"].vol_baseline_bp = 2.0
    return r


def _lane(params, vb):
    return {"meta": {"params": params,
                     "replay_baseline": {"vol_baseline_bp": vb}}}


def test_hot_reload_adopts_changed_meta(monkeypatch):
    r = _make_runner()
    calls = {"n": 0}

    def fake_get_lane(lid):
        calls["n"] += 1
        return _lane({"w_base_bp": 12.0, "vol_pause_sigma": 0.7},
                     {"BTC": 1.5, "XRP": 2.5})

    monkeypatch.setattr("backend.services.lane_registry.get_lane", fake_get_lane)
    r._maybe_reload_meta(force=True)
    assert calls["n"] == 1
    assert r.params.w_base_bp == 12.0, "params 必须热采用"
    assert r.limits.vol_pause_sigma == 0.7, "limits 必须热采用"
    assert r.states["BTC"].vol_baseline_bp == 1.5, "BTC 波动基准必须热采用"
    assert r.states["XRP"].vol_baseline_bp == 2.5, "XRP 波动基准必须热采用"


def test_hot_reload_throttled_within_60s(monkeypatch):
    r = _make_runner()
    calls = {"n": 0}

    def fake_get_lane(lid):
        calls["n"] += 1
        return _lane({"w_base_bp": 10.0}, {"BTC": 1.1, "XRP": 2.1})

    monkeypatch.setattr("backend.services.lane_registry.get_lane", fake_get_lane)
    r._maybe_reload_meta(force=True)
    n1 = calls["n"]
    r._maybe_reload_meta()                       # <60s：不得再读注册表
    assert calls["n"] == n1, "60s 内非强制检查必须跳过注册表读取"
    r._last_meta_check = time.time() - 61.0
    r._maybe_reload_meta()                       # 到期：恢复检查
    assert calls["n"] == n1 + 1


def test_no_change_is_idempotent(monkeypatch):
    r = _make_runner()
    lane = _lane({"w_base_bp": 8.0, "vol_pause_sigma": 0.5},
                 {"BTC": 1.0, "XRP": 2.0})
    monkeypatch.setattr("backend.services.lane_registry.get_lane", lambda lid: lane)
    r._maybe_reload_meta(force=True)
    fp = r._meta_fp
    r._maybe_reload_meta(force=True)
    assert r._meta_fp == fp, "指纹不变的重复检查不得改变指纹"
    assert r.params.w_base_bp == 8.0 and r.limits.vol_pause_sigma == 0.5


def test_missing_symbol_keeps_old_baseline(monkeypatch):
    r = _make_runner()
    monkeypatch.setattr("backend.services.lane_registry.get_lane",
                        lambda lid: _lane({"w_base_bp": 10.0}, {"BTC": 1.6}))
    r._maybe_reload_meta(force=True)
    assert r.states["BTC"].vol_baseline_bp == 1.6
    assert r.states["XRP"].vol_baseline_bp == 2.0, "注册表缺该币 ⇒ 沿用旧值（F96 语义）"
