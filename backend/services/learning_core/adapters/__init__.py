"""统一进化学习内核 — 适配器层

薄封装现有引擎（假设 / Hermes / 在线学习 / 进化），"合门面、不改内核"。
所有导入都做惰性 + 异常保护，缺失任一子系统都不影响内核整体可用（实盘安全）。
"""

from .hypothesis_adapter import HypothesisAdapter
from .hermes_adapter import HermesAdapter
from .learning_adapter import LearningAdapter

__all__ = ["HypothesisAdapter", "HermesAdapter", "LearningAdapter"]
