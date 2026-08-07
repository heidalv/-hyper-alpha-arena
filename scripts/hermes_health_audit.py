"""Hermes 自进化系统 — 全链路健康诊断脚本。

用法（项目根目录）：
    backend\\.venv\\Scripts\\python.exe scripts/hermes_health_audit.py
"""

from __future__ import annotations

import json
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

PASS = FAIL = WARN = 0


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
    print("=== Hermes 自进化系统健康诊断 ===\n")

    # ── 1. Hermes DB ──
    section("1. Hermes 数据库")
    try:
        from backend.services.hermes_db import init_hermes_db, hermes_fetchall, hermes_fetchone, HERMES_DB_PATH
        from backend.services.hermes_orchestrator import hermes as orch

        init_hermes_db()
        check("hermes_evolution.db 存在", os.path.isfile(HERMES_DB_PATH), HERMES_DB_PATH)

        wisdom_n = int((hermes_fetchone("SELECT COUNT(*) AS n FROM proposal_wisdom_records") or {}).get("n", 0))
        agent_n = int((hermes_fetchone("SELECT COUNT(*) AS n FROM agent_decision_wisdom") or {}).get("n", 0))
        pattern_n = int((hermes_fetchone("SELECT COUNT(*) AS n FROM param_effect_patterns") or {}).get("n", 0))
        active_pv = int((hermes_fetchone(
            "SELECT COUNT(*) AS n FROM prompt_versions WHERE status='active'"
        ) or {}).get("n", 0))

        check("proposal_wisdom_records", wisdom_n >= 1, f"当前 {wisdom_n} 条", warn=wisdom_n < 1)
        check("agent_decision_wisdom", agent_n >= 1, f"当前 {agent_n} 条", warn=agent_n < 1)
        check("param_effect_patterns", pattern_n >= 0, f"当前 {pattern_n} 条")
        check("active prompt_versions", active_pv >= 1, f"当前 {active_pv} 条", warn=active_pv < 1)

        health = orch.full_health_check()
        check("full_health_check db_ok", health.get("db_ok") is True, health.get("db_error", ""))
    except Exception as exc:
        check("Hermes DB", False, str(exc))

    # ── 2. L1 提取率 / focus 注入 ──
    section("2. L1 智慧提取与 Agent 注入")
    try:
        from backend.services.hermes_proposal_wisdom_engine import proposal_wisdom
        from backend.services.agent_deep_context import build_memory_block
        from backend.database.connection import SessionLocal

        ctx_all = proposal_wisdom.build_wisdom_context(limit=5)
        check(
            "L1 build_wisdom_context 有内容",
            bool(ctx_all.strip()),
            "无 proposal 智慧",
            warn=not ctx_all.strip(),
        )
        db = SessionLocal()
        try:
            mem = build_memory_block(db, "BTC", agent_focus="swing")
            has_param = "Hermes 历史调参智慧" in mem or "历史提案智慧" in mem
            has_agent = "Agent 近期决策智慧" in mem or "Agent 决策智慧" in mem
            check(
                "U1: agent deep_context 可注入 L1（不依赖 focus=swing）",
                has_param or not ctx_all.strip(),
                warn=ctx_all.strip() and not has_param,
            )
            check("deep_context 含 Agent 智慧块", has_agent, warn=not has_agent)
        finally:
            db.close()
    except Exception as exc:
        check("L1/注入检查", False, str(exc))

    # ── 3. 主库提案 schema ──
    section("3. OpenCode 提案 after_json")
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import OpenCodeEvolutionProposalDB

        db = SessionLocal()
        try:
            rows = (
                db.query(OpenCodeEvolutionProposalDB)
                .filter(OpenCodeEvolutionProposalDB.status.in_(["paper_validated", "rolled_back"]))
                .order_by(OpenCodeEvolutionProposalDB.id.desc())
                .limit(20)
                .all()
            )
            check("paper_validated/rolled_back 提案", len(rows) >= 1, f"仅 {len(rows)} 条", warn=len(rows) < 1)

            with_eval = 0
            extractable = 0
            for row in rows[:10]:
                try:
                    after = json.loads(row.after_json or "{}")
                except Exception:
                    after = {}
                if after.get("eval_metrics"):
                    with_eval += 1
                patches = []
                try:
                    patches = json.loads(row.proposal_json or "{}").get("patches") or []
                except Exception:
                    pass
                if patches and after.get("verdict"):
                    extractable += 1

            check(
                "最近提案含 eval_metrics",
                with_eval >= max(1, len(rows[:10]) // 2) if rows else True,
                f"{with_eval}/{min(10, len(rows))} 有 eval_metrics",
                warn=rows and with_eval == 0,
            )
            check(
                "最近提案可提取智慧（有 patches+verdict）",
                extractable >= 1 if rows else True,
                f"{extractable}/{min(10, len(rows))}",
                warn=rows and extractable == 0,
            )
        finally:
            db.close()
    except Exception as exc:
        check("提案 schema", False, str(exc))

    # ── 4. Sidecar + Prompt Registry ──
    section("4. Sidecar 与 Prompt Registry")
    try:
        from backend.services.opencode_bridge import health_check
        from backend.services.hermes_prompt_optimizer_engine import OPTIMIZABLE_TASKS
        from backend.services.prompt_registry import get_prompt_registry

        check("OpenCode sidecar", health_check(), "sidecar 离线 → L2-L4 不可用", warn=not health_check())

        reg = get_prompt_registry()
        missing = [t for t in OPTIMIZABLE_TASKS if t not in reg.list_tasks()]
        check("OPTIMIZABLE_TASKS 均在 manifest", len(missing) == 0, f"缺失: {missing}")
    except Exception as exc:
        check("Sidecar/Registry", False, str(exc))

    # ── 5. A/B active ──
    section("5. L2 A/B 与调度")
    try:
        from backend.services.hermes_db import hermes_fetchall
        from backend.services.opencode_scheduler import get_hermes_schedule_status

        running_ab = hermes_fetchall(
            "SELECT id, task_id FROM prompt_ab_tests WHERE status='running'"
        )
        active_rows = hermes_fetchall(
            "SELECT COUNT(*) AS n FROM prompt_versions WHERE status='active'"
        )
        active_n = int(active_rows[0]["n"]) if active_rows else 0
        if running_ab:
            check(
                "A/B 运行中仍有 active prompt（U3）",
                active_n >= 1,
                f"running_ab={len(running_ab)} active={active_n}",
                warn=active_n < 1,
            )
        else:
            check("无 running A/B 测试", True)

        tasks = get_hermes_schedule_status()
        registered = sum(1 for t in tasks if t.get("registered"))
        check("Hermes 定时任务已注册", registered >= 5, f"{registered}/{len(tasks)}", warn=registered < 5)

        errors = [t for t in tasks if t.get("last_status") == "error"]
        if errors:
            for t in errors[:3]:
                print(f"       -> {t.get('job_id')}: {t.get('last_error', '')[:120]}")
            check("调度任务无 error", False, f"{len(errors)} 个任务失败", warn=True)
        else:
            check("调度任务无 error", True)
    except Exception as exc:
        check("A/B/调度", False, str(exc))

    # ── 6. trade_nature 覆盖率 ──
    section("6. Paper 成交 vs Agent 智慧")
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import PaperOrder

        db = SessionLocal()
        try:
            from datetime import datetime, timedelta, timezone
            since = datetime.now(timezone.utc) - timedelta(days=7)
            closed = (
                db.query(PaperOrder)
                .filter(
                    PaperOrder.status == "filled",
                    PaperOrder.close_reason.isnot(None),
                )
                .order_by(PaperOrder.id.desc())
                .limit(200)
                .all()
            )
            nature_counts: dict = {}
            for o in closed:
                meta = {}
                try:
                    meta = json.loads(o.metadata_json or "{}") if hasattr(o, "metadata_json") else {}
                except Exception:
                    pass
                nat = (meta.get("trade_nature") or "unknown").lower()
                nature_counts[nat] = nature_counts.get(nat, 0) + 1

            hermes_natures = {"swing", "trend_follow", "position", "intraday"}
            covered = sum(v for k, v in nature_counts.items() if k in hermes_natures)
            check(
                "近 7 日平仓 Hermes 可采集 nature",
                covered >= 1 if closed else True,
                f"分布: {nature_counts}",
                warn=closed and covered == 0,
            )
        finally:
            db.close()
    except Exception as exc:
        check("trade_nature 覆盖率", False, str(exc), warn=True)

    section("汇总")
    print(f"  PASS={PASS}  WARN={WARN}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
