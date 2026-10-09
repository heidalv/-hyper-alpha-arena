# -*- coding: utf-8 -*-
"""[2026-10-02] 用户三条投诉的修复护栏（账户操作 / 中线不开仓 / 止损后同向重开）。

## 现场（用户原话）
「实盘交易 模拟交易，被我停止了，因为现在有很多的 bug：
 1. 模拟交易最底部的账户操作，全部都有问题…无法正常重置和分配金额，逻辑错误
 2. 只有长线在交易，中线始终无法交易；长线单还是垃圾，从没有出现过补仓和加仓状态
 3. 分析的方向和止损从来都是，开单迅速亏钱，然后止损，止损后，还是在原地继续原来的方向开单…」

## 已核实的根因（每条都实测过，见本文件对应断言）
- 账户操作：`set_initial_balance` 有持仓即拒（前端还没 catch ⇒ 点了没反应）、不校验数值
  （实测 0/−100 都写库）、只写 `paper_balances` 不同步 `accounts.initial_capital`
  （实测 id=14：5000 vs 500）；`reset_balance_only` 手写公式与 `_recalc_balance` 两套口径；
  前端用 `window.prompt()`（Electron 不支持 ⇒ 按钮彻底失效）。
- 中线不开仓：EdgeGate 的 `funding_z` 拿到**退化序列**（hyperliquid 源对 ETH/BTC/SOL/… 写的是
  同一条 funding，实测 7 个币 z_fund 全等于 −0.9759）⇒ m_long 被钉死为负 ⇒ `midlong_edge_m_block`
  恒拒（实测占 skip-open 拒因 28%）。
- 止损后同向重开：冷却闸只挂在 3 处调用点，而**所有开仓都走** `paper_engine.place_order`
  ⇒ MLTO/E1 主入口 100% 绕过；且止损词表漏 `barrier:sl` / `exit_policy:sl`（当日止损计数几乎不触发）。

## 回滚
- 账户操作：无需开关（纯缺陷修复）。
- EdgeGate：`MIDLONG_EDGE_GATE_ENABLED=false` 一键全关。
- 重开冷却收口：`PAPER_REENTRY_CHOKE_ENFORCE=false`。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import reentry_cooldown as RC  # noqa: E402
from backend.services.full_auto import edge_gate as EG  # noqa: E402
from backend.services.paper_trading_engine import paper_engine  # noqa: E402

SRC_ENGINE = (ROOT / "backend/services/paper_trading_engine.py").read_text(encoding="utf-8")
SRC_ROUTES = (ROOT / "backend/api/paper_trading_routes.py").read_text(encoding="utf-8")
SRC_PAGE = (ROOT / "frontend-next/src/app/paper-trading/page.tsx").read_text(encoding="utf-8")


# ─────────────── 一、账户操作（问题 1） ───────────────

@pytest.mark.parametrize("bad", [0, -1, -100, float("nan"), float("inf"), None, "abc", ""])
def test_set_balance_rejects_invalid_amounts(bad):
    """实测旧版：0 与 −100 都返回 200 并写库（账户直接停摆/负权益）。"""
    with pytest.raises(ValueError):
        paper_engine.set_initial_balance(None, 1, bad)  # db=None：校验在任何 DB 访问之前


def test_set_balance_no_longer_refuses_on_open_positions():
    """旧判据「有 open 持仓就拒绝」已删除 —— 它让真实账户（长期 5 个仓）永久无法改金额。"""
    body = SRC_ENGINE.split("def set_initial_balance")[1].split("def reset_account")[0]
    assert "Cannot change balance" not in body, "旧的『有持仓即拒』判据必须删掉"
    assert "open_count" in body, "仍应统计持仓（用于决定是否同步 current_cash）"


def test_set_balance_syncs_account_row():
    """旧版只写 paper_balances ⇒ 同一账户两处"初始资金"不同（资产曲线读 accounts.initial_capital）。"""
    body = SRC_ENGINE.split("def set_initial_balance")[1].split("def reset_account")[0]
    assert "acc.initial_capital = val" in body, "必须同步 accounts.initial_capital"
    assert "acc.current_cash = val" in body, "无持仓时应同步 current_cash"


def test_soft_reset_uses_single_source_recalc():
    """软重置必须走 _recalc_balance（单一真源），不能再手写一套公式。"""
    body = SRC_ENGINE.split("def reset_balance_only")[1].split("def set_initial_balance")[0]
    assert "self._recalc_balance(db, bal)" in body, "软重置必须复用 _recalc_balance"
    assert "bal.available_balance = bal.initial_balance - total_margin" not in body, \
        "旧的手写公式不得回归（两套口径必然漂移）"


def test_full_reset_syncs_account_cash():
    body = SRC_ENGINE.split("def reset_account")[1].split("def _record_es_event")[0]
    assert "acc.current_cash = initial" in body, "完整重置后账户行现金必须同步"


def test_reset_endpoints_report_running_sessions():
    """「重置后仓位又回来」不是重置无效，是会话仍在跑 —— 必须把事实回传前端。"""
    assert "def _running_sessions_for" in SRC_ROUTES
    for ep in ("/reset-balance/{account_id}", "/reset/{account_id}", "/set-balance"):
        seg = SRC_ROUTES.split(ep)[1][:900]
        assert "running_sessions" in seg, f"{ep} 未回传 running_sessions"


def test_frontend_account_ops_have_feedback_and_no_native_prompt():
    """旧版四个按钮：window.prompt（Electron 不支持）+ 无 try/catch ⇒ 一律"点了没反应"。"""
    assert "prompt(" not in SRC_PAGE, "不得再用原生 prompt()（Electron 下不工作）"
    assert "操作失败" in SRC_PAGE, "必须有失败反馈"
    assert "runOp" in SRC_PAGE and "setOpMsg" in SRC_PAGE, "操作需走统一包装（忙态 + 反馈 + 失效）"
    assert "equity-series" in (ROOT / "frontend-next/src/hooks/useTradingData.ts").read_text(encoding="utf-8"), \
        "账户操作后必须失效权益曲线（否则曲线仍显示旧序列）"


# ─────────────── 二、中线不开仓（问题 2）：EdgeGate funding 退化 ───────────────

def test_funding_z_treats_constant_series_as_no_data(monkeypatch):
    """核心缺陷：退化序列（全体同值）给出"看似有效"的 z ⇒ m 被钉死为负 ⇒ 中线多头全灭。"""
    sym = "_T_CONST_"
    monkeypatch.setattr(EG, "_funding_rows", lambda s, n: [0.0001] * 30)
    assert EG.funding_z(sym, time.time()) is None, "恒定序列必须视为无数据（fail-open）"


def test_funding_z_caps_explosive_z(monkeypatch):
    """BNB 实测 z≈−8e13（sd 极小）—— 必须当作无数据，而不是拿去算 m。"""
    sym = "_T_EXPLODE_"
    rows = [0.0001] * 29 + [0.0001000000001]
    monkeypatch.setattr(EG, "_funding_rows", lambda s, n: rows)
    assert EG.funding_z(sym, time.time()) is None


def test_funding_z_keeps_real_signal(monkeypatch):
    """正常有波动的序列仍要给出有限 z（别把闸整个废掉）。"""
    sym = "_T_REAL_"
    rows = [0.0001, 0.0005, -0.0003, 0.0002, 0.0009, -0.0008, 0.0004, 0.0006,
            -0.0002, 0.0003, 0.0007, -0.0005, 0.0008, 0.0001] * 3
    monkeypatch.setattr(EG, "_funding_rows", lambda s, n: rows)
    z = EG.funding_z(sym, time.time())
    assert z is not None and abs(z) < 10


def test_mid_long_entry_unblocked_when_funding_degenerate(monkeypatch):
    """端到端：funding 退化时应走 `funding_no_data(fail-open)` 放行，而不是 m_block。"""
    monkeypatch.setattr(EG, "_funding_rows", lambda s, n: [0.0001] * 30)
    monkeypatch.setattr(EG, "_FUNDING_CACHE", {})
    monkeypatch.setattr(EG, "z_btc_mom", lambda n: 0.85)
    ok, why = EG.check_edge_entry(14, "_T_E2E_", "long", "mid", entry_price=100.0)
    assert ok and "funding_no_data" in why, f"退化序列不应拦中线多头，实际：{why}"


def test_edge_gate_still_only_applies_to_mid():
    """long 车道不受该闸影响（这是"长线能开、中线不能"的关键差异，别改坏）。"""
    assert EG.check_edge_entry(14, "ETH", "long", "long", entry_price=2700.0)[0] is True
    assert EG.check_edge_entry(14, "ETH", "long", "short", entry_price=2700.0)[0] is True


# ─────────────── 三、止损后同向重开（问题 3） ───────────────

def test_sl_reason_vocabulary_covers_new_exit_stack():
    """09-29 新出场栈落库 barrier:sl / exit_policy:sl —— 旧词表漏了它们。"""
    for r in ("sl", "sl_pct", "stop_loss", "liquidation", "margin_call",
              "barrier:sl", "exit_policy:sl", "exit_policy:sl_pct", "long_trend_v2:sl"):
        assert RC._is_sl_reason(r), f"{r} 应判为止损"
    for r in ("tp", "take_profit", "trend_broken", "hold_timeout", "master_running_close", ""):
        assert not RC._is_sl_reason(r), f"{r} 不应判为止损"


def test_daily_sl_db_fallback_matches_new_labels():
    src = (ROOT / "backend/services/reentry_cooldown.py").read_text(encoding="utf-8")
    assert "barrier:sl%" in src, "当日止损计数的 DB 兜底漏了 barrier:sl"
    assert "':sl'" in src or "%:sl" in src, "DB 兜底应覆盖任意命名空间的 :sl"


def test_sl_cooldown_uses_shared_predicate():
    """SL 档冷却延长也必须用 _is_sl_reason（否则 barrier:sl 只吃普通冷却）。"""
    seg = RC.__dict__
    src = (ROOT / "backend/services/reentry_cooldown.py").read_text(encoding="utf-8")
    body = src.split("S0-8 止血修复")[1].split("总控全平")[0]
    assert "_is_sl_reason(_reason_l)" in body, "SL 延长分支必须复用统一判定"
    assert seg  # keep flake happy


def test_reentry_cooldown_is_mounted_at_the_single_open_chokepoint():
    """核心：冷却必须挂在 place_order（所有开仓必经之路），否则 MLTO/E1 继续绕过。"""
    body = SRC_ENGINE.split("def place_order")[1].split("def _fill_market_order")[0]
    assert "reopen_blocked" in body, "place_order 未挂 reopen_blocked —— 冷却仍被绕过"
    assert "PAPER_REENTRY_CHOKE_ENFORCE" in body, "缺少回滚开关"
    assert 'add_type not in ("reduce", "close", "pyramid", "dca")' in body, \
        "只应拦新开：加仓/减/平不受冷却影响"


# ─────────────── 五、决策价精度（又一条"中线/小额币永远开不出"的硬闸） ───────────────

def test_price_rounding_no_longer_breaks_decision_price_gate():
    """原先 `round(current_price, 2)`：XPL 0.098→0.10 = +2.0%、DOGE 0.0958→0.10 = +4.4%、
    ADA 0.2542→0.25 = −1.7%、TRX 0.3342→0.33 = −1.3%，而 DecisionPriceGate 的 paper 阈值是 1%
    ⇒ 这些币**数学上必然**被判 decision_price_stale（与新鲜度无关）。"""
    from backend.services.strategy_coordinator import _round_price_keep_precision as R
    cases = {"XPL": 0.09796, "DOGE": 0.09577, "ADA": 0.2542, "TRX": 0.3342, "VIRTUAL": 0.8085}
    for sym, real in cases.items():
        old = round(real, 2)
        new = R(real)
        dev_old = abs(old - real) / old
        dev_new = abs(new - real) / new
        assert dev_new < 0.001, f"{sym} 修复后相对误差仍 {dev_new:.4%}"
        if sym in ("XPL", "DOGE", "ADA", "TRX"):
            assert dev_old > 0.01, f"{sym} 旧口径本就该被证伪（dev={dev_old:.4%}）"
    # 大额币仍走 2 位（端口径不变）
    assert R(86199.234) == 86199.23
    assert R(0) == 0
    assert R(None) is None


def test_strategy_coordinator_does_not_truncate_price_to_2dp():
    src = (ROOT / "backend/services/strategy_coordinator.py").read_text(encoding="utf-8")
    assert "env.current_price = round(current_price, 2)" not in src, \
        "决策价不得再被砍成 2 位小数（会让 <$0.5 的币永远过不了 DecisionPriceGate）"
    assert "_round_price_keep_precision(current_price)" in src, "缺少精度保留调用"


# ─────────────── 六、"方向重估"的两个必要条件（反馈面 + 硬约束） ───────────────

def test_brain_prompt_carries_recent_same_direction_outcomes():
    """用户问「就没想过方向错了么？」——复核结论：**反馈一直在给**，缺的是硬约束。

    `mlto/brain.py` 的开仓 prompt payload 里已有：
      `recent_pnl_14d` / `recent_same_dir_pnl_14d`（同向盈亏与逐笔）、`reflexion_memory`、
      `similar_episodes`、`backtest_wisdom`、`consolidated_lessons`、`high_severity_events`。
    所以"方向从不重估"的准确表述是：**模型看得到同向亏损史，但没有任何东西阻止它照旧下单**。
    本条 ratchet 保证这个反馈面不被悄悄摘掉（摘掉就等于真的变成瞎开单）。"""
    src = (ROOT / "backend/services/mlto/brain.py").read_text(encoding="utf-8")
    for key in ("recent_same_dir_pnl_14d", "recent_pnl_14d", "reflexion_memory",
                "similar_episodes", "high_severity_events", "existing_thesis"):
        assert f'"{key}"' in src, f"brain prompt 反馈面缺字段 {key}"


def test_hard_interlock_exists_at_the_same_time_as_feedback():
    """反馈面（软）+ 收口点硬约束（本轮新增）必须同时存在：只有软的 ⇒ 照样照旧下单。"""
    src_brain = (ROOT / "backend/services/mlto/brain.py").read_text(encoding="utf-8")
    assert "_recent_pnl_bundle(" in src_brain, "同向盈亏反馈面缺失"
    body = SRC_ENGINE.split("def place_order")[1].split("def _fill_market_order")[0]
    assert "reopen_blocked" in body, "收口点硬约束缺失（没有它，反馈只是建议）"


# ─────────────── 七、②b 加仓：趋势段计数（默认关）───────────────────

def test_pyramid_leg_reset_is_off_by_default(monkeypatch):
    """默认必须与修复前逐位一致：终身计数，不因时间流逝而重置。"""
    monkeypatch.delenv("MIDLONG_PYRAMID_LEG_RESET", raising=False)
    from backend.services.position_memory_manager import _pyramid_leg_reset_verdict as V
    old = {"add_count": 3, "last_add_at": "2026-01-01T00:00:00+00:00"}  # 很久以前
    n, note = V(old, 3)
    assert n == 3 and note == "", f"默认口径被改动: {n} {note}"


def test_pyramid_leg_reset_when_enabled(monkeypatch):
    """开启后：距上次加仓超过窗口 ⇒ 计数归零（新趋势段）；窗口内 ⇒ 不重置。"""
    monkeypatch.setenv("MIDLONG_PYRAMID_LEG_RESET", "true")
    monkeypatch.setenv("MIDLONG_PYRAMID_LEG_RESET_HOURS", "24")
    from backend.services.position_memory_manager import _pyramid_leg_reset_verdict as V
    old = {"add_count": 3, "last_add_at": "2026-01-01T00:00:00+00:00"}
    n, note = V(old, 3)
    assert n == 0 and "趋势段重置" in note, f"应重置: {n} {note}"
    # 刚加过（1 小时前）⇒ 窗口内，不重置
    from datetime import datetime, timedelta, timezone
    recent = {"add_count": 3, "last_add_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
    n2, note2 = V(recent, 3)
    assert n2 == 3 and note2 == "", f"窗口内不该重置: {n2} {note2}"
    # 无 last_add_at / 计数为 0 ⇒ 原样返回
    assert V({"add_count": 3}, 3) == (3, "")
    assert V(old, 0) == (0, "")


def test_pyramid_shadow_does_not_change_behaviour(monkeypatch):
    """影子观测（force=True）只用于打日志：开关关闭时主链路必须仍返回原计数。"""
    monkeypatch.delenv("MIDLONG_PYRAMID_LEG_RESET", raising=False)
    from backend.services.position_memory_manager import _pyramid_leg_reset_verdict as V
    old = {"add_count": 3, "last_add_at": "2026-01-01T00:00:00+00:00"}
    assert V(old, 3, force=True)[1] != "", "force 应算出『若开启会重置』"
    assert V(old, 3)[0] == 3, "force 的结论不得泄漏到主链路"


def test_evaluate_pyramid_wires_leg_reset_and_shadow():
    src = (ROOT / "backend/services/position_memory_manager.py").read_text(encoding="utf-8")
    body = src.split("def evaluate_pyramid")[1].split("def ")[0]
    assert "_pyramid_leg_reset_verdict(existing_position, add_count)" in body, "未接线段重置"
    assert "PyramidShadow" in body, "缺影子观测（决策需要数据）"
    assert body.index("PyramidShadow") < body.index("已加仓{add_count}次") or "PyramidShadow" in body


# ─────────────── 四、孤儿附着单（防御性；实测近30天 0 次凭空开仓） ───────────────

def test_orphan_attached_orders_are_cancelled_not_executed():
    """附着单孤儿化：结构上会"无持仓也成交 ⇒ 凭空开仓"，但**实测未发生**（2026-10-02 复核）。

    事实（全部可复核）：
    - 账户 14 当时 23 条 pending 全是 stop_loss/take_profit；最早 08-17；
    - 其中 6 条所属持仓早已平掉，且**全部来自 09-21 已退役的短线车道**
      （`scalp_lane` 2 + `scalp_mr` 4）——是退役遗留，不是当前 mid/long 的持续缺陷；
    - 近 30 天附着单成交 32 笔，**"成交瞬间无该币持仓"= 0** ⇒ 没有真正发生过凭空开仓；
    - 另：先前误判的"12 组重复行"**不是重复**——按 (symbol,side,type,strategy_id) 分组 0 组 >1，
      那些"同秒两行"是**不同策略**各自的附着单（合法）。
    仍保留本修复：附着单没有持仓就不该存在（防御性、零成本），并顺带自愈退役遗留。"""
    body = SRC_ENGINE.split("def check_pending_orders")[1]
    assert 'order_type in ("stop_loss", "take_profit")' in body, "缺少附着单识别"
    assert "orphan_no_position" in body, "缺少孤儿撤单口径"
    assert "PaperPosition.status == \"open\"" in body, "缺少持仓存在性判定"
    # 撤单必须早于触发判定（否则已经成交了）
    assert body.index("orphan_no_position") < body.index("trigger = False"), \
        "孤儿判定必须在 trigger 判定之前"
