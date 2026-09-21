# -*- coding: utf-8 -*-
"""[调研轮16 2026-09-16] AI 候选**策略按需供给**契约测试。

## 被锁定的缺陷

AI 候选池（mid=APT/DOT、long=FET/APT）与策略宇宙（9 个固定币
BTC/ETH/SOL/BNB/VIRTUAL/ASTER/XPL/UNI/XRP）**零交集**，而 `proposal_execution.py:93`
要求 `(primary_symbol, timeframe_tier, status='active')` 的 `ai_strategies` 行 ⇒
AI 选中的币 100% 死于 `eval_false:no_active_strategy`（实测 DOT 近 7 天 25 次全因此），
且建策略链（`auto_create_strategy` / `bg_create_strategy`）全仓无调用点。

## 锁定语义（本文件）

1. 开关/上限/实盘许可三重约束都生效，且**只**对 AI 候选池内的 symbol 放行；
2. 新策略克隆同账户同层母本的杠杆/仓位/因子口径，**不继承 genome**（跨 symbol 无意义）；
3. 每日上限读失败 ⇒ **按超限处理**（不建），异常 ⇒ 保持历史行为（拒绝）；
4. **接线护栏**：`resolve_independent_strategy` 尾部必须真的调用它（只定义不接线 = 没修）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.full_auto import midlong_helpers as m  # noqa: E402


# ── 替身 ────────────────────────────────────────────────────────────
class _FakeDB:
    def __init__(self):
        self.added = []
        self.flushed = 0

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flushed += 1

    # resolve_independent_strategy 早期分支需要的最小查询面（一律"查不到"）
    def query(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def first(self):
        return None

    def all(self):
        return []


def _session(mode="paper", acct=14, ids=None):
    return SimpleNamespace(
        trading_mode=mode, paper_account_id=acct, account_id=acct,
        session_id="fa_test", active_strategy_ids=list(ids or []),
    )


def _donor(**over):
    base = dict(
        strategy_id="auto_donor01", auto_mode="full_auto", max_position_size=0.12,
        stop_loss_pct=0.017, take_profit_pct=0.061, max_leverage=20.0,
        default_leverage=12.0, leverage_mode="isolated",
        enabled_factors=["f1", "f2"], factor_weights={"f1": 0.5},
        trigger_mode="hybrid", trigger_interval=300, signal_pool_ids=[1, 2],
        genome={"source_template_id": "tpl_x"},
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for k in ("MIDLONG_AI_AUTOCREATE_STRATEGY", "MIDLONG_AI_AUTOCREATE_MAX_PER_DAY",
              "MIDLONG_AI_AUTOCREATE_LIVE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(m, "_AI_POOL_CACHE", {"key": "", "ts": 0.0, "syms": frozenset()})
    yield


# ── 1. 开关 / 上限 / 实盘许可 ────────────────────────────────────────
def test_switch_off_returns_none(monkeypatch):
    monkeypatch.setenv("MIDLONG_AI_AUTOCREATE_STRATEGY", "false")
    monkeypatch.setattr(m, "ai_pool_symbols", lambda *a, **k: {"DOT"})
    db, s = _FakeDB(), _session()
    assert m.provision_ai_strategy(db, s, "DOT", "mid", account_id=14) is None
    assert db.added == []


def test_max_per_day_zero_blocks(monkeypatch):
    monkeypatch.setenv("MIDLONG_AI_AUTOCREATE_MAX_PER_DAY", "0")
    monkeypatch.setattr(m, "ai_pool_symbols", lambda *a, **k: {"DOT"})
    db, s = _FakeDB(), _session()
    assert m.provision_ai_strategy(db, s, "DOT", "mid", account_id=14) is None
    assert db.added == []


def test_daily_cap_enforced(monkeypatch):
    monkeypatch.setenv("MIDLONG_AI_AUTOCREATE_MAX_PER_DAY", "3")
    monkeypatch.setattr(m, "ai_pool_symbols", lambda *a, **k: {"DOT"})
    monkeypatch.setattr(m, "count_ai_provisioned_today", lambda db: 3)
    monkeypatch.setattr(m, "pick_strategy_donor", lambda *a, **k: _donor())
    db, s = _FakeDB(), _session()
    assert m.provision_ai_strategy(db, s, "DOT", "mid", account_id=14) is None
    assert db.added == [], "达到上限后不得建策略"


def test_live_session_blocked_by_default(monkeypatch):
    monkeypatch.setattr(m, "ai_pool_symbols", lambda *a, **k: {"DOT"})
    monkeypatch.setattr(m, "count_ai_provisioned_today", lambda db: 0)
    monkeypatch.setattr(m, "pick_strategy_donor", lambda *a, **k: _donor())
    db, s = _FakeDB(), _session(mode="live")
    assert m.provision_ai_strategy(db, s, "DOT", "mid", account_id=188) is None
    assert db.added == [], "实盘默认不按需建策略（实盘从严）"


def test_live_allowed_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("MIDLONG_AI_AUTOCREATE_LIVE", "true")
    monkeypatch.setattr(m, "ai_pool_symbols", lambda *a, **k: {"DOT"})
    monkeypatch.setattr(m, "count_ai_provisioned_today", lambda db: 0)
    monkeypatch.setattr(m, "pick_strategy_donor", lambda *a, **k: _donor())
    db, s = _FakeDB(), _session(mode="live")
    row = m.provision_ai_strategy(db, s, "DOT", "mid", account_id=188)
    assert row is not None and len(db.added) == 1


# ── 2. 只对 AI 候选池内 symbol 放行 ──────────────────────────────────
def test_non_ai_symbol_not_provisioned(monkeypatch):
    monkeypatch.setattr(m, "ai_pool_symbols", lambda *a, **k: {"DOT"})
    monkeypatch.setattr(m, "count_ai_provisioned_today", lambda db: 0)
    monkeypatch.setattr(m, "pick_strategy_donor", lambda *a, **k: _donor())
    db, s = _FakeDB(), _session()
    assert m.provision_ai_strategy(db, s, "APT", "mid", account_id=14) is None
    assert db.added == [], "池外 symbol 一律不动（固定币/其它车道不受影响）"


@pytest.mark.parametrize("tier", ["short", "", "position"])
def test_only_mid_long_tiers(monkeypatch, tier):
    monkeypatch.setattr(m, "ai_pool_symbols", lambda *a, **k: {"DOT"})
    db, s = _FakeDB(), _session()
    assert m.provision_ai_strategy(db, s, "DOT", tier, account_id=14) is None
    assert db.added == []


# ── 3. 克隆语义 ─────────────────────────────────────────────────────
def test_creates_cloned_strategy_with_donor_config(monkeypatch):
    monkeypatch.setattr(m, "ai_pool_symbols", lambda *a, **k: {"DOT"})
    monkeypatch.setattr(m, "count_ai_provisioned_today", lambda db: 0)
    monkeypatch.setattr(m, "pick_strategy_donor", lambda *a, **k: _donor())
    db, s = _FakeDB(), _session()
    row = m.provision_ai_strategy(db, s, "DOT", "mid", account_id=14)
    assert row is not None and db.added == [row] and db.flushed == 1
    assert row.primary_symbol == "DOT" and row.timeframe_tier == "mid"
    assert row.account_id == 14 and row.status == "active"
    assert row.strategy_id.startswith("ai_auto_dotmid")
    # 母本口径（杠杆/仓位/因子）必须一致，否则新车的仓位口径与现役不同
    assert row.max_position_size == pytest.approx(0.12)
    assert row.default_leverage == pytest.approx(12.0)
    assert row.max_leverage == pytest.approx(20.0)
    assert row.enabled_factors == ["f1", "f2"]
    assert row.factor_weights == {"f1": 0.5}
    # genome 不继承（母本 genome 内嵌原 symbol 的模板绑定）
    assert row.genome is None
    assert "不继承 genome" in (row.description or "")
    # 会话立即认得它（否则下一 tick 又要走慢查询）
    assert row.strategy_id in s.active_strategy_ids


def test_no_donor_returns_none(monkeypatch):
    monkeypatch.setattr(m, "ai_pool_symbols", lambda *a, **k: {"DOT"})
    monkeypatch.setattr(m, "count_ai_provisioned_today", lambda db: 0)
    monkeypatch.setattr(m, "pick_strategy_donor", lambda *a, **k: None)
    db, s = _FakeDB(), _session()
    assert m.provision_ai_strategy(db, s, "DOT", "mid", account_id=14) is None
    assert db.added == []


# ── 4. 失败方向：读失败/异常都不得放行 ──────────────────────────────
def test_count_failure_is_fail_closed():
    """计数查询异常 ⇒ 返回超大值（按超限处理），而不是 0（放行）。"""

    class _Boom:
        def query(self, *a, **k):
            raise RuntimeError("db down")

    assert m.count_ai_provisioned_today(_Boom()) >= 10 ** 6


def test_pool_read_failure_returns_empty(monkeypatch):
    """候选池读取异常 ⇒ 空集（不补策略 = 保持历史行为）。"""
    import backend.services.auto_coin_selector as sel

    def _boom(*a, **k):
        raise RuntimeError("selector down")

    monkeypatch.setattr(sel, "get_ai_mid_candidates_for_session", _boom)
    assert m.ai_pool_symbols(_FakeDB(), _session(), "mid", ttl=0.0) == set()


# ── 5. 接线护栏（只定义不接线 = 没修）───────────────────────────────
def test_resolve_calls_provision_at_tail(monkeypatch):
    monkeypatch.setattr(m, "ai_pool_symbols", lambda *a, **k: {"DOT"})
    monkeypatch.setattr(m, "count_ai_provisioned_today", lambda db: 0)
    monkeypatch.setattr(m, "pick_strategy_donor", lambda *a, **k: _donor())
    host = SimpleNamespace(get_trading_account_id=lambda db, s: 14)
    db, s = _FakeDB(), _session()
    row = m.resolve_independent_strategy(db, s, "DOT", "mid", host)
    assert row is not None, "resolve 尾部必须调用按需供给，否则 AI 选币仍 100% 被拒"
    assert row.primary_symbol == "DOT" and len(db.added) == 1


def test_resolve_rejects_when_provision_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("provision exploded")

    monkeypatch.setattr(m, "provision_ai_strategy", _boom)
    host = SimpleNamespace(get_trading_account_id=lambda db, s: 14)
    db, s = _FakeDB(), _session()
    assert m.resolve_independent_strategy(db, s, "DOT", "mid", host) is None, (
        "异常时必须保持历史行为（拒绝），不得变成新的放行口"
    )


# ── 6. 部署凭据 ─────────────────────────────────────────────────────
def test_deployed_env_defaults():
    from dotenv import load_dotenv

    load_dotenv(str(ROOT / ".env"), override=False)
    import os

    assert os.environ.get("MIDLONG_AI_AUTOCREATE_STRATEGY", "").lower() == "true"
    # [轮153 2026-09-21] 3 → 8：调研轮18（2026-09-17「放开容量与出场语义」）已把部署值
    # 提为 8（见 reports/_调研轮18_放开容量与出场语义_20260917.md 第 54 行），
    # 但本断言仍钉着 3 ⇒ 自轮18 起**一直是红的**（陈旧棘轮会掩盖真实回归）。
    # 现已对齐部署值；若要再改容量，请同时改 `.env` 与本行。
    assert int(os.environ.get("MIDLONG_AI_AUTOCREATE_MAX_PER_DAY", "0")) == 8
    assert os.environ.get("MIDLONG_AI_AUTOCREATE_LIVE", "").lower() == "false"
