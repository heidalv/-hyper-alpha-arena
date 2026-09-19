# -*- coding: utf-8 -*-
"""轮116 车道资金**重新分配** + 阀口接线（2026-09-19）。

## 用户反馈

> 「短线没有，自己分配比例，阀口都没动过，这个就不对了。马上进行深度的调研和维修，重新分配」

## 调研结论（`reports/_probe116*.txt`）

1. **车道模型已经是两条，预算层还停在三车道**：
   轮63（2026-09-18，用户确认）写死 —— 交易只有 `intraday`（日内，tier=mid）与
   `trend`（长线趋势，tier=long），且"`scalp` 不再是独立车道：短线车道已停
   （2026-09-17），存量 scalp 仓位归入 intraday"。但预算表里 `short` 仍有 0.25
   （tier）+ 0.35（layer），而这条车道实测 30 天 853 笔净 **−201.55**、胜率 0.393、
   笔均 −0.122% ⇒ 配额**从来没被回收/重分配**。
2. **同一算式四处各自重算**：`budget_service.get_tier_cap`（**零调用者**）、
   `full_auto/master_execution`（真的在拦单）、`tier_parallel_executor`、
   `api/full_auto_routes`（展示）⇒ 改 .env 无法保证一致。
3. **静默兜底**：`get_tier_cap` 用 `else: tier_l = "mid"`，任何未知标签都拿到 mid 的
   配额，而 `tier_to_layer()` 对它们返回 None ⇒ "不占任何层额度、却有一个看起来
   存在的 tier 上限"的幽灵配额；`NATURE_TO_LAYER['intraday']='scalp'` 还把日内车道
   的钱记进已停的 scalp 池。

## 本文件钉住的契约

* 两车道口径 + 三桶配额（short 退役=0 / mid 0.35 / long 0.50），总分配 0.85 不变；
* 层额度必须 ≥ 该层各 tier 配额之和（否则 tier 阀口形同虚设）；
* 短线（scalp）**开不出来**（cap 0 / 乘子 0 / can_open False）；
* tier 阀口真的接线（唯一实现 + 层/tier 取更严 + 未知 fail-closed）。
"""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _src(rel: str) -> str:
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8").read()


# ══════════════════════════════════════════════════════════════════════
# ① 重新分配的数字与不变量
# ══════════════════════════════════════════════════════════════════════

def test_allocations_match_the_two_lane_model():
    from backend.config.settings import TIER_BUDGET_ALLOCATION as A, TIER_MAX_MARGIN_PCT as M
    assert A == {"short": 0.0, "mid": 0.35, "long": 0.50}, A
    assert M == {"short": 0.0, "mid": 0.35, "long": 0.50}, M
    assert sum(A.values()) == pytest.approx(0.85), "总分配保持 0.85（安全边际 0.15 不变）"


def test_layer_split_matches_and_does_not_override_tiers():
    from backend.config.settings import TIER_BUDGET_ALLOCATION as A
    from backend.services.budget_service import budget_service as BS
    la = BS.layer_allocations
    assert la == {"scalp": 0.0, "trend": 0.85}, la
    # 层额度必须 ≥ 该层各 tier 配额之和，否则层上限会盖住 tier 阀口
    _trend_tiers = A["mid"] + A["long"]
    assert la["trend"] + 1e-9 >= _trend_tiers, (la["trend"], _trend_tiers)


def test_retired_short_lane_is_zeroed():
    s = _src("backend/config/settings.py")
    assert 'TIER_SHORT_BUDGET", "0.15"' in s, "代码默认值仍在（.env 覆盖层才是本轮重新分配）"


def test_env_file_carries_the_reallocation():
    env = io.open(os.path.join(_ROOT, ".env"), encoding="utf-8",
                  errors="replace").read()
    for kv in ("TIER_SHORT_BUDGET=0.0", "TIER_SHORT_MAX_MARGIN=0.0",
               "TIER_MID_BUDGET=0.35", "TIER_LONG_BUDGET=0.50",
               "LAYER_BUDGET_SCALP=0.0", "LAYER_BUDGET_TREND=0.85"):
        assert kv in env, f".env 缺 {kv}（本轮重新分配的契约）"


# ══════════════════════════════════════════════════════════════════════
# ② 两车道口径的映射（未知 ≠ mid）
# ══════════════════════════════════════════════════════════════════════

def test_tier_aliases_follow_lane_semantics():
    from backend.config.lane_semantics import LANE_SPECS, LANE_INTRADAY, LANE_TREND
    from backend.services.budget_service import normalize_tier
    # lane_semantics 是车道真源：日内车道 tier=mid、趋势 tier=long
    assert LANE_SPECS[LANE_INTRADAY].tier == "mid"
    assert LANE_SPECS[LANE_TREND].tier == "long"
    for raw, want in (("mid", "mid"), ("swing", "mid"), ("intraday", "mid"),
                      ("midlong", "mid"), ("long", "long"), ("trend", "long"),
                      ("trend_follow", "long"), ("position", "long"),
                      ("scalp", "short"), ("short", "short")):
        assert normalize_tier(raw) == want, (raw, normalize_tier(raw))
    # 未指定 = 默认车道（沿用全库 `or "mid"` 约定），不是 None
    assert normalize_tier("") == "mid"


def test_unknown_tier_is_fail_closed_not_mid():
    from backend.services.budget_service import normalize_tier, budget_service as BS
    for junk in ("1h", "15m", "scalp_directional"):
        assert normalize_tier(junk) is None, junk
        assert BS.get_tier_cap(junk, 10000.0) == 0.0, junk
        assert BS.scale_factor_for_layer(junk, 10000.0) == 0.0, junk


