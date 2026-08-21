"""P0 中线修复回归（M1/M2/M3，设计文档《短线中线修复升级设计_20260820》§4）。

- M1  entry_source 落库（executor→helpers→proposal.extra→exit_state_json）
      + 出场分流（factor_route 仓禁方向复查/叙事平仓 + factor_invalidated 最小实现）
- M2  持仓互锁改同 nature（has_open_position_of_nature + exists 语义）
- M3  路由 K 线与成交同所（FACTOR_ROUTE_KLINE_EXCHANGE=active，不足 hold 不回退）
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))


# ── M2：同 nature 互锁 ──────────────────────────────────
class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, model):
        return _FakeQuery(self._rows)


class _Pos:
    def __init__(self, nature="", tier=""):
        self.trade_nature = nature
        self.timeframe_tier = tier


class TestM2NatureInterlock:
    def test_long_does_not_block_mid(self):
        from backend.services.full_auto.midlong_position_manager import (
            has_open_position_of_nature,
        )
        db = _FakeDB([_Pos(nature="trend_follow", tier="long")])
        assert has_open_position_of_nature(db, 1, "BTC", "mid") is False
        assert has_open_position_of_nature(db, 1, "BTC", "long") is True

    def test_mid_blocks_mid_not_second_mid(self):
        from backend.services.full_auto.midlong_position_manager import (
            has_open_position_of_nature,
        )
        db = _FakeDB([_Pos(nature="swing", tier="mid")])
        assert has_open_position_of_nature(db, 1, "BTC", "mid") is True
        assert has_open_position_of_nature(db, 1, "BTC", "long") is False

    def test_scalp_never_blocks(self):
        from backend.services.full_auto.midlong_position_manager import (
            has_open_position_of_nature,
        )
        db = _FakeDB([_Pos(nature="scalp", tier="short")])
        assert has_open_position_of_nature(db, 1, "BTC", "mid") is False
        assert has_open_position_of_nature(db, 1, "BTC", "long") is False

    def test_tier_fallback_when_nature_missing(self):
        from backend.services.full_auto.midlong_position_manager import (
            has_open_position_of_nature,
        )
        db = _FakeDB([_Pos(nature="", tier="mid")])
        assert has_open_position_of_nature(db, 1, "BTC", "mid") is True

    def test_mode_b_detection_still_any_midlong(self):
        # mlto_cycle:259 的模式 B 判定语义保留：任何 mid/long 仓 → True
        from backend.services.full_auto.midlong_position_manager import (
            has_open_midlong_position,
        )
        db = _FakeDB([_Pos(nature="trend_follow", tier="long")])
        assert has_open_midlong_position(db, 1, "BTC") is True
        db2 = _FakeDB([_Pos(nature="scalp", tier="short")])
        assert has_open_midlong_position(db2, 1, "BTC") is False

    def test_mixed_rows_scan_all_not_first(self):
        # 原缺陷：.first() 只看任意一行——scalp 行在前会漏检 mid 行
        from backend.services.full_auto.midlong_position_manager import (
            has_open_midlong_position,
        )
        db = _FakeDB([
            _Pos(nature="scalp", tier="short"),
            _Pos(nature="swing", tier="mid"),
        ])
        assert has_open_midlong_position(db, 1, "BTC") is True


# ── M1：entry_source 链路 ───────────────────────────────
class TestM1EntrySourceChain:
    def test_proposal_from_agent_collects_entry_source(self):
        from backend.services.decision_core.proposal import TradeProposal
        p = TradeProposal.from_agent(
            sym="BTC", tier="mid", action="buy", confidence=60,
            trade_nature="swing", entry_source="factor_route",
        )
        assert p.extra.get("entry_source") == "factor_route"

    def test_helpers_extra_kwargs_carry_entry_source(self, monkeypatch):
        # 只验证 _extra_kwargs 构造段（不跑完整 open 链）
        import inspect
        from backend.services.full_auto import midlong_helpers as mh
        src = inspect.getsource(mh.try_execute_independent_agent_open)
        assert '"entry_source": entry_source' in src
        assert 'entry_source: str = ""' in src

    def test_executor_passes_source(self):
        import inspect
        from backend.services.full_auto import midlong_executor as mex
        src = inspect.getsource(mex.execute_midlong_open)
        assert "entry_source=str(source" in src

    def test_exit_state_stamp_after_lifecycle_init(self):
        # 落库必须发生在 _exit_state 初始化之后（曾出现过插错位置的回归）
        p = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "services", "full_auto", "proposal_execution.py",
        )
        src = open(p, encoding="utf-8").read()
        i_init = src.find('_exit_state["lifecycle_state"] = "initial"')
        i_stamp = src.find('_exit_state["entry_source"] = _entry_src')
        assert 0 < i_init < i_stamp


class TestM1FactorInvalidated:
    def _patch_set(self, monkeypatch, n=None, raise_exc=None):
        import backend.services.factor_engine.midlong_active_factor_set as mafs

        class _Fake:
            def get_active_factors(self):
                if raise_exc:
                    raise raise_exc
                return [{"factor_id": f"f{i}"} for i in range(n)]

        monkeypatch.setattr(mafs, "midlong_active_factor_set", _Fake())

    def test_below_threshold_returns_reason(self, monkeypatch):
        import backend.config.settings as _s
        monkeypatch.setattr(_s, "FACTOR_ROUTE_MIN_ACTIVE_FACTORS", 2, raising=False)
        self._patch_set(monkeypatch, n=1)
        from backend.services.full_auto.midlong_position_manager import (
            _factor_invalidated_reason,
        )
        r = _factor_invalidated_reason()
        assert r and "活跃因子1<2" in r

    def test_enough_factors_returns_none(self, monkeypatch):
        import backend.config.settings as _s
        monkeypatch.setattr(_s, "FACTOR_ROUTE_MIN_ACTIVE_FACTORS", 2, raising=False)
        self._patch_set(monkeypatch, n=5)
        from backend.services.full_auto.midlong_position_manager import (
            _factor_invalidated_reason,
        )
        assert _factor_invalidated_reason() is None

    def test_read_failure_returns_none_no_action(self, monkeypatch):
        self._patch_set(monkeypatch, raise_exc=RuntimeError("db down"))
        from backend.services.full_auto.midlong_position_manager import (
            _factor_invalidated_reason,
        )
        assert _factor_invalidated_reason() is None


class TestM1ExitRoutingSourceContracts:
    """出场分流源码契约：factor_route 仓禁方向复查/叙事平仓。"""

    _P = os.path.join(
        os.path.dirname(__file__), "..", "..",
        "services", "full_auto", "midlong_position_manager.py",
    )

    @classmethod
    def _source(cls):
        return open(cls._P, encoding="utf-8").read()

    def test_entry_source_read_from_state(self):
        src = self._source()
        assert '_state.get("entry_source")' in src
        assert '_is_factor_pos = _entry_source == "factor_route"' in src

    def test_reversal_skip_and_factor_invalidated(self):
        src = self._source()
        assert "factor_invalidated" in src
        assert "因子仓跳过叙事平仓" in src

    def test_direction_review_skipped_for_factor_pos(self):
        src = self._source()
        assert "因子仓跳过方向复查" in src


# ── M3：路由 K 线同所 ───────────────────────────────────
class TestM3RouteKlineExchange:
    def test_default_is_active(self, monkeypatch):
        import backend.config.settings as _s
        monkeypatch.setattr(_s, "FACTOR_ROUTE_KLINE_EXCHANGE", "active", raising=False)
        from backend.services.factor_engine.midlong_factor_route import (
            _route_kline_exchange,
        )
        assert _route_kline_exchange() == "active"

    def test_active_insufficient_returns_none_without_binance_fallback(self, monkeypatch):
        import backend.config.settings as _s
        import backend.services.kline_data_service as kds
        monkeypatch.setattr(_s, "FACTOR_ROUTE_KLINE_EXCHANGE", "active", raising=False)
        monkeypatch.setattr(
            kds.kline_service, "get_klines_from_db", lambda *a, **k: []
        )
        import backend.services.factor_engine.factor_backtest_scorer as fbs

        def _no_fallback(*a, **k):
            raise AssertionError("不得回退 scorer/binance 加载器")

        monkeypatch.setattr(fbs.factor_backtest_scorer, "_load_klines", _no_fallback)
        from backend.services.factor_engine.midlong_factor_route import _load_df
        assert _load_df("BTC", "4h", 260) is None

    def test_explicit_exchange_uses_scorer_loader(self, monkeypatch):
        import backend.config.settings as _s
        import backend.services.factor_engine.factor_backtest_scorer as fbs
        monkeypatch.setattr(_s, "FACTOR_ROUTE_KLINE_EXCHANGE", "binance", raising=False)
        rows = [{"open": 1, "high": 1, "low": 1, "close": float(i), "volume": 1} for i in range(80)]
        monkeypatch.setattr(
            fbs.factor_backtest_scorer, "_load_klines", lambda *a, **k: rows
        )
        from backend.services.factor_engine.midlong_factor_route import _load_df
        df = _load_df("BTC", "4h", 260)
        assert df is not None and len(df) == 80

    def test_decide_carries_kline_exchange(self, monkeypatch):
        import backend.config.settings as _s
        monkeypatch.setattr(_s, "FACTOR_ROUTE_KLINE_EXCHANGE", "active", raising=False)
        from backend.services.factor_engine.midlong_factor_route import factor_route_decide
        out = factor_route_decide("BTC", market_summary={})
        assert out.get("kline_exchange") == "active"

    def test_backtest_default_not_touched(self):
        # 回测/晋升默认仍是 binance 深历史，不受路由开关影响
        import backend.services.factor_engine.factor_backtest_scorer as fbs
        assert fbs.FactorBacktestScorer._backtest_exchange() == "binance"
