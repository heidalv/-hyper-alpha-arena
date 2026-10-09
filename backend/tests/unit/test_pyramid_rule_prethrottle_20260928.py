# -*- coding: utf-8 -*-
"""[2026-09-28 用户指令] 滚仓规则直通**不受 LLM 节流约束**——单测。

## 现场（用户："这么久了，没有看见补仓和滚仓"）
实测（09-25 凌晨日志）：LLM 每 ~30 分钟判一次 add，但先撞上 09-19 滚仓事故残留的
「已加仓3次，达tier上限3」快照；而代码里的规则直通 `_pyr_direct` 写在
`_llm_due` 早退**之后** ⇒ 长线 4h 一次 LLM 复审，规则直通永远够不到。
本修复把规则直通提到节流早退**之前**（`MIDLONG_PYRAMID_RULE_PRETHROTTLE`，
默认 true，回滚 false），判据抽成纯函数 `_pyramid_rule_prethrottle_ready`。

约束：
  ① 判据 = 保证金浮盈 > 5%(阈值) 且 4h/1d 双周期同向（与 _dim_pyramid 规则分支同口径）；
  ② 开关非法值/关闭 ⇒ 不直通（fail-closed）；
  ③ 只负责"该不该进 5 层门控"，不执行动作；门控内仍有 3 次上限/逐档盈利门槛/冷却。
"""
from __future__ import annotations

import pytest

from backend.services.full_auto import midlong_position_manager as M


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MIDLONG_PYRAMID_RULE_PRETHROTTLE", raising=False)
    yield


def _pos(margin=100.0, upnl=10.0, side="long"):
    return {"margin": margin, "unrealized_pnl": upnl, "side": side}


def _md(tf4="bullish", tf1="bullish"):
    return {"BTC": {
        "indicators_4h": {"macd": 1.0, "trend": tf4},
        "indicators_1d": {"macd": 1.0, "trend": tf1},
        "orchestrator": {"mid_bias": tf4, "long_bias": tf1},
    }}


def test_ready_when_profit_and_aligned():
    # 保证金浮盈 10% > 5%，4h/1d 同向 → 进 5 层门控
    assert M._pyramid_rule_prethrottle_ready("BTC", _pos(upnl=10.0), _md()) is True


def test_not_ready_when_profit_below_threshold():
    # 浮盈 4% ≤ 5% → 不直通
    assert M._pyramid_rule_prethrottle_ready("BTC", _pos(upnl=4.0), _md()) is False
    # 浮亏 → 不直通（仅浮盈滚仓原则）
    assert M._pyramid_rule_prethrottle_ready("BTC", _pos(upnl=-2.0), _md()) is False


def test_not_ready_when_any_period_opposes():
    # 4h 反向 → 不直通（趋势未共振）
    assert M._pyramid_rule_prethrottle_ready("BTC", _pos(upnl=10.0),
                                             _md(tf4="bearish")) is False
    # 1d 反向 → 不直通
    assert M._pyramid_rule_prethrottle_ready("BTC", _pos(upnl=10.0),
                                             _md(tf1="bearish")) is False
    # 空头仓 + 双周期 bullish → 不直通
    assert M._pyramid_rule_prethrottle_ready("BTC", _pos(upnl=10.0, side="short"),
                                             _md()) is False


def test_missing_market_summary_fails_closed():
    assert M._pyramid_rule_prethrottle_ready("BTC", _pos(upnl=10.0), {}) is False
    assert M._pyramid_rule_prethrottle_ready("BTC", _pos(upnl=10.0), None) is False


def test_switch_semantics(monkeypatch):
    """开关经 settings 读取：环境置 false 后 helpers 判定为关（fail-closed）。"""
    from backend.config import settings as S

    # 默认 true
    assert M._cfg_bool("MIDLONG_PYRAMID_RULE_PRETHROTTLE", True) is True
    monkeypatch.setattr(S, "MIDLONG_PYRAMID_RULE_PRETHROTTLE", False, raising=False)
    assert M._cfg_bool("MIDLONG_PYRAMID_RULE_PRETHROTTLE", True) is False


def test_call_site_is_before_throttle_early_return():
    """源码守卫：直通调用必须位于「LLM维度节流中」早退**之前**，否则修了等于没修。"""
    import inspect

    src = inspect.getsource(M.manage_position)
    i_call = src.find("_pyramid_rule_prethrottle_ready(sym, position, market_summary)")
    i_throttle = src.find("LLM维度节流中")
    assert i_call >= 0, "调用点不存在"
    assert i_throttle >= 0, "找不到节流早退日志（源码结构变了，守卫需更新）"
    assert i_call < i_throttle, "直通调用仍在节流早退之后（修复失效）"
