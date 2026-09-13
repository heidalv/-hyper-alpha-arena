# -*- coding: utf-8 -*-
"""[M8 2026-09-14] AI 链活链加固契约测试。

背景（审计实证）：
- ops/v3_jobs.py:96-97 裸 `except ImportError: pass` 可静默杀掉全部扩展定时任务。
- 「LLM 定方向需框架同意」闸随旧 orchestrator 下线成死路径，现网 LLM 方向不经
  任何框架校验（LLM 方向 24h 胜率 0.434/0.343，n=192）。
- OWM 权重（mlto_signal_weights）写活跃、读全死（write-only）。

契约：
- v3_jobs 扩展导入 ImportError → WARNING 可见且主流程继续（不再静默）。
- 框架同意闸：long 需 fw≥0.55；short 需 fw≤0.45；不一致时框架决定性取框架方向，
  否则 neutral；开关关闭 = 旧行为。
- OWM 乘子 clamp [0.5,1.5] 作用于 conviction；非法权重回退 1.0。
"""
import importlib
import logging
import sys
import types

import pytest


# ── 1. v3_jobs 静默修复 ──

def test_v3_jobs_import_error_is_visible(monkeypatch, caplog):
    import backend.services.ops.v3_jobs as v3j

    calls = []

    class _Sched:
        def start(self):
            pass

        def add_cron_task(self, **kw):
            calls.append(kw)

        def add_interval_task(self, **kw):
            calls.append(kw)

    import backend.services.scheduler as _sched_mod
    monkeypatch.setattr(_sched_mod, "task_scheduler", _Sched(), raising=False)
    # 强制 v3_jobs_ext 导入失败（sys.modules 置 None → ImportError）
    monkeypatch.setitem(sys.modules, "backend.services.ops.v3_jobs_ext", None)
    with caplog.at_level(logging.WARNING, logger="backend.services.ops.v3_jobs"):
        registered = v3j.register_v3_jobs()
    assert "v3_jobs_ext" in caplog.text and "失败" in caplog.text  # WARNING 可见
    assert isinstance(registered, list)  # 主流程未中断


# ── 2. 框架同意闸 ──

def test_fw_agree_long_requires_fw55():
    from backend.services.mlto.brain import _framework_agree_adjust
    assert _framework_agree_adjust("long", 0.70) == "long"      # 同向放行
    assert _framework_agree_adjust("long", 0.55) == "long"      # 边界放行
    assert _framework_agree_adjust("long", 0.54) == "neutral"   # 框架不决定性 → neutral
    assert _framework_agree_adjust("long", 0.30) == "short"     # 框架决定性反向 → 回落框架


def test_fw_agree_short_requires_fw45():
    from backend.services.mlto.brain import _framework_agree_adjust
    assert _framework_agree_adjust("short", 0.30) == "short"
    assert _framework_agree_adjust("short", 0.45) == "short"
    assert _framework_agree_adjust("short", 0.46) == "neutral"
    assert _framework_agree_adjust("short", 0.70) == "long"


def test_fw_agree_off_keeps_llm_direction():
    from backend.services.mlto.brain import _framework_agree_adjust
    assert _framework_agree_adjust("long", 0.30, require_agree=False) == "long"
    assert _framework_agree_adjust("short", 0.70, require_agree=False) == "short"
    assert _framework_agree_adjust("neutral", 0.90) == "neutral"


# ── 3. OWM 乘子 ──

def test_owm_scales_and_clamps_conviction():
    from backend.services.mlto.brain import _apply_owm_to_conviction
    assert _apply_owm_to_conviction(60, 1.5) == 90     # 连胜加码
    assert _apply_owm_to_conviction(60, 0.5) == 30     # 连败打折
    assert _apply_owm_to_conviction(90, 1.5) == 100    # clamp 上界
    assert _apply_owm_to_conviction(10, 0.5) == 5      # clamp 下界
    assert _apply_owm_to_conviction(60, 1.0) == 60     # 中性不变
    assert _apply_owm_to_conviction(60, None) == 60    # 缺权重回退 1.0
    assert _apply_owm_to_conviction(60, 99.0) == 90    # 非法放大值被 clamp 到 1.5
