# -*- coding: utf-8 -*-
"""实验卡闭环（v3 方向 3，p2-agents-b）。

方案要求「任何复盘建议必须写成实验卡：假设 / 改动 / 预期指标与阈值 / 验证窗口 / 回滚条件」，
并且要真的**跑完**——不能永远挂在 proposed。本包补齐让卡片能自己走完一生的两件事：

  metrics.py     `expected_metrics` 的求值器：把 {"metric","op","threshold","scope"} 算成真实数值
  lifecycle.py   生命周期推进：proposed → running → evaluating → adopted / rejected / extended

判定只用真实账本数据（signal_ledger / agent_predictions / edge_ledger / paper_orders）；
样本不足一律返回 `ok=False` 并让卡片 `extended`（延长观察），绝不用占位值凑一个结论。
"""
from backend.services.experiments.metrics import (  # noqa: F401
    METRIC_REGISTRY,
    evaluate_metric,
    evaluate_expected_metrics,
)
from backend.services.experiments.lifecycle import (  # noqa: F401
    advance_experiments,
    evaluate_experiment,
    start_experiment,
)
