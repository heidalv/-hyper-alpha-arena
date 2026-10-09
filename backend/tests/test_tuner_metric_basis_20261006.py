# -*- coding: utf-8 -*-
r"""[整顿轮·T21 2026-10-06] 自调器的证据报告**不得混用权重口径**。

病根：`collect_evidence` 原来的窗口字典里
  · `net_usd` / `spread_usd` / `price_usd` = **名义加权**（SUM(x*notional)）
  · `net_bp_per_leg`                       = **简单平均**（AVG(net_bp)）
字段名不说明用的是哪种。名义分布极偏（实测单腿 $15 ~ $68,247）⇒ 两者会**反号**：

    同日 6h 窗口（161 腿）：
      net_usd           = +$235.67
      net_bp_per_leg    = −0.259 bp   （简单平均）
      net_bp_weighted   = +1.402 bp   （名义加权）

复核方看到这种报告极易误判方向。而调优器的提示词自己写着
「判据是真成交往返的**平均可执行盈亏**」——口径必须说清楚。

修法：① 每个字段标注口径；② 补 `net_bp_weighted`；③ 加 `basis_note` 说明。
"""
from __future__ import annotations

from backend.services.market_maker import self_tuner as ST


def test_window_has_both_bases():
    ev = ST.collect_evidence(6.0)
    w = ev.get("window") or {}
    assert "net_bp_per_leg" in w, "保留简单平均（向后兼容）"
    assert "net_bp_weighted" in w, "必须补名义加权口径"
    assert "basis_note" in w, "必须标注口径，否则复核方会误判"


def test_basis_note_explains_the_difference():
    ev = ST.collect_evidence(6.0)
    note = str((ev.get("window") or {}).get("basis_note") or "")
    assert "名义加权" in note
    assert "简单平均" in note
    assert "反号" in note, "必须写明两者可能反号这个后果"


def test_sql_computes_weighted_average():
    """名义加权的每腿净额必须由 SQL 的 SUM(x*notional)/SUM(notional) 得出。"""
    import inspect
    src = inspect.getsource(ST.collect_evidence)
    assert "SUM(net_bp*notional)/NULLIF(SUM(notional),0)" in src, (
        "名义加权必须用 SUM(net_bp*notional)/SUM(notional)，"
        "不能复用 AVG(net_bp)")
