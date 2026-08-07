#!/usr/bin/env python3
"""审计：短线因子 / 中长线 AI 路径是否仍被拦截。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import settings as s  # noqa: E402


def main() -> int:
    checks = []

    def ok(name: str, cond: bool, detail: str) -> None:
        checks.append((name, cond, detail))
        mark = "PASS" if cond else "FAIL"
        print(f"[{mark}] {name}: {detail}")

    ok(
        "短线 HYBRID 预筛选",
        s.HYBRID_SIGNAL_MODE_ENABLED,
        f"HYBRID_SIGNAL_MODE_ENABLED={s.HYBRID_SIGNAL_MODE_ENABLED}",
    )
    ok(
        "中长线 AI 强制",
        s.MIDLONG_AI_MANDATORY,
        f"MIDLONG_AI_MANDATORY={s.MIDLONG_AI_MANDATORY} flow={s.FULLAUTO_FLOW_MODE}",
    )
    ok(
        "中长线开单走 MLTO 新链路",
        s.MIDLONG_MLTO_CONTROLS_EXEC,
        f"MIDLONG_MLTO_CONTROLS_EXEC={s.MIDLONG_MLTO_CONTROLS_EXEC}",
    )
    ok(
        "MLTO 账本仍可维护",
        s.MIDLONG_THESIS_LEDGER_ENABLED,
        f"MIDLONG_THESIS_LEDGER_ENABLED={s.MIDLONG_THESIS_LEDGER_ENABLED}",
    )
    ok(
        "QuantBrief 不硬拦截",
        not s.MIDLONG_QUANT_BRIEF_HARD_GATE,
        f"MIDLONG_QUANT_BRIEF_HARD_GATE={s.MIDLONG_QUANT_BRIEF_HARD_GATE}",
    )
    ok(
        "全局 AI 主导默认关（不误伤短线因子）",
        not s.FULLAUTO_AI_DOMINANT,
        f"FULLAUTO_AI_DOMINANT={s.FULLAUTO_AI_DOMINANT}",
    )
    ok(
        "QAA 深度 tick",
        getattr(s, "QAA_DEEP_ANALYSIS_EVERY_N_TICKS", 99) <= 1,
        f"QAA_DEEP_ANALYSIS_EVERY_N_TICKS={getattr(s, 'QAA_DEEP_ANALYSIS_EVERY_N_TICKS', '?')}",
    )

    failed = [n for n, c, _ in checks if not c]
    print("-" * 60)
    if failed:
        print(f"审计未通过: {', '.join(failed)}")
        return 1
    print("审计通过：架构=短线因子+AI辅助，中长线 MLTO(LLM thesis_update) 主决策")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
