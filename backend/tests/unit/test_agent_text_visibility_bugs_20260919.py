# -*- coding: utf-8 -*-
"""[2026-09-19 Agent 排查] 分析正文可见性 + 两个真实故障的回归锁。

## 一、正文可见性（M2/M3）
排查实测：主脑的正文被**硬编码截断**，界面上看不到 agent 在说什么：
- `full_auto_routes.py` 活动流 `reasoning[:120]` → **mid 83% / long 68% 的条目正好顶在 120 字符**；
- 而同一批论题的 `reasoning_content` 中位 **2853~3809 字符**（最长 4472）⇒ 界面只见约 **4%**；
- `atas_routes.py` 决策流 `reasoning[:200]`。
现集中到 `backend/utils/text_clip.py`（默认 800，`AGENT_ACTIVITY_REASONING_MAX` 可调，0=不截断）。

## 二、两个真实故障
1. **`execution_qa.advise` 每日必崩**：`execution_qa.py` 读 `w['avg_bp']`，而 `worst_symbols`
   的键是 `median_bp`（同文件 :215）⇒ `KeyError('avg_bp')`。
   现场日志：`[Agent:execution_qa] advise 失败: 'avg_bp'`（09-18 07:15、09-19 07:15 两次），
   `latest_execution_qa.json` 里 `errors=["advise: 'avg_bp'"]`。
2. **故障被 `ok=true` 掩盖**：`base.py` 原先只有以 `analyze` 开头的错误才置 `ok=False`
   ⇒ `/api/agents/status` 一直显示 `last_run_ok: true`。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.utils import text_clip  # noqa: E402

FULL_AUTO = ROOT / "backend" / "api" / "full_auto_routes.py"
ATAS = ROOT / "backend" / "api" / "atas_routes.py"
EXEC_QA = ROOT / "backend" / "services" / "agents" / "execution_qa.py"
BASE = ROOT / "backend" / "services" / "agents" / "base.py"


def _code(path: Path) -> str:
    """剥离注释与 docstring 后的**代码文本**。

    必要性：本文件的源码断言必须打在"代码"上，而不是"注释"上——
    修复时我们会在注释里写明旧写法（如 `w['avg_bp']`），
    若不剥离注释，断言会把解释性文字误判成缺陷残留。
    """
    src = path.read_text(encoding="utf-8")
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    src = re.sub(r"'''[\s\S]*?'''", "", src)
    src = re.sub(r"#[^\n]*", "", src)
    return src


# ───────────────────────── 一、截断口径 ─────────────────────────

def test_default_is_800_not_120(monkeypatch):
    monkeypatch.delenv("AGENT_ACTIVITY_REASONING_MAX", raising=False)
    assert text_clip.reasoning_max() == 800
    long_text = "字" * 2000
    assert len(text_clip.clip(long_text)) == 800, "默认口径应为 800（旧硬编码为 120）"


def test_env_override_and_zero_means_no_clip(monkeypatch):
    monkeypatch.setenv("AGENT_ACTIVITY_REASONING_MAX", "1500")
    assert len(text_clip.clip("字" * 3000)) == 1500
    monkeypatch.setenv("AGENT_ACTIVITY_REASONING_MAX", "0")
    assert text_clip.clip("字" * 3000) == "字" * 3000, "0 必须表示不截断"


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("AGENT_ACTIVITY_REASONING_MAX", "not-a-number")
    assert text_clip.reasoning_max() == 800
    monkeypatch.setenv("AGENT_ACTIVITY_REASONING_MAX", "-5")
    assert text_clip.reasoning_max() == 0, "负数按 0（不截断）处理，不得变成负数切片"


def test_short_text_untouched_and_none_safe():
    assert text_clip.clip("short") == "short"
    assert text_clip.clip(None) == ""
    out, truncated = text_clip.clip_with_flag("字" * 900)
    assert truncated is True and len(out) == 800
    out2, truncated2 = text_clip.clip_with_flag("ok")
    assert truncated2 is False and out2 == "ok"


def test_call_sites_use_shared_clip_not_hardcoded_slices():
    """钉住调用点：活动流/决策流不得再出现写死的 `[:120]` / `[:200]`（只看代码，不看注释）。"""
    fa_all = FULL_AUTO.read_text(encoding="utf-8")
    at_all = ATAS.read_text(encoding="utf-8")
    assert "_clip_text" in fa_all and "text_clip import clip" in fa_all
    assert "_clip_text" in at_all and "text_clip import clip" in at_all

    fa = _code(FULL_AUTO)
    at = _code(ATAS)
    assert 's.ai_reasoning or "")[:120]' not in fa, "活动流仍在写死 [:120]"
    assert 'str(ev[10] or "")[:120]' not in fa, "论题事件正文仍在写死 [:120]"
    assert "d.reason[:200]" not in at, "决策流仍在写死 [:200]"


# ───────────────────── 二、execution_qa advise 不再崩 ─────────────────────

def _slippage_findings():
    return {
        "issues": [{
            "account_id": 14,
            "type": "slippage",
            "value": 9.7,
            "threshold": 8.0,
            "worst": [
                {"symbol": "BTC", "n": 6, "median_bp": 12.3},
                {"symbol": "ETH", "n": 4, "median_bp": 10.1},
            ],
            "symbols": ["BTC", "ETH"],
        }]
    }


def test_execution_qa_advise_does_not_raise_keyerror():
    """现场故障：`KeyError('avg_bp')` —— 每次滑点告警必崩。"""
    from backend.services.agents import execution_qa

    agent = execution_qa.build()
    advs = agent.advise(_slippage_findings())          # 旧实现此处必抛 KeyError
    assert advs, "滑点告警应产出建议（旧实现因 KeyError 恒为空）"
    reason = advs[0].reason
    assert "BTC" in reason and "12.3" in reason, f"应带最差币与中位滑点，实际: {reason}"


def test_worst_symbols_without_bp_still_renders_symbol():
    """防御：`worst` 里若两个键都没有，至少要显示币种，不得再抛。"""
    from backend.services.agents import execution_qa

    agent = execution_qa.build()
    f = _slippage_findings()
    f["issues"][0]["worst"] = [{"symbol": "SOL", "n": 3}]
    advs = agent.advise(f)
    assert advs and "SOL" in advs[0].reason


def test_source_reads_median_bp():
    src = _code(EXEC_QA)
    assert "w['avg_bp']" not in src, "旧键名 avg_bp 不应再出现在代码里"
    assert "median_bp" in src


# ───────────────────── 三、ok=False 不再被掩盖 ─────────────────────

class _AdviseBoomAgent:
    """最小可用假 agent：analyze 正常、advise 崩 —— 复现 execution_qa 的故障形态。"""

    def __new__(cls):
        from backend.services.agents.base import ObservationAgent

        class _A(ObservationAgent):
            agent_id = "test_advise_boom"
            description = "测试用"
            kinds = ()

            def analyze(self, errors):
                return {"ok": True}

            def advise(self, findings):
                raise KeyError("avg_bp")   # 与现场同形态

        a = _A()
        # 避开真实可信度查询（需要库/账本），直接给定 observe 模式与通过的判定
        a.effective_mode = lambda: ("observe", {"agent": a.agent_id}, {"passed": True})  # type: ignore[assignment]
        return a


def test_advise_failure_sets_ok_false():
    agent = _AdviseBoomAgent()
    res = agent.run(dry_run=True)          # dry_run ⇒ 不写 latest_*.json
    assert res.ok is False, "advise 阶段失败必须 ok=False（旧实现仍报 true，掩盖了两天）"
    assert any(e.startswith("advise") for e in res.errors), f"errors 应含 advise 失败: {res.errors}"


def test_base_uses_any_error_rule():
    src = BASE.read_text(encoding="utf-8")
    assert 'any(e.startswith("analyze") for e in errors)' not in src, \
        "旧的『仅 analyze 开头才置假』规则仍在（故障会被掩盖）"
    assert "res.ok = res.ok and not errors" in src


def test_status_surface_still_reads_ok():
    """确保这条修复是有观测价值的：status 确实把 ok 暴露成 last_run_ok。"""
    src = BASE.read_text(encoding="utf-8")
    assert '"last_run_ok": latest.get("ok")' in src
