# -*- coding: utf-8 -*-
"""[轮153 2026-09-21] `pick_strategy_donor` 跨层回退（用户批准方案 A）。

## 背景
轮153 归档 21 条 `tpl_long*`（长线模板族残余）后，long 层 active 母本 10 → 2。
`pick_strategy_donor(db, account_id, tier)` 只认
`status=="active" AND timeframe_tier==tier`；若返回 None，`provision_ai_strategy`
立刻退化成日志「无同层 active 母本可克隆」并拒建 ⇒ 走该路径的长线提案被**静默拒绝**。

## 改法
本层无母本时，按 ("mid","long","short") 顺序借用同账户其他层的 active 母本。
语义成立：克隆的是**配置面**（杠杆/仓位/因子口径/触发参数），
`build_cloned_strategy` **不继承 genome**（母本 genome 内嵌原 symbol 的模板绑定），
新策略的 `timeframe_tier` 由调用方按目标层写入。
回滚：`MIDLONG_DONOR_CROSS_TIER_FALLBACK=false`。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.full_auto import midlong_helpers as m  # noqa: E402


class _TierAwareDB:
    """只认 `timeframe_tier == X` 这一条筛选条件的替身；记录被问过的层。"""

    def __init__(self, donor_tier: str | None):
        self.donor_tier = donor_tier
        self.seen_tiers: list[str] = []
        self._tier: str | None = None

    def query(self, *a, **k):
        return self

    def filter(self, *criteria):
        for c in criteria:
            left = getattr(c, "left", None)
            right = getattr(c, "right", None)
            if getattr(left, "key", None) == "timeframe_tier" and right is not None:
                self._tier = getattr(right, "value", None)
                self.seen_tiers.append(self._tier)
        return self

    def order_by(self, *a, **k):
        return self

    def first(self):
        if self.donor_tier is not None and self._tier == self.donor_tier:
            return type("D", (), {"strategy_id": f"auto_{self._tier}_donor",
                                  "timeframe_tier": self._tier})()
        return None


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("MIDLONG_DONOR_CROSS_TIER_FALLBACK", raising=False)
    yield


def test_same_tier_donor_wins_and_no_fallback():
    """本层有母本 ⇒ 直接用，且**不得**去问其他层（避免无谓查询）。"""
    db = _TierAwareDB("long")
    got = m.pick_strategy_donor(db, 14, "long")
    assert got is not None and got.timeframe_tier == "long"
    assert db.seen_tiers == ["long"], f"本层命中后仍查了其他层：{db.seen_tiers}"


def test_cross_tier_fallback_borrows_other_tier():
    """long 层无母本 ⇒ 借用 mid 层母本（轮153 的真实场景）。"""
    db = _TierAwareDB("mid")
    got = m.pick_strategy_donor(db, 14, "long")
    assert got is not None, "跨层回退未生效：长线会退化成『无同层 active 母本可克隆』"
    assert got.timeframe_tier == "mid"
    assert "long" in db.seen_tiers and "mid" in db.seen_tiers


def test_fallback_disabled_returns_none(monkeypatch):
    """回滚开关：关掉后必须回到"本层没有就拒绝"的旧行为。"""
    monkeypatch.setenv("MIDLONG_DONOR_CROSS_TIER_FALLBACK", "false")
    db = _TierAwareDB("mid")
    assert m.pick_strategy_donor(db, 14, "long") is None
    assert db.seen_tiers == ["long"], "关闭回退后不得再查其他层"


def test_all_tiers_empty_returns_none():
    db = _TierAwareDB(None)
    assert m.pick_strategy_donor(db, 14, "long") is None


def test_provision_uses_fallback_end_to_end(monkeypatch):
    """端到端：long 层无同层母本时，provision_ai_strategy 仍应建出 long 策略。"""
    monkeypatch.setattr(m, "ai_pool_symbols", lambda *a, **k: {"DOT"})
    monkeypatch.setattr(m, "count_ai_provisioned_today", lambda db: 0)
    real_pick = m.pick_strategy_donor

    class _DB(_TierAwareDB):
        def __init__(self):
            super().__init__("mid")
            self.added = []

        def add(self, row):
            self.added.append(row)

        def flush(self):
            pass

    db = _DB()
    monkeypatch.setattr(m, "pick_strategy_donor",
                        lambda d, acct, tier: real_pick(d, acct, tier))
    s = type("S", (), {"trading_mode": "paper", "paper_account_id": 14, "account_id": 14,
                       "session_id": "fa_test", "active_strategy_ids": []})()
    row = m.provision_ai_strategy(db, s, "DOT", "long", account_id=14)
    assert row is not None, "跨层回退后 long 层仍建不出策略"
    assert row.timeframe_tier == "long", "新策略必须落在目标层 long，而不是母本所在的 mid"
