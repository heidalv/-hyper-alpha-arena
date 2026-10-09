# -*- coding: utf-8 -*-
"""[h900 2026-10-07] choose_exit 强信号豁免时间平仓单测。

病根:赢腿均 +4.12bp 就被时间层提前平掉(盈亏比 0.33 ⇒ 60% 胜率还亏钱)。
修法:概率优势仍强(mu ≥ 2bp)时不按时间提前平,让赢家跑大。
"""
from __future__ import annotations

from backend.services.market_maker.flow_rules import choose_exit


def _mk(**kw):
    """构造 choose_exit 调用的默认参数(一个盈利中的多头)。"""
    d = dict(qty=1.0, entry_px=100.0, bid=100.05, ask=100.08,
             now_ts=1000.0, opened_ts=970.0, max_hold_sec=45.0,
             mu=0.5, vol_300s_bp=5.0, regime="R2", book_stale=False)
    d.update(kw)
    return d


def test_time_exit_fires_when_edge_weak():
    # 持仓 30s(>45×0.33=15s)+ 优势弱(mu=0.5) ⇒ maker_time
    assert choose_exit(**_mk(mu=0.5)) == "maker_time"


def test_time_exit_suppressed_when_edge_strong():
    # 持仓 30s + 优势强(mu=+3bp) ⇒ 不按时间平(hold,让赢家跑)
    assert choose_exit(**_mk(mu=3.0)) == "hold"


def test_edge_exit_still_fires_on_flip():
    # 优势翻负(mu=−1) ⇒ maker_edge(信号翻面,优先于一切)
    assert choose_exit(**_mk(mu=-1.0)) == "maker_edge"


def test_strong_edge_does_not_disable_stop():
    # 强信号也不能关止损:浮亏超 2×止损 ⇒ taker_stop(保命优先)
    assert choose_exit(**_mk(mu=5.0, bid=99.0, ask=99.03)) in (
        "taker_stop", "maker_risk")
