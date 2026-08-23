"""R2 已实现亏损风控事件 + R1 流动性杠杆分档（阶段3）单测。"""
import os
import tempfile

import backend.services.symbol_penalty as sp
from backend.services.position_sizing_agent import _liquidity_lev_cap, _LIQ_CACHE


def _fresh_sp():
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    os.remove(tmp.name)
    sp._STATE_PATH = tmp.name
    return tmp.name


def test_risk_event_bans_symbol_24h():
    path = _fresh_sp()
    assert not sp.is_risk_banned("XPL")
    sp.flag_symbol_risk_event("XPL", {"pnl": -7.55, "pct": -1.9})
    assert sp.is_risk_banned("XPL")
    assert sp.is_risk_banned("xpl")  # 大小写不敏感
    snap = sp.snapshot()
    assert "risk_events" in snap["symbols"]["XPL"]


def test_risk_event_does_not_ban_other_symbols():
    path = _fresh_sp()
    sp.flag_symbol_risk_event("LDO", {})
    assert not sp.is_risk_banned("BTC")


def test_liquidity_cap_caching_and_fallback():
    _LIQ_CACHE.clear()
    # 无 data_center 数据 → 保守 10x
    cap = _liquidity_lev_cap("SOMENEWCOINXYZ")
    assert cap == 10
    # 缓存命中（第二次不重查）
    assert ("SOMENEWCOINXYZ", 10) in _LIQ_CACHE.values() or _LIQ_CACHE.get("SOMENEWCOINXYZ")
