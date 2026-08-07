"""
S1 Prompt v3 + Schema 升级单元测试（对应 04 综合方案 §3.3）

覆盖：
  S1-1: protocol_midlong_risk_constitution 注册
  S1-2: agent_quant_feature_table 渲染
  S1-3/4: task_swing_agent / task_trend_agent_direction v3 渲染
  S1-9/10: SwingDecision 新字段 + _normalize v3 schema 解析
  S1-11: trend_agent._normalize_direction v3 schema 解析
  S1-12: persist_independent_scan_log account_id 兜底
  S1-13: manifest v3 + L2 版本校验回退
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch


# ════════════════════════════════════════════════════════════════════
# S1-1: protocol_midlong_risk_constitution 注册
# ════════════════════════════════════════════════════════════════════
class TestS01ProtocolRegistered:
    def test_protocol_layer_in_manifest(self):
        from backend.services.prompt_registry import _load_manifest
        _load_manifest.cache_clear()
        manifest = _load_manifest()
        assert "protocol_midlong_risk_constitution" in manifest["layers"]

    def test_swing_task_extends_protocol(self):
        from backend.services.prompt_registry import _load_manifest
        _load_manifest.cache_clear()
        manifest = _load_manifest()
        swing = manifest["tasks"]["task_swing_agent"]
        assert "protocol_midlong_risk_constitution" in swing.get("extends", [])

    def test_trend_direction_task_extends_protocol(self):
        from backend.services.prompt_registry import _load_manifest
        _load_manifest.cache_clear()
        manifest = _load_manifest()
        trend = manifest["tasks"]["task_trend_agent_direction"]
        assert "protocol_midlong_risk_constitution" in trend.get("extends", [])


# ════════════════════════════════════════════════════════════════════
# S1-2: agent_quant_feature_table
# ════════════════════════════════════════════════════════════════════
class TestS02QuantFeatureTable:
    def test_renders_with_no_db_graceful_degradation(self):
        """无 db/account_id 时优雅降级，仍含关键字段。"""
        from backend.services.agent_quant_feature_table import render_quant_feature_table
        table = render_quant_feature_table("BTC", {}, db=None, account_id=None, nature="swing")
        assert "量化特征表" in table
        assert "same_dir_losses_24h" in table
        assert "cooldown_remain_sec" in table
        assert "blocked_sides" in table
        assert "max_sl_pct" in table
        assert "min_rr" in table

    def test_swing_nature_uses_4h_primary_tf(self):
        """swing nature 主周期应是 4h。"""
        from backend.services.agent_quant_feature_table import render_quant_feature_table
        table = render_quant_feature_table("BTC", {}, db=None, account_id=None, nature="swing")
        assert "4h" in table

    def test_trend_nature_uses_1d_primary_tf(self):
        """trend_follow nature 主周期应是 1d。"""
        from backend.services.agent_quant_feature_table import render_quant_feature_table
        table = render_quant_feature_table("BTC", {}, db=None, account_id=None, nature="trend_follow")
        assert "1d" in table

    def test_swing_risk_bounds(self):
        """swing 风控边界正确。"""
        from backend.services.agent_quant_feature_table import render_quant_feature_table
        table = render_quant_feature_table("BTC", {}, db=None, account_id=None, nature="swing")
        assert "2.0%-8.0%" in table
        assert "2.0" in table  # min_rr
        assert "5x" in table    # max_leverage

    def test_trend_risk_bounds(self):
        """trend 风控边界正确（更宽）。"""
        from backend.services.agent_quant_feature_table import render_quant_feature_table
        table = render_quant_feature_table("BTC", {}, db=None, account_id=None, nature="trend_follow")
        assert "5.0%-15.0%" in table
        assert "3.0" in table   # min_rr
        assert "3x" in table    # max_leverage

    def test_memory_block_renders(self):
        """render_memory_block 无历史时返回友好提示。"""
        from backend.services.agent_quant_feature_table import render_memory_block
        result = render_memory_block(None, "NEWCOIN", "swing", 0, limit=5)
        assert "暂无历史交易记录" in result


# ════════════════════════════════════════════════════════════════════
# S1-3/4: task v3 渲染（含 protocol 层 + 新变量注入）
# ════════════════════════════════════════════════════════════════════
class TestS03V3PromptRendering:
    def _render_swing(self):
        from backend.services.prompt_registry import get_prompt_registry, _load_manifest
        _load_manifest.cache_clear()
        reg = get_prompt_registry()
        return reg.render_task("task_swing_agent", {
            "symbol": "BTC",
            "quant_feature_table": "## 量化特征表-TEST\n- same_dir_losses_24h: 0",
            "memory_block": "## 历史教训-TEST",
            "atr_block": "## 波动率-TEST",
            "recent_loss_block": "## 同方向-TEST",
            "cooldown_active": "false",
            "compact_report": "报告",
            "deep_context": "ctx",
            "orchestrator": {"mid_bias": "bullish"},
            "evidence_block": "evi",
            "regime": "trending",
            "mid_opens_today": "1",
            "agent_constraints": "",
        }, consumer="test")

    def test_swing_v3_includes_quant_feature_table(self):
        rendered = self._render_swing()
        assert "量化特征表-TEST" in rendered
        assert "same_dir_losses_24h" in rendered

    def test_swing_v3_includes_memory_block(self):
        rendered = self._render_swing()
        assert "历史教训-TEST" in rendered

    def test_swing_v3_includes_atr_block(self):
        rendered = self._render_swing()
        assert "波动率-TEST" in rendered

    def test_swing_v3_includes_exit_plan_schema(self):
        rendered = self._render_swing()
        assert "exit_plan" in rendered
        assert "invalidation_condition" in rendered
        assert "self_check" in rendered

    def test_swing_v3_includes_protocol_layer(self):
        """v3 应注入 protocol_midlong_risk_constitution 协议层。"""
        rendered = self._render_swing()
        assert "风险宪法" in rendered
        assert "单笔风险上限" in rendered
        assert "同方向冷却规则" in rendered

    def test_swing_v3_uses_3_layer_reasoning(self):
        """v3 应使用 3 层精炼推理（替代 v2 的 5 层空转）。"""
        rendered = self._render_swing()
        assert "3 层精炼" in rendered


# ════════════════════════════════════════════════════════════════════
# S1-9/10: SwingDecision 新字段 + _normalize v3 schema 解析
# ════════════════════════════════════════════════════════════════════
class TestS09S10SwingDecisionV3Fields:
    def test_swing_decision_has_new_fields(self):
        """SwingDecision dataclass 含 7 个新字段。"""
        from backend.services.swing_agent import SwingDecision
        new_fields = [
            "tp_sl_proposal", "lifecycle", "scenarios",
            "invalidation", "self_check", "expected_hold_hours", "conviction_level",
        ]
        for f in new_fields:
            assert f in SwingDecision.__dataclass_fields__, f"SwingDecision 缺字段: {f}"

    def test_normalize_parses_v3_tp_sl_proposal(self):
        """v3 schema: tp_sl_proposal 中的 sl_pct 覆盖扁平 sl_pct。"""
        from backend.services.swing_agent import swing_agent
        result = {
            "action": "buy", "confidence": 65, "direction": "long",
            "sl_pct": 0.035,  # 扁平字段（旧）
            "tp_sl_proposal": {
                "sl_pct": 0.045,  # v3 更精确的 SL
                "tp_stages": [{"pct": 0.06, "close_ratio": 0.30}],
            },
            "risk_reward": 0,
            "reasoning": "v3 测试",
        }
        decision = swing_agent._normalize(result, "BTC")
        # v3 tp_sl_proposal.sl_pct=0.045 应覆盖扁平 0.035
        assert decision.sl_pct == pytest.approx(0.045, abs=0.001)
        # tp_pct 应从 tp_stages[0].pct=0.06 提取
        assert decision.tp_pct == pytest.approx(0.06, abs=0.001)
        # tp_sl_proposal 应透传
        assert decision.tp_sl_proposal["sl_pct"] == 0.045

    def test_normalize_parses_scenarios_object(self):
        """v3 schema: scenarios 是对象 {a/b/c: {prob, trigger, target_pct}}。"""
        from backend.services.swing_agent import swing_agent
        result = {
            "action": "hold", "confidence": 60, "direction": "neutral",
            "scenarios": {
                "a": {"prob": 0.55, "trigger": "回调", "target_pct": 0.07},
                "b": {"prob": 0.30, "trigger": "跌破", "target_pct": -0.035},
            },
            "reasoning": "场景测试",
        }
        decision = swing_agent._normalize(result, "BTC")
        assert "a" in decision.scenarios
        assert decision.scenarios["a"]["prob"] == 0.55

    def test_normalize_parses_invalidation(self):
        """v3 schema: invalidation 对象。"""
        from backend.services.swing_agent import swing_agent
        result = {
            "action": "hold", "confidence": 60, "direction": "neutral",
            "invalidation": {"price_level": 95000, "condition": "4h 跌破 EMA21"},
            "reasoning": "失效条件测试",
        }
        decision = swing_agent._normalize(result, "BTC")
        assert decision.invalidation["condition"] == "4h 跌破 EMA21"
        assert decision.invalidation["price_level"] == 95000

    def test_normalize_applies_self_check_confidence_adjustment(self):
        """v3 schema: self_check.confidence_adjustment 应用到 confidence。"""
        from backend.services.swing_agent import swing_agent
        result = {
            "action": "buy", "confidence": 70, "direction": "long",
            "self_check": {"confidence_adjustment": -10, "counter_argument": "反方"},
            "reasoning": "自检测试",
        }
        decision = swing_agent._normalize(result, "BTC")
        # 70 + (-10) = 60
        assert decision.confidence == 60
        assert decision.self_check["counter_argument"] == "反方"

    def test_normalize_parses_lifecycle_and_conviction(self):
        """v3 schema: lifecycle + conviction_level。"""
        from backend.services.swing_agent import swing_agent
        result = {
            "action": "buy", "confidence": 65, "direction": "long",
            "lifecycle": "加速",
            "conviction_level": "high",
            "expected_hold_hours": 6,
            "reasoning": "生命周期测试",
        }
        decision = swing_agent._normalize(result, "BTC")
        assert decision.lifecycle == "加速"
        assert decision.conviction_level == "high"
        assert decision.expected_hold_hours == 6

    def test_normalize_v2_schema_backward_compatible(self):
        """v2 schema（扁平字段，无 exit_plan）仍能正常**解析**不报错。

        [阶段1 止血 Killer A] should_open 场景下不再因缺 exit_plan/invalidation
        一刀切拒单——只要 LLM 给了非零扁平 sl_pct/tp_pct，就自动合成单档
        tp_stages + invalidation，保持开仓（此前这条硬校验把中线焊死）。
        只有连扁平 sl/tp 都没有（key 缺失/值为 0）才真正拒单。
        """
        from backend.services.swing_agent import swing_agent
        result = {
            "action": "buy", "confidence": 65, "direction": "long",
            "sl_pct": 0.035, "tp_pct": 0.07, "risk_reward": 2.0,
            "reasoning": "v2 兼容测试",
        }
        decision = swing_agent._normalize(result, "BTC")
        # v2 字段仍被正常解析（sl_pct/tp_pct 未丢失）
        assert decision.sl_pct == 0.035
        assert decision.tp_pct == 0.07
        # 阶段1 止血：扁平 sl/tp 被自动补全成 v3 结构（不再拒单）
        assert decision.tp_sl_proposal.get("tp_stages"), (
            "Killer A：扁平 sl/tp 应自动合成 tp_stages"
        )
        assert decision.invalidation.get("condition"), (
            "Killer A：应自动合成 invalidation.condition"
        )
        assert decision.lifecycle == ""
        # 不再降级为 hold——止血后这条信号应保持开仓
        assert decision.action == "buy"
        assert decision.should_open is True


# ════════════════════════════════════════════════════════════════════
# S1-11: trend_agent._normalize_direction v3 schema
# ════════════════════════════════════════════════════════════════════
class TestS11TrendAgentV3Schema:
    def test_normalize_parses_tp_sl_proposal_sl(self):
        """v3: tp_sl_proposal.sl_pct 覆盖 suggested_sl_pct。"""
        from backend.services.trend_agent import trend_agent
        result = {
            "trend_score": 70, "trend_direction": "long",
            "should_open_trend": True,
            "suggested_sl_pct": 0.08,
            "tp_sl_proposal": {"sl_pct": 0.10},
            "reasoning": "v3 趋势测试",
        }
        normalized = trend_agent._normalize_direction(result, "BTC", "long", min_score=50, market_envs={})
        # v3 tp_sl_proposal.sl_pct=0.10 应覆盖 0.08
        assert normalized["suggested_sl_pct"] == pytest.approx(0.10, abs=0.001)

    def test_normalize_parses_expected_hold_days_to_hours(self):
        """v3: expected_hold_days 转为 expected_hold_hours。"""
        from backend.services.trend_agent import trend_agent
        result = {
            "trend_score": 70, "trend_direction": "long", "should_open_trend": True,
            "suggested_sl_pct": 0.08,
            "expected_hold_days": 5,
            "reasoning": "持仓天数测试",
        }
        normalized = trend_agent._normalize_direction(result, "BTC", "long", min_score=50, market_envs={})
        # 5 天 = 120 小时
        assert normalized["expected_hold_hours"] == 120.0

    def test_normalize_scenario_not_truncated_to_150(self):
        """S7 修复：scenario_a 不再被截断到 150 字。"""
        from backend.services.trend_agent import trend_agent
        long_scenario = "A" * 400  # 400 字
        result = {
            "trend_score": 70, "trend_direction": "long", "should_open_trend": True,
            "suggested_sl_pct": 0.08,
            "scenario_a": long_scenario,
            "reasoning": "截断测试",
        }
        normalized = trend_agent._normalize_direction(result, "BTC", "long", min_score=50, market_envs={})
        # 应保留 400 字（不再截到 150）
        assert len(normalized["scenario_a"]) == 400

    def test_normalize_parses_invalidation(self):
        """v3: invalidation 对象。"""
        from backend.services.trend_agent import trend_agent
        result = {
            "trend_score": 70, "trend_direction": "long", "should_open_trend": True,
            "suggested_sl_pct": 0.08,
            "invalidation": {"condition": "日线趋势结构破坏"},
            "reasoning": "失效测试",
        }
        normalized = trend_agent._normalize_direction(result, "BTC", "long", min_score=50, market_envs={})
        assert normalized["invalidation"]["condition"] == "日线趋势结构破坏"

    def test_normalize_returns_new_v3_fields(self):
        """return dict 含 v3 新字段。"""
        from backend.services.trend_agent import trend_agent
        result = {
            "trend_score": 70, "trend_direction": "long", "should_open_trend": True,
            "suggested_sl_pct": 0.08, "reasoning": "v3 字段测试",
        }
        normalized = trend_agent._normalize_direction(result, "BTC", "long", min_score=50, market_envs={})
        for f in ["tp_sl_proposal", "scenarios", "invalidation", "self_check",
                  "expected_hold_hours", "lifecycle_evidence", "conviction_level"]:
            assert f in normalized, f"return 缺 v3 字段: {f}"


# ════════════════════════════════════════════════════════════════════
# S1-12: persist_independent_scan_log account_id 兜底
# ════════════════════════════════════════════════════════════════════
class TestS12PersistScanLogAccountIdFallback:
    def test_function_accepts_new_v3_params(self):
        """函数签名应支持 v3 新参数。"""
        import inspect
        from backend.services.full_auto.midlong_helpers import persist_independent_scan_log
        sig = inspect.signature(persist_independent_scan_log)
        params = list(sig.parameters.keys())
        assert "llm_tp_sl_proposal" in params
        assert "lifecycle" in params
        assert "scenarios" in params
        assert "invalidation" in params

    def test_zero_account_id_not_silently_skipped(self):
        """account_id=0 时不应静默 return（应尝试落库）。"""
        from backend.services.full_auto import midlong_helpers
        # mock AnalyticsSessionLocal + AIDecisionLog 避免真实 DB
        with patch("backend.database.connection.AnalyticsSessionLocal") as mock_session_local:
            mock_db = MagicMock()
            mock_session_local.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_session_local.return_value.__exit__ = MagicMock(return_value=False)
            with patch("backend.database.models.AIDecisionLog"):
                # account_id=None 应该走兜底逻辑（不 return），尝试调用 AnalyticsSessionLocal
                midlong_helpers.persist_independent_scan_log(
                    account_id=None,
                    symbol="TESTCOIN", tier="mid", trade_nature="swing",
                    action="hold", confidence=0, reasoning="测试兜底",
                    agent_source="test", market_summary={},
                )
                # 验证 AnalyticsSessionLocal 被调用（说明没在 account_id=None 时 return）
                mock_session_local.assert_called()


# ════════════════════════════════════════════════════════════════════
# S1-13: L2 版本校验回退
# ════════════════════════════════════════════════════════════════════
class TestS13L2VersionCheck:
    """
    [P1-7 修复 2026-07-19] 原测试硬编码假设"真实 DB 里 task_swing_agent
    version=2.0.0,manifest=3.0.0"——这个假设只在 S1-13 修复刚落地、旧缓存
    还没被新版覆盖的那个时间点成立。后来 prompt 归档机制正常运行后，DB
    里的 active 版本已经自然升级到 3.0.0，与 manifest 一致，导致本测试
    的前提失效（不代表版本校验回退逻辑本身坏了）。
    改为直接 mock hermes_fetchone / _load_manifest，不再依赖易变的真实
    DB 行数据，同时补充"版本匹配时应正常返回"的对照用例。
    """

    def test_l2_resolver_returns_none_when_db_version_below_manifest(self):
        """DB version < manifest version 时,L2 resolver 应返回 None（回退到 manifest 文件）。"""
        from backend.services import prompt_l2_resolver

        fake_row = {"id": 31, "version": "2.0.0", "full_text": "旧版 prompt 内容"}
        fake_manifest = {"tasks": {"task_swing_agent": {"version": "3.0.0"}}}

        with patch(
            "backend.services.hermes_db.hermes_fetchone", return_value=fake_row,
        ), patch(
            "backend.services.prompt_registry._load_manifest", return_value=fake_manifest,
        ), patch(
            "backend.services.prompt_l2_resolver._ab_enabled", return_value=False,
        ):
            res = prompt_l2_resolver.resolve_l2_prompt("task_swing_agent", consumer="test")
        assert res is None, f"DB v2.0.0 < manifest v3.0.0 应返回 None,实际: {res}"

    def test_l2_resolver_returns_resolution_when_version_matches(self):
        """DB version == manifest version 时,应正常返回 hermes_l2 来源的 PromptResolution。"""
        from backend.services import prompt_l2_resolver

        fake_row = {"id": 170, "version": "3.0.0", "full_text": "新版 prompt 内容"}
        fake_manifest = {"tasks": {"task_swing_agent": {"version": "3.0.0"}}}

        with patch(
            "backend.services.hermes_db.hermes_fetchone", return_value=fake_row,
        ), patch(
            "backend.services.prompt_registry._load_manifest", return_value=fake_manifest,
        ), patch(
            "backend.services.prompt_l2_resolver._ab_enabled", return_value=False,
        ):
            res = prompt_l2_resolver.resolve_l2_prompt("task_swing_agent", consumer="test")
        assert res is not None, "版本匹配时应返回有效 PromptResolution"
        assert res.version == "3.0.0"
        assert res.source == "hermes_l2"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
