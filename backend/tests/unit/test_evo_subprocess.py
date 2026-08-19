# -*- coding: utf-8 -*-
"""FIX-4: make_subprocess_task 按 FACTOR_EVO_SUBPROCESS 路由（出进程 vs 进程内回退）。"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.evolution.evo_subprocess import make_subprocess_task


def test_subprocess_disabled_uses_fallback():
    """默认（env=0/未设）走进程内回退函数，返回其返回值。"""
    with patch.dict(os.environ, {"FACTOR_EVO_SUBPROCESS": "0"}, clear=False):
        called = []
        def fallback():
            called.append(1)
            return {"ok": True}
        task = make_subprocess_task("4h", fallback)
        out = task()
        assert out == {"ok": True}
        assert called == [1]


def test_subprocess_enabled_spawns_popen():
    """env=1 时 Popen 出进程，返回 subprocess_pid。"""
    with patch.dict(os.environ, {"FACTOR_EVO_SUBPROCESS": "1"}, clear=False):
        fake = type("Proc", (), {"pid": 12345})()
        def fallback():
            raise AssertionError("不应走进程内回退")
        with patch("backend.services.evolution.evo_subprocess.subprocess.Popen", return_value=fake) as popen:
            task = make_subprocess_task("5m", fallback)
            out = task()
        assert out["subprocess_pid"] == 12345
        assert popen.called
        # 命令为 python -m ...evo_subprocess 5m
        cmd = popen.call_args[0][0]
        assert "backend.services.evolution.evo_subprocess" in cmd
        assert "5m" in cmd
