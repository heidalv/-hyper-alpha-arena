"""
2026-05-08 修复运行时验证

验证内容：
1. process_outcome 写出的 StrategyTrade 真带 position_size + opened_at（Bug B + C）
2. paper place_order 在杠杆爆表时被 DeterministicRiskGate 拦截（谎言 2）
3. _call_llm_for_prompt_evolution_v2 在 LLM 不可用时写入 fail_reason / error_class（谎言 3）

跑法（项目根）：
    PYTHONPATH=. backend/.venv/bin/python scripts/runtime_verify_2026_05_08.py
"""
from __future__ import annotations

import json
import os
import sys
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONUNBUFFERED", "1")

# 让 backend 内部的相对 import 能解析
os.chdir(str(ROOT))


def line(c: str = "─") -> str:
    return c * 80


def section(title: str):
    print()
    print(line("═"))
    print(f"  {title}")
    print(line("═"))


def check(label: str, ok: bool, details: str = ""):
    flag = "✅ PASS" if ok else "❌ FAIL"
    print(f"  {flag}  {label}")
    if details:
        for ln in details.splitlines():
            print(f"           {ln}")
    return ok


# ──────────────────────────────────────────────────────────
# 测试 1: process_outcome 写出的 StrategyTrade 字段
# ──────────────────────────────────────────────────────────
def test_persist_strategy_trade() -> bool:
    section("测试 1：StrategyTrade 真带上 position_size + opened_at（Bug B + C）")
    from backend.database.connection import SessionLocal
    from backend.database.models import StrategyTrade
    from backend.services.unified_learning_service import (
        UnifiedLearningService, TradeOutcome,
    )
    db = SessionLocal()
    svc = UnifiedLearningService()
    test_strategy_id = "verify-fix-2026-05-08"
    try:
        # 删旧数据
        db.query(StrategyTrade).filter(StrategyTrade.strategy_id == test_strategy_id).delete()
        db.commit()

        opened = datetime.utcnow() - timedelta(minutes=37)
        outcome = TradeOutcome(
            source="paper",
            strategy_id=test_strategy_id,
            template_id="1",
            symbol="VERIFY-USDT",
            side="long",
            tier="mid",
            trade_nature="swing",
            entry_price=120.5,
            exit_price=125.8,
            pnl=53.0,
            pnl_pct=0.044,
            duration_seconds=37 * 60,
            confidence=0.65,
            position_size=10.0,
            opened_at=opened,
            metadata={"close_reason": "tp_hit", "leverage": 3.0},
            persist_trade=True,
        )
        svc.process_outcome(db, outcome)

        # 重新查
        rec = db.query(StrategyTrade).filter(
            StrategyTrade.strategy_id == test_strategy_id
        ).order_by(StrategyTrade.id.desc()).first()
        if not rec:
            return check("写入 StrategyTrade", False, "未查到刚写入的记录")

        ok_size = abs(rec.position_size - 10.0) < 1e-6
        ok_opened = rec.opened_at is not None and rec.closed_at is not None and rec.opened_at < rec.closed_at
        details = (
            f"id={rec.id} pos_size={rec.position_size} opened_at={rec.opened_at} "
            f"closed_at={rec.closed_at} pnl={rec.pnl}"
        )
        passed = check("position_size = 10.0 (真实仓位，非 abs(exit_price))", ok_size, details)
        passed &= check("opened_at < closed_at（开仓时间真实）", ok_opened)

        # 清理测试数据
        db.query(StrategyTrade).filter(StrategyTrade.strategy_id == test_strategy_id).delete()
        db.commit()
        return passed
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# 测试 2：paper place_order 风控拦截（谎言 2）
# ──────────────────────────────────────────────────────────
def test_paper_risk_gate_block() -> bool:
    section("测试 2：paper_trading_engine.place_order 风控生效（谎言 2）")
    from backend.database.connection import SessionLocal
    from backend.database.models import (
        PaperBalance, Account, RiskControlEvent,
    )
    from backend.services.paper_trading_engine import PaperTradingEngine

    db = SessionLocal()
    try:
        acct = db.query(Account).first()
        if not acct:
            return check("找到任意账户", False, "数据库无账户，跳过")
        bal = db.query(PaperBalance).filter(PaperBalance.account_id == acct.id).first()
        if not bal:
            bal = PaperBalance(account_id=acct.id, total_equity=10000, available_balance=10000)
            db.add(bal)
            db.commit()

        # 故意构造一个超大杠杆 + 超大仓位的下单（必被风控拦截）
        engine = PaperTradingEngine()
        result = engine.place_order(
            db=db,
            account_id=acct.id,
            strategy_id="risk-gate-verify",
            symbol="BTC",
            side="buy",
            order_type="market",
            price=120000,        # 模拟价格
            quantity=10,         # 10 BTC
            leverage=100,        # 100x —— 必触发杠杆上限
        )
        if not isinstance(result, dict):
            return check("place_order 返回 dict", False, f"实际类型: {type(result)}")
        is_blocked = bool(result.get("blocked"))
        details = (
            f"result={ {k: v for k, v in result.items() if k in ('blocked', 'blocked_by', 'reason_code')} }"
        )
        if not is_blocked:
            details += f"\n注意：result 完整内容: {result}"
        passed = check("100x 杠杆下单被拦截（result.blocked=True）", is_blocked, details)

        # 看是否写了 risk_control_events
        evt = db.query(RiskControlEvent).filter(
            RiskControlEvent.account_id == acct.id,
            RiskControlEvent.event_type == "paper_blocked",
        ).order_by(RiskControlEvent.id.desc()).first()
        ok_evt = evt is not None
        evt_details = ""
        if evt and evt.details:
            try:
                ed = json.loads(evt.details)
                evt_details = f"event id={evt.id} rule={ed.get('rule')} symbol={ed.get('symbol')}"
            except Exception:
                evt_details = f"event id={evt.id} (details parse failed)"
        passed &= check("RiskControlEvent 已写入 paper_blocked", ok_evt, evt_details)
        return passed
    except Exception as e:
        return check("paper 风控测试运行", False, f"异常: {type(e).__name__}: {e}")
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# 测试 3：进化失败错因记录（谎言 3）
# ──────────────────────────────────────────────────────────
def test_evolution_fail_diagnostics() -> bool:
    section("测试 3：prompt 进化失败错因记录（谎言 3）")
    from backend.services.strategy_learning_service import StrategyLearningService
    svc = StrategyLearningService()
    # 用一个一定不存在的 account_id 强制走异常分支
    text, debug = svc._call_llm_for_prompt_evolution_v2(
        instruction="this is a verification call, please ignore",
        account_id=999_999_999,
    )
    has_diag = isinstance(debug, dict) and (
        debug.get("error_class") or debug.get("raw_response_type") or debug.get("error_message")
    )
    details = f"text_len={len(text) if text else 0} debug={debug}"
    return check("v2 调用返回 (text, debug) 且 debug 非空", bool(has_diag), details)


