# -*- coding: utf-8 -*-
"""[P6 bug 修复⑥ 2026-09-28] 同向持仓极端安全网可配契约测试。"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def test_safety_net_is_env_configurable():
    """常量必须从 env 读取（回滚 = 设回 10）。"""
    src = (ROOT / "backend" / "services" / "position_memory_manager.py"
           ).read_text(encoding="utf-8")
    assert 'MIDLONG_MAX_SAME_DIRECTION_SAFETY_NET' in src
    assert 'os.getenv("MIDLONG_MAX_SAME_DIRECTION_SAFETY_NET"' in src


def test_default_remains_10_for_rollback(monkeypatch):
    import importlib
    from backend.services import position_memory_manager as pmm
    # 空字符串 = 未配置 → 回退旧值 10（回滚语义）
    monkeypatch.setenv("MIDLONG_MAX_SAME_DIRECTION_SAFETY_NET", "")
    importlib.reload(pmm)
    assert pmm.MAX_SAME_DIRECTION_POSITIONS_SAFETY_NET == 10, \
        "缺省必须是旧值 10（回滚语义）"


def test_deployment_env_is_aligned():
    from dotenv import load_dotenv
    load_dotenv(str(ROOT / ".env"), override=False)
    v = os.getenv("MIDLONG_MAX_SAME_DIRECTION_SAFETY_NET")
    assert v is not None and int(v) == 16, f"部署值应为 16（§13.2 对齐），实际 {v}"
