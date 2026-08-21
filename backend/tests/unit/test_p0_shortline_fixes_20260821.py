"""P0 短线修复回归（S1/S2/S4/S11/S3，设计文档《短线中线修复升级设计_20260820》）。

- S1  涨跌幅只补缺失（None/NaN），0/负值保留；DataFrame 用 iloc 取值
- S2  FlashVeto accept 不再把周末/薄时段缩仓重置为 1.0（*= 而非 =）
- S4  apply_short_tier_gate 透传 mode（冷却时长 paper/live 分档）
- S11 入场价<=0 不下单；拒单不写冷却/计数/TCP executed 副作用
- S3  BudgetService.get_used_margin 按 mode 分源（live→LiveSubPosition）
"""
import math
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))


# ── S1：缺失判定 ─────────────────────────────────────────
class TestS1PctMissing:
    def test_zero_is_not_missing(self):
        from backend.services.full_auto.loops.scalp_loop import _pct_missing
        assert _pct_missing(0) is False          # 横盘 0 是合法观测

    def test_negative_is_not_missing(self):
        from backend.services.full_auto.loops.scalp_loop import _pct_missing
        assert _pct_missing(-1.5) is False       # 下跌样本不得触发重算

    def test_none_and_nan_are_missing(self):
        from backend.services.full_auto.loops.scalp_loop import _pct_missing
        assert _pct_missing(None) is True
        assert _pct_missing(float("nan")) is True


# ── S1：DataFrame 用 iloc 取收盘 ────────────────────────
class TestS1KlineCloseAt:
    def test_dataframe_iloc(self):
        from backend.services.full_auto.loops.scalp_loop import _kline_close_at
        df = pd.DataFrame({
            "close": [100.0, 101.0, 102.0, 103.5, 104.0],
        })
        assert _kline_close_at(df, 1) == 104.0
        assert _kline_close_at(df, 3) == 102.0

    def test_list_of_dicts_compat(self):
        from backend.services.full_auto.loops.scalp_loop import _kline_close_at
        kl = [{"close": 10.0}, {"close": 11.0}, {"close": 12.0}]
        assert _kline_close_at(kl, 1) == 12.0
        assert _kline_close_at(kl, 2) == 11.0

    def test_out_of_range_returns_none(self):
        from backend.services.full_auto.loops.scalp_loop import _kline_close_at
        df = pd.DataFrame({"close": [1.0]})
        assert _kline_close_at(df, 5) is None


# ── S2 / S11：源码级断言（仓库既有惯例，见 test_swing_deprecated.py）──
class TestS2S11SourceContracts:
    _SRC = os.path.join(
        os.path.dirname(__file__), "..", "..", "services", "full_auto", "loops", "scalp_loop.py"
    )

    @classmethod
    def _source(cls):
        with open(cls._SRC, encoding="utf-8") as f:
            return f.read()

    def test_s2_veto_uses_compound_assign(self):
        src = self._source()
        # veto 未否决时不得用赋值覆盖 _liquidity_mult
        assert "_size_mult *= (_veto.size_multiplier if _veto.verdict == \"downsize\" else 1.0)" in src
        assert "_size_mult = _veto.size_multiplier if" not in src

    def test_s11_entry_guard_before_order(self):
        src = self._source()
        assert "if _scalp_entry <= 0:" in src
        assert "no_entry_price" in src

    def test_s11_fill_gate_before_side_effects(self):
        src = self._source()
        assert "_fill_ok" in src
        assert "order_not_filled" in src
        # 拒单必须跳过后续副作用（continue），不能只打日志
        assert "_bump_block(\"order_not_filled\")" in src

    def test_s1_no_legacy_df_minus_one_access(self):
        src = self._source()
        # 旧实现的实际代码模式（float(...) 取值 + [-12] 回看）必须消失
        assert "float(_klines[-1].get" not in src
        assert "_klines[-12]" not in src
        assert "_klines[-_n24]" not in src

    def test_s3_budget_calls_use_real_mode(self):
        src = self._source()
        assert '"short", equity, "paper"' not in src
        assert '"short", _scalp_req_margin, equity, "paper"' not in src

    def test_s3_tcp_channel_uses_real_mode(self):
        src = self._source()
        assert 'execution_channel="paper"' not in src


# ── S4：mode 透传 ────────────────────────────────────────
class TestS4ShortTierMode:
    def test_apply_gate_forwards_mode(self, monkeypatch):
        import backend.services.short_tier_entry_gate as stg

        captured = {}

        class _Res:
            allowed = True
            reason = ""
            adjusted_threshold = 50

        def _fake_check(**kwargs):
            captured.update(kwargs)
            return _Res()

        monkeypatch.setattr(stg, "check_short_tier_entry", _fake_check)
        ok, _ = stg.apply_short_tier_gate(
            account_id=1, symbol="BTC", side="buy", action="buy",
            confidence=80, tier="short", trade_nature="scalp", mode="live",
        )
        assert ok is True
        assert captured.get("mode") == "live"

    def test_apply_gate_default_mode_paper(self, monkeypatch):
        import backend.services.short_tier_entry_gate as stg

        captured = {}

        class _Res:
            allowed = True
            reason = ""
            adjusted_threshold = 50

        monkeypatch.setattr(
            stg, "check_short_tier_entry", lambda **kw: (captured.update(kw), _Res())[1]
        )
        stg.apply_short_tier_gate(
            account_id=1, symbol="BTC", side="buy", action="buy",
            confidence=80, tier="short", trade_nature="scalp",
        )
        assert captured.get("mode") == "paper"


# ── S3：BudgetService 按 mode 分源 ──────────────────────
class TestS3BudgetModeSplit:
    def _make_fake_conn(self, monkeypatch, rows):
        import backend.database.connection as conn

        class _FakeQuery:
            def __init__(self, rows_):
                self._rows = rows_

            def filter(self, *a, **k):
                return self

            def all(self):
                return self._rows

        class _FakeDB:
            def __init__(self):
                self.models = []

            def query(self, model):
                self.models.append(model)
                return _FakeQuery(rows)

            def close(self):
                pass

        fake = _FakeDB()
        monkeypatch.setattr(conn, "SessionLocal", lambda: fake)
        return fake

    def test_paper_mode_reads_paper_position(self, monkeypatch):
        from backend.database.models import PaperPosition
        from backend.services.budget_service import budget_service

        class _P:
            account_id = 7
            trade_nature = "scalp"
            margin = 123.0

        fake = self._make_fake_conn(monkeypatch, [_P()])
        used = budget_service.get_used_margin("scalp", mode="paper", account_id=7)
        assert fake.models and fake.models[0] is PaperPosition
        assert used == pytest.approx(123.0)

    def test_live_mode_reads_live_sub_position(self, monkeypatch):
        from backend.database.models import LiveSubPosition
        from backend.services.budget_service import budget_service

        class _P:
            account_id = 7
            trade_nature = "scalp"
            margin = 55.0

        fake = self._make_fake_conn(monkeypatch, [_P()])
        used = budget_service.get_used_margin("scalp", mode="live", account_id=7)
        assert fake.models and fake.models[0] is LiveSubPosition
        assert used == pytest.approx(55.0)

    def test_layer_nature_mapping_unaffected(self, monkeypatch):
        from backend.services.budget_service import budget_service

        class _P:
            account_id = 7
            trade_nature = "trend_follow"
            margin = 10.0

        self._make_fake_conn(monkeypatch, [_P()])
        assert budget_service.get_used_margin("scalp", mode="paper", account_id=7) == 0.0