# ──────────────────────────────────────────────────────────
# 测试 4：DB 中能查出真实数据指标
# ──────────────────────────────────────────────────────────
def test_data_consistency() -> bool:
    section("测试 4：数据一致性指标（清理后）")
    db_path = ROOT / "data" / "alpha_arena.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    # 测试桩残留
    c.execute(
        "SELECT COUNT(*) FROM strategy_trades "
        "WHERE strategy_id IN ('full-chain-test-001','test-chain-001','test-bus-001')"
    )
    n_test = c.fetchone()[0]
    ok_a = check(f"测试桩 0 条残留（实际: {n_test}）", n_test == 0)

    # 因子方向类型
    c.execute("SELECT id, value FROM atas_factor_cache")
    bad = []
    for rid, val in c.fetchall():
        try:
            d = json.loads(val) if isinstance(val, str) else val
            if isinstance(d, dict) and isinstance(d.get("direction"), str):
                bad.append(rid)
        except Exception:
            continue
    ok_b = check(f"atas_factor_cache.value.direction 全部为 float（仍字符串: {len(bad)}）", not bad)

    # ai_strategies 绑定
    c.execute("SELECT COUNT(*) FROM ai_strategies WHERE master_prompt_template_id IS NULL")
    n_null = c.fetchone()[0]
    ok_c = check(f"ai_strategies 全部绑了模板（NULL: {n_null}）", n_null == 0)

    return ok_a and ok_b and ok_c


# ──────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────
def main() -> int:
    print(line("═"))
    print("Hyper-Alpha-Arena 修复运行时验证 — 2026-05-08")
    print(line("═"))
    results = []
    for name, fn in [
        ("test_data_consistency", test_data_consistency),
        ("test_persist_strategy_trade", test_persist_strategy_trade),
        ("test_paper_risk_gate_block", test_paper_risk_gate_block),
        ("test_evolution_fail_diagnostics", test_evolution_fail_diagnostics),
    ]:
        try:
            results.append((name, fn()))
        except Exception as e:
            import traceback
            traceback.print_exc()
            results.append((name, False))

    print()
    print(line("═"))
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        flag = "✅" if ok else "❌"
        print(f"  {flag} {name}")
    print(line("─"))
    print(f"  汇总: {passed}/{total} 项通过")
    print(line("═"))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
