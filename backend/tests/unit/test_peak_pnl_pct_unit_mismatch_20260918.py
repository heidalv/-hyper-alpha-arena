# -*- coding: utf-8 -*-
"""轮69 P1 回归：`peak_pnl_pct` 在 ExitSM 边界的单位错配（分数当百分数用）。

## 事故

`position_exit_orchestrator.py` 构造 `PositionContext` 时：

    _pnl_pct      = state.peak_pnl_pct                              # 分数 0.05 = +5%
    _current_pnl  = (mark - entry) / entry * 100                    # 百分数 5.0
    PositionContext(unrealized_pnl_pct=_current_pnl, peak_pnl_pct=_pnl_pct)

同一对象里两个字段差 100×。而 `PositionContext` 的契约是**百分数**
（`exit_types.py:85-86` 注明「浮盈亏百分比」；消费方 `tier_exit_strategies.py:274`
显式 `/100` 转回分数、`:263` 与 `0.3`（=0.3%）比较）。

后果（`tier_exit_strategies.py:132`）：

    drawdown = ctx.peak_pnl_pct - ctx.unrealized_pnl_pct
             = 0.05 - 5.0 = 负数（恒为负）

→ 追踪/回撤出场永不触发；`:263` 的 ranging regime 例外恒被取用，
使「只上移不放宽」的 SL 收紧对几乎所有仓位被抑制。

## 上游尺度（本测试同时锁死）

`state.peak_pnl_pct` 是分数，由两处佐证：

- `nature_staged_tp.pnl_pct()` 返回 `(cur-entry)/entry`（分数），
  并据此反推峰值价 `entry*(1+pct)`（`nature_staged_tp.py:129`）；
- `position_exit_orchestrator.py:118` 把它与 `paper_positions.peak_pnl_pct`
  取 max（该列实测 0.0301 = 3.01%，同为分数）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.exit.exit_types import PositionContext
from backend.services.exit.tier_exit_strategies import TierExitStrategy
from backend.services.nature_staged_tp import pnl_pct as nature_pnl_pct


# ══════════════════════════════════════════════════════════════════════
# 1. 上游确实是分数
# ══════════════════════════════════════════════════════════════════════

def test_nature_pnl_pct_returns_fraction():
    """+5% 涨幅 → 0.05（分数），不是 5.0。"""
    assert nature_pnl_pct(100.0, 105.0, "long") == 0.05 or \
        abs(nature_pnl_pct(100.0, 105.0, "long") - 0.05) < 1e-9
    assert nature_pnl_pct(100.0, 95.0, "short") > 0


def test_peak_price_reconstruction_assumes_fraction():
    """`entry*(1+pct)` 只有在 pct 是分数时才得到正确峰值价。"""
    entry, pct = 742.5192725941735, 0.030134644231798874
    peak = entry * (1 + pct)
    assert abs(peak - 764.9) < 1.0, peak          # 分数解释 → 合理峰值价
    wrong = entry * (1 + pct * 100)               # 百分数解释 → 荒谬
    assert wrong > entry * 4


# ══════════════════════════════════════════════════════════════════════
# 2. PositionContext 契约是百分数
# ══════════════════════════════════════════════════════════════════════

def test_position_context_documents_percent():
    """:274 的 `/100` 是契约的可执行证据。"""
    import io
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    src = io.open(os.path.join(root, 'backend/services/exit/tier_exit_strategies.py'), encoding='utf-8').read()
    assert '/ 100.0' in src, 'tier_exit_strategies 必须把百分数 /100 转回分数'
    assert '0.3' in src, 'ranging 阈值 0.3 是按百分数写的（0.3%）'


# ══════════════════════════════════════════════════════════════════════
# 3. 修复后的 drawdown 必须为正（原为恒负）
# ══════════════════════════════════════════════════════════════════════

def _ctx(peak_pct_field, unreal_pct):
    return PositionContext(
        position_id=1, symbol="BNB", tier="long", side="long",
        entry_price=100.0, current_price=100.0 + unreal_pct, quantity=1.0,
        leverage=1.0, sl_price=None, tp_price=None,
        unrealized_pnl_pct=unreal_pct,
        peak_pnl_pct=peak_pct_field,
        hold_seconds=3600, atr_pct=1.0,
    )


def test_drawdown_is_positive_with_correct_units():
    """峰值 +8%、当前 +3% → drawdown = 5（百分点）。"""
    ctx = _ctx(8.0, 3.0)
    assert ctx.peak_pnl_pct - ctx.unrealized_pnl_pct == 5.0


def test_old_buggy_units_give_negative_drawdown():
    """回归证据：分数 + 百分数混用 → drawdown 恒负（这正是被修掉的行为）。"""
    buggy = _ctx(0.08, 3.0)          # 峰值按分数传（旧行为）
    assert buggy.peak_pnl_pct - buggy.unrealized_pnl_pct < 0


def test_orchestrator_converts_peak_to_percent():
    """源码级守卫：orchestrator 必须把 state.peak_pnl_pct ×100 后再传给 context。"""
    import io
    import re
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    src = io.open(os.path.join(root, 'backend/services/position_exit_orchestrator.py'), encoding='utf-8').read()
    assert re.search(r'_pnl_pct\s*=\s*float\(state\.peak_pnl_pct[^)]*\)\s*\*\s*100', src), \
        'orchestrator 必须把 peak 分数 ×100 转成百分数再放进 PositionContext'


# ══════════════════════════════════════════════════════════════════════
# 4. 修复后追踪出场具备触发条件
# ══════════════════════════════════════════════════════════════════════

def test_trailing_exit_can_now_fire():
    """修复后 drawdown 可达阈值；修复前永不可达。

    注：`TierExitStrategy` 是抽象基类（`evaluate` 未实现），此处只验证
    `tier_exit_strategies.py:132` 那一步算术是否成立，不实例化策略。
    """
    good = _ctx(10.0, 2.0)       # 峰值 +10%、当前 +2%
    bad = _ctx(0.10, 2.0)        # 旧行为：峰值按分数传
    dd_good = (good.peak_pnl_pct or 0) - (good.unrealized_pnl_pct or 0)
    dd_bad = (bad.peak_pnl_pct or 0) - (bad.unrealized_pnl_pct or 0)
    assert dd_good == 8.0
    assert dd_bad < 0
    # 以百分点计的 trailing_dist 都可被 dd_good 越过，dd_bad 永远越不过
    assert dd_good > 0.1
    assert dd_bad < 0.1
