# -*- coding: utf-8 -*-
"""[h490 2026-09-29] 出场决策点探针（`logs/mm_exit_probe.jsonl`）契约测试。

为什么要有它：账本 `position_id` 会被复用（实测近 3h 213 行只有 12 个 id），
`position_id_state` 恒为 'paired' ⇒ **往返无法可靠配平**；而"被动优先要不要改成
N 秒后认输"只差**决策点那一刻的持仓状态**（开仓均价/中价/浮盈亏/年龄）。
探针把这些字段落盘，是宽限期反事实的唯一干净来源。

本测试守住三件事：
  1. 字段完整、语义正确（浮盈亏按方向算）；
  2. 限流生效（同一 symbol+kind 不刷屏）、**绝不抛异常**；
  3. **硬顶分支的探针必须在 `avg_px/opened_ts` 被清零之前触发**
     —— 这是最容易悄悄写错的一处（写晚了就只剩 0，等于没埋）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402


def _fresh(tmp_path, monkeypatch):
    p = tmp_path / "probe.jsonl"
    monkeypatch.setattr(mmrunner, "EXIT_PROBE_PATH", p)
    mmrunner._EXIT_PROBE_LAST.clear()
    return p


def _state(symbol="BTC", qty=0.5, avg=100.0, opened_ago=50.0, now=1_000_000.0):
    st = mmrunner.SymbolState(symbol=symbol)
    st.qty = qty
    st.avg_px = avg
    st.avg_mid = avg
    st.opened_ts = now - opened_ago
    st.mid_hist = [avg] * 20
    return st


NOW = 1_000_000.0


def test_probe_fields(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    st = _state(now=NOW)
    mmrunner.persist_exit_probe(symbol="BTC", state=st, mid=100.2, kind="giveup",
                                ofi=0.9, now_ts=NOW)
    rec = json.loads(p.read_text(encoding="utf-8").strip())
    assert rec["kind"] == "giveup" and rec["symbol"] == "BTC"
    assert abs(rec["qty"] - 0.5) < 1e-12
    assert abs(rec["avg_mid"] - 100.0) < 1e-9
    # 多头、中价 100.2 > 均价 100 ⇒ 浮盈 +20bp
    assert abs(rec["unrealized_bp"] - 20.0) < 0.1, rec["unrealized_bp"]
    assert rec["age_s"] > 0 and abs(rec["ofi"] - 0.9) < 1e-9


def test_probe_short_side_sign(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    st = _state(qty=-0.5, avg=100.0, now=NOW)
    mmrunner.persist_exit_probe(symbol="BTC", state=st, mid=100.2, kind="giveup",
                                now_ts=NOW)
    rec = json.loads(p.read_text(encoding="utf-8").strip())
    # 空头、中价涨 ⇒ 浮亏 −20bp
    assert rec["unrealized_bp"] < 0, rec["unrealized_bp"]


def test_probe_rate_limited(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    st = _state(now=NOW)
    for _ in range(5):
        mmrunner.persist_exit_probe(symbol="BTC", state=st, mid=100.0, kind="giveup",
                                    now_ts=NOW)
    assert len(p.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_probe_never_raises(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    # 故意传坏对象：探针必须吞掉异常（交易链路优先）
    mmrunner.persist_exit_probe(symbol="BTC", state=None, mid=None, kind="x")
    assert p.exists() or True


def test_replay_age_is_rejected(tmp_path, monkeypatch):
    """★ 回归：历史时间戳（回放/测试）配事件时钟 ⇒ 年龄仍自洽，但**超上限必被挡**。

    事故：本探针第一版用墙钟算年龄，把 `pytest -k "mm or lane"` 里回放用例的
    10 行假事件（age≈42000s）写进了**生产的** `logs/mm_exit_probe.jsonl`。
    """
    p = _fresh(tmp_path, monkeypatch)
    st = _state(opened_ago=42000.0, now=NOW)      # 年龄 42000s > 上限 600s
    mmrunner.persist_exit_probe(symbol="BTC", state=st, mid=100.0, kind="hardcap",
                                now_ts=NOW)
    assert not p.exists() or p.read_text(encoding="utf-8").strip() == ""


def test_disable_switch(tmp_path, monkeypatch):
    """`MM_EXIT_PROBE_DISABLE=1` 时完全不写（回放模块已在入口默认设上）。"""
    p = _fresh(tmp_path, monkeypatch)
    monkeypatch.setenv("MM_EXIT_PROBE_DISABLE", "1")
    st = _state(opened_ago=60.0, now=NOW)
    mmrunner.persist_exit_probe(symbol="BTC", state=st, mid=100.0, kind="giveup",
                                now_ts=NOW)
    assert not p.exists() or p.read_text(encoding="utf-8").strip() == ""


def _tick(st, mid, now, limits, ofi=0.0):
    return mmrunner.plan_tick(
        state=st, mid=mid, seg_low=mid - 0.1, seg_high=mid + 0.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=now,
        equity=5000.0, fill_notional=100.0, ofi=ofi,
        params=QuoteParams(w_base_bp=8.0, k_inv=0.5, frozen_width_bp=None),
        limits=limits)


def _limits(**kw):
    base = dict(stop_loss_bp=0.0, take_profit_bp=0.0, stop_maker_grace_sec=0.0,
                min_hold_seconds=0.0, trend_pause_bp=0.0, sudden_move_bp=0.0,
                ofi_confirm_threshold=0.0, ofi_block_threshold=0.0,
                max_one_side_seconds=90.0, timeout_exit_maker_only=True,
                timeout_hard_taker_sec=300.0, ofi_flatten_threshold=0.5,
                ofi_flatten_min_age_ratio=0.5, ofi_flatten_maker_only=1.0)
    base.update(kw)
    return LaneRiskLimits(**base)


def test_probe_fires_on_giveup(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    now = 1_000_000.0
    st = _state(opened_ago=60.0, now=now)
    _tick(st, 100.0, now, _limits(), ofi=0.9)
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").strip().splitlines()]
    assert any(r["kind"] == "giveup" for r in lines), lines


def test_hardcap_probe_fires_before_reset(tmp_path, monkeypatch):
    """★ 关键回归：硬顶分支会先把 avg_px/opened_ts 清零，探针必须在那之前。"""
    p = _fresh(tmp_path, monkeypatch)
    now = 1_000_000.0
    st = _state(qty=0.5, avg=100.0, opened_ago=400.0, now=now)   # 400s > 300s 硬顶
    _tick(st, 99.6, now, _limits(), ofi=0.0)
    recs = [json.loads(x) for x in p.read_text(encoding="utf-8").strip().splitlines()]
    hc = [r for r in recs if r["kind"] == "hardcap"]
    assert hc, f"硬顶分支未落盘：{recs}"
    assert abs(hc[0]["qty"] - 0.5) < 1e-12, hc[0]
    assert abs(hc[0]["avg_mid"] - 100.0) < 1e-9, hc[0]
    assert hc[0]["age_s"] > 300, hc[0]
    assert hc[0]["unrealized_bp"] < 0, hc[0]     # 99.6 < 100 ⇒ 浮亏
