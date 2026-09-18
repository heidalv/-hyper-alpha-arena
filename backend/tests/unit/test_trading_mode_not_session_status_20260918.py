# -*- coding: utf-8 -*-
"""轮67 P0-3 回归：交易模式（paper/live）不得被会话状态（running/defensive/paused）顶替。

## 事故

`analyst_system_cycle.py` 与 `tier_parallel_executor.py` 都写了 `mode = session.status`，
并把该值当**交易模式**往下传：

    analyst_system_cycle.mode
      ├─ host.execute_master_decisions(..., mode)
      │    └─ master_execution.py:1720,1921  if mode == "live":   ← 永不可达
      │         · live 平仓改走 paper_engine.close_position（交易所仓位没减）
      │         · LIVE_DAILY_OPEN_CAP 唯一检查点死在该分支内
      │         · resolve_relief(mode="paper") 写死 → live 阈值被按试单期放宽
      └─ maintain_mlto_theses_for_session(..., mode=...)

    tier_parallel_executor.mode
      └─ analyst_system.run_full_analysis(mode=...)
           └─ dual_agent_coordinator.coordinate(mode=...) / master.synthesize(mode=...)

兄弟闸门（`full_auto/execution_gates.py:82`、`tp_sl_gates.py:78`、
`lock_strength_service.py:73,203,228`、`portfolio_budget.py:215,384`、
`midlong_helpers.py:353,459`）全部在做 `mode == "live"` / `mode == "paper"` 判定 ——
收到 `"running"` 时会落到「既非 live 也非 paper」的第三态。

`mlto_cycle.py:176-179` 早就对该值做了防御性重推，本测试锁死统一口径。
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.full_auto.analyst_system_cycle import resolve_trading_mode


def _sess(**kw):
    s = MagicMock()
    s.status = kw.get("status", "running")
    s.trading_mode = kw.get("trading_mode", "paper")
    return s


# ══════════════════════════════════════════════════════════════════════
# 1. 决策：必须是 trading_mode，绝不是 status
# ══════════════════════════════════════════════════════════════════════

def test_returns_trading_mode_not_status():
    assert resolve_trading_mode(_sess(status="running", trading_mode="paper")) == "paper"
    assert resolve_trading_mode(_sess(status="running", trading_mode="live")) == "live"


def test_never_returns_a_session_status_value():
    """核心不变式：任何 status 取值都不得成为返回值。"""
    for st in ("running", "defensive", "paused", "stopped", "error"):
        out = resolve_trading_mode(_sess(status=st, trading_mode="live"))
        assert out == "live", (st, out)
        assert out not in ("running", "defensive", "paused", "stopped", "error")


def test_status_is_ignored_entirely():
    """即便 status 恰好叫 live/paper，也只认 trading_mode。"""
    s = MagicMock()
    s.status = "live"
    s.trading_mode = "paper"
    assert resolve_trading_mode(s) == "paper"


# ══════════════════════════════════════════════════════════════════════
# 2. 容错：默认保守为 paper
# ══════════════════════════════════════════════════════════════════════

def test_defaults_to_paper_when_missing_or_empty():
    s = MagicMock()
    s.status = "running"
    del s.trading_mode          # 属性缺失
    assert resolve_trading_mode(s) == "paper"
    assert resolve_trading_mode(_sess(trading_mode="")) == "paper"
    assert resolve_trading_mode(_sess(trading_mode=None)) == "paper"


def test_defaults_to_paper_for_unknown_values():
    """未知值不得透传（透传会把第三态带给下游闸门）。"""
    for bad in ("RUNNING", "live_mode", "real", "prod", "  ", "unknown"):
        out = resolve_trading_mode(_sess(trading_mode=bad))
        assert out == "paper", (bad, out)


def test_normalizes_case_and_whitespace():
    assert resolve_trading_mode(_sess(trading_mode="LIVE")) == "live"
    assert resolve_trading_mode(_sess(trading_mode="  Live  ")) == "live"
    assert resolve_trading_mode(_sess(trading_mode=" PAPER ")) == "paper"


# ══════════════════════════════════════════════════════════════════════
# 3. 调用点：不得再出现 mode = session.status
# ══════════════════════════════════════════════════════════════════════

def test_no_callsite_assigns_status_to_mode():
    """源码级守卫：两个已知站点（及未来新增）都不得把 status 赋给 mode。"""
    import io
    import re
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    targets = [
        'backend/services/full_auto/analyst_system_cycle.py',
        'backend/services/tier_parallel_executor.py',
    ]
    pat = re.compile(r'^\s*mode\s*=\s*session\.status\s*$')
    for rel in targets:
        path = os.path.join(root, rel)
        with io.open(path, encoding='utf-8') as f:
            for i, line in enumerate(f, 1):
                assert not pat.match(line), f'{rel}:{i} 又把 session.status 赋给 mode 了: {line.strip()}'
