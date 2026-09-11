# -*- coding: utf-8 -*-
"""score_due / FactorIC 事务卫生：读完即 release，算价/IC 不拖着核心库事务。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.services.analysis import ledgers as ledgers_mod
from backend.services import factor_ic_evaluator as ic_mod


def test_score_due_releases_before_price_at():
    """SELECT 到期行后必须 release，再调 price_at；禁止边 UPDATE 边查价。"""
    calls = []

    class _Result:
        def mappings(self):
            return self

        def all(self):
            return [
                {
                    "id": "s1",
                    "symbol": "ETH",
                    "direction": 1,
                    "confidence": 0.6,
                    "created_ms": 1_000_000,
                    "expires_ms": 2_000_000,
                    "entry_price": 100.0,
                }
            ]

    db = MagicMock()
    db.execute.return_value = _Result()

    def _rel(session, *, where=""):
        calls.append(("release", where))
        return True

    def _price(sym, ts, **kw):
        calls.append(("price", sym, ts))
        return 101.0

    with patch.object(ledgers_mod, "ensure_schema"), \
         patch.object(ledgers_mod, "_db", return_value=db), \
         patch.object(ledgers_mod, "now_ms", return_value=3_000_000), \
         patch.object(ledgers_mod, "price_at", side_effect=_price), \
         patch("backend.database.connection.release_idle_txn", side_effect=_rel), \
         patch.object(ledgers_mod, "_OUTCOME_EVALUATORS", {}):
        # 第二段 preds 空
        empty = MagicMock()
        empty.mappings.return_value.all.return_value = []

        def _exec(*a, **k):
            sql = str(a[0]) if a else ""
            if "agent_predictions" in sql:
                return empty
            return _Result()

        db.execute.side_effect = _exec
        stats = ledgers_mod.score_due(limit=10)

    assert stats["signals_scored"] == 1
    # 第一次 release 必须早于任何 price_at
    first_rel = next(i for i, c in enumerate(calls) if c[0] == "release")
    first_price = next(i for i, c in enumerate(calls) if c[0] == "price")
    assert first_rel < first_price, calls


def test_factor_ic_releases_after_fetch():
    db = MagicMock()
    row = MagicMock()
    row.signal_type = "factor:foo_bar"
    row.signal_value = 1.0
    row.trade_pnl = 1.5
    row.trade_side = "long"
    db.query.return_value.filter.return_value.all.return_value = [row]

    released = []

    def _rel(session, *, where=""):
        released.append(where)
        return True

    with patch("backend.database.connection.release_idle_txn", side_effect=_rel), \
         patch.object(ic_mod, "_resolvable_factor_names", return_value={"foo_bar"}), \
         patch.object(ic_mod, "_rank_ic", return_value=0.2), \
         patch.object(ic_mod, "MIN_SAMPLES", 1), \
         patch("builtins.open", create=True), \
         patch("os.makedirs"):
        # 避免写 analytics / 文件副作用过深
        with patch.dict("os.environ", {"FACTOR_IC_WEIGHT_MODE": "ic_ev"}):
            try:
                ic_mod.run_factor_ic_evaluation(db, lookback_days=7)
            except Exception:
                # 写盘路径可能 mock 不全；只要 release 被调用即过
                pass

    assert any("factor_ic_eval.pre_compute" in w for w in released), released
