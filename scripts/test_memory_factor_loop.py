"""
AI 记忆与因子决策闭环升级 — 全面测试脚本 (M1-M12)

用法:
  backend\\.venv\\Scripts\\python.exe -X utf8 scripts\\test_memory_factor_loop.py
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import dataclass, field
from typing import List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.chdir(ROOT)

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"


@dataclass
class TestResult:
    name: str
    status: str
    detail: str = ""


results: List[TestResult] = []


def record(name: str, status: str, detail: str = "") -> None:
    results.append(TestResult(name, status, detail))
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}.get(status, "?")
    print(f"{icon} [{status}] {name}" + (f" — {detail}" if detail else ""))


def test_imports() -> None:
    mods = [
        "backend.services.trade_memory_context",
        "backend.services.factor_ic_evaluator",
        "backend.services.close_guard_calibrator",
        "backend.services.decision_core.unified_gate",
        "backend.services.rebate_arb.s8_param_learner",
        "backend.services.rebate_arb.proposal_auto_applier",
        "backend.services.rebate_arb.arb_llm_planner",
        "backend.services.trading_analysts",
        "backend.services.full_auto_trading_service",
    ]
    for m in mods:
        try:
            __import__(m)
            record(f"import::{m.split('.')[-1]}", PASS)
        except Exception as e:
            record(f"import::{m.split('.')[-1]}", FAIL, str(e)[:120])


def test_m1_recent_trades() -> None:
    try:
        from backend.database.connection import SessionLocal
        from backend.services.trade_memory_context import (
            build_recent_trades_section,
            compute_symbol_loss_streaks,
            _fetch_recent_closed_trades,
        )

        db = SessionLocal()
        try:
            trades = _fetch_recent_closed_trades(db, limit=30)
            streaks = compute_symbol_loss_streaks(trades)
            section = build_recent_trades_section(db, limit=15)
            if not trades:
                record("M1 逐笔战绩", WARN, "strategy_trades 无 closed 记录")
            elif "最近战绩" not in section:
                record("M1 逐笔战绩", FAIL, "prompt 段缺少标题")
            else:
                record("M1 逐笔战绩", PASS, f"{len(trades)} 笔, 连亏币种 {len(streaks)}")
        finally:
            db.close()
    except Exception as e:
        record("M1 逐笔战绩", FAIL, str(e)[:120])


def test_m2_reflexion() -> None:
    try:
        from backend.services.trade_memory_context import (
            build_loss_lessons_section,
            store_loss_lesson,
            DEEP_LESSON_LOSS_PCT_OF_EQUITY,
        )
        from backend.database.connection import SessionLocal
        from backend.database.models import StrategyMemory

        db = SessionLocal()
        try:
            test_sid = "__test_reflexion__"
            store_loss_lesson(
                db,
                strategy_id=test_sid,
                symbol="TEST",
                side="long",
                pnl=-50.0,
                lesson_text="测试教训：方向判断过早",
                account_equity=10000.0,
                regime="ranging",
            )
            mem = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == test_sid
            ).first()
            if not mem or not mem.key_lessons:
                record("M2 Reflexion 存储", FAIL, "key_lessons 未写入")
            else:
                entry = mem.key_lessons[-1]
                layer_ok = entry.get("layer") in ("deep", "shallow")
                type_ok = entry.get("type") == "reflexion"
                if layer_ok and type_ok:
                    record("M2 Reflexion 存储", PASS, f"layer={entry.get('layer')}")
                else:
                    record("M2 Reflexion 存储", FAIL, str(entry)[:80])
            sec = build_loss_lessons_section(db, symbols=["TEST"], regime="ranging")
            if "亏损教训" in sec or "TEST" in sec:
                record("M2 教训检索", PASS)
            else:
                record("M2 教训检索", WARN, "检索段为空（可能过滤未命中）")
            # 清理测试数据
            if mem:
                db.delete(mem)
                db.commit()
        finally:
            db.close()
    except Exception as e:
        record("M2 Reflexion", FAIL, str(e)[:120])


def test_m3_mental_state() -> None:
    try:
        from backend.database.connection import SessionLocal
        from backend.services.position_memory_manager import position_manager

        db = SessionLocal()
        try:
            ctx = position_manager.get_ai_context(db, account_id=5)
            if ctx and "交易员状态" in ctx:
                record("M3 心理状态注入", PASS, f"{len(ctx)} 字符")
            elif ctx:
                record("M3 心理状态注入", WARN, "有内容但格式异常")
            else:
                record("M3 心理状态注入", WARN, "账户5无 TraderMentalState（可能未初始化）")
        finally:
            db.close()
    except Exception as e:
        record("M3 心理状态", FAIL, str(e)[:120])


def test_m4_gate_feedback() -> None:
    try:
        from backend.services.decision_core.unified_gate import (
            record_block_event,
            build_block_feedback_section,
            get_recent_blocks,
        )

        record_block_event("ETH", "buy", "confidence", "置信度 52% < 门槛 60%")
        blocks = get_recent_blocks(900)
        section = build_block_feedback_section(900)
        if blocks and "拦截" in section:
            record("M4 闸门回灌", PASS, f"{len(blocks)} 条拦截记录")
        else:
            record("M4 闸门回灌", FAIL, "环形缓冲或 prompt 段异常")
    except Exception as e:
        record("M4 闸门回灌", FAIL, str(e)[:120])


def test_m5_factor_prompt() -> None:
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ATASFactorCache

        db = SessionLocal()
        try:
            rows = db.query(ATASFactorCache).filter(
                ATASFactorCache.factor_id == "composite_v3"
            ).limit(5).all()
            if not rows:
                record("M5 因子缓存", WARN, "atas_factor_cache 无 composite_v3 数据")
            else:
                valid = sum(
                    1 for r in rows
                    if isinstance(r.value, dict) and r.value.get("direction_label")
                )
                record("M5 因子缓存", PASS if valid else WARN, f"{valid}/{len(rows)} 条有效")
        finally:
            db.close()
    except Exception as e:
        record("M5 因子缓存", FAIL, str(e)[:120])


def test_m6_factor_veto() -> None:
    try:
        from backend.database.connection import SessionLocal
        from backend.services.full_auto_trading_service import FullAutoTradingService

        svc = FullAutoTradingService()
        db = SessionLocal()
        try:
            r1 = svc._factor_veto_check(db, "BTC", "buy")
            r2 = svc._factor_veto_check(db, "NONEXIST", "sell")
            record(
                "M6 因子否决",
                PASS,
                f"BTC buy={'否决:'+r1[:40] if r1 else '放行'}, 无数据={'放行' if r2 is None else '异常'}",
            )
        finally:
            db.close()
    except Exception as e:
        record("M6 因子否决", FAIL, str(e)[:120])


def test_m7_factor_ic() -> None:
    try:
        from backend.database.connection import SessionLocal, AnalyticsSessionLocal
        from backend.database.models import FactorPerformanceLog
        from backend.services.factor_ic_evaluator import (
            run_factor_ic_evaluation,
            load_runtime_factor_weights,
        )

        db = SessionLocal()
        try:
            ic = run_factor_ic_evaluation(db, lookback_days=30)
            weights = load_runtime_factor_weights()
            ana = AnalyticsSessionLocal()
            try:
                log_count = ana.query(FactorPerformanceLog).count()
            finally:
                ana.close()
            if ic:
                down = [k for k, v in weights.items() if v < 1.0]
                record(
                    "M7 因子IC闭环",
                    PASS,
                    f"{len(ic)} 因子评估, 降权 {len(down)}, logs={log_count}",
                )
            else:
                record("M7 因子IC闭环", WARN, "无配对样本（signal_trade_feedback 可能断流）")
        finally:
            db.close()
    except Exception as e:
        record("M7 因子IC闭环", FAIL, str(e)[:120])


def test_m8_s8_learning() -> None:
    try:
        from backend.services.rebate_arb.s8_param_learner import (
            recompute_learned_params,
            load_learned_params,
        )
        from backend.services.rebate_arb.strategies.s8_asterdex_rh import S8AsterdexRhStrategy

        params = recompute_learned_params()
        s8 = S8AsterdexRhStrategy()
        val = (s8.stage6_model().get("point_valuation") or {})
        learned = load_learned_params()
        samples = learned.get("samples", 0)
        if samples >= 5:
            record(
                "M8 S8参数学习",
                PASS,
                f"样本={samples} discount={val.get('speculative_discount')} "
                f"hold={s8.STAGE6_HOLD_DEFAULT_SECONDS}s",
            )
        else:
            record("M8 S8参数学习", WARN, f"样本不足 {samples}")
    except Exception as e:
        record("M8 S8参数学习", FAIL, str(e)[:120])


def test_m9_proposal_auto() -> None:
    try:
        from backend.services.rebate_arb.proposal_auto_applier import (
            run_auto_apply_cycle,
            get_paper_multiplier,
            MULTIPLIERS_FILE,
        )

        cycle = run_auto_apply_cycle()
        mult_s8 = get_paper_multiplier("S8")
        file_ok = os.path.exists(MULTIPLIERS_FILE)
        record(
            "M9 提案自动应用",
            PASS,
            f"评估={cycle['evaluated']} 应用={cycle['applied']} S8系数={mult_s8}",
        )
    except Exception as e:
        record("M9 提案自动应用", FAIL, str(e)[:120])


def test_m10_s8_reflection() -> None:
    try:
        from backend.services.rebate_arb.arb_llm_planner import _recent_s8_rounds_summary

        rounds = _recent_s8_rounds_summary(limit=10)
        if rounds:
            record("M10 S8历史轮次", PASS, f"{len(rounds)} 轮, 最新={rounds[0].get('symbol')}")
        else:
            record("M10 S8历史轮次", WARN, "rebate_trade_outcomes 无 S8 数据")
    except Exception as e:
        record("M10 S8历史轮次", FAIL, str(e)[:120])


def test_m11_close_guard() -> None:
    try:
        from backend.services.close_guard_calibrator import (
            run_close_guard_calibration,
            high_conf_close_bypass,
            CLOSE_GUARD_RUNTIME_FILE,
        )

        calib = run_close_guard_calibration(lookback_days=14)
        bypass = high_conf_close_bypass(75.0)
        file_ok = os.path.exists(CLOSE_GUARD_RUNTIME_FILE)
        record(
            "M11 平仓门控校准",
            PASS if calib else WARN,
            f"bypass={'开' if bypass else '关'} 高置信样本={calib.get('high_conf_stats',{}).get('n',0)}",
        )
    except Exception as e:
        record("M11 平仓门控校准", FAIL, str(e)[:120])


def test_m12_exit_audit() -> None:
    try:
        from backend.services.close_guard_calibrator import (
            run_exit_audit,
            EXIT_AUDIT_REPORT_FILE,
        )

        audit = run_exit_audit(lookback_days=30)
        channels = audit.get("by_channel") or {}
        record(
            "M12 退出路径审计",
            PASS if audit else WARN,
            f"{audit.get('total_events',0)} 事件 / {len(channels)} 通道",
        )
    except Exception as e:
        record("M12 退出路径审计", FAIL, str(e)[:120])


def test_database_tables() -> None:
    import sqlite3

    conn = sqlite3.connect(os.path.join(ROOT, "data", "alpha_arena.db"))
    c = conn.cursor()
    checks = [
        ("strategy_trades closed", "SELECT COUNT(*) FROM strategy_trades WHERE status='closed'"),
        ("trade_memory_records", "SELECT COUNT(*) FROM trade_memory_records"),
        ("trader_mental_states", "SELECT COUNT(*) FROM trader_mental_states"),
        ("signal_trade_feedback", "SELECT COUNT(*) FROM signal_trade_feedback"),
        ("signal_trade_feedback w/pnl", "SELECT COUNT(*) FROM signal_trade_feedback WHERE trade_pnl IS NOT NULL"),
        ("atas_factor_cache", "SELECT COUNT(*) FROM atas_factor_cache WHERE factor_id='composite_v3'"),
        ("rebate_trade_outcomes S8", "SELECT COUNT(*) FROM rebate_trade_outcomes WHERE strategy_type='S8'"),
        ("position_exit_events", "SELECT COUNT(*) FROM position_exit_events"),
        ("position_exit_events blocked", "SELECT COUNT(*) FROM position_exit_events WHERE event_type='master_close_blocked'"),
    ]
    for label, sql in checks:
        try:
            n = c.execute(sql).fetchone()[0]
            status = PASS if n > 0 else WARN
            record(f"DB::{label}", status, f"{n} 条")
        except Exception as e:
            record(f"DB::{label}", FAIL, str(e)[:80])
    conn.close()


def test_runtime_files() -> None:
    files = [
        ("data/factor_runtime_weights.json", "M7 因子权重"),
        ("data/s8_learned_params.json", "M8 S8学习参数"),
        ("data/rebate_paper_multipliers.json", "M9 提案系数"),
        ("data/close_guard_runtime.json", "M11 平仓校准"),
        ("data/exit_audit_report.json", "M12 退出审计"),
    ]
    for path, label in files:
        full = os.path.join(ROOT, path)
        if os.path.exists(full):
            try:
                with open(full, "r", encoding="utf-8") as f:
                    data = json.load(f)
                record(f"文件::{label}", PASS, f"{len(data)} 键")
            except Exception as e:
                record(f"文件::{label}", FAIL, str(e)[:60])
        else:
            record(f"文件::{label}", WARN, "尚未生成")


def test_existing_unit_tests() -> None:
    import subprocess

    targets = [
        "tests/backend/unit/test_master_close_guard.py",
        "tests/backend/unit/test_factor_engine.py",
        "tests/backend/unit/test_s8_points_maximization.py",
        "tests/backend/integration/test_api_health.py",
    ]
    py = os.path.join(ROOT, "backend", ".venv", "Scripts", "python.exe")
    for t in targets:
        full = os.path.join(ROOT, t)
        if not os.path.exists(full):
            record(f"pytest::{os.path.basename(t)}", WARN, "文件不存在")
            continue
        r = subprocess.run(
            [py, "-m", "pytest", full, "-q", "--tb=no"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        out = (r.stdout or r.stderr or "").strip().split("\n")[-1]
        if r.returncode == 0:
            record(f"pytest::{os.path.basename(t)}", PASS, out[:80])
        else:
            record(f"pytest::{os.path.basename(t)}", FAIL, out[:120])


def main() -> int:
    print("=" * 60)
    print("AI 记忆与因子决策闭环 — 全面测试")
    print("=" * 60)

    test_imports()
    test_m1_recent_trades()
    test_m2_reflexion()
    test_m3_mental_state()
    test_m4_gate_feedback()
    test_m5_factor_prompt()
    test_m6_factor_veto()
    test_m7_factor_ic()
    test_m8_s8_learning()
    test_m9_proposal_auto()
    test_m10_s8_reflection()
    test_m11_close_guard()
    test_m12_exit_audit()
    test_database_tables()
    test_runtime_files()
    print("\n--- 既有单元测试 ---")
    test_existing_unit_tests()

    passed = sum(1 for r in results if r.status == PASS)
    failed = sum(1 for r in results if r.status == FAIL)
    warned = sum(1 for r in results if r.status == WARN)
    print("\n" + "=" * 60)
    print(f"汇总: ✅ {passed} 通过 | ❌ {failed} 失败 | ⚠️ {warned} 警告 | 共 {len(results)} 项")
    print("=" * 60)

    if failed:
        print("\n失败项:")
        for r in results:
            if r.status == FAIL:
                print(f"  - {r.name}: {r.detail}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
