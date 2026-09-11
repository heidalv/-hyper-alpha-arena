"""D11 实盘补扫链路层级还原测试（2026-09-02 因子系统闭环修复）。

原病症：``_do_live_outcome_backfill`` 把 ``tier`` 硬编码为 ``"mid"``、
``trade_nature`` 留空。由于 unified_learning 的分层依据是"nature 优先、tier 兜底"，
所有实盘补扫样本都落进 mid 桶 —— 短线实盘在分层统计里完全消失，中线统计被短线
交易稀释。

本测试锁定两件事：真实层级要还原得出来；还原不出来时必须留空并标记 unknown，
不许再回落成 "mid"（那是把污染写死进统计）。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.services.learning_loop_service import LearningLoopService


class _Log:
    """AIDecisionLog 的最小替身，只带层级还原用到的两个字段。"""

    def __init__(self, decision_snapshot=None, reason=""):
        self.decision_snapshot = decision_snapshot
        self.reason = reason


_resolve = LearningLoopService._resolve_tier_nature


# ── 1. 结构化快照（最可靠来源）─────────────────────────────────────

@pytest.mark.parametrize("nature,expect_tier", [
    ("scalp", "short"),
    ("intraday", "short"),
    ("swing", "mid"),
    ("trend_follow", "long"),
    ("trend", "long"),
    ("position", "long"),
])
def test_resolve_from_snapshot_nature(nature, expect_tier):
    """decision_snapshot.trade_nature 应能还原出正确 tier。"""
    log = _Log(decision_snapshot=json.dumps({"trade_nature": nature}))
    tier, got_nature = _resolve(log)
    assert got_nature == nature
    assert tier == expect_tier


def test_snapshot_explicit_tier_wins_over_inference():
    """快照里已有显式 tier 时直接采用，不必由 nature 反推。"""
    log = _Log(decision_snapshot=json.dumps(
        {"trade_nature": "scalp", "tier": "short"}))
    assert _resolve(log) == ("short", "scalp")


def test_scalp_is_not_recorded_as_mid():
    """核心回归：短线实盘绝不能再被记成 mid。"""
    log = _Log(decision_snapshot=json.dumps({"trade_nature": "scalp"}))
    tier, _ = _resolve(log)
    assert tier == "short"
    assert tier != "mid", "scalp 记成 mid 正是 D11 要修的污染"


# ── 2. reason 文本兜底 ────────────────────────────────────────────

def test_resolve_from_reason_text():
    """无结构化快照时，从 reason 兜底（LiveExecutor 写 unified_executor: <nature>）。"""
    assert _resolve(_Log(reason="unified_executor: scalp")) == ("short", "scalp")
    assert _resolve(_Log(reason="unified_executor: swing")) == ("mid", "swing")


def test_reason_prefers_trend_follow_over_substring():
    """trend_follow 必须先于 position/swing 匹配，避免被短词抢先误判。"""
    tier, nature = _resolve(_Log(reason="unified_executor: trend_follow"))
    assert (tier, nature) == ("long", "trend_follow")


# ── 3. 未知时诚实留空（不得回落 mid）──────────────────────────────

def test_unknown_returns_blank_not_mid():
    """判不出层级时返回空串 —— 下游会归入 unknown 桶，而非污染 mid。"""
    for log in (
        _Log(),
        _Log(decision_snapshot="这不是 JSON，是 qaa 兜底写的纯文本推理"),
        _Log(decision_snapshot=json.dumps({"confidence": 0.7})),
        _Log(reason="no tier hint here"),
    ):
        tier, nature = _resolve(log)
        assert tier == "", f"未知层级必须留空，实际={tier!r}"
        assert nature == ""


def test_invalid_tier_value_is_rejected():
    """快照里的非法 tier 值不得直接透传给下游。"""
    log = _Log(decision_snapshot=json.dumps({"tier": "medium-term"}))
    assert _resolve(log)[0] == ""


def test_malformed_snapshot_does_not_raise():
    """快照字段类型异常时不得抛异常（补扫链路单笔失败会毒化整批）。"""
    for bad in (json.dumps([1, 2, 3]), json.dumps("str"), "{", b"\x00"):
        assert _resolve(_Log(decision_snapshot=bad)) == ("", "")


# ── 4. 接线契约（防回归）──────────────────────────────────────────

def test_backfill_no_longer_hardcodes_mid():
    """补扫链路必须调用还原函数，且不再出现 tier="mid" 硬编码。"""
    path = os.path.join(os.path.dirname(__file__), "..", "..",
                        "services", "learning_loop_service.py")
    with open(path, encoding="utf-8") as fh:
        code = [ln for ln in fh if not ln.strip().startswith("#")]
    src = "".join(code)

    assert "_resolve_tier_nature(log)" in src, "补扫链路未调用层级还原"
    assert 'tier="mid"' not in src, (
        'tier="mid" 硬编码仍在 —— 短线实盘会继续被记成中线'
    )
    assert '"tier_unknown"' in src, "缺少 tier_unknown 可观测标记"
