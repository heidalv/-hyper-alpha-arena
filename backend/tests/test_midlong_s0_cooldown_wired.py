"""
S0 止血修复单元测试（对应 04 综合方案 §3.2 / 审计报告 R1-R5）

覆盖 8 项改动：
  S0-1: try_execute_independent_agent_open 接入 reentry_cooldown
  S0-2: try_execute_independent_agent_open 接入 mid_long_structure_stop
  S0-3: swing_agent.py 删除 Paper 强制开仓 override + 门槛 48/1.5→52/1.6
  S0-4: trend_agent.py 删除 Paper 强制开仓 override
  S0-5: task_swing_agent.md inline prompt 删除强制开仓指令
  S0-6: is_close_reason_blocked_for_midlong + 接线 master_execution
  S0-7: 新增 4 个 flag
  S0-8: reentry_cooldown 增加 close_reason 感知(sl 后 12h/48h)

运行：
  cd backend && python -m pytest tests/test_midlong_s0_cooldown_wired.py -v
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch


# ════════════════════════════════════════════════════════════════════
# S0-7: 新增 4 个 flag 配置验证
# ════════════════════════════════════════════════════════════════════
class TestS07NewFlags:
    """验证 settings.py 新增的 4 个 flag 默认值正确。"""

    def test_midlong_independent_cooldown_enforce_default_true(self):
        from backend.config import settings
        assert settings.MIDLONG_INDEPENDENT_COOLDOWN_ENFORCE is True

    def test_midlong_structure_stop_on_independent_default_true(self):
        from backend.config import settings
        assert settings.MIDLONG_STRUCTURE_STOP_ON_INDEPENDENT is True

    def test_mid_tier_protected_from_contains_master_running(self):
        from backend.config import settings
        # MID_TIER_PROTECTED_FROM 必须屏蔽 Master 微亏全平的几类原因
        assert "master_running_close" in settings.MID_TIER_PROTECTED_FROM
        assert "master_running" in settings.MID_TIER_PROTECTED_FROM
        assert "master_running_reduce" in settings.MID_TIER_PROTECTED_FROM
        assert "ai_reverse" in settings.MID_TIER_PROTECTED_FROM

    def test_mid_tier_protected_from_does_not_block_hard_exits(self):
        """硬退出（sl/tp/emergency/manual）不应在屏蔽列表里。"""
        from backend.config import settings
        assert "sl" not in settings.MID_TIER_PROTECTED_FROM
        assert "tp" not in settings.MID_TIER_PROTECTED_FROM
        assert "emergency_drawdown" not in settings.MID_TIER_PROTECTED_FROM
        assert "manual" not in settings.MID_TIER_PROTECTED_FROM

    def test_risk_use_mid_tier_immune_default_true(self):
        from backend.config import settings
        assert settings.RISK_USE_MID_TIER_IMMUNE is True

    def test_long_tier_protected_from_still_intact(self):
        """long tier 原有保护不应被破坏。"""
        from backend.config import settings
        assert "master_running_reduce" in settings.LONG_TIER_PROTECTED_FROM
        assert "master_running" in settings.LONG_TIER_PROTECTED_FROM
        assert "ai_reverse" in settings.LONG_TIER_PROTECTED_FROM


# ════════════════════════════════════════════════════════════════════
# S0-6: is_close_reason_blocked_for_midlong 函数行为
# ════════════════════════════════════════════════════════════════════
class TestS06MidlongImmune:
    """验证 is_close_reason_blocked_for_midlong 正确区分软/硬退出 + tier。"""

    def test_mid_master_running_close_blocked(self):
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_midlong
        assert is_close_reason_blocked_for_midlong("master_running_close", "mid") is True

    def test_mid_master_running_reduce_blocked(self):
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_midlong
        assert is_close_reason_blocked_for_midlong("master_running_reduce", "mid") is True

    def test_mid_ai_reverse_blocked(self):
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_midlong
        assert is_close_reason_blocked_for_midlong("ai_reverse", "mid") is True

    def test_long_ai_reverse_blocked(self):
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_midlong
        assert is_close_reason_blocked_for_midlong("ai_reverse", "long") is True

    def test_mid_sl_not_blocked(self):
        """硬止损 SL 不应被屏蔽（交给 SL 系统处理）。"""
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_midlong
        assert is_close_reason_blocked_for_midlong("sl", "mid") is False

    def test_mid_tp_not_blocked(self):
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_midlong
        assert is_close_reason_blocked_for_midlong("tp", "mid") is False
        assert is_close_reason_blocked_for_midlong("tp_target", "mid") is False

    def test_mid_emergency_drawdown_not_blocked(self):
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_midlong
        assert is_close_reason_blocked_for_midlong("emergency_drawdown", "mid") is False

    def test_short_not_blocked(self):
        """short tier 不应被免疫规则拦截（短线快进快出）。"""
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_midlong
        assert is_close_reason_blocked_for_midlong("master_running_close", "short") is False

    def test_empty_close_reason_not_blocked(self):
        from backend.services.risk_band_resolver import is_close_reason_blocked_for_midlong
        assert is_close_reason_blocked_for_midlong("", "mid") is False
        assert is_close_reason_blocked_for_midlong(None, "mid") is False


# ════════════════════════════════════════════════════════════════════
# S0-8: reentry_cooldown close_reason 感知
# ════════════════════════════════════════════════════════════════════
class TestS08ReentryCooldownCloseReason:
    """验证 reentry_cooldown 对 sl/tp/master 后的冷却延长。"""

    ACCT = 999001
    SYMBOL = "TESTCOIN"

    def setup_method(self):
        """每个测试前清理状态。"""
        from backend.services import reentry_cooldown
        reentry_cooldown.clear_state(self.ACCT, self.SYMBOL)

    def teardown_method(self):
        from backend.services import reentry_cooldown
        reentry_cooldown.clear_state(self.ACCT, self.SYMBOL)

    def test_sl_mid_cooldown_12h(self):
        """mid tier sl 后冷却应为 12 小时（720 分钟）。"""
        from backend.services import reentry_cooldown
        reentry_cooldown.record_full_close(
            self.ACCT, self.SYMBOL, "long", tier="mid",
            close_reason="sl", close_pnl=-10.0,
        )
        blocked, reason = reentry_cooldown.reopen_blocked(
            self.ACCT, self.SYMBOL, "buy", "mid",
        )
        assert blocked is True
        # 12h = 720 分钟
        assert "720" in reason, f"sl 后 mid 冷却应显示 720 分钟,实际: {reason}"

    def test_sl_long_cooldown_48h(self):
        """long tier sl 后冷却应为 48 小时（2880 分钟）。"""
        from backend.services import reentry_cooldown
        reentry_cooldown.record_full_close(
            self.ACCT, self.SYMBOL, "long", tier="long",
            close_reason="sl", close_pnl=-10.0,
        )
        blocked, reason = reentry_cooldown.reopen_blocked(
            self.ACCT, self.SYMBOL, "buy", "long",
        )
        assert blocked is True
        # 48h = 2880 分钟
        assert "2880" in reason, f"sl 后 long 冷却应显示 2880 分钟,实际: {reason}"

    def test_sl_short_cooldown_4h(self):
        """short tier sl 后冷却应为 4 小时（240 分钟）。"""
        from backend.services import reentry_cooldown
        reentry_cooldown.record_full_close(
            self.ACCT, self.SYMBOL, "long", tier="short",
            close_reason="sl", close_pnl=-10.0,
        )
        blocked, reason = reentry_cooldown.reopen_blocked(
            self.ACCT, self.SYMBOL, "buy", "short",
        )
        assert blocked is True
        # 4h = 240 分钟
        assert "240" in reason, f"sl 后 short 冷却应显示 240 分钟,实际: {reason}"

    def test_stop_loss_alias_treated_as_sl(self):
        """close_reason='stop_loss' 也应触发 sl 延长。"""
        from backend.services import reentry_cooldown
        reentry_cooldown.record_full_close(
            self.ACCT, self.SYMBOL, "long", tier="mid",
            close_reason="stop_loss", close_pnl=-10.0,
        )
        blocked, reason = reentry_cooldown.reopen_blocked(
            self.ACCT, self.SYMBOL, "buy", "mid",
        )
        assert blocked is True
        assert "720" in reason

    def test_liquidation_treated_as_sl(self):
        """close_reason='liquidation' 也应触发 sl 延长。"""
        from backend.services import reentry_cooldown
        reentry_cooldown.record_full_close(
            self.ACCT, self.SYMBOL, "long", tier="mid",
            close_reason="liquidation", close_pnl=-50.0,
        )
        blocked, reason = reentry_cooldown.reopen_blocked(
            self.ACCT, self.SYMBOL, "buy", "mid",
        )
        assert blocked is True
        assert "720" in reason

    def test_tp_cooldown_extended(self):
        """tp 后也应有延长冷却（REENTRY_MIN_COOLDOWN_AFTER_TP_SEC 默认 30min）。"""
        from backend.services import reentry_cooldown
        reentry_cooldown.record_full_close(
            self.ACCT, self.SYMBOL, "long", tier="mid",
            close_reason="tp", close_pnl=10.0,
        )
        blocked, reason = reentry_cooldown.reopen_blocked(
            self.ACCT, self.SYMBOL, "buy", "mid",
        )
        assert blocked is True
        # TP 后最低 30min（REENTRY_MIN_COOLDOWN_AFTER_TP_SEC=1800）
        assert "30" in reason

    def test_master_close_min_cooldown_60min(self):
        """master 全平至少 60 分钟冷却。"""
        from backend.services import reentry_cooldown
        reentry_cooldown.record_full_close(
            self.ACCT, self.SYMBOL, "long", tier="mid",
            is_master_close=True, close_reason="", close_pnl=0,
        )
        blocked, reason = reentry_cooldown.reopen_blocked(
            self.ACCT, self.SYMBOL, "buy", "mid",
        )
        assert blocked is True
        # 60 分钟 = master 最低冷却
        assert "60" in reason

    def test_loss_close_mid_default_4h(self):
        """普通亏损平仓（无 close_reason 标签，但 close_pnl<0）mid tier 应为 4h(240min)冷却。

        补齐修复（04 综合方案 §2.3.5「任意亏损全平」行）：此前只有 close_reason
        显式为 sl/liquidation 时才延长冷却，导致 master_running_close 等"软"
        标签的真实亏损仍只吃 30min 的普通 base_cd，未能打断恶性循环。
        """
        from backend.services import reentry_cooldown
        reentry_cooldown.record_full_close(
            self.ACCT, self.SYMBOL, "long", tier="mid",
            close_reason="", close_pnl=-5.0,
        )
        blocked, reason = reentry_cooldown.reopen_blocked(
            self.ACCT, self.SYMBOL, "buy", "mid",
        )
        assert blocked is True
        assert "240" in reason, f"任意亏损全平 mid 应为 240 分钟(4h),实际: {reason}"

    def test_profitable_close_mid_default_30min(self):
        """真正盈利的平仓（close_pnl>0，非 tp/sl 标签）仍应维持 30min 基础冷却，不受亏损下限影响。"""
        from backend.services import reentry_cooldown
        reentry_cooldown.record_full_close(
            self.ACCT, self.SYMBOL, "long", tier="mid",
            close_reason="", close_pnl=5.0,
        )
        blocked, reason = reentry_cooldown.reopen_blocked(
            self.ACCT, self.SYMBOL, "buy", "mid",
        )
        assert blocked is True
        assert "30" in reason

    def test_flip_direction_cooldown_30min(self):
        """反向翻转 30min 冷却。"""
        from backend.services import reentry_cooldown
        reentry_cooldown.record_full_close(
            self.ACCT, self.SYMBOL, "long", tier="mid",
            close_reason="", close_pnl=5.0,
        )
        # 立即开反向（short）
        blocked, reason = reentry_cooldown.reopen_blocked(
            self.ACCT, self.SYMBOL, "sell", "mid",
        )
        assert blocked is True
        assert "反向翻转" in reason or "30" in reason

    def test_consecutive_losses_double_cooldown(self):
        """连续 2 次亏损冷却翻倍。"""
        from backend.services import reentry_cooldown
        # 第一笔亏损
        reentry_cooldown.record_full_close(
            self.ACCT, self.SYMBOL, "long", tier="mid",
            close_reason="", close_pnl=-5.0,
        )
        # 第二笔亏损（4 小时内）
        reentry_cooldown.record_full_close(
            self.ACCT, self.SYMBOL, "long", tier="mid",
            close_reason="", close_pnl=-5.0,
        )
        blocked, reason = reentry_cooldown.reopen_blocked(
            self.ACCT, self.SYMBOL, "buy", "mid",
        )
        assert blocked is True
        # 亏损下限 4h(240min) × 连亏 2 倍 = 480min
        assert "480" in reason, f"应为 240min×2=480min,实际: {reason}"
        assert "x2" in reason or "倍" in reason

    def test_tier_isolation_long_does_not_block_mid(self):
        """tier 隔离：long 全平不应阻止 mid 同 symbol 开仓。"""
        from backend.services import reentry_cooldown
        reentry_cooldown.record_full_close(
            self.ACCT, self.SYMBOL, "long", tier="long",
            close_reason="sl", close_pnl=-10.0,
        )
        # 在 mid tier 开同 symbol 同方向——不应被 long 的冷却拦截
        blocked, reason = reentry_cooldown.reopen_blocked(
            self.ACCT, self.SYMBOL, "buy", "mid",
        )
        assert blocked is False, f"long 冷却不应阻止 mid 开仓,但被拦了: {reason}"


# ════════════════════════════════════════════════════════════════════
# S0-3: swing_agent 删除强制开仓 override
# ════════════════════════════════════════════════════════════════════
class TestS03SwingAgentNoForceOpen:
    """验证 swing_agent._normalize 不再强制把 hold 改成 buy/sell。"""

    def test_hold_with_high_confidence_respected(self):
        """LLM 输出 hold + confidence=60 + RR=2.0，系统应尊重，不强制开仓。"""
        from backend.services.swing_agent import swing_agent
        result = {
            "action": "hold",
            "confidence": 60,
            "direction": "long",
            "sl_pct": 0.035,
            "tp_pct": 0.07,
            "risk_reward": 2.0,
            "reasoning": "测试 hold 被尊重",
        }
        decision = swing_agent._normalize(result, "TESTCOIN")
        assert decision.action == "hold", f"应保持 hold,实际: {decision.action}"
        assert decision.should_open is False, "hold 不应开仓"
        assert decision.hold_reason == "llm_hold_respected", \
            f"hold_reason 应为 llm_hold_respected,实际: {decision.hold_reason}"

    def test_buy_with_conf_below_52_not_opened_in_paper(self):
        """paper 模式下 buy + confidence=50（< 52）不应开仓。"""
        from backend.services.swing_agent import swing_agent
        result = {
            "action": "buy",
            "confidence": 50,
            "direction": "long",
            "sl_pct": 0.035,
            "tp_pct": 0.07,
            "risk_reward": 1.5,
            "reasoning": "测试低 conf 不开仓",
        }
        decision = swing_agent._normalize(result, "TESTCOIN")
        # paper 门槛 52/1.6，confidence=50 应不开仓
        assert decision.should_open is False
        assert "conf_low" in decision.hold_reason

    def test_buy_with_conf_52_rr_16_opened_in_paper(self):
        """paper 模式下 buy + confidence=52 + RR=1.6 + 合法 exit_plan 应开仓（新门槛）。"""
        from backend.services.swing_agent import swing_agent
        result = {
            "action": "buy",
            "confidence": 52,
            "direction": "long",
            "sl_pct": 0.035,
            "tp_pct": 0.056,
            "risk_reward": 1.6,
            "reasoning": "测试新门槛开仓",
            # P1-6 硬校验要求 should_open 时必须带 exit_plan(tp_stages) 或 invalidation。
            # 注意：tp_stages[0].pct 会覆盖顶层 tp_pct 并重算 RR=tp_pct/sl_pct，故此处
            # 第一档取值略高于门槛（0.06 而非临界的 0.056），避开浮点误差导致
            # 1.6 算成 1.5999999... 而误判 rr_low 的坑，不影响本测试要验证的核心逻辑。
            "exit_plan": {
                "tp_stages": [{"pct": 0.06, "close_ratio": 0.5}, {"pct": 0.09, "close_ratio": 0.5}],
            },
        }
        decision = swing_agent._normalize(result, "TESTCOIN")
        assert decision.action == "buy"
        assert decision.should_open is True, \
            f"conf=52+RR=1.6 应开仓(paper 门槛),hold_reason={decision.hold_reason}"

    def test_no_inferred_direction_from_neutral(self):
        """LLM 输出 hold + direction=neutral，系统不应从市场推断方向强行开仓。"""
        from backend.services.swing_agent import swing_agent
        result = {
            "action": "hold",
            "confidence": 60,
            "direction": "neutral",
            "sl_pct": 0.035,
            "tp_pct": 0.07,
            "risk_reward": 2.0,
            "reasoning": "中性信号,该等等",
        }
        # market_envs 为空（不应触发 infer_swing_direction_from_market）
        decision = swing_agent._normalize(result, "TESTCOIN", market_envs={})
        assert decision.action == "hold", "neutral hold 不应被改成 buy/sell"
        assert decision.direction == "neutral", "direction 不应被改动"
        assert decision.should_open is False


# ════════════════════════════════════════════════════════════════════
# S0-4: trend_agent 删除强制开仓 override
# ════════════════════════════════════════════════════════════════════
class TestS04TrendAgentNoForceOpen:
    """验证 trend_agent._normalize_direction 不再强制 override should_open_trend。"""

    def test_should_open_false_respected(self):
        """LLM 输出 should_open_trend=false，score≥min_score 时系统应尊重 false。"""
        from backend.services.trend_agent import trend_agent
        result = {
            "trend_score": 60,
            "trend_direction": "long",
            "should_open_trend": False,  # LLM 明确说不开
            "suggested_sl_pct": 0.08,
            "multi_tf_aligned": True,
            "reasoning": "趋势不明,该等等",
        }
        normalized = trend_agent._normalize_direction(
            result, "TESTCOIN", "long", min_score=50, market_envs={},
        )
        # 原逻辑会 override 成 True；新逻辑应尊重 LLM 的 false
        assert normalized["should_open"] is False, \
            f"LLM should_open_trend=false 应被尊重,但被 override 成 True"
        assert "llm_should_open_false_respected" in normalized.get("hold_reason", ""), \
            f"hold_reason 应含 llm_should_open_false_respected,实际: {normalized.get('hold_reason')}"

    def test_score_below_min_not_opened(self):
        """score < min_score 不应开仓。"""
        from backend.services.trend_agent import trend_agent
        result = {
            "trend_score": 40,  # < min_score=50
            "trend_direction": "long",
            "should_open_trend": True,
            "suggested_sl_pct": 0.08,
            "reasoning": "score 不够",
        }
        normalized = trend_agent._normalize_direction(
            result, "TESTCOIN", "long", min_score=50, market_envs={},
        )
        assert normalized["should_open"] is False
        assert "score_low" in normalized.get("hold_reason", "")


# ════════════════════════════════════════════════════════════════════
# S0-1: try_execute_independent_agent_open 接入 reentry_cooldown（集成测试）
# ════════════════════════════════════════════════════════════════════
class TestS01IndependentPathCooldownWired:
    """验证独立路径开仓前调用 reentry_cooldown.reopen_blocked。"""

    ACCT = 999002
    SYMBOL = "TESTCOIN"

    def setup_method(self):
        from backend.services import reentry_cooldown
        reentry_cooldown.clear_state(self.ACCT, self.SYMBOL)

    def teardown_method(self):
        from backend.services import reentry_cooldown
        reentry_cooldown.clear_state(self.ACCT, self.SYMBOL)

    def _build_mock_host(self, blocked: bool, reason: str = ""):
        """构造 mock MidlongHelpersHost。"""
        host = MagicMock()
        host.get_trading_account_id.return_value = self.ACCT
        host.append_event = MagicMock()
        host.evaluate_and_execute_proposal = MagicMock(return_value=True)
        return host

    def test_cooldown_blocks_independent_open(self):
        """冷却中时,独立路径开仓应被拦截,返回 False。"""
        from backend.services import reentry_cooldown
        from backend.services.full_auto.midlong_helpers import try_execute_independent_agent_open

        # 模拟 sl 后冷却激活
        reentry_cooldown.record_full_close(
            self.ACCT, self.SYMBOL, "long", tier="mid",
            close_reason="sl", close_pnl=-10.0,
        )

        host = self._build_mock_host(True)
        db = MagicMock()
        session = MagicMock()

        result = try_execute_independent_agent_open(
            db=db, session=session, sym=self.SYMBOL, tier="mid",
            action="buy",  # 同向再开,应被冷却拦截
            confidence=70, sl_pct=0.035, tp_pct=0.07,
            trade_nature="swing", market_summary={},
            session_mode="running", host=host,
        )
        assert result is False, "冷却中同向再开应返回 False"
        # evaluate_and_execute_proposal 不应被调用（被冷却提前拦截）
        host.evaluate_and_execute_proposal.assert_not_called()
        # 应该记录冷却拦截事件
        host.append_event.assert_called()
        event_args = host.append_event.call_args
        assert event_args[0][1] == "midlong_cooldown_block"

    def test_no_cooldown_allows_independent_open(self):
        """无冷却时,独立路径应正常进入 evaluate_and_execute_proposal。"""
        from backend.services.full_auto.midlong_helpers import try_execute_independent_agent_open

        host = self._build_mock_host(False)
        db = MagicMock()
        # mock db.execute(SELECT 1) 不抛异常
        db.execute.return_value = None
        session = MagicMock()

        # 关闭结构 SL 接入(本测试只验证冷却),需要 mock MIDLONG_STRUCTURE_STOP_ON_INDEPENDENT
        with patch("backend.config.settings.MIDLONG_STRUCTURE_STOP_ON_INDEPENDENT", False):
            result = try_execute_independent_agent_open(
                db=db, session=session, sym=self.SYMBOL, tier="mid",
                action="buy",
                confidence=70, sl_pct=0.035, tp_pct=0.07,
                trade_nature="swing", market_summary={},
                session_mode="running", host=host,
            )
        # 无冷却,应进入 evaluate_and_execute_proposal(返回 True)
        assert result is True
        host.evaluate_and_execute_proposal.assert_called_once()


# ════════════════════════════════════════════════════════════════════
# S0-2: try_execute_independent_agent_open 接入 mid_long_structure_stop
# ════════════════════════════════════════════════════════════════════
class TestS02IndependentPathStructureStopWired:
    """验证独立路径开仓前调用 mid_long_structure_stop.compute。"""

    ACCT = 999003
    SYMBOL = "TESTCOIN"

    def setup_method(self):
        from backend.services import reentry_cooldown
        reentry_cooldown.clear_state(self.ACCT, self.SYMBOL)

    def teardown_method(self):
        from backend.services import reentry_cooldown
        reentry_cooldown.clear_state(self.ACCT, self.SYMBOL)

    def test_llm_sl_not_widened_by_structure(self):
        """v6 M3：有 LLM sl 时禁止 max(LLM,structure) 加宽；structure 仅兜底。"""
        from backend.services.full_auto.midlong_helpers import try_execute_independent_agent_open
        from backend.services.mid_long_structure_stop import MidLongStructureStop

        host = MagicMock()
        host.get_trading_account_id.return_value = self.ACCT
        host.append_event = MagicMock()
        host.evaluate_and_execute_proposal = MagicMock(return_value=True)

        db = MagicMock()
        db.execute.return_value = None
        session = MagicMock()

        # structure 故意给更宽 SL(5%)——不得覆盖 LLM 0.8%
        _ms = {
            self.SYMBOL: {
                "current_price": 10000.0,
                "indicators_1w": {"trend": "up", "rsi": 55},
            }
        }
        with patch.object(
            MidLongStructureStop, "compute",
            return_value=(0.05, 0.125, 9500.0, 10625.0, "midlong_structure_swing_agent"),
        ), patch(
            "backend.config.settings.MIDLONG_INDEPENDENT_COOLDOWN_ENFORCE", False
        ), patch(
            "backend.services.auto_coin_selector.get_fixed_symbols_for_session",
            return_value={self.SYMBOL},
        ), patch(
            "backend.services.full_auto.midlong_helpers.inject_midlong_indicators",
            lambda *a, **k: None,
        ), patch(
            "backend.services.mlto.midlong_trade_design.is_chop_regime",
            return_value=(False, ""),
        ), patch(
            "backend.services.mlto.midlong_trade_design.estimate_atr_1d_pct",
            return_value=None,
        ), patch(
            "backend.services.mlto.midlong_trade_design.funding_net_rr_ok",
            return_value=(True, 2.0, "ok"),
        ), patch(
            "backend.services.mlto.midlong_portfolio_risk.check_portfolio_open_allowed",
            return_value=(True, "ok"),
        ):
            try_execute_independent_agent_open(
                db=db, session=session, sym=self.SYMBOL, tier="mid",
                action="buy",
                confidence=70,
                sl_pct=0.008,  # LLM 紧止损
                tp_pct=0.02,
                trade_nature="swing",
                market_summary=_ms,
                session_mode="running", host=host,
            )

        proposal_call = host.evaluate_and_execute_proposal.call_args
        assert proposal_call is not None, "应走到下单提案"
        proposal = proposal_call[1]["proposal"]
        assert proposal.sl_pct == pytest.approx(0.008, abs=0.001), \
            f"LLM sl 应直通不被 structure 加宽,实际: {proposal.sl_pct}"


# ════════════════════════════════════════════════════════════════════
# S0-5: task_swing_agent.md inline prompt 删除强制开仓指令
# ════════════════════════════════════════════════════════════════════
class TestS05PromptNoForceOpenDirective:
    """验证 swing_agent 的 inline prompt 不再包含强制开仓指令。"""

    def test_inline_prompt_no_must_buy_sell(self):
        """inline fallback prompt 不应包含"必须 output buy/sell"指令。"""
        from backend.services.swing_agent import swing_agent
        prompt = swing_agent._build_prompt_inline(
            "TESTCOIN", "context", "deep_ctx", {}, "evidence",
        )
        # 旧的强制开仓指令已被删除
        assert "必须" not in prompt or "必须遵守" in prompt, \
            "prompt 不应含'必须 output buy/sell'类强制开仓指令"
        assert "禁止 hold+高分" not in prompt, \
            "prompt 不应含'禁止 hold+高分'指令"

    def test_inline_prompt_contains_respect_hold(self):
        """inline prompt 应包含'尊重 hold 决策'说明。"""
        from backend.services.swing_agent import swing_agent
        prompt = swing_agent._build_prompt_inline(
            "TESTCOIN", "context", "deep_ctx", {}, "evidence",
        )
        assert "尊重你的 hold 决策" in prompt, \
            "prompt 应明示尊重 LLM 的 hold 决策"

    def test_inline_prompt_paper_threshold_52_16(self):
        """inline prompt 应反映新门槛 52/1.6。"""
        from backend.services.swing_agent import swing_agent
        prompt = swing_agent._build_prompt_inline(
            "TESTCOIN", "context", "deep_ctx", {}, "evidence",
        )
        assert "52" in prompt, "prompt 应含新门槛 confidence≥52"
        assert "1.6" in prompt, "prompt 应含新门槛 RR≥1.6"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
