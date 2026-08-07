"""
阶段1 中线失效止血 — stopgap 单元测试（3 个 killer）

Killer A — exit_plan 硬校验：LLM 返回 v2 扁平 sl_pct/tp_pct（无 tp_stages、无
invalidation）时不再拒单，而是自动合成 exit_plan。即便扁平值恰好等于代码兜底默认
(0.035/0.07) 也接受。此前任何缺少 tp_stages+invalidation 的输出都被一刀切降级 hold。

Killer B — MTF blend 稀释：llm_weight 0.70→0.90。原 0.70 把 LLM conf=55 与中性
MTF(35) 融成 ≈49（< 52 地板），should_open 永远 False；0.90 时融成 ≈53，重回地板上方。

Killer C — wrapper TypeError：_try_execute_independent_agent_open 签名须接受
tp_sl_proposal/invalidation_condition/expected_hold_hours（2026-07-21 已修，这里钉
回归）。
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ────────────────────────────────────────────────────────────────────
# Killer A — 扁平 sl_pct/tp_pct 不再被 exit_plan 校验拒单
# ────────────────────────────────────────────────────────────────────
class TestKillerAExitPlanFlatAccept:
    """LLM 给了非零扁平 sl_pct/tp_pct（无 tp_stages / invalidation）→ 不拒单。

    覆盖两个场景：
      1. 扁平值非默认（0.03/0.06）— 修复前后都应通过（回归保护）。
      2. 扁平值恰好等于代码默认（0.035/0.07）— 修复后必须通过（核心止血点）。
    """

    def _normalize(self, result, market_envs=None):
        from backend.services.swing_agent import SwingAgent
        # SwingAgent 是单例；直接调用 _normalize 这条纯函数链路，避免走 LLM。
        agent = SwingAgent()
        return agent._normalize(result, "BTC", market_envs=market_envs or {})

    def test_flat_non_default_sl_tp_is_not_rejected(self):
        """LLM 给 sl=0.03/tp=0.06（无 tp_stages）→ should_open 可为 True。"""
        result = {
            "action": "buy",
            "confidence": 60,            # 高于 52 地板
            "direction": "long",
            "sl_pct": 0.03,
            "tp_pct": 0.06,              # RR = 2.0 ≥ 1.6
            "risk_reward": 2.0,
            "reasoning": "flat v2 plan, no tp_stages",
        }
        dec = self._normalize(result)
        assert dec.should_open is True, (
            f"Killer A 回归：非默认扁平 sl/tp 不应被 exit_plan 拒单 "
            f"(hold_reason={dec.hold_reason!r})"
        )
        # 应已自动补全 v3 结构
        assert dec.tp_sl_proposal.get("tp_stages"), "应合成 tp_stages"
        assert dec.invalidation.get("condition"), "应合成 invalidation.condition"

    def test_flat_default_equal_sl_tp_is_not_rejected(self):
        """核心止血点：扁平 sl=0.035/tp=0.07（等于代码默认）→ 也不拒单。

        修复前若 LLM 恰好返回默认值会被当成"纯代码兜底"误判拒单；修复后接受。
        """
        result = {
            "action": "buy",
            "confidence": 60,
            "direction": "long",
            "sl_pct": 0.035,
            "tp_pct": 0.07,
            "risk_reward": 2.0,
            "reasoning": "flat v2 plan equal to code defaults",
        }
        dec = self._normalize(result)
        assert dec.should_open is True, (
            f"Killer A 止血失败：等于默认值的扁平 sl/tp 不应被拒单 "
            f"(hold_reason={dec.hold_reason!r})"
        )

    def test_truly_missing_flat_plan_still_rejected(self):
        """LLM 既没给 tp_stages 也没给扁平 sl/tp（0/缺）→ 仍应拒单（守住底线）。"""
        result = {
            "action": "buy",
            "confidence": 60,
            "direction": "long",
            # sl_pct / tp_pct 缺失 → 走代码默认，但 raw 字段缺失 → 视为真没给
            "risk_reward": 2.0,
            "reasoning": "no plan at all",
        }
        dec = self._normalize(result)
        assert dec.should_open is False
        assert dec.hold_reason == "exit_plan_missing_reject"


# ────────────────────────────────────────────────────────────────────
# Killer B — MTF blend llm_weight=0.90 让 conf=55 留在地板上方
# ────────────────────────────────────────────────────────────────────
class TestKillerBMTFBlendWeight:
    def test_blend_090_keeps_conf55_above_floor(self):
        """llm_weight=0.90：LLM 55 + 中性 MTF(35) → ≥ 52（地板上方）。"""
        from backend.services.decision_core.mtf_resonance import MTFResonance
        mtf = MTFResonance(score=35, aligned=False, direction="neutral", detail="t")
        blended = mtf.blend_with_llm(55, llm_weight=0.90)
        # 55*0.9 + 35*0.1 = 49.5 + 3.5 = 53.0
        assert blended >= 52, f"Killer B：0.90 blend 应 ≥52，实际 {blended}"

    def test_blend_070_would_drop_below_floor(self):
        """对照：旧 0.70 把同样输入融成 ≈49（< 52），印证止血必要性。"""
        from backend.services.decision_core.mtf_resonance import MTFResonance
        mtf = MTFResonance(score=35, aligned=False, direction="neutral", detail="t")
        blended = mtf.blend_with_llm(55, llm_weight=0.70)
        # 55*0.7 + 35*0.3 = 38.5 + 10.5 = 49.0
        assert blended < 52, f"对照失败：0.70 blend 应 <52，实际 {blended}"

    def test_swing_agent_calls_blend_with_090(self):
        """源码静态校验：swing_agent 调用 blend_with_llm 时 llm_weight=0.90。"""
        import re
        src_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "services", "swing_agent.py",
        )
        with open(src_path, encoding="utf-8") as f:
            src = f.read()
        m = re.search(r"blend_with_llm\(\s*confidence\s*,\s*llm_weight=([0-9.]+)\s*\)", src)
        assert m, "未找到 blend_with_llm 调用"
        assert float(m.group(1)) == pytest.approx(0.90), (
            f"Killer B：llm_weight 应为 0.90，源码中是 {m.group(1)}"
        )

    def test_blend_step_stays_above_floor(self):
        """Killer B 的直接效果：blend 步骤本身（不含后续 regime 微调）≥ 52。

        _normalize 里 blend 之后还有 ranging/未对齐的 -5/-8 二次微调，那是另一层
        逻辑、不在本阶段 3 个 killer 范围内。这里只钉 blend 这一步不再把 conf 稀释
        到地板下方（0.70 时是 49，0.90 时是 53）。
        """
        from backend.services.decision_core.mtf_resonance import compute_mtf_resonance
        me = {"BTC": {
            "indicators_4h": {"ema_trend": "neutral", "rsi": 50},
            "indicators_1d": {"ema_trend": "neutral", "rsi": 50},
        }}
        mtf = compute_mtf_resonance(me["BTC"])
        blended = mtf.blend_with_llm(55, llm_weight=0.90)
        assert blended >= 52, f"blend 步骤应 ≥52，实际 {blended}"

    def test_end_to_end_conf60_aligned_mtf_opens(self):
        """端到端（Killer A+B 联动）：conf=60 + MTF 对齐 + 扁平 sl/tp → 开仓。

        用 MTF 对齐（4h/1d 同向 long）的 market_envs，这样 blend 后不再触发 ranging
        二次惩罚，直接验止血后中线能在合理信号下真正开仓。
        """
        market_envs = {
            "BTC": {
                "indicators_4h": {"ema_trend": "bullish", "rsi": 52},
                "indicators_1d": {"ema_trend": "bullish", "rsi": 52},
            }
        }
        result = {
            "action": "buy",
            "confidence": 60,
            "direction": "long",
            "sl_pct": 0.03,
            "tp_pct": 0.06,
            "risk_reward": 2.0,
            "reasoning": "end-to-end stopgap",
        }
        from backend.services.swing_agent import SwingAgent
        dec = SwingAgent()._normalize(result, "BTC", market_envs=market_envs)
        assert dec.should_open is True, (
            f"端到端止血失败：conf=60+对齐MTF+扁平plan 应开仓 "
            f"(final_conf={dec.confidence}, hold_reason={dec.hold_reason!r})"
        )
        # Killer A 联动：扁平 plan 被自动补全成 v3 结构，而不是拒单
        assert dec.tp_sl_proposal.get("tp_stages"), "Killer A：应自动合成 tp_stages"


# ────────────────────────────────────────────────────────────────────
# Killer C — wrapper 签名接受三个 kwargs（2026-07-21 已修，钉回归）
# ────────────────────────────────────────────────────────────────────
class TestKillerCWrapperKwargs:
    def test_wrapper_signature_accepts_three_kwargs(self):
        """_try_execute_independent_agent_open 签名须含三个 kwargs，否则 TypeError。"""
        from backend.services.full_auto_trading_service import (
            FullAutoTradingService,
        )
        sig = inspect.signature(FullAutoTradingService._try_execute_independent_agent_open)
        params = set(sig.parameters.keys())
        for kw in ("tp_sl_proposal", "invalidation_condition", "expected_hold_hours"):
            assert kw in params, (
                f"Killer C 回归：wrapper 签名缺参数 {kw}（params={params}）"
            )

    def test_wrapper_invokes_helper_without_typeerror(self):
        """真实调用 wrapper（mock 掉底层 helper），确认 kwargs 不再触发 TypeError。"""
        from backend.services.full_auto_trading_service import (
            FullAutoTradingService,
        )

        captured = {}

        def _fake_helper(**kwargs):
            captured.update(kwargs)
            return True

        svc = FullAutoTradingService.__new__(FullAutoTradingService)

        with pytest.MonkeyPatch().context() as mp:
            import backend.services.full_auto.midlong_helpers as mh

            mp.setattr(mh, "try_execute_independent_agent_open", _fake_helper)
            mp.setattr(
                mh,
                "build_midlong_helpers_host",
                lambda self: object(),
            )
            # 不应抛 TypeError
            ok = svc._try_execute_independent_agent_open(
                db=object(),
                session=object(),
                sym="BTC",
                tier="mid",
                action="buy",
                confidence=55,
                sl_pct=0.03,
                tp_pct=0.06,
                trade_nature="swing",
                market_summary={},
                session_mode="running",
                tp_sl_proposal={"sl_pct": 0.03, "tp_stages": [{"pct": 0.06, "close_ratio": 1.0}]},
                invalidation_condition="破位",
                expected_hold_hours=48.0,
            )
        assert ok is True
        assert captured["tp_sl_proposal"]["sl_pct"] == pytest.approx(0.03)
        assert captured["invalidation_condition"] == "破位"
        assert captured["expected_hold_hours"] == pytest.approx(48.0)
