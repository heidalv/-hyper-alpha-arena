# -*- coding: utf-8 -*-
"""[2026-10-03] 第三批补齐护栏：①账本接真实交易 ②重置≠回撤（风控误判） ③方向反转 P0。

## 三个实测根因

### ①「决策 → 血缘账本」断链
`/api/learning/events` 长期只有 **4 条**，来源全是 `selftest`/`signal_backtest`/`backtest_loop`
（最新 2026-07-07）—— 真实开/平仓从未入账，学习链路看不到生产。修复：`services/trade_learning_ledger.py`
在 `place_order`（stage=observe）与 `close_position`（stage=feedback）写入，同一持仓共享
确定性 lineage `lin_trade_{account}_{position_id}`。**实测：4 → 5（observe）→ 7（feedback，
status=rejected, pnl=-0.0002），lineage `lin_trade_259_4867` 前后一致。**

### ② 账户重置被当成 −90% 组合回撤 ⇒ 全局"只平不开"24h
`risk_engine._tick_drawdown` 用进程内峰值缓存 `_peaks` 做基线，而**该缓存在账户重置/改金额时不清零**。
实测：监控在权益 ≈5000 时记下峰值 → 用户把金额改成 500 → 下一次 tick 判「组合回撤 90.0% ≥ 30%」→
`TradingState=REDUCING`（TTL 24h，`history.note="drawdown:组合回撤 90.0% ≥ 30%"`）⇒
**所有新开仓在 RiskEngine v3 被 `trading_state_reducing` 拦掉**（连已修好的中线门禁也过不了）。
修复：读 `paper_balances.last_reset_at`，若重置晚于峰值观测时刻 ⇒ 该账户峰值基线重置为当前权益。
**实测：巡检输出 `baseline_reset: true`，无账户触发 reducing，状态保持 active；随后开仓从被拒 → 200。**

### ③ 方向反转 P0：`side="long"` 落成 short 仓
`paper_trading_engine` 内 `pos_side = "long" if order.side == "buy" else "short"` **只认 buy/sell**，
而 REST `/api/paper/order` 传 `long/short` ⇒ 实测「请求 long → 订单行 side=long → 持仓行 side=short」，
方向完全反转（且平仓按 long 去平会 404「无可平持仓」）。
修复：新增模块级 `_normalize_position_side()`（buy/long→long，sell/short→short，未知告警），
两处调用点（一方向反转净额、持仓方向映射）统一走它。
**实测：开仓 200 且 position side=long；平仓 404 → 200。**
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC_LEDGER = (ROOT / "backend/services/trade_learning_ledger.py").read_text(encoding="utf-8")
SRC_ENGINE = (ROOT / "backend/services/paper_trading_engine.py").read_text(encoding="utf-8")
SRC_RISK = (ROOT / "backend/services/risk/risk_engine.py").read_text(encoding="utf-8")


# ─────────────── ① 账本接真实交易 ───────────────

def test_trade_ledger_module_contract():
    assert "def record_trade_open(" in SRC_LEDGER and "def record_trade_close(" in SRC_LEDGER
    assert 'LEARNING_LEDGER_TRADE_EVENTS", "true"' in SRC_LEDGER, "开关默认应为开（可回滚）"
    assert 'stage="observe"' not in SRC_LEDGER or '"observe"' in SRC_LEDGER
    # 开仓 observe / 平仓 feedback，且盈利 passed、亏损 rejected
    assert '"observe", "pending"' in SRC_LEDGER
    assert '"feedback", "passed" if float(pnl or 0) >= 0 else "rejected"' in SRC_LEDGER
    # 同一持仓共享确定性血缘
    assert 'f"lin_trade_{int(account_id)}_{int(position_id or 0)}"' in SRC_LEDGER
    assert "fail-open" in SRC_LEDGER or "绝不" in SRC_LEDGER


def test_engine_hooks_both_events_fail_open():
    assert "record_trade_open(" in SRC_ENGINE and "record_trade_close(" in SRC_ENGINE
    # 两个钩子都必须包在 try/except 里（账本失败不能影响成交）
    for fn in ("record_trade_open(", "record_trade_close("):
        idx = SRC_ENGINE.index(fn)
        seg = SRC_ENGINE[max(0, idx - 200): idx + 900]
        assert "except Exception" in seg, f"{fn} 必须 fail-open"


# ─────────────── ② 重置 ≠ 回撤 ───────────────

def test_drawdown_ignores_user_reset():
    seg = SRC_RISK.split("def _tick_drawdown(")[1].split("def _ledger_accounts(")[0]
    assert "last_reset_at" in seg, "回撤巡检必须读取重置水位"
    assert "_reset_since_peak(" in seg
    assert 'RISK_DD_IGNORE_RESET", True' in seg, "修复开关默认应开（可回滚）"
    assert "reset_peak(aid)" in seg, "命中重置必须重置该账户峰值基线"
    helper = SRC_RISK.split("def _reset_since_peak(")[1].split("def reset_peak(")[0]
    assert "reset_ts >= float(peak_ts)" in helper
    # 峰值观测时刻必须被记录，否则判定失效
    assert "self._peak_at[int(account_id)] = time.time()" in SRC_RISK


# ─────────────── ③ 方向归一化（P0） ───────────────

def test_position_side_normalization():
    assert "def _normalize_position_side(" in SRC_ENGINE
    helper = SRC_ENGINE.split("def _normalize_position_side(")[1].split("def _as_float_or_none(")[0]
    assert '"buy", "long", "l", "b"' in helper and '"sell", "short", "s"' in helper
    # 旧的"只认 buy"写法不得回归
    assert '_pos_side = "long" if side == "buy" else "short"' not in SRC_ENGINE
    assert 'pos_side = "long" if order.side == "buy" else "short"' not in SRC_ENGINE
    assert SRC_ENGINE.count("_normalize_position_side(") >= 3, "两处调用点都要走归一化"