def test_research_lanes_are_unbudgeted_not_blocked():
    """研究车道 ≠ 未知标签：它**刻意**不在预算体系内，不得被缩仓/拦单。

    原语义（`tier_to_layer is None → 1.0`）对研究车道是对的；轮116 只把
    "刻意不预算"（research/pair_research/arb）与"没人认识的脏标签"分开：
    前者 1.0（不缩仓），后者 0.0（fail-closed）。
    """
    from backend.services.budget_service import (
        is_research_tier, normalize_tier, budget_service as BS,
    )
    for r in ("research", "pair_research", "arb", "arbitrage"):
        assert is_research_tier(r) is True
        assert normalize_tier(r) is None, "研究车道没有预算桶"
        assert BS.get_tier_cap(r, 10000.0) == 0.0, "没有配额"
        assert BS.scale_factor_for_layer(r, 10000.0) == 1.0, "但也不因其缩仓"
    assert is_research_tier("1h") is False


def test_unknown_tier_warns_only_once(monkeypatch, caplog):
    import logging
    from backend.services import budget_service as M
    M._TIER_UNKNOWN_WARNED.discard("zzz_unknown")
    with caplog.at_level(logging.WARNING):
        M.normalize_tier("zzz_unknown")
        M.normalize_tier("zzz_unknown")
        M.normalize_tier("zzz_unknown")
    hits = [r for r in caplog.records if "未知 tier" in r.getMessage()]
    assert len(hits) == 1, [r.getMessage() for r in hits]


def test_intraday_money_no_longer_goes_to_the_dead_scalp_pool():
    from backend.services.budget_service import NATURE_TO_LAYER, tier_for_position

    class _P:
        def __init__(self, nature, tier):
            self.trade_nature, self.timeframe_tier, self.margin = nature, tier, 100.0

    assert NATURE_TO_LAYER["intraday"] == "trend"
    assert NATURE_TO_LAYER["scalp"] == "trend"
    # 存量 intraday 仓位存的是 tier='short'（引擎存储档位），但钱要算在日内(mid)桶
    assert tier_for_position(_P("intraday", "short")) == "mid"
    assert tier_for_position(_P("swing", "mid")) == "mid"
    assert tier_for_position(_P("trend_follow", "long")) == "long"
    assert tier_for_position(_P("scalp", "short")) == "mid"
    assert tier_for_position(_P("research", "research")) is None


# ══════════════════════════════════════════════════════════════════════
# ③ 阀口真的接线：唯一实现 + 层/tier 取更严 + 0 配额 = 拦
# ══════════════════════════════════════════════════════════════════════

def test_single_source_of_truth_for_tier_caps():
    for rel in ("backend/services/full_auto/master_execution.py",
                "backend/services/tier_parallel_executor.py",
                "backend/api/full_auto_routes.py"):
        src = _src(rel)
        assert "get_tier_cap(" in src, f"{rel} 未走唯一实现"
        assert "TIER_BUDGET_ALLOCATION.get(" not in src.replace(
            "TIER_BUDGET_ALLOCATION)  # ", ""), f"{rel} 仍在自行重算 tier 配额"


def test_layer_budget_takes_the_stricter_of_layer_and_tier(monkeypatch):
    from backend.services.budget_service import budget_service as BS
    monkeypatch.setattr(BS, "get_used_margin", lambda *a, **k: 0.0)
    monkeypatch.setattr(BS, "get_layer_cap", lambda *a, **k: 1000.0)
    monkeypatch.setattr(BS, "get_tier_cap", lambda *a, **k: 300.0)
    monkeypatch.setattr(BS, "get_tier_used_margin", lambda *a, **k: 250.0)
    assert BS.get_layer_budget("trend", 1.0, "paper", 14, tier="mid") == pytest.approx(50.0)
    # 不给 tier → 只看层（向后兼容）
    assert BS.get_layer_budget("trend", 1.0, "paper", 14) == pytest.approx(1000.0)


def test_zero_allocation_blocks_instead_of_meaning_no_limit(monkeypatch):
    from backend.services.budget_service import budget_service as BS
    monkeypatch.setattr(BS, "get_layer_cap", lambda *a, **k: 0.0)
    monkeypatch.setattr(BS, "get_tier_cap", lambda *a, **k: 0.0)
    assert BS.scale_factor_for_layer("short", 4676.0, "paper", 14) == 0.0, \
        "配额 0 必须等价于『不许多开』（原实现 `cap<=0 → 1.0` 等价于不限仓）"
    # 算不出权益（冷启动）时不制造新的硬拦
    assert BS.scale_factor_for_layer("short", 0.0, "paper", 14) == 1.0


def test_retired_lane_cannot_open():
    from backend.services.budget_service import budget_service as BS
    eq = 4676.0
    assert BS.can_open("short", 10.0, eq, "paper", 14) is False
    assert BS.get_tier_cap("short", eq) == 0.0
    assert BS.scale_factor_for_layer("short", eq, "paper", 14) == 0.0


def test_tier_utilization_is_exposed():
    from backend.services.budget_service import budget_service as BS
    u = BS.get_tier_utilization(4676.0, "paper", 14)
    assert set(u) == {"short", "mid", "long"}
    assert u["short"]["retired"] is True and u["short"]["cap"] == 0.0
    assert u["mid"]["alloc"] == pytest.approx(0.35)
    assert u["long"]["alloc"] == pytest.approx(0.50)
