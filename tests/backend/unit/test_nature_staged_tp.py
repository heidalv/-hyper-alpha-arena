import pytest


@pytest.mark.unit
class TestNatureStagedTp:
    def test_trend_follow_stages_then_trailing_update(self):
        from backend.services.nature_staged_tp import NatureStagedTpState, check

        state = NatureStagedTpState()
        d1 = check(
            entry_price=100,
            current_price=108,
            side="long",
            trade_nature="trend_follow",
            atr_pct=0.01,
            state=state,
        )
        assert d1.action == "reduce"
        assert d1.stage_idx == 0

        d2 = check(
            entry_price=100,
            current_price=115,
            side="long",
            trade_nature="trend_follow",
            atr_pct=0.01,
            state=state,
        )
        assert d2.action == "reduce"
        assert d2.stage_idx == 1

        d3 = check(
            entry_price=100,
            current_price=125,
            side="long",
            trade_nature="trend_follow",
            atr_pct=0.01,
            state=state,
        )
        assert d3.action == "reduce"
        assert d3.stage_idx == 2

        d4 = check(
            entry_price=100,
            current_price=126,
            side="long",
            trade_nature="trend_follow",
            atr_pct=0.01,
            state=state,
        )
        assert d4.action == "trailing_update"
        assert d4.suggested_sl_price is not None
        assert state.trailing_active is True
