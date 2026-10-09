# -*- coding: utf-8 -*-
"""混合打分中心（ADR-23 × AI选币集成 HC-v1）。

双通道证据融合：通道A（因子特征 + LightGBM lambdarank 排序）×
通道B（证据包 + LLM 综合器）→ James-Stein 收缩融合 + regime gate。
默认 shadow 模式：只计算只落盘只统计，不影响选币注入（影子隔离纪律）。
"""
from backend.services.hybrid_scoring import channel_a, channel_b, config, evidence, fusion, kpanel, ltr, report

__all__ = [
    "channel_a", "channel_b", "config", "evidence", "fusion",
    "kpanel", "ltr", "report", "service",
]


def __getattr__(name):  # 惰性导入 service（其自身 import 本包其他子模块，防初始化环）
    if name == "service":
        import importlib

        return importlib.import_module(f"{__name__}.service")
    raise AttributeError(name)
