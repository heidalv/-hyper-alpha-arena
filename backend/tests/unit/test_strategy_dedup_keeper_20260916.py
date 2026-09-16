# -*- coding: utf-8 -*-
"""[调研轮17 2026-09-16] 启动去重的 **keeper 选择** 与 **零 active 检测** 契约测试。

## 被锁定的缺陷

`cleanup_duplicate_strategies` 原实现盲取 `strats[0]`（created_at 最早的一条）当 keeper。
若那条恰好是 `paused`，同组所有 **active** 会被归档 ⇒ 该 (账户, 币, 层) 变成**零 active**，
该 symbol 的提案随即以 `eval_false:no_active_strategy` 静默死掉。

实测（2026-09-16）：`logs/backend.log` 22:26:31
`[FullAuto] 启动去重: 归档 tpl_mid_ra (UNI/mid)，保留 tpl_mid_ra` /
`归档 auto_d23ca (XRP/mid)，保留 tpl_mid_ra`，之后 acct188 的 UNI/XRP 中线只剩
1 条 paused、其余 archived；24h 审计里 UNI 54 行 / XRP 10 行因此被丢弃
（该缺陷只在**会话恢复**时触发，且看起来像"选币没选出来"，排查成本高）。

## 锁定语义

1. keeper 必须优先 `active`（同状态内仍取最早的）；
2. 组内全是 paused 时保持旧行为（取最早）；
3. 被归档的 sid 必须从会话 `active_strategy_ids` 移除；
4. 会话 symbol 在某层**零 active** 时必须留下 WARNING（只告警不改状态）。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database.models import AIStrategy, FullAutoSession  # noqa: E402
from backend.services.full_auto import paper_session_helpers as psh  # noqa: E402


# ── 谓词感知的最小假 DB ─────────────────────────────────────────────
def _match(row, crit) -> bool:
    try:
        key = getattr(crit.left, "key", None)
        op = getattr(crit.operator, "__name__", "")
        right = crit.right
        val = getattr(right, "value", right)
    except Exception:
        return True
    if key is None:
        return True
    cur = getattr(row, key, None)
    if op in ("in_op", "in_"):
        try:
            return cur in list(val)
        except TypeError:
            return True
    if op == "eq":
        return cur == val
    return True


class _Q:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *crits):
        rows = self.rows
        for c in crits:
            rows = [r for r in rows if _match(r, c)]
        return _Q(rows)

    def order_by(self, *a, **k):
        return self

    def all(self):
        return list(self.rows)


class _FakeDB:
    def __init__(self, strategies, sessions):
        self.strategies = strategies
        self.sessions = sessions

    def query(self, model):
        if model is AIStrategy:
            return _Q(self.strategies)
        if model is FullAutoSession:
            return _Q(self.sessions)
        return _Q([])


class _Host:
    def __init__(self):
        self.commits = []

    def safe_commit(self, db, label):
        self.commits.append(label)


def _strat(sid, sym, tier, status, created, acct=188, genome=None):
    return SimpleNamespace(
        strategy_id=sid, primary_symbol=sym, timeframe_tier=tier, status=status,
        created_at=created, account_id=acct, updated_at=created,
        genome=genome if genome is not None else {},
    )


# ── 1. keeper 必须优先 active ───────────────────────────────────────
def test_keeper_prefers_active_over_older_paused(caplog):
    """老 paused + 新 active ⇒ 归档 paused，保留 active（旧实现会反过来）。"""
    paused_old = _strat("tpl_old_paused", "UNI", "mid", "paused", "2026-09-15 10:00:00")
    active_new = _strat("auto_new_active", "UNI", "mid", "active", "2026-09-16 10:00:00")
    sess = SimpleNamespace(session_id="s1", status="running", symbols=["UNI"],
                           auto_coin_symbols=[], active_strategy_ids=["auto_new_active"],
                           paper_account_id=188, account_id=188)
    db, host = _FakeDB([paused_old, active_new], [sess]), _Host()

    with caplog.at_level(logging.WARNING):
        psh.cleanup_duplicate_strategies(db, host)

    assert active_new.status == "active", "keeper 必须是 active 的那条"
    assert paused_old.status == "archived"
    assert "auto_new_active" in sess.active_strategy_ids
    assert host.commits, "去重后必须提交"


def test_keeper_oldest_when_all_paused(caplog):
    """组内全是 paused ⇒ 保持旧行为（取最早），不得把状态改成 active。"""
    older = _strat("tpl_a", "XRP", "mid", "paused", "2026-09-15 09:00:00")
    newer = _strat("tpl_b", "XRP", "mid", "paused", "2026-09-16 09:00:00")
    sess = SimpleNamespace(session_id="s1", status="running", symbols=["XRP"],
                           auto_coin_symbols=[], active_strategy_ids=[],
                           paper_account_id=188, account_id=188)
    db, host = _FakeDB([older, newer], [sess]), _Host()

    with caplog.at_level(logging.WARNING):
        psh.cleanup_duplicate_strategies(db, host)

    assert older.status == "paused" and newer.status == "archived"


def test_active_only_group_keeps_single(caplog):
    """两条 active ⇒ 保留最早，另一条归档（原有语义不变）。"""
    a = _strat("auto_a", "DOT", "mid", "active", "2026-09-15 08:00:00", acct=14)
    b = _strat("auto_b", "DOT", "mid", "active", "2026-09-16 08:00:00", acct=14)
    sess = SimpleNamespace(session_id="s2", status="running", symbols=["DOT"],
                           auto_coin_symbols=[], active_strategy_ids=["auto_a", "auto_b"],
                           paper_account_id=14, account_id=14)
    db, host = _FakeDB([a, b], [sess]), _Host()

    with caplog.at_level(logging.WARNING):
        psh.cleanup_duplicate_strategies(db, host)

    assert a.status == "active" and b.status == "archived"
    assert sess.active_strategy_ids == ["auto_a"], "被归档的 sid 必须从会话移除"


# ── 2. 零 active 检测（只告警）──────────────────────────────────────
def test_detects_session_symbol_without_any_active(caplog):
    sess = SimpleNamespace(session_id="fa_live", status="running", symbols=["UNI", "BTC"],
                           auto_coin_symbols=[], active_strategy_ids=[],
                           paper_account_id=188, account_id=188)
    btc = _strat("auto_btc", "BTC", "mid", "active", "2026-09-15 08:00:00")
    uni_paused = _strat("tpl_uni", "UNI", "mid", "paused", "2026-09-15 08:00:00")
    db, host = _FakeDB([btc, uni_paused], [sess]), _Host()

    with caplog.at_level(logging.WARNING):
        psh.cleanup_duplicate_strategies(db, host)

    txt = "\n".join(r.getMessage() for r in caplog.records)
    assert "零 active" in txt and "fa_live:UNI" in txt, txt
    assert "BTC" not in txt.split("零 active")[1][:120], "有 active 的 symbol 不该被列出"
    assert uni_paused.status == "paused", "检测只告警，不得修改状态"


def test_no_warning_when_symbols_have_active(caplog):
    sess = SimpleNamespace(session_id="fa_paper", status="running", symbols=["BTC"],
                           auto_coin_symbols=[], active_strategy_ids=["auto_btc"],
                           paper_account_id=14, account_id=14)
    btc = _strat("auto_btc", "BTC", "mid", "active", "2026-09-15 08:00:00", acct=14)
    db, host = _FakeDB([btc], [sess]), _Host()

    with caplog.at_level(logging.WARNING):
        psh.cleanup_duplicate_strategies(db, host)

    txt = "\n".join(r.getMessage() for r in caplog.records)
    assert "零 active" not in txt, txt


# ══════════════════════════════════════════════════════════════════════
# 同族第二个机制：`cap_paper_active_strategies` 的**分层守卫**
#
# 实测（2026-09-16 23:11）`[FullAuto] paper cap SOL: kept 5 active, paused 2`
# 之后 acct14 的 SOL **中线**零 active，而 SOL 中线正是当时在跑的车道
# ⇒ 与去重 keeper 同因：暂停例程不保护"某 (币, 层) 的最后一条 active"。
# ══════════════════════════════════════════════════════════════════════
class _CapHost:
    def __init__(self):
        self.paused = []

    def paper_loss_locks_disabled(self, session):
        return True

    def record_strategy_pause(self, sid, reason, by=""):
        self.paused.append((sid, reason, by))


def _by_id(sid, sym, tier, sid_id):
    s = _strat(sid, sym, tier, "active", "2026-09-15 08:00:00", acct=14)
    s.id = sid_id
    return s


def _cap_session(ids):
    return SimpleNamespace(session_id="fa_paper", status="running", symbols=["SOL"],
                           auto_coin_symbols=[], active_strategy_ids=list(ids),
                           paper_account_id=14, account_id=14)


def test_cap_never_pauses_last_active_of_a_tier(monkeypatch, caplog):
    """cap 之外那条恰是某层唯一 active ⇒ 必须跳过（否则该层静默开不出单）。"""
    # 5 条 long（id 大，排前面被保留）+ 2 条 mid（id 小，落在 cap 之外）
    strats = [_by_id(f"auto_l{i}", "SOL", "long", 100 + i) for i in range(5)]
    strats += [_by_id("auto_m1", "SOL", "mid", 11), _by_id("auto_m2", "SOL", "mid", 10)]
    ids = [s.strategy_id for s in strats]
    sess = _cap_session(ids)
    db, host = _FakeDB(strats, [sess]), _CapHost()

    with caplog.at_level(logging.WARNING):
        psh.cap_paper_active_strategies(db, sess, sess.active_strategy_ids, host,
                                        max_per_symbol=5)

    assert [s.status for s in strats[5:]] == ["paused", "active"], (
        "两条 mid 里只能暂停一条，另一条是该层最后的 active，必须保留"
    )
    assert "auto_m2" in sess.active_strategy_ids and "auto_m1" not in sess.active_strategy_ids
    assert [p[0] for p in host.paused] == ["auto_m1"], host.paused
    txt = "\n".join(r.getMessage() for r in caplog.records)
    assert "最后一条 active" in txt and "auto_m2" in txt


def test_cap_pauses_normally_when_tier_has_slack(monkeypatch):
    """正常情形不受影响：同一层有多条时照旧暂停 cap 之外的那些。"""
    strats = [_by_id(f"auto_l{i}", "SOL", "long", 200 + i) for i in range(4)]
    strats += [_by_id("auto_m1", "SOL", "mid", 12), _by_id("auto_m2", "SOL", "mid", 11),
               _by_id("auto_m3", "SOL", "mid", 10)]
    sess = _cap_session([s.strategy_id for s in strats])
    db, host = _FakeDB(strats, [sess]), _CapHost()

    psh.cap_paper_active_strategies(db, sess, sess.active_strategy_ids, host,
                                    max_per_symbol=5)

    # cap=5：保留 4 long + 1 mid，暂停 2 条 mid（该层仍有 1 条 active）
    assert [s.status for s in strats[4:]] == ["active", "paused", "paused"]
    assert len(host.paused) == 2


# ══════════════════════════════════════════════════════════════════════
# 同族第三个机制：`health_check_cycle` 模板策略**复用守卫**
#
# 模板榜首随行情变化 ⇒ 旧逻辑每 30-40 分钟为同一 symbol/层新建一条，而启动去重
# 只保留最早的一条 ⇒ 新建的必然被归档（纯 churn）。归档那一刻正是"某层 active
# 被清空 ⇒ 提案静默死于 no_active_strategy"的时刻，故守卫放在**新建侧**。
# ══════════════════════════════════════════════════════════════════════
def test_template_reuse_guard_truth_table():
    from backend.services.full_auto.health_check_cycle import (
        template_strategy_creation_decision as d,
    )

    assert d(has_template=False, has_active_tier=False) == (True, "create")
    assert d(has_template=True, has_active_tier=False) == (False, "same_template")
    assert d(has_template=False, has_active_tier=True) == (False, "reuse_active")
    assert d(has_template=True, has_active_tier=True) == (False, "reuse_active")
    # 回滚位：关闭守卫 ⇒ 回到"同模板不存在就建"的旧行为
    assert d(has_template=False, has_active_tier=True, reuse_guard=False) == (True, "create")
    assert d(has_template=True, has_active_tier=True, reuse_guard=False) == (False, "same_template")


def test_reuse_guard_is_wired():
    """接线护栏：判定函数必须真的被调用，且计数/日志都在。"""
    import inspect

    from backend.services.full_auto import health_check_cycle as hcc

    src = inspect.getsource(hcc)
    assert "HEALTH_TEMPLATE_REUSE_GUARD" in src, "守卫开关未接线"
    idx = src.index("_has_active_tier = any(")
    window = src[idx:idx + 1600]
    assert "template_strategy_creation_decision(" in window, "判定函数未在新建点被调用"
    assert "_template_reuse_skipped += 1" in window, "复用跳过未计数（不可观测）"
    assert "复用既有 active 跳过" in src, "汇总日志未暴露跳过次数"
