"""S2-8: paper 平仓盈亏 → ai_decision_logs 回填 单元测试。"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.services.calibration.decision_pnl_backfill import (
    _match,
    backfill_decision_pnl,
)

_T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _dt(minutes_offset):
    return _T0 + timedelta(minutes=minutes_offset)


class TestMatch:
    def test_matches_decision_within_window_before_open(self):
        # 决策在开仓前 10 分钟 → 窗口 15 分钟内 → 命中
        decisions = [(1, 1, "BTC", _dt(-10))]
        positions = [(1, "BTC", _dt(0), 25.5)]
        assert _match(decisions, positions, 15) == [(1, 25.5)]

    def test_rejects_decision_too_early(self):
        # 决策在开仓前 30 分钟 → 超出窗口 → 不命中
        decisions = [(1, 1, "BTC", _dt(-30))]
        positions = [(1, "BTC", _dt(0), 25.5)]
        assert _match(decisions, positions, 15) == []

    def test_rejects_decision_after_open(self):
        # 决策晚于开仓 → 不命中（先开仓后决策不可能）
        decisions = [(1, 1, "BTC", _dt(5))]
        positions = [(1, "BTC", _dt(0), 25.5)]
        assert _match(decisions, positions, 15) == []

    def test_different_symbol_or_account_not_matched(self):
        decisions = [(1, 1, "BTC", _dt(-5)), (2, 2, "ETH", _dt(-5))]
        positions = [(1, "ETH", _dt(0), 10.0)]
        assert _match(decisions, positions, 15) == []

    def test_picks_closest_decision_to_open(self):
        # 两条候选（-12min 与 -2min）→ 只取最接近开仓的一条
        decisions = [(1, 1, "BTC", _dt(-12)), (2, 1, "BTC", _dt(-2))]
        positions = [(1, "BTC", _dt(0), 8.0)]
        matched = _match(decisions, positions, 15)
        assert matched == [(2, 8.0)]

    def test_each_decision_used_once(self):
        # 一个决策不能同时归属两个仓位（先到先得）
        decisions = [(1, 1, "BTC", _dt(-5))]
        positions = [(1, "BTC", _dt(-4), 1.0), (1, "BTC", _dt(0), 2.0)]
        matched = _match(decisions, positions, 15)
        assert len(matched) == 1
        assert matched[0][1] in (1.0, 2.0)


class TestBackfillEndToEnd:
    """端到端：patch 两个 SessionLocal，验证回填链路。"""

    def _install(self, monkeypatch, dec_rows, pos_rows, updated_log):
        class _Q:
            def __init__(self, rows):
                self._rows = rows

            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def limit(self, *a, **k):
                return self

            def all(self):
                return list(self._rows)

        class _DecDb:
            def __init__(self):
                self.calls = []

            def query(self, *entities):
                return _Q(dec_rows)

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        class _PosDb:
            def __init__(self):
                self.calls = []

            def query(self, *entities):
                return _Q(pos_rows)

            def rollback(self):
                pass

            def close(self):
                pass

        class _UpdDb:
            """记录 update 调用。"""
            def __init__(self):
                self.updates = []
                self.committed = False

            def query(self, *entities):
                return _UpdateQuery(self.updates)

            def commit(self):
                self.committed = True

            def rollback(self):
                pass

            def close(self):
                pass

        class _UpdateQuery:
            def __init__(self, updates):
                self.updates = updates

            def filter(self, *a, **k):
                return self

            def update(self, values, **kwargs):
                self.updates.append(values)
                return 1

        dbs = {"dec": _DecDb(), "pos": _PosDb(), "upd": _UpdDb()}

        def _dec_factory():
            if not dbs["dec"].calls:
                dbs["dec"].calls.append(1)
                return dbs["dec"]
            return dbs["upd"]

        monkeypatch.setattr(
            "backend.database.connection.AnalyticsSessionLocal",
            _dec_factory,
        )
        monkeypatch.setattr(
            "backend.database.connection.SessionLocal",
            lambda: dbs["pos"],
        )
        return dbs

    def test_end_to_end_backfill(self, monkeypatch):
        dec_rows = [(1, 1, "BTC", _dt(-10))]  # id, account, symbol, decision_time
        pos_rows = [(1, "BTC", _dt(0), 33.3)]  # account, symbol, opened_at, pnl
        dbs = self._install(monkeypatch, dec_rows, pos_rows, None)
        res = backfill_decision_pnl(lookback_days=90)
        assert res["candidates"] == 1
        assert res["positions"] == 1
        assert res["matched"] == 1
        assert res["updated"] == 1
        assert dbs["upd"].committed is True
        assert len(dbs["upd"].updates) == 1
        assert dbs["upd"].updates[0]["realized_pnl"] == 33.3
        assert dbs["upd"].updates[0]["pnl_updated_at"] is not None

    def test_no_match_no_update(self, monkeypatch):
        dec_rows = [(1, 1, "BTC", _dt(-30))]  # 超出窗口
        pos_rows = [(1, "BTC", _dt(0), 33.3)]
        dbs = self._install(monkeypatch, dec_rows, pos_rows, None)
        res = backfill_decision_pnl(lookback_days=90)
        assert res["matched"] == 0
        assert res["updated"] == 0
        assert dbs["upd"].updates == []

    def test_empty_candidates_short_circuit(self, monkeypatch):
        dbs = self._install(monkeypatch, [], [], None)
        res = backfill_decision_pnl(lookback_days=90)
        assert res["candidates"] == 0
        assert res["positions"] == 0
