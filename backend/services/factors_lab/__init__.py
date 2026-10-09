# -*- coding: utf-8 -*-
"""factors_lab —— 五Agent因子研究闭环（ADR-21 / v0.3 因子研究中心 Agent侧核心）。

① 文献情报 → ② 假设生成 → ③ 因子工程(DSL AST) → ④ 回测(gpfactor/gpbacktest)
→ ⑤ 反馈记忆；多样性正则前置（假设 Jaccard / AST 相似 / crowded 拒收）。
隔离纪律：只写 backend/data/factors_lab/ 与 v7 记忆表；晋级走 REV-P10/P12，不动 lifecycle。
"""
from backend.services.factors_lab import (
    agent1_literature, agent2_hypothesis, agent3_engineer, agent4_backtester,
    agent5_feedback, calibration, common, config,
)

__all__ = [
    "agent1_literature", "agent2_hypothesis", "agent3_engineer", "agent4_backtester",
    "agent5_feedback", "calibration", "common", "config", "loop", "promotion",
]


def __getattr__(name):
    if name == "loop":
        import importlib

        return importlib.import_module(f"{__name__}.loop")
    raise AttributeError(name)
