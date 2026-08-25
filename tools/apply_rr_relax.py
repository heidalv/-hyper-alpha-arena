#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补丁2: decide_scalp 每次决策前重载 .env + RR 分支日志带 pwin(2026-08-25)。"""
import shutil

PATH = "backend/services/decision_fusion_arbiter.py"
BAK = PATH + ".bak_20260825c"
shutil.copy(PATH, BAK)
print("backup ->", BAK)

src = open(PATH, encoding="utf-8").read()

old1 = '''def decide_scalp(
    pwin: Optional[float],'''
new1 = '''def decide_scalp(
    pwin: Optional[float],'''
assert src.count(old1) == 1, f"e1={src.count(old1)}"

old2 = '''    """短线入场仲裁（v2 矩阵，pwin 主轴）。"""
    # 1. LLM 硬否决（清算簇/黑天鹅/流动性告警）→ 因子不可覆盖
    if llm_veto:'''
new2 = '''    """短线入场仲裁（v2 矩阵，pwin 主轴）。"""
    # 0. 每次决策前重载 .env(门槛调整免重启生效)
    _maybe_reload_env()
    # 1. LLM 硬否决（清算簇/黑天鹅/流动性告警）→ 因子不可覆盖
    if llm_veto:'''
assert src.count(old2) == 1, f"e2={src.count(old2)}"
src = src.replace(old2, new2)

# RR 分支 tags 带 pwin 便于日志诊断
old3 = '''        if rr < _rr_floor:
            return FusionDecision("hold", 0.0, "rule", "rr_below_floor",
                                  {"rr": round(rr, 3), "tp_pct": tp_pct, "sl_pct": sl_pct})'''
new3 = '''        if rr < _rr_floor:
            return FusionDecision("hold", 0.0, "rule", "rr_below_floor",
                                  {"rr": round(rr, 3), "tp_pct": tp_pct, "sl_pct": sl_pct,
                                   "pwin": round(float(pwin), 4)})'''
assert src.count(old3) == 1, f"e3={src.count(old3)}"
src = src.replace(old3, new3)

open(PATH, "w", encoding="utf-8").write(src)
print("patched OK")
