# -*- coding: utf-8 -*-
"""[续作 R14] 量化特征表注入活主脑 prompt 的单测。

背景（台账 §45）：`render_quant_feature_table` 此前只被已停跑的 trend_agent/swing_agent 使用，
活主脑 prompt 里没有这块。现以开关 `MIDLONG_BRAIN_FEATURE_TABLE`（默认 true）注入；
注入失败只 debug，不影响 prompt 组装。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.mlto import brain as B

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MIDLONG_BRAIN_FEATURE_TABLE", raising=False)
    yield


def test_switch_defaults_on(monkeypatch):
    assert B._feature_table_enabled() is True
    monkeypatch.setenv("MIDLONG_BRAIN_FEATURE_TABLE", "false")
    assert B._feature_table_enabled() is False
    monkeypatch.setenv("MIDLONG_BRAIN_FEATURE_TABLE", "garbage")
    assert B._feature_table_enabled() is False  # 非法值 fail-closed：不注入


def test_injection_site_has_guard_and_marker():
    """源码守卫：注入点必须存在，且带开关与 fail-safe（全文件唯一子串，不依赖窗口长度）。"""
    src = (ROOT / "backend/services/mlto/brain.py").read_text(encoding="utf-8")
    i = src.find("_feat_block = ")
    assert i >= 0, "未找到注入点"
    seg = src[i:i + 4000]  # 窗口加长：注入区段后续又加入了 K 线块（续作R18），勿再缩短
    assert "if _feature_table_enabled():" in seg, "注入必须受开关保护"
    assert "【量化特征表】" in seg, "注入段标题必须存在"
    assert "except Exception" in seg, "注入失败必须 fail-safe"
    assert "SessionLocal" in seg, "交易记忆/冷却/配额在 core 库，须用核心会话"
    assert "render_quant_feature_table" in seg


def test_failure_of_renderer_never_blocks_prompt():
    """渲染器抛异常时 prompt 组装必须继续（源码级断言：except 只 debug）。"""
    src = (ROOT / "backend/services/mlto/brain.py").read_text(encoding="utf-8")
    assert 'logger.debug("[MidLongBrain] feature_table 注入跳过' in src
    # 渲染为空时走条件分支，不抛异常
    assert "if _feat_block else" in src
    # 注入成功的可见确认也必须在（与体检同节流）
    assert "feature_table 注入 chars=" in src
