# -*- coding: utf-8 -*-
"""[F230 2026-09-15] 上限失效根因的回归锁：同一张旧挂单不得被重复判定成交。

现场（账本逐笔）：22:30~22:47 阴跌段多头被**同一张买单连判 57 次**、每次 +$30；
23:17~23:22 上涨段空头被**同一张卖单连判 16 次**（XRP/SOL/ETH 各 -$461 =
单币上限的 5.3× ✗）。机制：`_quote_hist` 只在**挂了新单**时追加 ⇒ 闸门拦住新单后，
`_lagged_quote` 每个 tick 返回**同一张旧单**，而水位线仍在前进 ⇒ 每个新桶都让旧单
再成交一次，上限检查（只约束挂单）形同虚设 ✗✗。

修复：闸门拦住新单的 tick 也追加一条**零单标记**（bid=0/ask=0）——真实语义里旧单
已被撤，其存续期之后的分片不得再拿它判成交 ✓。

本测试用 monkeypatch 驱动 `ShadowRunner.tick` 的 12 个 tick（上涨行情、每 tick
区间都穿越卖单、lag=1），断言仓位**绝不超过**单币上限 + 1 腿的滞后余量：
修复前该测试会看到仓位每 tick +$30 直线打到 -$360 以上 ✗。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import (  # noqa: E402
    LaneRiskLimits,
    QuoteParams,
)
from backend.services.market_maker.runner import ShadowRunner  # noqa: E402

CAP = 0.3        # max_net_directional_ratio → 单币上限 = 30% 权益 = $90
LEG = 30.0       # 腿量
EQ = 300.0


def _make_runner():
    r = ShadowRunner(
        lane_id="t_f230", venue="x", symbols=["BTC"],
        equity=EQ, params=QuoteParams(
            side_mode="both", w_base_bp=10.0, k_inv=0.0, k_vol=0.0,
            frozen_width_bp=None),
        limits=LaneRiskLimits(
            max_net_directional_ratio=CAP, max_net_exposure_ratio=0.6,
            max_gross_notional_ratio=1.0, vol_pause_sigma=0.0,
            trend_pause_bp=0.0, stop_loss_bp=0.0, max_one_side_seconds=3600.0,
            ofi_block_threshold=0.0),
        fill_notional=LEG)
    r.judge_lag_buckets = 1
    r.compound_ratio = 0.0
    r.account_id = None
    # 免 DB：tick 的落库/登记/健康回调全部 stub
    r.save_states = lambda: None
    r._record_fills = lambda dec: None
    r._refresh_registry = lambda *a, **k: None
    return r


def _fake_market(r, clock):
    """每 tick 快照 +15s；区间恒穿越卖单（seg_high=101 > ask≈100.1）、
    不穿越买单（seg_low=99 < bid≈99.9 其实会穿越……见下）。"""
    def fetch(since_ms):
        nonlocal clock
        clock["t"] += 15.0
        snap_ms = int(clock["t"] * 1000)
        out = {}
        for s in r.symbols:
            hi_use = snap_ms - r.judge_lag_buckets * 15000
            jq = r._lagged_quote(s, hi_use)
            out[s] = {
                "ts_ms": snap_ms, "mid": 100.0, "half_spread": 0.01,
                "rel_spread": 0.0002,
                "seg_low": 0.0, "seg_high": 101.0,   # 只让卖单被穿越
                "seg_sell": 0.0, "seg_buy": 100.0,    # 只有主动买量
                "ofi": 0.0, "seg_lo_ms": snap_ms - 15000,
                "seg_hi_ms": snap_ms, "seg_buckets": 1,
                "judged_quote": ((float(jq["bid"] or 0.0), float(jq["ask"] or 0.0),
                                  float(jq["mid"] or 0.0)) if jq is not None
                                 else (0.0, 0.0, 0.0)),
            }
        return out
    return fetch


def test_old_quote_cannot_keep_filling_after_cap_block(monkeypatch):
    """上涨行情连续 12 tick：仓位必须停在单币上限附近，不得被旧单反复成交推穿。"""
    r = _make_runner()
    clock = {"t": time.time()}
    monkeypatch.setattr(r, "fetch_market", _fake_market(r, clock))

    max_abs_usd = 0.0
    total_fills = 0
    for _ in range(12):
        res = r.tick()
        assert res.get("ok"), res
        st = r.states["BTC"]
        usd = abs(st.qty) * 100.0
        max_abs_usd = max(max_abs_usd, usd)
        total_fills += res.get("fills", 0)

    # 上限 $90；滞后一档最多多 1 腿（$30）⇒ 允许到 $120。旧 bug 会一路打到 $360+。
    assert max_abs_usd <= EQ * CAP + LEG + 1e-6, f"仓位峰值 ${max_abs_usd:.1f} 超过上限+1腿"
    # 有成交（说明测试场景真实成交过），但 12 tick 内绝不可能把仓位堆到 3 倍上限
    assert total_fills > 0, "场景应有成交，否则测试无效"
    assert abs(r.states["BTC"].qty) * 100.0 <= EQ * CAP + LEG + 1e-6, \
        "末仓也必须在上限+1腿之内"


def test_blocked_tick_appends_zero_marker(monkeypatch):
    """闸门拦住新单的 tick 必须留下零单标记：后续分片不得用旧单判成交。

    构造：先跑 1 个 tick 挂上一张会被穿越的卖单；然后把波动闸拧死
    （vol_pause_sigma=0.01 + 狂野 mid_hist ⇒ sigma_norm 巨大 ⇒ 整车道暂停，
    双侧都不挂新单），再跑 tick ⇒ 旧卖单若被复判就是 bug ✗。
    """
    import dataclasses

    r = _make_runner()
    clock = {"t": time.time()}
    monkeypatch.setattr(r, "fetch_market", _fake_market(r, clock))
    r.tick()
    assert len(r._quote_hist.get("BTC", [])) == 1, "首个 tick 应留下一张挂单"

    st = r.states["BTC"]
    st.mid_hist = [100.0, 102.0] * 30      # 狂野波动 ⇒ sigma_norm 巨大
    st.vol_baseline_bp = 1.0
    r.limits = dataclasses.replace(r.limits, vol_pause_sigma=0.01)

    fills_before = r.fills
    fills_per_tick = []
    for _ in range(3):
        r.tick()
        fills_per_tick.append(r.fills)
    # 第一张挂单在**它自己存续的那个分片**里成交一次是合法的（lag=1 ⇒ 第 2 tick
    # 判定第 1 tick 的挂单）；但之后必须**零成交**——旧单不得被后续分片复判 ✗
    d = [fills_per_tick[0] - fills_before] + [
        fills_per_tick[i] - fills_per_tick[i - 1] for i in range(1, len(fills_per_tick))]
    assert sum(d) <= 1, f"旧单被复判：每个 tick 的成交增量 {d} ✗"
    assert all(x == 0 for x in d[1:]), f"第 2 个 tick 之后仍有新成交 {d} ✗"
    markers = [q for q in r._quote_hist.get("BTC", []) if q["bid"] == 0 and q["ask"] == 0]
    assert markers, "被拦 tick 必须追加零单标记（否则旧单会被反复判定 ✗）"
