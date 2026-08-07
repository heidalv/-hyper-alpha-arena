"""decision_core 门面导出 — 修复 V5 门控 import 失败。"""


def test_decision_core_exports_evaluate_open_decision():
    from backend.services.decision_core import evaluate_open_decision, build_v5_prompt_block

    assert callable(evaluate_open_decision)
    assert callable(build_v5_prompt_block)
