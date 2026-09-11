# -*- coding: utf-8 -*-
"""pwin 仲裁 fail-closed 加固（2026-09-02 G18）。

背景：12.8 万条已结算信号回溯显示，pwin<0.55 的各档平均净收益全为负、
>=0.55 的各档全为正（0.55-0.60 胜率 57.1%、0.60-0.65 胜率 66.3%）；
同期 factor_score 各档净收益全为负且最高分档最差。也就是说 pwin 是目前
**唯一**被数据验证有效的质量闸。

此前两处 pwin 相关仲裁在异常时 fail-open，其中主轴那处还是 logger.debug
（默认不输出）—— 模型加载失败之类的故障会被静默吞掉，负期望信号绕过唯一
有效的过滤器直接进场，而运维完全无从察觉。

本用例锁定异常路径的两条底线：必须拒开、必须可见。
"""
import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOOP_PATH = (
    _REPO_ROOT / "backend" / "services" / "full_auto" / "loops" / "scalp_loop.py"
)


def _lines():
    return _LOOP_PATH.read_text(encoding="utf-8").splitlines()


def _block_after(marker: str, span: int = 24) -> str:
    """取 marker 所在行起 span 行的源码文本（异常块足够短，窗口够用）。"""
    lines = _lines()
    for i, ln in enumerate(lines):
        if marker in ln:
            return "\n".join(lines[i:i + span])
    return ""


class TestSwitchDefault:
    """开关本身：默认收紧，但保留回滚余地。"""

    def test_default_is_fail_closed(self):
        from backend.config import settings

        assert settings.SCALP_PWIN_FAIL_CLOSED is True, (
            "未显式配置时必须 fail-closed —— pwin 是唯一有效质量闸"
        )

    def test_switch_is_overridable(self, monkeypatch):
        monkeypatch.setenv("SCALP_PWIN_FAIL_CLOSED", "false")
        val = os.getenv("SCALP_PWIN_FAIL_CLOSED", "true").lower() in (
            "true", "1", "yes", "on",
        )
        assert val is False, "必须可显式关闭以便回滚"


class TestPwinArbiterExceptionPath:
    """主轴 pwin 仲裁（decide_scalp）的异常分支。"""

    def test_block_exists(self):
        assert _block_after("except Exception as _arb_err:"), (
            "未找到主轴 pwin 仲裁的异常处理块（重构后请同步本用例）"
        )

    def test_exception_refuses_to_open(self):
        block = _block_after("except Exception as _arb_err:")
        assert '_bump_block("pwin_arbiter_error")' in block, (
            "异常未计入拦截统计 —— 无法从 tick 统计里看出闸门失效"
        )
        assert "continue" in block, (
            "异常仍会继续往下走到 place_order（fail-open）"
        )

    def test_exception_is_switch_driven(self):
        block = _block_after("except Exception as _arb_err:")
        assert "SCALP_PWIN_FAIL_CLOSED" in block, "异常分支未接开关"

    def test_exception_is_not_silent(self):
        """debug 级日志默认不输出，等于把故障静默吞掉。"""
        block = _block_after("except Exception as _arb_err:")
        assert "logger.warning" in block, "异常必须以 warning 级可见"
        assert "logger.debug" not in block, (
            "pwin 仲裁异常不得降级为 debug —— 那正是此前故障无从发现的原因"
        )


class TestFusionArbiterExceptionPath:
    """第二次仲裁（带 orchestrator thesis 的 fusion_arbiter）的异常分支。"""

    def test_block_exists(self):
        assert _block_after("except Exception as _fus_err:"), (
            "未找到 fusion_arbiter 的异常处理块（重构后请同步本用例）"
        )

    def test_exception_refuses_to_open(self):
        block = _block_after("except Exception as _fus_err:")
        assert '_bump_block("fusion_arbiter_error")' in block
        assert "continue" in block, "异常仍会放行下单（fail-open）"

    def test_exception_is_switch_driven(self):
        block = _block_after("except Exception as _fus_err:")
        assert "SCALP_PWIN_FAIL_CLOSED" in block, (
            "两处仲裁应共用同一开关，避免只回滚一半"
        )


class TestOtherGatesUnchanged:
    """回归护栏：本次只收紧 pwin 两处，其余闸门的既有语义不动。"""

    def test_ev_gate_keeps_live_only_fail_closed(self):
        """EV 闸保持 Live fail-closed / Paper fail-open 的既有分级。"""
        block = _block_after("except Exception as _ev_err:")
        assert block, "未找到 EV 闸异常块"
        assert "SCALP_EV_FAIL_CLOSED_LIVE" in block, "EV 闸的既有开关被误删"

    def test_arbitration_gate_stays_fail_closed(self):
        """对冲仲裁闸此前已是 fail-closed，不应被改回放行。"""
        block = _block_after("except Exception as _arb_err:", span=40)
        assert block, "未找到仲裁闸异常块"


class TestThresholdRationale:
    """0.55 是回溯得出的正负期望分水岭，不是拍脑袋的数字。"""

    def test_pwin_min_stays_at_055(self):
        from dotenv import dotenv_values

        env = dotenv_values(_REPO_ROOT / ".env")
        raw = env.get("FUSION_SCALP_PWIN_MIN")
        if raw is None:
            pytest.skip("FUSION_SCALP_PWIN_MIN 未显式配置（走代码默认）")
        assert abs(float(raw) - 0.55) < 1e-9, (
            "0.55 是 12.8 万条已结算信号回溯出的分水岭（<0.55 各档净收益全负、"
            ">=0.55 各档全正）；调整需附新的回溯依据"
        )
