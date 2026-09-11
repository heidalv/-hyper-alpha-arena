# -*- coding: utf-8 -*-
"""signal_engine 包出口。

[2026-08-29 修复] __init__ 曾被清空导致历史导入点
`from backend.services.signal_engine import unified_signal_bus` 全部 ImportError
（实测 /api/signals/unified/BTC 500——由 test_full_flow_integration 陈旧断言
意外揪出的真实生产回归）。恢复懒再导出，保持包导入零副作用。
"""
from __future__ import annotations

from typing import Any

_LAZY = ("unified_signal_bus", "UnifiedSignalBus")


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        from backend.services.signal_engine import signal_bus as _sb
        return getattr(_sb, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY))
