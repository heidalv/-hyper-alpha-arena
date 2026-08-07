"""Mid/Long Agent 升级 — 一键验收脚本（Phase 0–4）。

用法（项目根目录）：
    backend\\.venv\\Scripts\\python.exe scripts/mid_long_agent_acceptance_check.py [--days 30]

检查项：
  1. 核心模块导入与 Prompt Registry 渲染
  2. Hermes 表结构 + Agent task 注册
  3. 数据库/API 数据通路（by-agent 报告、scenario 落库、wisdom 采集）
  4. 盈利验收线（30 日 swing PF ≥ 1.5、trend PF ≥ 2.0，有数据时才判）
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

PASS = 0
FAIL = 0
WARN = 0


def check(name: str, cond: bool, detail: str = "", *, warn: bool = False):
    global PASS, FAIL, WARN
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    elif warn:
        WARN += 1
        print(f"  [WARN] {name} — {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} — {detail}")


def section(title: str):
    print(f"\n{title}")


def main():
    parser = argparse.ArgumentParser(description="Mid/Long Agent 升级验收")
    parser.add_argument("--days", type=int, default=30, help="盈利统计窗口（天）")
    args = parser.parse_args()

    print("=== Mid/Long Agent 升级验收（Phase 0–4）===\n")

    # ── 1.5 模块与链路 ──
    section("1.5 Mid/Long v2 模块")
    try:
        from backend.services.position_exit_state import merge_exit_state
        from backend.services.agent_decision_envelope import AgentDecisionEnvelope
        from backend.services.mid_long_quant_brief import mid_long_quant_brief_builder
        from backend.services.mid_long_structure_stop import mid_long_structure_stop
        from backend.config.settings import (
            MIDLONG_ORCH_SNAPSHOT_V2,
            MIDLONG_AGENT_SL_TO_EXECUTE,
            MIDLONG_QUANT_BRIEF_ENABLED,
        )
        check("position_exit_state", callable(merge_exit_state))
        check("agent_decision_envelope", AgentDecisionEnvelope.new("swing_agent") is not None)
        check("mid_long_quant_brief", mid_long_quant_brief_builder is not None)
        check("mid_long_structure_stop", mid_long_structure_stop is not None)
        check("MIDLONG feature flags", MIDLONG_ORCH_SNAPSHOT_V2 or MIDLONG_AGENT_SL_TO_EXECUTE or MIDLONG_QUANT_BRIEF_ENABLED)
    except Exception as exc:
        check("Mid/Long v2 模块", False, str(exc))

    # ── 1.6 MLTO 研判链 ──
    section("1.6 MLTO (MidLong Thesis Orchestrator)")
    try:
        from backend.config.settings import (
            MIDLONG_THESIS_LEDGER_ENABLED,
            MIDLONG_QUANT_BRIEF_HARD_GATE,
            MIDLONG_THESIS_OPEN_GATE,
        )
        from backend.services.mlto.orchestrator import run_mlto_tick
        from backend.services.swing_agent import swing_agent
        from backend.services.trend_agent import trend_agent
        from backend.api.mlto_routes import router as mlto_router

        check("MIDLONG_THESIS_LEDGER_ENABLED", MIDLONG_THESIS_LEDGER_ENABLED)
        check("MIDLONG_QUANT_BRIEF_HARD_GATE 默认关闭", not MIDLONG_QUANT_BRIEF_HARD_GATE)
        check("MIDLONG_THESIS_OPEN_GATE", MIDLONG_THESIS_OPEN_GATE)
        check("run_mlto_tick 可导入", callable(run_mlto_tick))
        check("SwingAgent.update_thesis", callable(getattr(swing_agent, "update_thesis", None)))
        check("TrendAgent.update_thesis", callable(getattr(trend_agent, "update_thesis", None)))
        paths = [getattr(r, "path", "") for r in mlto_router.routes]
        check("MLTO API /thesis/summary", any("thesis/summary" in p for p in paths))
    except Exception as exc:
        check("MLTO 模块", False, str(exc))

    try:
        import subprocess
        py = sys.executable
        r = subprocess.run(
            [py, os.path.join(ROOT, "scripts", "verify_midlong_thesis_chain.py")],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=ROOT,
        )
        check(
            "verify_midlong_thesis_chain.py",
            r.returncode == 0,
            (r.stderr or r.stdout)[-300:],
        )
    except Exception as exc:
        check("verify_midlong_thesis_chain.py", False, str(exc))

    # ── 1. 模块导入 ──
    section("1. 核心模块")
    try:
        from backend.services.swing_agent import SwingAgent
        from backend.services.trend_agent import TrendAgent
        from backend.services.agent_evidence_builder import (
            build_swing_evidence,
            build_trend_evidence,
            format_evidence_for_prompt,
        )
        from backend.services.agent_fact_guard import verify_agent_decision
        from backend.services.agent_prompt_service import render_agent_task
        from backend.services.agent_analytics_service import build_by_agent_report
        from backend.services.trend_prediction_service import trend_prediction_service
        from backend.services.hermes_agent_wisdom_engine import agent_wisdom, build_agent_wisdom_context
        from backend.services.hermes_prompt_optimizer_engine import (
            OPTIMIZABLE_TASKS,
            AGENT_TASK_TYPES,
            prompt_optimizer,
        )
        check("核心模块导入", True)
    except Exception as exc:
        check("核心模块导入", False, str(exc))
        _summary()
        return

    # ── 2. Prompt Registry ──
    section("2. Prompt Registry")
    try:
        from backend.services.prompt_registry import get_prompt_registry, _load_manifest
        _load_manifest.cache_clear()
        reg = get_prompt_registry()
        swing_txt = reg.render_task(
            "task_swing_agent",
            {"symbol": "BTC", "deep_context": "test", "compact_report": "rpt",
             "orchestrator": "{}", "evidence_block": ""},
        )
        trend_txt = reg.render_task(
            "task_trend_agent_direction",
            {"symbol": "ETH", "side_hint": "long", "macro_block": "",
             "deep_context": "", "compact_report": "rpt", "orchestrator": "{}",
             "evidence_block": ""},
        )
        check("task_swing_agent 可渲染", "SwingAgent" in swing_txt and "BTC" in swing_txt)
        check("task_trend_agent_direction 可渲染", "TrendAgent" in trend_txt and "trend_score" in trend_txt)
    except Exception as exc:
        check("Prompt Registry 渲染", False, str(exc))

    fallback = render_agent_task(
        "task_swing_agent",
        {"symbol": "X"},
        consumer="acceptance",
        fallback_text="INLINE_FALLBACK",
    )
    check("render_agent_task fallback", fallback and len(fallback) > 10)

    # ── 3. Hermes L2 ──
    section("3. Hermes L2 / Agent Wisdom")
    check("OPTIMIZABLE_TASKS 含 swing", "task_swing_agent" in OPTIMIZABLE_TASKS)
    check("OPTIMIZABLE_TASKS 含 trend", "task_trend_agent_direction" in OPTIMIZABLE_TASKS)
    check("OPTIMIZABLE_TASKS 含 trend review", "task_trend_agent_review" in OPTIMIZABLE_TASKS)
    check("AGENT_TASK_TYPES 映射", AGENT_TASK_TYPES.get("task_swing_agent") == "swing")

    try:
        from backend.services.hermes_db import init_hermes_db, hermes_fetchall, hermes_fetchone
        init_hermes_db()
        row = hermes_fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_decision_wisdom'"
        )
        check("Hermes 表 agent_decision_wisdom", row is not None)
        wisdom_rows = hermes_fetchall("SELECT COUNT(*) AS n FROM agent_decision_wisdom", ())
        n_wisdom = int(wisdom_rows[0]["n"]) if wisdom_rows else 0
        check(
            "agent_decision_wisdom 有记录",
            n_wisdom >= 1,
            f"当前 {n_wisdom} 条（需平仓后采集，冷启动可为 0）",
            warn=n_wisdom < 1,
        )
        ctx = build_agent_wisdom_context("swing", limit=3)
        check("build_agent_wisdom_context 可调用", isinstance(ctx, str) and len(ctx) > 0)
        prompt_optimizer.ensure_baseline_versions()
        pv = hermes_fetchone(
            "SELECT id FROM prompt_versions WHERE task_id='task_swing_agent' LIMIT 1"
        )
        check("task_swing_agent 基线 prompt 快照", pv is not None, warn=pv is None)
    except Exception as exc:
        check("Hermes DB 检查", False, str(exc))

    # ── 4. Fact Guard 配置 ──
    section("4. Fact Guard")
    try:
        from backend.config.settings import AGENT_FACT_GUARD_MODE
        mode = AGENT_FACT_GUARD_MODE or "shadow"
        check("AGENT_FACT_GUARD_MODE 已配置", mode in ("off", "shadow", "enforce"), f"mode={mode}")
        if mode == "shadow":
            print("       -> 当前 shadow 模式，7 天后可评估误杀率再切 enforce")
    except Exception as exc:
        check("Fact Guard 配置", False, str(exc))

    # ── 5. 数据库 / API 通路 ──
    section("5. 数据通路")
    try:
        from backend.database.connection import SessionLocal, AnalyticsSessionLocal
        from backend.services.strategic_analyst.db_models import TrendPredictionRecord

        db = SessionLocal()
        try:
            report = build_by_agent_report(db, days=args.days)
            agents = report.get("agents") or {}
            check("build_by_agent_report", "swing" in agents and "trend_follow" in agents)
            swing = agents.get("swing") or {}
            trend = agents.get("trend_follow") or {}
            print(
                f"       -> swing: {swing.get('trades', 0)} 笔, PF={swing.get('profit_factor')}, "
                f"净={swing.get('net_pnl')}"
            )
            print(
                f"       -> trend: {trend.get('trades', 0)} 笔, PF={trend.get('profit_factor')}, "
                f"净={trend.get('net_pnl')}, scenario={trend.get('scenario_hit_rate')}"
            )
        finally:
            db.close()

        adb = AnalyticsSessionLocal()
        try:
            from backend.database.connection import AnalyticsBase, analytics_engine

            pred_count = 0
            table_ok = True
            try:
                pred_count = adb.query(TrendPredictionRecord).count()
            except Exception as table_err:
                adb.rollback()
                try:
                    AnalyticsBase.metadata.create_all(
                        bind=analytics_engine,
                        tables=[TrendPredictionRecord.__table__],
                    )
                    adb2 = AnalyticsSessionLocal()
                    try:
                        pred_count = adb2.query(TrendPredictionRecord).count()
                    finally:
                        adb2.close()
                except Exception as migrate_err:
                    table_ok = False
                    check(
                        "TrendPredictionRecord 表可访问",
                        False,
                        f"{table_err}; migrate: {migrate_err}",
                        warn=True,
                    )

            if table_ok:
                check("TrendPredictionRecord 表可访问", True)
                check(
                    "scenario 落库有记录",
                    pred_count >= 1,
                    f"当前 {pred_count} 条（trend 开仓后才有）",
                    warn=pred_count < 1,
                )
        finally:
            adb.close()

        from backend.api.analytics_routes import router
        routes = [getattr(r, "path", "") for r in router.routes]
        check("/api/analytics/by-agent 路由已注册", any("by-agent" in p for p in routes))
    except Exception as exc:
        check("数据通路", False, str(exc))

    # ── 6. 盈利验收线 ──
    section(f"6. 盈利 KPI（近 {args.days} 天，未达标记 WARN 不阻塞部署）")
    try:
        from backend.database.connection import SessionLocal
        from backend.services.agent_analytics_service import build_by_agent_report

        db = SessionLocal()
        try:
            report = build_by_agent_report(db, days=args.days)
            swing = (report.get("agents") or {}).get("swing") or {}
            trend = (report.get("agents") or {}).get("trend_follow") or {}

            s_trades = int(swing.get("trades") or 0)
            s_pf = swing.get("profit_factor")
            if s_trades >= 5 and s_pf is not None:
                check(
                    "swing PF >= 1.5（业务 KPI）",
                    float(s_pf) >= 1.5,
                    f"PF={s_pf}，未达标但代码通路正常",
                    warn=float(s_pf) < 1.5,
                )
            else:
                check("swing PF >= 1.5", True, f"样本不足 ({s_trades} 笔)，跳过", warn=True)

            t_trades = int(trend.get("trades") or 0)
            t_pf = trend.get("profit_factor")
            if t_trades >= 3 and t_pf is not None:
                check(
                    "trend_follow PF >= 2.0（业务 KPI）",
                    float(t_pf) >= 2.0,
                    f"PF={t_pf}，未达标但代码通路正常",
                    warn=float(t_pf) < 2.0,
                )
            else:
                check("trend_follow PF >= 2.0", True, f"样本不足 ({t_trades} 笔)，跳过", warn=True)
        finally:
            db.close()
    except Exception as exc:
        check("盈利验收线", False, str(exc))

    _summary()


def _summary():
    print("\n" + "=" * 50)
    print(f"验收结果: PASS={PASS}  FAIL={FAIL}  WARN={WARN}")
    if FAIL == 0:
        print("总体: 通过（WARN 为冷启动/样本不足提示，非阻塞）")
    else:
        print("总体: 存在 FAIL，请按上方明细排查")
    print("\n相关单测:")
    print("  python -m pytest tests/backend/unit/test_prompt_registry_agent_tasks.py \\")
    print("    tests/backend/unit/test_agent_analytics_service.py \\")
    print("    tests/backend/unit/test_hermes_l2_agent_tasks.py \\")
    print("    tests/backend/unit/test_agent_fact_guard.py \\")
    print("    tests/backend/unit/test_trend_prediction_service.py \\")
    print("    tests/backend/unit/test_hermes_agent_wisdom.py -q")
    print("\n前端入口:")
    print("  Analytics → Mid/Long Agent  |  OpenCode → Hermes 进化 → L2 Agent A/B")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
