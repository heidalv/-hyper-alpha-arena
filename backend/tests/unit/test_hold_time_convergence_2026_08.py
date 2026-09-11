"""[三周期持仓时间收敛 2026-08-13] 单元测试。

覆盖计划 4.3.3 / 4.3.4:
  1. position_hold_time: pace 倍率作用域（short/research 固定 1.0，mid/long 生效）
     与 research 车道解析（2h 固定上限、禁 AI 延长、不进 AI 复审）
  2. master_close_guard: check_agent_exit_hardfact 的 long/mid min_hold 前置
     （72h/12h 内非紧急亏损不得放行，紧急亏损阈值除外）
  3. unified_exit_executor: Tier2 mid/long _eff_flag 强制 enforce（不受全局/pace shadow 降级）
  4. 四源配置一致性: state_machine.TIER_PROTECTION / TIER_PROMPT_HINTS /
     runtime_tuning tier_max_hold_sec / NATURE_RULES 与主配置对齐
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from backend.services.position_hold_time import (
    get_position_hold_status,
    is_short_no_ai_hold_nature,
    resolve_max_hold_seconds,
    resolve_tier_absolute_cap_seconds,
    resolve_tier_review_seconds,
)
from backend.services.master_close_guard import check_agent_exit_hardfact


def _pos(trade_nature, tier, expected_hold_hours=None, opened_at=None):
    return SimpleNamespace(
        trade_nature=trade_nature,
        timeframe_tier=tier,
        expected_hold_hours=expected_hold_hours,
        opened_at=opened_at,
    )


# ════════════════════════════════════════════════════════════════════
# 1. pace 倍率作用域 + research 车道解析
# ════════════════════════════════════════════════════════════════════
class TestPaceMultiplierScope:
    @pytest.fixture
    def _pace_1_5x(self):
        knobs = SimpleNamespace(hold_timeout_multiplier=1.5)
        return patch(
            "backend.services.paper_pace_controller.paper_pace_controller.get_knobs",
            return_value=knobs,
        )

    def test_short_review_fixed_1x(self, _pace_1_5x):
        """短线复审点固定，pace×1.5 不生效。

        [2026-09-09] 权威值 5400→43200（12h，日内波段上限）。
        本用例测的是"pace 倍率对 short 不生效"这条语义，不是某个具体秒数，
        故随 runtime_tuning 权威值同步即可。
        """
        with _pace_1_5x:
            sec = resolve_tier_review_seconds(_pos("scalp", "short"))
        assert sec == 43200, "short 复审点应等于 runtime_tuning 权威值且不乘 pace"

    def test_research_review_fixed_1x(self, _pace_1_5x):
        """研究车道复审点固定 7200s，pace×1.5 不生效。"""
        with _pace_1_5x:
            sec = resolve_tier_review_seconds(_pos("pair_research", "research"))
        assert sec == 7200

    def test_mid_review_uses_pace(self, _pace_1_5x):
        """中线复审点 = 604800 × 1.5（[2026-09-09] mid 权威值 48h→7d，结构对齐）。"""
        with _pace_1_5x:
            sec = resolve_tier_review_seconds(_pos("swing", "mid"))
        assert sec == 604800 * 1.5

    def test_long_review_uses_pace(self, _pace_1_5x):
        with _pace_1_5x:
            sec = resolve_tier_review_seconds(_pos("trend_follow", "long"))
        assert sec == 604800 * 1.5

    def test_research_lane_2h_cap_no_extend(self):
        """research 仓: 2h 固定上限、绝对天花板=复审点(禁 AI 延长)。"""
        pos = _pos("pair_research", "research", expected_hold_hours=2.0)
        assert resolve_max_hold_seconds(pos) == 7200
        assert resolve_tier_absolute_cap_seconds(pos) == 7200

    def test_research_no_ai_reviewable(self):
        """research 仓不进 AI 复审/延长（与 mid 隔离）。"""
        pos = _pos("pair_research", "research", expected_hold_hours=2.0,
                   opened_at="2020-01-01T00:00:00+00:00")
        st = get_position_hold_status(pos)
        assert st["tier"] == "research"
        assert st["max_hold_hours"] == 2.0
        assert st["hold_ai_reviewable"] is False
        assert st["extendable_hours"] == 0.0

    def test_research_natures_no_ai(self):
        assert is_short_no_ai_hold_nature("pair_research")
        assert is_short_no_ai_hold_nature("research")


# ════════════════════════════════════════════════════════════════════
# 2. check_agent_exit_hardfact: long/mid min_hold 前置
# ════════════════════════════════════════════════════════════════════
def _opened(hours_ago: float):
    return datetime.now(timezone.utc) - timedelta(hours=hours_ago)


def _hf_call(tier, hours_ago, upnl, margin=100.0, action="close",
             channel="trend_review_close", opened_at=True):
    return check_agent_exit_hardfact(
        tier=tier,
        action=action,
        entry_price=100.0,
        mark_price=99.0,
        sl_price=90.0,
        unrealized_pnl=upnl,
        margin=margin,
        exit_channel=channel,
        opened_at=(_opened(hours_ago) if opened_at else None),
    )


class TestAgentExitLongMinHold:
    def test_long_small_loss_within_72h_blocked(self):
        """long 72h 内小亏(-1%) → 即使白名单 channel 也拦截。"""
        hf = _hf_call("long", hours_ago=1.0, upnl=-1.0)
        assert hf.allow is False
        assert hf.matched_rule == "min_hold_protection"

    def test_long_emergency_loss_within_72h_allowed(self):
        """long 72h 内紧急亏损(≥5%) → 放行。"""
        hf = _hf_call("long", hours_ago=1.0, upnl=-5.5)
        assert hf.allow is True

    def test_long_after_72h_still_needs_hardfact(self):
        """long 超过 72h → min_hold 不再拦，但仍需硬事实（小亏不放行）。

        [2026-09-02 更新] 原用例名为 test_long_after_72h_whitelist_allowed，
        断言 `matched_rule.startswith("agent_channel")` 即"白名单通道放行"。
        该分支已在 2026-08-14 F6 整改中删除 —— 见 master_close_guard 内注释：
        trend_review* close 纳入与 master 同标准硬事实门，理由是历史 12 笔
        该通道平仓 0% 胜率、合计 -44.90。测试当时未同步，此后长期失败。

        整改后的正确语义是**两道门串联**：
          1) min_hold 门：long 72h 内小亏直接拦（matched_rule=min_hold_protection）
          2) 过了 72h 也不自动放行，还要过 loss_pct/sl_breach/risk_score 硬事实
        本用例锁定第 2 道门：72h 后 -1% 仍应拦，且拦截理由已不是 min_hold。
        """
        hf = _hf_call("long", hours_ago=100.0, upnl=-1.0)
        assert hf.allow is False, "F6 整改后小亏不得凭白名单通道平仓"
        assert hf.matched_rule != "min_hold_protection", (
            "72h 已过，拦截理由应是硬事实不足而非 min_hold"
        )
        assert "loss_pct" in hf.detail

    def test_long_after_72h_with_real_loss_allowed(self):
        """long 超过 72h 且亏损达标 → 放行（确认门不是一味拒绝）。"""
        hf = _hf_call("long", hours_ago=100.0, upnl=-5.5)
        assert hf.allow is True
        assert hf.matched_rule.startswith("loss_pct>=")

    def test_mid_small_loss_within_12h_blocked(self):
        """mid 12h 内小亏 → 拦截（12h min_hold）。"""
        hf = _hf_call("mid", hours_ago=1.0, upnl=-1.0)
        assert hf.allow is False
        assert hf.matched_rule == "min_hold_protection"

    def test_mid_after_12h_still_needs_hardfact(self):
        """mid 超过 12h → min_hold 不再拦，但小亏仍需硬事实（同 F6 整改）。"""
        hf = _hf_call("mid", hours_ago=13.0, upnl=-1.0)
        assert hf.allow is False
        assert hf.matched_rule != "min_hold_protection"
        hf2 = _hf_call("mid", hours_ago=13.0, upnl=-3.0)
        assert hf2.allow is True, "mid 亏损达标（≥2%）应放行"

    def test_short_unaffected_by_min_hold(self):
        """short 跳过 min_hold 前置（白名单放行）。"""
        hf = _hf_call("short", hours_ago=0.1, upnl=-1.0)
        assert hf.allow is True

    def test_missing_opened_at_skips_min_hold_only(self):
        """无 opened_at（旧调用方）→ 只跳过 min_hold，硬事实门仍生效。

        [2026-09-02 更新] 原断言 allow is True（"白名单放行"），同样是 F6
        整改前的语义。缺 opened_at 时无法判断持仓时长，故 min_hold 前置放过，
        但这不等于免检 —— 否则任何旧调用方都能绕开硬事实门平掉小亏仓位。
        """
        hf = _hf_call("long", hours_ago=0, upnl=-1.0, opened_at=False)
        assert hf.matched_rule != "min_hold_protection", "缺 opened_at 时不应触发 min_hold"
        assert hf.allow is False, "跳过 min_hold 不等于跳过硬事实门"
        hf2 = _hf_call("long", hours_ago=0, upnl=-5.5, opened_at=False)
        assert hf2.allow is True, "缺 opened_at 且亏损达标应放行"


# ════════════════════════════════════════════════════════════════════
# 3. unified_exit_executor: Tier2 mid/long _eff_flag 强制 enforce
# ════════════════════════════════════════════════════════════════════
def _blocking_pos(tier="mid"):
    return {
        "timeframe_tier": tier,
        "entry_price": 100.0,
        "mark_price": 99.5,
        "sl_price": 90.0,
        "unrealized_pnl": -0.5,
        "margin": 100.0,
        "opened_at": "2020-01-01T00:00:00+00:00",
        "side": "long",
    }


def _tier2_shadow_ctx():
    """全局 flag=shadow + pace master_close_mode=shadow + hardfat shadow=false。"""
    knobs = SimpleNamespace(master_close_mode="shadow")
    return (
        patch.dict(os.environ, {"RISK_P3_HARDFAT_SHADOW": "false"}, clear=False),
        patch("backend.config.settings.RISK_P3_ENABLED", True),
        patch(
            "backend.config.settings.RISK_P3_MASTER_CLOSE_REQUIRES_HARDFACT",
            "shadow",
        ),
        patch(
            "backend.services.master_close_guard.check_master_min_hold_block",
            return_value=SimpleNamespace(allow=True, detail="ok"),
        ),
        patch(
            "backend.services.paper_pace_controller.paper_pace_controller.get_knobs",
            return_value=knobs,
        ),
    )


class TestMidLongHardfactEnforce:
    @pytest.mark.parametrize("tier,action", [
        ("mid", "close"), ("mid", "reduce"),
        ("long", "close"), ("long", "reduce"),
    ])
    def test_mid_long_enforce_despite_shadow_flags(self, tier, action):
        """全局/pace 均为 shadow 时，mid/long 仍强制 enforce → 拦截。"""
        from backend.services.unified_exit_executor import (
            ExitExecuteRequest, UnifiedExitExecutor,
        )
        ex = UnifiedExitExecutor()
        req = ExitExecuteRequest(
            db=None, account_id=1, symbol="TEST", action=action,
            pos=_blocking_pos(tier),
            exit_channel="master_running",
            reason="ai_suggest", reasoning="", tier_level=2,
        )
        e, p1, p2, p3, p4 = _tier2_shadow_ctx()
        with e, p1, p2, p3, p4:
            res = ex.check_hardfact_gate(req, tier_level=2)
        assert res.blocked is True
        assert res.event_type != "hardfat_shadow_passthrough"

    def test_short_close_keeps_shadow_grayscale(self):
        """短线 close 保持 shadow 灰度（不受 mid/long enforce 收敛影响）。"""
        from backend.services.unified_exit_executor import (
            ExitExecuteRequest, UnifiedExitExecutor,
        )
        ex = UnifiedExitExecutor()
        req = ExitExecuteRequest(
            db=None, account_id=1, symbol="TEST", action="close",
            pos=_blocking_pos("short"),
            exit_channel="master_running",
            reason="ai_suggest", reasoning="", tier_level=2,
        )
        e, p1, p2, p3, p4 = _tier2_shadow_ctx()
        with e, p1, p2, p3, p4:
            res = ex.check_hardfact_gate(req, tier_level=2)
        assert res.blocked is False

    def test_tier1_long_min_hold_blocked_via_executor(self):
        """executor Tier1 路径传 opened_at → long 72h 内小亏 trend_review 被拦。"""
        from backend.services.unified_exit_executor import (
            ExitExecuteRequest, UnifiedExitExecutor,
        )
        ex = UnifiedExitExecutor()
        pos = _blocking_pos("long")
        pos["opened_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        pos["unrealized_pnl"] = -1.0
        req = ExitExecuteRequest(
            db=None, account_id=1, symbol="TEST", action="close",
            pos=pos, exit_channel="trend_review",
            reason="trend_review", reasoning="", tier_level=1,
        )
        with patch.dict(os.environ, {"RISK_P3_HARDFAT_SHADOW": "false"}, clear=False):
            res = ex.check_hardfact_gate(req, tier_level=1)
        assert res.blocked is True
        assert res.event_type == "agent_exit_blocked"


# ════════════════════════════════════════════════════════════════════
# 4. 四源配置一致性断言
# ════════════════════════════════════════════════════════════════════
class TestFourSourceConsistency:
    def test_state_machine_min_hold_matches_main_config(self):
        """state_machine.TIER_PROTECTION.min_hold_sec == settings.TIER_PROTECTION_PARAMS。"""
        from backend.config.settings import TIER_PROTECTION_PARAMS
        from backend.services.exit.unified_exit_state_machine import TIER_PROTECTION
        for tier in ("short", "mid", "long"):
            assert TIER_PROTECTION[tier].min_hold_sec == TIER_PROTECTION_PARAMS[tier]["min_hold_sec"], tier

    def test_prompt_hints_match_min_hold(self):
        """TIER_PROMPT_HINTS 承诺与 min_hold 保护一致。"""
        from backend.config.settings import TIER_PROMPT_HINTS
        assert "12h" in TIER_PROMPT_HINTS["mid"]
        assert "72h" in TIER_PROMPT_HINTS["long"]
        assert "< 2h" in TIER_PROMPT_HINTS["short"]

    def test_runtime_tuning_authority_values(self):
        """runtime_tuning tier_max_hold_sec 权威值 = 12h/7d/7d。

        [2026-09-02] 本用例此前长期失败且掩盖了两个真问题：

        1. runtime_tuning_store 用相对路径 "data/runtime_tuning.json"，依赖
           进程 cwd。项目里同时存在 <root>/data/ 与 <root>/backend/data/ 两份
           同名文件且内容分叉（2700 vs 7200）；从项目根跑单测时两份都读不到，
           回退到 _DEFAULT_SCHEMA 的 2700，于是断言 2700 == 7200 恒失败。
           已改为锚定项目根的绝对路径。
        2. 三处注释（settings.py、scalp_config_routes.py、本用例）都写着
           "权威值 7200"，而根目录那份实际是 2700 —— 短线最大持仓一直按
           45min 执行，文档全错。

        [2026-09-09] 权威值演进：short 5400→43200（12h 日内波段上限）、
        mid 172800→604800（7d，与出场结构 72h+ 边际对齐）。
        """
        from backend.services.runtime_tuning_store import (
            get_tier_value, invalidate_cache, TUNING_FILE,
        )
        import os
        invalidate_cache()
        assert os.path.isabs(TUNING_FILE), (
            "TUNING_FILE 必须是绝对路径，否则读到哪一份取决于启动目录"
        )
        assert os.path.isfile(TUNING_FILE), f"权威文件不存在: {TUNING_FILE}"
        assert int(get_tier_value("tier_max_hold_sec", "short", 0)) == 43200
        assert int(get_tier_value("tier_max_hold_sec", "mid", 0)) == 604800
        assert int(get_tier_value("tier_max_hold_sec", "long", 0)) == 604800

    def test_nature_rules_within_tier_review(self):
        """NATURE_RULES 预期持仓不超过 tier 复审点（写入时 min 收敛）。"""
        from backend.services.sub_position_manager import NATURE_RULES
        from backend.services.position_hold_time import (
            resolve_initial_expected_hold_hours,
        )
        # swing 24h ≤ mid 复审 7d（pace 1x，[2026-09-09] mid 权威值 604800s）
        assert NATURE_RULES["swing"]["expected_hold_hours"] <= 604800 / 3600
        # trend_follow/position 168h ≤ long 复审 7d
        for n in ("trend_follow", "position"):
            assert NATURE_RULES[n]["expected_hold_hours"] <= 604800 / 3600
        # scalp 3h ≤ short 复审 12h（[2026-09-09] short 权威值 43200s）
        # scalp 写入预期 = min(nature 预期, short 复审)
        assert resolve_initial_expected_hold_hours("scalp", "short") <= 43200 / 3600 + 1e-6
