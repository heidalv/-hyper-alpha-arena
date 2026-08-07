"""MLTO 设计方案执行度审计 — 对照计划逐项检查模块/文件/契约。"""

from __future__ import annotations

import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = FAIL = WARN = 0

CHECKS = []


def check(name: str, ok: bool, detail: str = "", *, warn: bool = False):
    global PASS, FAIL, WARN
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    elif warn:
        WARN += 1
        print(f"  [WARN] {name} — {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} — {detail}")
    CHECKS.append((name, ok, warn, detail))


def section(title: str):
    print(f"\n=== {title} ===")


def main():
    print("MLTO 设计执行度审计\n")

    # P0 核心包
    section("P0 核心模块 mlto/")
    modules = [
        "backend.services.mlto.orchestrator",
        "backend.services.mlto.thesis_store",
        "backend.services.mlto.layered_memory",
        "backend.services.mlto.evidence_ingest",
        "backend.services.mlto.quant_layer",
        "backend.services.mlto.qual_layer",
        "backend.services.mlto.decision_hub",
        "backend.services.mlto.debate_layer",
        "backend.services.mlto.open_gate",
        "backend.services.mlto.tranche_gate",
        "backend.services.mlto.learning_bridge",
        "backend.services.mlto.db_models",
    ]
    for m in modules:
        try:
            importlib.import_module(m)
            check(f"import {m.split('.')[-1]}", True)
        except Exception as exc:
            check(f"import {m.split('.')[-1]}", False, str(exc))

    section("P0 ORM 五表")
    from backend.services.mlto.db_models import (
        MltoThesis, MltoMemoryEvent, MltoThesisEvent, MltoSignalWeight, MltoDebateLog,
    )
    for cls in (MltoThesis, MltoMemoryEvent, MltoThesisEvent, MltoSignalWeight, MltoDebateLog):
        check(f"ORM {cls.__tablename__}", bool(cls.__tablename__))

    section("P1 Feature Flags")
    from backend.config import settings
    check("MIDLONG_THESIS_LEDGER_ENABLED", getattr(settings, "MIDLONG_THESIS_LEDGER_ENABLED", False))
    check("MIDLONG_QUANT_BRIEF_HARD_GATE=false", not getattr(settings, "MIDLONG_QUANT_BRIEF_HARD_GATE", True))
    check("MIDLONG_THESIS_OPEN_GATE", getattr(settings, "MIDLONG_THESIS_OPEN_GATE", False))
    check("MIDLONG_THESIS_DEBATE_ENABLED", getattr(settings, "MIDLONG_THESIS_DEBATE_ENABLED", False))
    check("MIDLONG_TRANCHE_ENTRY_ENABLED", getattr(settings, "MIDLONG_TRANCHE_ENTRY_ENABLED", False))
    check("MIDLONG_THESIS_REGIME_RESET", getattr(settings, "MIDLONG_THESIS_REGIME_RESET", False))

    section("P1 Agent thesis_update")
    from backend.services.swing_agent import swing_agent
    from backend.services.trend_agent import trend_agent
    check("SwingAgent.update_thesis", callable(getattr(swing_agent, "update_thesis", None)))
    check("TrendAgent.update_thesis", callable(getattr(trend_agent, "update_thesis", None)))

    section("P2 执行契约")
    from backend.services.agent_decision_envelope import AgentDecisionEnvelope
    fields = {f.name for f in AgentDecisionEnvelope.__dataclass_fields__.values()}
    for f in ("thesis_id", "hub_adjusted", "open_readiness", "memory_event_ids", "evidence_chain_snapshot", "open_readiness_at_entry"):
        check(f"envelope.{f}", f in fields)
    check("full_auto._execute_mlto_lane", hasattr(
        __import__("backend.services.full_auto_trading_service", fromlist=["FullAutoTradingService"]).FullAutoTradingService,
        "_execute_mlto_lane",
    ))

    section("P2 V5 审计字段")
    import inspect
    from backend.services.decision_core.unified_gate import evaluate_entry
    sig = inspect.signature(evaluate_entry)
    for p in ("thesis_id", "open_readiness", "hub_composite", "hub_adjusted"):
        check(f"evaluate_entry.{p}", p in sig.parameters)

    section("P3 学习闭环")
    from backend.services.decision_feedback_service import decision_feedback_service
    check("get_thesis_constraints", callable(getattr(decision_feedback_service, "get_thesis_constraints", None)))
    from backend.services.learning_bus import get_learning_bus
    check("enqueue_thesis_postmortem", callable(getattr(get_learning_bus(), "enqueue_thesis_postmortem", None)))
    check("macro_regime._notify_mlto_phase_shift", hasattr(
        __import__("backend.services.macro_regime_service", fromlist=["macro_regime_service"]).macro_regime_service,
        "_notify_mlto_phase_shift",
    ))

    section("P4 API & 前端")
    paths = [
        "backend/api/mlto_routes.py",
        "frontend/app/components/atas-v2/MidLongThesisPanel.tsx",
        "docs/MLTO_ARCHITECTURE.md",
        "docs/MID_LONG_EXECUTION_LANE.md",
        "docs/AGENT_DECISION_ENVELOPE.md",
    ]
    for p in paths:
        check(f"file {os.path.basename(p)}", os.path.isfile(os.path.join(ROOT, p.replace("/", os.sep))))

    from backend.api.mlto_routes import router
    routes = [getattr(r, "path", "") for r in router.routes]
    check("API thesis/summary", any("thesis/summary" in x for x in routes))
    check("main.py mlto_router", "mlto_router" in open(os.path.join(ROOT, "backend/main.py"), encoding="utf-8").read())

    section("P5 open_gate.describe_gate_status")
    from backend.services.mlto import open_gate
    check("describe_gate_status", callable(getattr(open_gate, "describe_gate_status", None)))

    section("可选增强项（原 WARN，现已落地）")
    manifest_text = open(os.path.join(ROOT, "docs/opencode/prompts/manifest.yaml"), encoding="utf-8", errors="ignore").read()
    check(
        "task_swing_thesis_update prompt 模板",
        "task_swing_thesis_update" in manifest_text
        and os.path.isfile(os.path.join(ROOT, "docs/opencode/prompts/tasks/task_swing_thesis_update.md")),
    )
    check(
        "task_trend_thesis_update prompt 模板",
        "task_trend_thesis_update" in manifest_text
        and os.path.isfile(os.path.join(ROOT, "docs/opencode/prompts/tasks/task_trend_thesis_update.md")),
    )
    qual_src = open(os.path.join(ROOT, "backend/services/mlto/qual_layer.py"), encoding="utf-8", errors="ignore").read()
    check(
        "qual_layer render_agent_task",
        "render_agent_task" in qual_src and "task_swing_thesis_update" in qual_src,
    )
    check(
        "trading_analysts symbol_tier_slices",
        "symbol_tier_slices" in open(os.path.join(ROOT, "backend/services/trading_analysts.py"), encoding="utf-8", errors="ignore").read(),
    )
    check(
        "hermes wisdom thesis_id 维度",
        "thesis_id" in open(os.path.join(ROOT, "backend/services/hermes_agent_wisdom_engine.py"), encoding="utf-8", errors="ignore").read(),
    )

    section("验收脚本")
    import subprocess
    for script in ("verify_midlong_thesis_chain.py", "mid_long_agent_acceptance_check.py"):
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", script), *(["--days", "7"] if "acceptance" in script else [])],
            capture_output=True, text=True, timeout=180, cwd=ROOT,
        )
        check(script, r.returncode == 0, (r.stderr or r.stdout)[-200:])

    print("\n" + "=" * 50)
    print(f"审计结果: PASS={PASS}  FAIL={FAIL}  WARN={WARN}")
    if FAIL == 0:
        print("设计执行度: 核心项已全部落地（WARN 为可选增强）")
    else:
        print("存在未落地项，请按 FAIL 明细补齐")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
