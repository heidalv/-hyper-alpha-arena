"""
阶段0 Task1 集成测试：try_execute_independent_agent_open 的固定交易对守卫。

背景：
  长线（long/MLTO）开仓唯一终点 try_execute_independent_agent_open 此前信任 caller
  传入的 sym 完全，没有 auto-coin / 固定币校验。Phase0 Task1 在该函数顶部加守卫：
  tier == "long" 或 trade_nature ∈ (trend_follow, position) 时，sym 必须在
  get_fixed_symbols_for_session 的正向白名单内，否则拒绝开仓（return False）。

  短线（scalp/short）不经此函数（直接调 paper_engine.place_order），故本守卫
  对短线零影响；这里也用一个 tier="short" 的测试确认 tier 作用域正确。

运行：
  cd backend && python -m pytest tests/integration/test_fixed_symbol_gate.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch

# 长线开仓唯一终点
from backend.services.full_auto.midlong_helpers import try_execute_independent_agent_open


# ════════════════════════════════════════════════════════════════════
# 固定交易对守卫（Phase0 Task1）
# ════════════════════════════════════════════════════════════════════
class TestFixedSymbolGatePhase0:
    """验证 auto-coin / 非固定符号在长线开仓门被拦截。"""

    SESSION_ID = "sess_test_fixed_gate"
    FIXED_SYMBOL = "BTC"        # 在白名单内
    AUTO_SYMBOL = "KBONKBONK"   # 不在白名单内（auto-coin）

    def _build_mock_host(self):
        """构造 mock MidlongHelpersHost；evaluate_and_execute_proposal 默认 True。"""
        host = MagicMock()
        host.get_trading_account_id.return_value = 999010
        host.append_event = MagicMock()
        host.evaluate_and_execute_proposal = MagicMock(return_value=True)
        return host

    def _run(self, *, sym, tier, trade_nature, fixed_set):
        """统一调用入口：mock get_fixed_symbols_for_session 返回 fixed_set。"""
        host = self._build_mock_host()
        db = MagicMock()
        db.execute.return_value = None  # SELECT 1 健康检查不抛
        session = MagicMock()
        session.session_id = self.SESSION_ID

        with patch(
            "backend.services.auto_coin_selector.get_fixed_symbols_for_session",
            return_value=fixed_set,
        ):
            result = try_execute_independent_agent_open(
                db=db, session=session, sym=sym, tier=tier,
                action="buy",
                confidence=70, sl_pct=0.035, tp_pct=0.07,
                trade_nature=trade_nature,
                market_summary={},
                session_mode="running", host=host,
            )
        return result, host

    # ── Test 1：固定币 + tier=long → 放行 ──
    def test_fixed_symbol_long_tier_passes(self):
        """固定币 + 长线 tier，应在白名单内，进入 evaluate_and_execute_proposal。"""
        result, host = self._run(
            sym=self.FIXED_SYMBOL, tier="long",
            trade_nature="trend_follow",
            fixed_set={self.FIXED_SYMBOL, "ETH"},
        )
        assert result is True, "固定币 + long tier 应放行（host.evaluate 返回 True）"
        host.evaluate_and_execute_proposal.assert_called_once()

    # ── Test 2：auto-coin 币 + tier=long → 拦截 ──
    def test_auto_coin_symbol_long_tier_blocked(self):
        """auto-coin 符号 + 长线 tier，不在白名单 → 应被拦截，返回 False。"""
        result, host = self._run(
            sym=self.AUTO_SYMBOL, tier="long",
            trade_nature="trend_follow",
            fixed_set={self.FIXED_SYMBOL, "ETH"},  # 不含 AUTO_SYMBOL
        )
        assert result is False, "auto-coin 符号在 long tier 应被守卫拦截"
        # 关键：executor 不应被调用
        host.evaluate_and_execute_proposal.assert_not_called()
        # 应记录拦截事件
        host.append_event.assert_called()
        event_args = host.append_event.call_args
        assert event_args[0][1] == "fixed_symbol_gate_block"

    # ── Test 3：auto-coin 币 + tier=short → 不拦截（守卫仅作用 long） ──
    def test_auto_coin_symbol_short_tier_not_blocked(self):
        """auto-coin 符号 + tier=short + 短线 trade_nature：守卫不作用，
        应进入 evaluate_and_execute_proposal（短线本不经此函数，此测试仅确认 tier 作用域）。
        """
        result, host = self._run(
            sym=self.AUTO_SYMBOL, tier="short",
            trade_nature="scalp",  # 短线性质，非 trend_follow/position
            fixed_set={self.FIXED_SYMBOL, "ETH"},
        )
        # tier=short & trade_nature=scalp 都不在守卫范围 → 不拦截 → 走 executor
        assert result is True, "tier=short + scalp 不在守卫范围,应放行"
        host.evaluate_and_execute_proposal.assert_called_once()
        # 没有固定币拦截事件
        for call in host.append_event.call_args_list:
            assert call[0][1] != "fixed_symbol_gate_block"

    # ── Test 4：auto-coin 币 + tier=mid + swing → 不拦截（mid 路径阶段0仍存活） ──
    def test_auto_coin_symbol_mid_swing_not_blocked(self):
        """阶段0 mid 路径仍存活,守卫只守 long/trend_follow/position。
        auto-coin + tier=mid + swing → 不拦截。"""
        result, host = self._run(
            sym=self.AUTO_SYMBOL, tier="mid",
            trade_nature="swing",
            fixed_set={self.FIXED_SYMBOL, "ETH"},
        )
        assert result is True, "tier=mid + swing 阶段0 不在守卫范围,应放行"
        host.evaluate_and_execute_proposal.assert_called_once()

    # ── Test 5：trade_nature=position + 任意 tier → 拦截（即便 tier 非 long） ──
    def test_position_nature_auto_coin_blocked(self):
        """trade_nature=position 是长线性质,即便 tier 不是 long 也应被守卫拦截。"""
        result, host = self._run(
            sym=self.AUTO_SYMBOL, tier="mid",  # tier 不是 long
            trade_nature="position",          # 但 trade_nature=position
            fixed_set={self.FIXED_SYMBOL, "ETH"},
        )
        assert result is False, "trade_nature=position 应被守卫拦截"
        host.evaluate_and_execute_proposal.assert_not_called()

    # ── Test 6：白名单为空集（异常容错）→ 不拦截（容错优先） ──
    def test_empty_fixed_set_does_not_block(self):
        """get_fixed_symbols_for_session 返回空集（查询失败/session 不存在）时，
        守卫不拦截（容错优先：避免误伤正常开仓）。"""
        result, host = self._run(
            sym=self.AUTO_SYMBOL, tier="long",
            trade_nature="trend_follow",
            fixed_set=set(),  # 空
        )
        assert result is True, "白名单为空时应容错放行"
        host.evaluate_and_execute_proposal.assert_called_once()
