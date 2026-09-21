# -*- coding: utf-8 -*-
"""[轮154 2026-09-21] entry-zone promotion 不得再把模型的"不开"改写成"开"。

## 现场（reports/_轮154_UNI连续开多_根因_20260921.md）
UNI mid 论题 982fc260（驱动 02:30/03:10/07:04 三笔满档多头，每次止损 −$12.5）：

```
落库字段 : direction=long   recommend_open=1   accepted=1   llm_conviction=47
模型原始 : {"direction":"neutral","confidence":0.35,"recommend_open":false,…}
论题摘要 : "…UNI 近 14d 同向多头连续三笔 -1.77%~-1.96% 止损…故不追高。现价 8.649 相对入场区
            8.55-8.75 基本贴沿，但短周期动能未止跌、追入风险回报不佳，选择观望等 4h 企稳信号。"
```

`brain.py::promote_open_if_in_zone` 以"现价 8.649 落入它自己给的 8.55-8.75"为由把
`recommend_open` 改成 true（**无置信门槛、无开关、无日志、无落库**），且它跑在
`open_gate` 之前 ⇒ 「底线 4：尊重 LLM recommend_open=False」结构上不可能命中。

## 本轮口径（用户批准 A + B + E）
- 总开关 `MIDLONG_ENTRY_ZONE_PROMOTE`，**默认 false**（关着就完全不动 final）；
- 打开时：`recommend_open is False`（模型明确表态）⇒ **一律不改写**，只对"未表态"生效；
- 硬前提：辩论主周期 `reject` 或 位置闸口径"高位追多"命中 ⇒ 不 promotion；
- 每次 promotion / 每次被前置拦下都写日志，并把 `_promoted_open` 落进论题事件。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.mlto import brain as B  # noqa: E402


def _final(**over):
    base = {
        "direction": "long",
        "confidence": 0.35,
        "recommend_open": False,
        "entry_zone": {"low": 8.55, "high": 8.75},
        "invalidation": {"price": 8.454, "condition": "1d 收盘跌破 8.454 作废"},
        "thesis_summary": "现价 8.649 相对入场区 8.55-8.75 基本贴沿，选择观望等 4h 企稳信号。",
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("MIDLONG_ENTRY_ZONE_PROMOTE", "MIDLONG_AGGRESSIVE_ENTRY",
              "MIDLONG_AGGRESSIVE_MIN_CONF", "MIDLONG_CHASE_TOL_PCT"):
        monkeypatch.delenv(k, raising=False)
    # 默认屏蔽两条硬前提的真实数据源（下面按需 monkeypatch）
    monkeypatch.setattr(B, "_promotion_blocked_by_analysis", lambda *a, **k: (False, ""))
    yield


def test_default_is_off_and_never_overrides():
    """默认关：模型明确 false + 现价就在入场区内 ⇒ 一个字都不许改。"""
    out = B.promote_open_if_in_zone(_final(), last_price=8.649, symbol="UNI", tier="mid",
                                    market_block={"symbol": "UNI", "price": 8.649})
    assert out["recommend_open"] is False, "默认关闭时 promotion 不得改写 recommend_open"
    assert "_promoted_open" not in out


def test_switch_on_still_respects_explicit_false(monkeypatch):
    """开关打开也只对"模型未表态"生效 —— 明确的 false 是模型的判断。"""
    monkeypatch.setenv("MIDLONG_ENTRY_ZONE_PROMOTE", "true")
    out = B.promote_open_if_in_zone(_final(), last_price=8.649, symbol="UNI", tier="mid")
    assert out["recommend_open"] is False, "模型明确 false 被改写了（本轮的根因）"
    assert "_promoted_open" not in out


def test_switch_on_promotes_when_model_silent(monkeypatch):
    """模型没表态（缺字段）时，开关打开才允许按入场区提为 true，并打标记 + 日志。"""
    monkeypatch.setenv("MIDLONG_ENTRY_ZONE_PROMOTE", "true")
    fin = _final()
    fin.pop("recommend_open")
    out = B.promote_open_if_in_zone(fin, last_price=8.649, symbol="UNI", tier="mid")
    assert out["recommend_open"] is True
    assert out["_promoted_open"] == "entry_zone_touched"


def test_chase_tolerance_also_marked(monkeypatch):
    monkeypatch.setenv("MIDLONG_ENTRY_ZONE_PROMOTE", "true")
    fin = _final()
    fin.pop("recommend_open")
    out = B.promote_open_if_in_zone(fin, last_price=8.80, symbol="UNI", tier="mid", atr_pct=2.0)
    assert out["recommend_open"] is True
    assert out["_promoted_open"] == "entry_zone_chase_tol"


def test_hard_precondition_debate_reject_blocks(monkeypatch):
    """方案 B：辩论主周期 reject ⇒ 禁止 promotion（即使开关打开、模型未表态）。"""
    monkeypatch.setenv("MIDLONG_ENTRY_ZONE_PROMOTE", "true")
    monkeypatch.setattr(B, "_promotion_blocked_by_analysis",
                        lambda *a, **k: (True, "debate_primary_reject:intraday(risk_min=0.25)"))
    fin = _final()
    fin.pop("recommend_open")
    out = B.promote_open_if_in_zone(fin, last_price=8.649, symbol="UNI", tier="mid")
    assert out.get("recommend_open") is not True, "辩论 reject 时仍被 promotion 放行"
    assert "debate_primary_reject" in str(out.get("_promoted_open_blocked"))


def test_hard_precondition_location_blocks(monkeypatch):
    """方案 B：位置闸口径"高位追多" ⇒ 禁止 promotion。"""
    monkeypatch.setenv("MIDLONG_ENTRY_ZONE_PROMOTE", "true")
    monkeypatch.setattr(B, "_promotion_blocked_by_analysis",
                        lambda *a, **k: (True, "location:高位追多：24h区间分位64%≥60%"))
    fin = _final()
    fin.pop("recommend_open")
    out = B.promote_open_if_in_zone(fin, last_price=8.649, symbol="UNI", tier="mid")
    assert out.get("recommend_open") is not True
    assert "location" in str(out.get("_promoted_open_blocked"))


def test_high_position_long_uses_same_threshold(monkeypatch):
    """`high_position_long` 复用位置闸的阈值 key（单一来源），并夹取分位 ∈[0,100]。"""
    from backend.services.full_auto import midlong_location_gate as LG

    blk = {"symbol": "UNI", "price": 8.649, "range_24h_high": 8.80, "range_24h_low": 8.10}
    monkeypatch.delenv("MIDLONG_LOCATION_MAX_PCT_LONG", raising=False)
    hit, why = LG.high_position_long(blk, tier="mid")
    assert isinstance(hit, bool) and "分位" in why
    monkeypatch.setenv("MIDLONG_LOCATION_MAX_PCT_LONG", "10")
    hit2, _why2 = LG.high_position_long(blk, tier="mid")
    assert hit2 is True, "阈值降到 10% 后同一分位必须判为高位"
    monkeypatch.setenv("MIDLONG_LOCATION_MAX_PCT_LONG", "99")
    hit3, _why3 = LG.high_position_long(blk, tier="mid")
    assert hit3 is False


def test_promotion_marker_persisted_in_event_payload():
    """方案 E：`_promoted_open` 必须出现在论题事件载荷里（此前全库无人消费）。"""
    src = (ROOT / "backend/services/mlto/brain.py").read_text(encoding="utf-8-sig")
    assert '"promoted_open": final.get("_promoted_open")' in src
    assert '"model_recommend_open": _model_recommend_open' in src
