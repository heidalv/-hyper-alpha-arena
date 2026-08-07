#!/usr/bin/env python3
"""学习层 + Hermes 自进化深度修复验收。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main() -> int:
    fails = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        if not ok:
            fails += 1
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}" + (f" - {detail}" if detail else ""))

    print("=== verify_learning_layer ===\n")

    # 1. L2 A/B 分流
    try:
        from backend.services.prompt_l2_resolver import resolve_l2_prompt, pick_ab_arm_for_test
        use_b, ratio = pick_ab_arm_for_test("task_swing_agent", "test_consumer")
        check("Hermes AB split ratio", 0.05 <= ratio <= 0.95, f"ratio={ratio}")
        check("Hermes AB arm pick", isinstance(use_b, bool))
    except Exception as e:
        check("Hermes AB resolver", False, str(e))

    # 2. PromptRegistry 接入 resolver
    try:
        from backend.services.prompt_registry import get_prompt_registry
        text = get_prompt_registry().render_task("task_swing_agent", {}, consumer="verify")
        check("PromptRegistry render+consumer", isinstance(text, str) and len(text) > 10)
    except Exception as e:
        check("PromptRegistry", False, str(e))

    # 3. BackendRegistry bridge
    try:
        from backend.services.learning_registry_bridge import get_registry
        reg = get_registry()
        names = [b.name for b in reg.list_backends()]
        check("Registry bridge loaded", len(names) >= 13, f"count={len(names)}")
        check("block_pattern_learning", "block_pattern_learning" in names)
        check("hermes_agent_wisdom", "hermes_agent_wisdom" in names)
    except Exception as e:
        check("Registry bridge", False, str(e))

    # 4. RuntimeGovernor 扩展
    try:
        from backend.services.runtime_governor import runtime_governor, GOVERNED_KEYS
        om = runtime_governor.get_ownership_map()
        check("Governor ownership", "hermes_l2_prompt" in om)
        check("Governor list_pending", callable(runtime_governor.list_pending))
        check("Governor propose_hermes_prompt", callable(runtime_governor.propose_hermes_prompt))
        check("Governor submit_intent", callable(runtime_governor.submit_intent))
        check("Governor withdraw", callable(runtime_governor.withdraw))
        r = runtime_governor.submit_intent(
            "min_risk_reward", 1.85, source="manual", confidence=0.9, reason="verify",
            ttl_sec=60,
        )
        check("Governor submit_intent roundtrip", r.get("ok") is True)
        runtime_governor.withdraw("manual", ["min_risk_reward"])
        check("Governor GOVERNED_KEYS", len(GOVERNED_KEYS) >= 4)
    except Exception as e:
        check("RuntimeGovernor", False, str(e))

    # 5. L3 accept
    try:
        from backend.services.hermes_architecture_evolution_engine import architecture_evolution
        check("L3 accept_proposal", callable(architecture_evolution.accept_proposal))
    except Exception as e:
        check("L3 architecture", False, str(e))

    # 6. L4 promote
    try:
        from backend.services.hermes_strategy_genesis_engine import strategy_genesis
        check("L4 propose_promote", callable(strategy_genesis.propose_promote_validated))
    except Exception as e:
        check("L4 genesis", False, str(e))

    # 7. PROMPT_EVOLUTION 默认关 + L2 默认直接激活
    try:
        from backend.config.settings import PROMPT_EVOLUTION_ENABLED, HERMES_AB_TRAFFIC_RATIO, HERMES_L2_AB_ENABLED
        check("PROMPT_EVOLUTION default off", PROMPT_EVOLUTION_ENABLED is False)
        check("HERMES_L2_AB default off (direct active)", HERMES_L2_AB_ENABLED is False)
        check("HERMES_AB_TRAFFIC_RATIO", 0.05 <= HERMES_AB_TRAFFIC_RATIO <= 0.95)
    except Exception as e:
        check("Settings", False, str(e))

    # 8. recover stuck
    try:
        from backend.services.hermes_prompt_optimizer_engine import PromptOptimizerEngine
        rec = PromptOptimizerEngine().recover_stuck_versions()
        check("recover_stuck_versions", "recovered" in rec or "skipped" in rec, str(rec.get("count", rec)))
    except Exception as e:
        check("recover_stuck", False, str(e))
    # 9. Block pattern backend
    try:
        from backend.services.learning.backends.block_pattern_learning_backend import BlockPatternLearningBackend
        be = BlockPatternLearningBackend()
        check("BlockPattern backend", be.name == "block_pattern_learning")
    except Exception as e:
        check("BlockPattern", False, str(e))

    # 10. Prompt training 直接激活 + drain API
    try:
        from backend.config.settings import PROMPT_TRAINING_AB_ENABLED
        from backend.services.prompt_training_system import PromptTrainingSystem, _ab_enabled
        from backend.services.opencode_proposal_reviewer import drain_pending_proposals
        check("PROMPT_TRAINING_AB default off", PROMPT_TRAINING_AB_ENABLED is False)
        check("prompt_training _ab_enabled", _ab_enabled() is False)
        check("PromptTraining recover_stuck", callable(PromptTrainingSystem.recover_stuck_ab_tests))
        check("OpenCode drain_pending", callable(drain_pending_proposals))
        from backend.services.hermes_architecture_evolution_engine import architecture_evolution
        check("L3 auto_accept_pending_paper", callable(architecture_evolution.auto_accept_pending_paper))
        check("L3 mark_implemented", callable(architecture_evolution.mark_implemented))
        check("L3 reconcile_implemented_paper", callable(architecture_evolution.reconcile_implemented_paper))
        from backend.config.settings import (
            ANALYST_RULES_PARALLEL,
            FULLAUTO_ORCH_SKIP_SYNC_WHEN_CACHE_FRESH,
        )
        check("ANALYST_RULES_PARALLEL default on", ANALYST_RULES_PARALLEL is True)
        check("ORCH skip sync when cache fresh", FULLAUTO_ORCH_SKIP_SYNC_WHEN_CACHE_FRESH is True)
    except Exception as e:
        check("P1 prompt/opencode", False, str(e))

    # 11. API routes import
    try:
        from backend.api.hermes_routes import hermes_accept_architecture, hermes_promote_genesis
        check("Hermes API accept/promote", callable(hermes_accept_architecture) and callable(hermes_promote_genesis))
    except Exception as e:
        check("Hermes API", False, str(e))

    print(f"\n=== result === FAIL={fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
