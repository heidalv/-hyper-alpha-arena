# -*- coding: utf-8 -*-
"""轮69 P1 回归：`SubPositionManager.reconcile()` 对空头与「交易所已平」失明。

## 事故

旧实现唯一的门槛是 `if exchange_qty > 0:`，而调用方
（`full_auto/health_check_cycle.py:1207-1210`）传的是**带符号**数量：

    _exchange_qty_by_sym[_psym] = _sz if _side == "long" else -_sz

于是：

- **空头**（传入负数）整段被跳过，永远 `matched: True`；
- **交易所已平但内部仍持仓**（传入 0）同样被跳过 —— 漏报的正是最该报的背离。

对账是唯一能发现「漏成交 / 平仓没减仓 / 交易所被手动平掉」的检查，
对主力车道失明等于这层保护不存在。
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.sub_position_manager import SubPositionManager


def _mgr(subs):
    """构造一个 get_sub_positions 被替换成固定子仓列表的 manager。"""
    m = SubPositionManager()
    m.get_sub_positions = MagicMock(return_value=subs)   # type: ignore[assignment]
    return m


def _sub(size, side, nature="trend_follow"):
    return {"size": size, "side": side, "trade_nature": nature}


# ══════════════════════════════════════════════════════════════════════
# 1. 空头必须真的参与对账（旧实现直接跳过）
# ══════════════════════════════════════════════════════════════════════

def test_short_matched_when_quantities_agree():
    """空头数量一致 → matched。旧实现是「没比较就说 matched」，这里是真比较过。"""
    m = _mgr([_sub(2.0, "short")])
    r = m.reconcile(None, 1, "BTC", exchange_qty=-2.0, exchange_side="short")
    assert r["matched"] is True
    assert "mismatch_reason" not in r


def test_short_mismatch_is_now_detected():
    """空头数量不符 → 必须报不一致。旧实现恒 matched: True。"""
    m = _mgr([_sub(2.0, "short")])
    r = m.reconcile(None, 1, "BTC", exchange_qty=-5.0, exchange_side="short")
    assert r["matched"] is False
    assert r["mismatch_reason"] == "数量不符"


def test_short_direction_mismatch_detected():
    """内部空头、交易所多头 → 方向相反。"""
    m = _mgr([_sub(2.0, "short")])
    r = m.reconcile(None, 1, "BTC", exchange_qty=+2.0, exchange_side="long")
    assert r["matched"] is False
    assert r["mismatch_reason"] == "方向相反"
    assert r["exchange_derived_side"] == "long"


# ══════════════════════════════════════════════════════════════════════
# 2. 「交易所已平 / 内部仍持」必须报出来
# ══════════════════════════════════════════════════════════════════════

def test_exchange_flat_but_internal_open_is_flagged():
    """交易所 0、内部有仓 → 背离（旧实现直接跳过）。"""
    m = _mgr([_sub(1.5, "long")])
    r = m.reconcile(None, 1, "BTC", exchange_qty=0.0)
    assert r["matched"] is False
    assert r["mismatch_reason"] == "交易所无仓位但内部仍持仓"


def test_exchange_open_but_internal_missing_is_flagged():
    """交易所有仓、内部无记录 → 漏记开仓。"""
    m = _mgr([])
    r = m.reconcile(None, 1, "BTC", exchange_qty=3.0)
    assert r["matched"] is False
    assert r["mismatch_reason"] == "交易所持仓但内部无子仓记录"


def test_both_flat_is_matched():
    """双方都空 → 一致，不应误报。"""
    m = _mgr([])
    r = m.reconcile(None, 1, "BTC", exchange_qty=0.0)
    assert r["matched"] is True


# ══════════════════════════════════════════════════════════════════════
# 3. 内部方向自相矛盾
# ══════════════════════════════════════════════════════════════════════

def test_mixed_internal_sides_flagged():
    """同币既有空头又有多头子仓 → 内部方向不一致。"""
    m = _mgr([_sub(1.0, "long"), _sub(1.0, "short")])
    r = m.reconcile(None, 1, "BTC", exchange_qty=2.0)
    assert r["matched"] is False
    assert r["mismatch_reason"] == "内部子仓方向不一致"
    assert r["internal_sides"] == ["long", "short"]


# ══════════════════════════════════════════════════════════════════════
# 4. 多头回归（原有行为不得回退）
# ══════════════════════════════════════════════════════════════════════

def test_long_matched_unchanged():
    m = _mgr([_sub(1.0, "long"), _sub(1.0, "long")])
    r = m.reconcile(None, 1, "BTC", exchange_qty=2.0, exchange_side="long")
    assert r["matched"] is True


def test_long_tolerance_still_1pct():
    """1% 容差内算一致；超出才算不符（保持原阈值）。"""
    m = _mgr([_sub(1.0, "long")])
    assert m.reconcile(None, 1, "BTC", exchange_qty=1.005)["matched"] is True
    assert m.reconcile(None, 1, "BTC", exchange_qty=1.05)["matched"] is False


def test_none_quantity_treated_as_flat():
    """`exchange_qty=None` 不应抛异常（调用方可能取不到值）。"""
    m = _mgr([_sub(1.0, "long")])
    r = m.reconcile(None, 1, "BTC", exchange_qty=None)
    assert r["matched"] is False
    assert r["mismatch_reason"] == "交易所无仓位但内部仍持仓"


def test_source_guard_no_positive_only_gate():
    """源码级守卫：不得再写成 `if exchange_qty > 0:`（空头/已平会被跳过）。"""
    import io
    import re
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    src = io.open(os.path.join(root, 'backend/services/sub_position_manager.py'), encoding='utf-8').read()
    m = re.search(r'def reconcile\(.*?(?=\n    def |\Z)', src, re.S)
    body = m.group(0)
    # 去掉三引号 docstring（修复说明里会引用旧写法）与注释行，只看可执行代码
    body = re.sub(r'""".*?"""', '', body, flags=re.S)
    body = '\n'.join(l for l in body.splitlines() if not l.lstrip().startswith('#'))
    assert not re.search(r'if\s+exchange_qty\s*>\s*0\s*:', body), \
        'reconcile 不得再用 `exchange_qty > 0` 作唯一门槛（会漏掉空头与已平）'
