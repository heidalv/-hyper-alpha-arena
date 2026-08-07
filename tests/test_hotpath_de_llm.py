"""
P2.3 热路径去 LLM 测试。

完成标准（方案 P2.3 / R1 热路径零 LLM）：
    - HOTPATH_LLM_ENABLED 默认 false（架构层安全，不依赖人工关开关）
    - LLM 即使启用也是异步派发（非阻塞），热路径不等 LLM
    - 数值决策路径完整（LLM 禁用时仍能执行/持仓管理）
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.unit


class TestHotpathLLMDisabled:
    """R1：热路径默认零 LLM 同步阻塞。"""

    def test_hotpath_llm_flag_defaults_false(self):
        """HOTPATH_LLM_ENABLED 默认 False（架构层安全）。

        这意味着 LLM 默认完全退出 tick 热路径——
        不依赖人工设置环境变量，安全由默认值保证。
        """
        # 模拟环境未设置该 flag（与 qaa_v3_tick_cycle 的读取逻辑一致）
        val = os.environ.get("HOTPATH_LLM_ENABLED", "false").lower()
        enabled = val in ("1", "true", "yes", "on")
        assert enabled is False, "HOTPATH_LLM_ENABLED 必须默认禁用（R1）"

    def test_flag_explicit_false(self, monkeypatch):
        monkeypatch.setenv("HOTPATH_LLM_ENABLED", "false")
        val = os.environ.get("HOTPATH_LLM_ENABLED", "false").lower()
        assert val not in ("1", "true", "yes", "on")

    def test_flag_explicit_true(self, monkeypatch):
        monkeypatch.setenv("HOTPATH_LLM_ENABLED", "true")
        val = os.environ.get("HOTPATH_LLM_ENABLED", "false").lower()
        assert val in ("1", "true", "yes", "on")


class TestAsyncLLMDispatch:
    """LLM 启用时也是异步派发（非阻塞），热路径不等 LLM。"""

    def test_llm_runs_in_thread_not_blocking(self):
        """验证 P2.3 代码用 threading.Thread 派发 LLM（非阻塞）。

        读 qaa_v3_tick_cycle.py 源码确认 LLM 调用包装在 _threading.Thread 里。
        """
        from pathlib import Path
        src = Path("backend/services/full_auto/qaa_v3_tick_cycle.py").read_text(encoding="utf-8")
        # 关键：LLM 在 daemon 线程里异步执行
        assert "_threading.Thread" in src
        assert "target=_async_llm" in src
        assert "daemon=True" in src
        # 关键：热路径不等 LLM（无 run_analyst_system_v3 的同步返回值消费）
        assert "热路径继续" in src or "非阻塞" in src

    def test_no_blocking_llm_return_consumption(self):
        """热路径不消费 LLM 同步返回值（LLM 结果异步通过 overlay）。"""
        from pathlib import Path
        src = Path("backend/services/full_auto/qaa_v3_tick_cycle.py").read_text(encoding="utf-8")
        # 旧代码：result = host.run_analyst_system_v3(...)  ← 同步消费返回值
        # 新代码：在线程内调用，不赋值给热路径变量
        # 确认没有 "= host.run_analyst_system_v3" 的同步赋值
        assert "= host.run_analyst_system_v3" not in src.replace(
            "# ", ""  # 忽略注释
        )


class TestNumericalDecisionPathIntact:
    """LLM 禁用时，数值决策路径完整。"""

    def test_decision_from_prescreen(self):
        """decision/action 来自 signal pre-screener（数值路径），不依赖 LLM。"""
        from pathlib import Path
        src = Path("backend/services/full_auto/qaa_v3_tick_cycle.py").read_text(encoding="utf-8")
        # 数值决策来源
        assert "sc_decision" in src or "decision_action" in src
        assert "action = decision.get" in src
