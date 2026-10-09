# -*- coding: utf-8 -*-
"""[P2 2026-09-21] M3 应用链契约测试：`action=keep -> adjust` 的四道闸。

# 背景

P1 阶段 M3 的 `action` **硬编码为 `keep`**（只看不改），刻意零风险。
P2 打开它，但必须证明"打开后 LLM 也不能乱改"。本文件锁的就是这个"不能"。

# 四道闸（顺序不可换）

  ① **G1 实测最优守卫** —— 越界建议连写都不写
  ② **作用域闸** —— LLM 按币输出，注册表参数是全车道一份；
     多个币对同一键给不同值时**无法同时满足** ⇒ 整键拒绝
  ③ **[F327] 需重启闸** —— `needs_restart(key)` 为真时**拒绝**，
     而不是"写了但没生效"（后者正是 F189/F280/F287/F292 那条静默偏差老路）
  ④ **G2 限额** —— 单次 ≤1 币、单币 ≤2 次/时、全局 ≥10 分钟；被拒的**不占配额**

# 为什么测试要 monkeypatch G3

`apply_and_verify` 会**真的等 150 秒**读心跳核对（F251 指纹周期 60s + 心跳 15s）。
测试不能等，所以把"验证结果"注入。**但被替换的只有 I/O 边界**
（写注册表 / 读心跳 / 等待），四道闸的**判定逻辑一条都没替** ——
否则测的就只是 mock 自己。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from datetime import datetime

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

M3_PATH = ROOT / "scripts" / "m3_llm_diagnose.py"


def _load_m3():
    """加载 M3 模块（它 import 时只读常量，不触网、不连库）。"""
    spec = importlib.util.spec_from_file_location("m3_under_test", M3_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def m3():
    return _load_m3()


@pytest.fixture()
def g3(monkeypatch):
    """注入 G3 的 I/O 边界；返回记录器。"""
    import g3_apply_verify as G3

    calls = []

    def fake_apply_and_verify(key, value, *, timeout_s=150.0, dry_run=False):
        calls.append({"key": key, "value": value, "timeout_s": timeout_s})
        return {"key": key, "old": 0.5, "new": value, "applied": True,
                "verified": True, "rolled_back": False, "note": "stub",
                "effective": value}

    monkeypatch.setattr(G3, "apply_and_verify", fake_apply_and_verify)
    monkeypatch.setattr(G3, "needs_restart", lambda k: False)
    return calls


@pytest.fixture()
def g2spy(monkeypatch):
    """记录 G2 实际收到/记录的条目。"""
    import g2_rate_limit as G2

    seen = {"check": [], "adjust": []}
    monkeypatch.setattr(G2, "check_call",
                        lambda items, now=None: (seen["check"].append(list(items)) or (items, [])))
    monkeypatch.setattr(G2, "append_adjustment",
                        lambda **kw: seen["adjust"].append(kw))
    return seen


def _v(sym, key, val, allowed=True, reason="ok"):
    return {"symbol": sym, "key": key, "value": val,
            "allowed": allowed, "reason": reason}


def _now():
    return datetime(2026, 9, 21, 17, 0).astimezone()


# ── ① G1 拒绝的建议绝不写入 ──────────────────────────────────────────────
@pytest.mark.unit
def test_g1_rejected_never_written(m3, g3, g2spy):
    """被 G1 拒绝的建议连 G2 都不该进（连写都不写）。"""
    rec = m3.apply_guarded([_v("SOL", "spread_mult", 0.9, allowed=False,
                               reason="超出实测最优")],
                           {"llm_out": {}}, now=_now())
    assert g3 == [], "G1 拒绝的建议不得调用 G3"
    assert g2spy["check"] == [], "G1 拒绝的建议不得进 G2"
    assert rec["action"] == "keep" and rec["applied"] is False


# ── ② 作用域闸：同键多值必须整键拒绝 ────────────────────────────────────
@pytest.mark.unit
def test_conflicting_per_symbol_values_rejected(m3, g3, g2spy):
    """**核心**：SOL 想 0.5、XRP 想 0.8 ⇒ 注册表只有一份 ⇒ 拒绝，不许"谁先来谁赢"。

    否则行为取决于建议的排列顺序 —— 一个随字典序变化的隐蔽 bug。
    """
    verdicts = [_v("SOL", "spread_mult", 0.5), _v("XRP", "spread_mult", 0.8)]
    rec = m3.apply_guarded(verdicts, {"llm_out": {}}, now=_now())
    d = rec["apply_detail"]
    assert len(d["conflicts"]) == 1
    assert d["conflicts"][0]["key"] == "spread_mult"
    assert d["conflicts"][0]["rule"] == "scope"
    assert g3 == [], "冲突键不得写入"
    assert g2spy["check"] == [], "冲突键不得进 G2"
    assert rec["action"] == "keep"


@pytest.mark.unit
def test_same_value_from_many_symbols_is_not_a_conflict(m3, g3, g2spy):
    """同键**同值**不算冲突（两个币都认为该改到 0.5 ⇒ 没有分歧）。"""
    verdicts = [_v("SOL", "spread_mult", 0.5), _v("XRP", "spread_mult", 0.5)]
    rec = m3.apply_guarded(verdicts, {"llm_out": {}}, now=_now())
    assert rec["apply_detail"]["conflicts"] == []
    assert [c["key"] for c in g3] == ["spread_mult"]


# ── ③ 需重启闸：拒绝，而不是静默不生效 ──────────────────────────────────
@pytest.mark.unit
def test_restart_bound_key_is_refused_not_silently_noop(m3, g3, g2spy, monkeypatch):
    """**核心**：`needs_restart` 为真 ⇒ 拒绝并记录理由。

    若放行，`apply_and_verify` 会写注册表但不生效 ⇒ **静默偏差**：
    注册表显示新值、实盘跑旧值，后续所有归因建立在错前提上。
    """
    import g3_apply_verify as G3

    monkeypatch.setattr(G3, "needs_restart", lambda k: k == "k_inv")
    rec = m3.apply_guarded([_v("SOL", "k_inv", 1.4)], {"llm_out": {}}, now=_now())
    d = rec["apply_detail"]
    assert len(d["restart_bound"]) == 1
    assert d["restart_bound"][0]["key"] == "k_inv"
    assert "须重启" in d["restart_bound"][0]["reason"] or \
           "不生效" in d["restart_bound"][0]["reason"]
    assert g3 == [], "需重启的键不得写入注册表"
    assert rec["action"] == "keep" and rec["applied"] is False


# ── ④ G2 限额 ───────────────────────────────────────────────────────────
@pytest.mark.unit
def test_g2_rejection_blocks_write_and_logs_unapplied(m3, g3, monkeypatch):
    """被 G2 拒 ⇒ 不写注册表；且日志里 `applied=False`（**不占配额**）。"""
    import g2_rate_limit as G2

    logged = []
    monkeypatch.setattr(G2, "check_call",
                        lambda items, now=None: ([], [{**items[0], "rule": "L3",
                                                       "reason": "距上次 2 分钟"}]))
    monkeypatch.setattr(G2, "append_adjustment", lambda **kw: logged.append(kw))

    rec = m3.apply_guarded([_v("SOL", "take_profit_bp", 14.0)], {"llm_out": {}},
                           now=_now())
    assert g3 == [], "限额拒绝不得写入"
    assert rec["action"] == "keep" and rec["applied"] is False
    assert len(logged) == 1 and logged[0]["applied"] is False
    assert logged[0]["rule"] == "L3"


@pytest.mark.unit
def test_verified_apply_sets_action_adjust(m3, g3, g2spy):
    """验证通过 ⇒ `action=adjust`、`applied=True`（这是唯一会改参数的分支）。"""
    rec = m3.apply_guarded([_v("SOL", "take_profit_bp", 13.0)], {"llm_out": {}},
                           now=_now())
    assert rec["action"] == "adjust" and rec["applied"] is True
    assert [c["key"] for c in g3] == ["take_profit_bp"]
    assert len(g2spy["adjust"]) == 1 and g2spy["adjust"][0]["applied"] is True


@pytest.mark.unit
def test_failed_verification_does_not_count_as_adjust(m3, monkeypatch, g2spy):
    """**核心**：写了但没验证生效 ⇒ `action` 回到 `keep`。

    这是"证明生效"这条纪律的落点：G3 会自己回滚，M3 也不许声称改成功。
    """
    import g3_apply_verify as G3

    monkeypatch.setattr(G3, "needs_restart", lambda k: False)
    monkeypatch.setattr(G3, "apply_and_verify",
                        lambda key, value, **kw: {
                            "key": key, "old": 12.0, "new": value, "applied": True,
                            "verified": False, "rolled_back": True,
                            "note": "150s 内未生效 ⇒ 已回滚", "effective": 12.0})
    rec = m3.apply_guarded([_v("SOL", "take_profit_bp", 13.0)], {"llm_out": {}},
                           now=_now())
    assert rec["action"] == "keep", "未验证生效不得声称 adjust"
    assert rec["applied"] is False
    assert len(rec["apply_detail"]["verify_failed"]) == 1


# ── 默认路径必须逐字不变 ────────────────────────────────────────────────
@pytest.mark.unit
def test_default_run_once_never_applies(m3, g3, g2spy):
    """`do_apply=False`（默认）⇒ 绝不调用 G2/G3。

    P1 的全部价值在于"只看不改也跑对"。默认路径一旦偷改参数，
    P1 攒下的所有基线都作废。
    """
    import inspect

    src = inspect.getsource(m3.run_once)
    assert "do_apply" in src, "run_once 必须有 do_apply 开关"
    # 应用链必须在 `if not do_apply: return` **之后**
    i_guard = src.index("if not do_apply:")
    i_apply = src.index("apply_guarded(")
    assert i_apply > i_guard, "apply_guarded 必须在 do_apply 守卫之后"
    # 且守卫里必须真的 return（否则会掉进应用链）
    seg = src[i_guard:src.index("apply_guarded(")]
    assert "return rec" in seg, "do_apply 守卫必须 return，否则默认路径会改参数"
