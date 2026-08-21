"""S8 回归：总控对短线持仓 close/reduce 的按持仓拦截（设计 §3.4）。

- 白名单外：短线持仓的 close/reduce 在决策循环早期 continue（不再落穿到
  master_running_close/reduce、short 减仓浮亏规则与统一出场 fast close）
- 「本tick已独立交易」分支不再把短线 close/reduce 静默改写为 hold
- 白名单关键字可配置，置空 = 永不放行
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))


class TestScalpPositionPredicate:
    def test_nature_scalp(self):
        from backend.services.full_auto.master_execution import _is_scalp_position
        assert _is_scalp_position({"trade_nature": "scalp"}) is True
        assert _is_scalp_position({"trade_nature": "Intraday"}) is True

    def test_tier_short(self):
        from backend.services.full_auto.master_execution import _is_scalp_position
        assert _is_scalp_position({"trade_nature": "", "timeframe_tier": "short"}) is True

    def test_midlong_not_scalp(self):
        from backend.services.full_auto.master_execution import _is_scalp_position
        assert _is_scalp_position({"trade_nature": "swing", "timeframe_tier": "mid"}) is False
        assert _is_scalp_position({"trade_nature": "trend_follow", "timeframe_tier": "long"}) is False

    def test_empty_and_none(self):
        from backend.services.full_auto.master_execution import _is_scalp_position
        assert _is_scalp_position({}) is False
        assert _is_scalp_position(None) is False


class TestWhitelist:
    def test_default_tokens(self, monkeypatch):
        import backend.config.settings as _s
        import backend.services.full_auto.master_execution as me
        monkeypatch.setattr(_s, "MASTER_SCALP_EXIT_WHITELIST",
                            "risk_engine,liquidation,margin_call,emergency", raising=False)
        tokens = me._master_scalp_exit_whitelist()
        assert "risk_engine" in tokens and "liquidation" in tokens

    def test_empty_means_never_allow(self, monkeypatch):
        import backend.config.settings as _s
        import backend.services.full_auto.master_execution as me
        monkeypatch.setattr(_s, "MASTER_SCALP_EXIT_WHITELIST", "", raising=False)
        assert me._master_scalp_exit_whitelist() == ()

    def test_custom_tokens(self, monkeypatch):
        import backend.config.settings as _s
        import backend.services.full_auto.master_execution as me
        monkeypatch.setattr(_s, "MASTER_SCALP_EXIT_WHITELIST", "MyRisk, X ", raising=False)
        assert me._master_scalp_exit_whitelist() == ("myrisk", "x")


class TestS8SourceContracts:
    """源码级契约（仓库惯例）：守卫位于决策循环早期，且覆盖 close/reduce。"""

    _SRC = os.path.join(
        os.path.dirname(__file__), "..", "..", "services", "full_auto", "master_execution.py"
    )

    @classmethod
    def _source(cls):
        with open(cls._SRC, encoding="utf-8") as f:
            return f.read()

    def test_guard_before_dedup_block(self):
        src = self._source()
        guard = src.find("短线仓的 close/reduce 也按持仓拦截")
        dedup = src.find("已被 _run_scalp_independent 处理")
        assert 0 < guard < dedup, "S8 守卫必须先于「本tick已独立交易」去重分支"

    def test_guard_uses_position_not_decision_nature(self):
        src = self._source()
        guard_block = src[src.find("短线仓的 close/reduce 也按持仓拦截"):src.find("短线仓的 close/reduce 也按持仓拦截") + 1600]
        assert "symbol_positions" in guard_block
        assert "_is_scalp_position" in guard_block

    def test_dedup_block_preserves_close_reduce(self):
        src = self._source()
        assert 'if action not in ("close", "reduce"):' in src

    def test_short_reduce_loss_rule_not_removed(self):
        # 二道防线保留（对非短线仓仍生效）
        src = self._source()
        assert "MASTER_REDUCE_MIN_LOSS_PCT" in src
