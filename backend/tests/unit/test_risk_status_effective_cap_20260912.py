# -*- coding: utf-8 -*-
"""[2026-09-12 F38w] /api/risk/status 显示口径 = 生效口径模式锁。

现场：面板 max_daily_trades 显示 50（settings 兜底默认），而主门实际按
runtime_tuning=20 拦单——运维看面板以为 50，实际 20（安全网 30）。
日配额单一来源是 runtime_tuning.json，显示必须与之一致。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.api import risk_routes  # noqa: E402


def test_status_reads_effective_tuning_cap():
    src = inspect.getsource(risk_routes.get_risk_monitor_status)
    assert 'get_tuning_int("max_daily_trades"' in src, "状态端点必须读 runtime_tuning 生效值"
    assert '"max_daily_trades": svc.config.max_daily_trades' not in src, "禁止直接显示兜底默认值（口径分裂）"
