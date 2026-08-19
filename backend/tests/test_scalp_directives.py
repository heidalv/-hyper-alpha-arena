"""scalp 报告指挥闭环单测（设计 D1/D2/D3，2026-08-19）。

覆盖：
- pnl_authority.realized_pnl：partial 权威 / 价差复原 / 脏数据
- symbol_penalty 状态机：2 连亏→0.5、5 连亏→watchlist、3 连盈→恢复、同日幂等、笔数门槛
- report_directives.analyze_directives：D1 超时重训触发、D2 亏损币指令、健康日报无指令
- scalp_ranging_mr.apply_learned_mr：learned 覆盖 / 缺失回退 / 下限夹取
"""
import pytest

from backend.services.pnl_authority import realized_pnl
import backend.services.symbol_penalty as sp
from backend.services.report_directives import analyze_directives


# ───────────────────────── D3: pnl_authority ─────────────────────────

def test_pnl_authority_partial_wins():
    pos = {"partial_realized_pnl": 5.0, "entry_price": 100, "close_price": 110,
           "size": 1.0, "side": "long"}
    assert realized_pnl(pos) == 5.0


def test_pnl_authority_price_diff_long():
    pos = {"partial_realized_pnl": 0.0, "entry_price": 100, "close_price": 110,
           "size": 2.0, "side": "long"}
    assert realized_pnl(pos) == 20.0


def test_pnl_authority_price_diff_short():
    pos = {"partial_realized_pnl": 0.0, "entry_price": 110, "close_price": 100,
           "size": 2.0, "side": "short"}
    assert realized_pnl(pos) == 20.0  # (close-entry)*-1*size


def test_pnl_authority_garbage_zero():
    assert realized_pnl({}) == 0.0
    assert realized_pnl({"entry_price": 100, "close_price": 110}) == 0.0


# ─────────────────── D2: symbol_penalty 状态机 ───────────────────

@pytest.fixture
def sp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "_STATE_PATH", str(tmp_path / "symbol_penalty_state.json"))
    return tmp_path


def test_symbol_penalty_below_min_trades_no_advance(sp_state):
    st = sp.update_daily("GPS", -10.0, 2, "2026-08-17")
    assert st["penalty"] == 1.0 and not st["watchlisted"]
    assert st["consecutive_loss_days"] == 0


def test_symbol_penalty_two_loss_days_half_signal(sp_state):
    sp.update_daily("GPS", -10.0, 2, "2026-08-17")  # total=2 <5 不推进
    st = sp.update_daily("GPS", -8.0, 3, "2026-08-18")  # total=5 → loss_days=1
    assert st["consecutive_loss_days"] == 1
    assert st["penalty"] == 1.0
    st = sp.update_daily("GPS", -5.0, 1, "2026-08-19")  # loss_days=2 → 0.5
    assert st["penalty"] == 0.5
    assert not st["watchlisted"]
    assert sp.get_penalty("GPS") == 0.5
    assert not sp.is_watchlisted("GPS")


def test_symbol_penalty_watchlist_after_five_days(sp_state):
    for i in range(5):
        st = sp.update_daily("BNB", -5.0, 5, f"2026-08-{17 + i}")
    assert st["watchlisted"] is True
    assert st["penalty"] == 0.0
    assert sp.is_watchlisted("BNB")
    assert sp.get_penalty("BNB") == 0.0


def test_symbol_penalty_recovery_three_win_days(sp_state):
    for i in range(5):
        sp.update_daily("LIT", -5.0, 5, f"2026-08-{17 + i}")
    assert sp.is_watchlisted("LIT")
    for i in range(3):
        st = sp.update_daily("LIT", 3.0, 2, f"2026-08-{22 + i}")
    assert not st["watchlisted"]
    assert st["penalty"] == 1.0
    assert st["consecutive_win_days"] == 3


def test_symbol_penalty_idempotent_same_date(sp_state):
    sp.update_daily("ASTER", -4.0, 5, "2026-08-19")
    st2 = sp.update_daily("ASTER", -99.0, 50, "2026-08-19")  # 同日重复不推进
    assert st2["total_trades"] == 5
    assert len(st2["history"]) == 1


def test_symbol_penalty_zero_trades_day_no_change(sp_state):
    sp.update_daily("VIRTUAL", -4.0, 5, "2026-08-18")  # loss_days=1
    st = sp.update_daily("VIRTUAL", -4.0, 0, "2026-08-19")  # n=0 不推进
    assert st["consecutive_loss_days"] == 1


# ─────────────── D1/D2: report_directives 指挥层 ───────────────

def _report(date, scalp_symbols=None, exit_total=100, exit_timeout=20):
    scalp = {
        "symbol_daily": scalp_symbols or [],
        "exit_stats": {"total_exits": exit_total, "max_hold_timeout": exit_timeout,
                       "by_channel": {}},
    }
    return {"report_date": date, "account_id": 326,
            "sections": {"scalp": scalp, "midlong": {}, "long": {}}}


def test_directives_d1_timeout_ratio_over_35(sp_state):
    r = _report("2026-08-19", exit_total=100, exit_timeout=40)
    out = analyze_directives(r)
    types = [d["type"] for d in out]
    assert "tp_sl_retrain" in types
    d1 = next(d for d in out if d["type"] == "tp_sl_retrain")
    assert d1["timeout_ratio"] == 0.4


def test_directives_d1_no_trigger_below_35(sp_state):
    out = analyze_directives(_report("2026-08-19", exit_total=100, exit_timeout=20))
    assert not any(d["type"] == "tp_sl_retrain" for d in out)


def test_directives_d2_half_signal_after_two_loss_days(sp_state):
    analyze_directives(_report("2026-08-17", scalp_symbols=[
        {"symbol": "GPS", "pnl": -5.9, "n": 6}]))
    assert sp.get_penalty("GPS") == 1.0  # loss_days=1
    out = analyze_directives(_report("2026-08-18", scalp_symbols=[
        {"symbol": "GPS", "pnl": -3.0, "n": 2}]))
    d2 = [d for d in out if d["type"] == "symbol_penalty"]
    assert d2 and d2[0]["symbol"] == "GPS"
    assert d2[0]["action"] == "half_signal"
    assert sp.get_penalty("GPS") == 0.5


def test_directives_d2_watchlist_after_five_days(sp_state):
    for i in range(5):
        out = analyze_directives(_report(f"2026-08-{17 + i}", scalp_symbols=[
            {"symbol": "BNB", "pnl": -5.0, "n": 6}]))
    assert sp.is_watchlisted("BNB")
    last = [d for d in out if d["type"] == "symbol_penalty"]
    assert last and last[-1]["action"] == "watchlist"


def test_directives_healthy_no_op(sp_state):
    out = analyze_directives(_report("2026-08-19", scalp_symbols=[
        {"symbol": "CYS", "pnl": 20.8, "n": 3}], exit_total=100, exit_timeout=10))
    assert out == []
    assert sp.get_penalty("CYS") == 1.0


def test_directives_skip_zero_trades(sp_state):
    analyze_directives(_report("2026-08-19", scalp_symbols=[
        {"symbol": "ETH", "pnl": -4.0, "n": 0}]))
    assert sp.get_penalty("ETH") == 1.0
    assert not sp.snapshot()["symbols"].get("ETH")


# ─────────────── D1: ScalpMR learned 覆盖 ───────────────

def test_apply_learned_mr_override(monkeypatch):
    from backend.services.risk import tp_sl_grid_trainer as trainer
    from backend.services.scalp.scalp_ranging_mr import apply_learned_mr
    monkeypatch.setattr(trainer, "get_learned_pct",
                        lambda tier, band=None, morph=None: {"tp_pct": 0.025, "sl_pct": 0.015})
    tp, sl = apply_learned_mr(0.012, 0.012)
    assert tp == 0.025 and sl == 0.015


def test_apply_learned_mr_sl_floor_clip(monkeypatch):
    from backend.services.risk import tp_sl_grid_trainer as trainer
    from backend.services.scalp.scalp_ranging_mr import apply_learned_mr
    monkeypatch.setattr(trainer, "get_learned_pct",
                        lambda tier, band=None, morph=None: {"tp_pct": 0.010, "sl_pct": 0.002})
    tp, sl = apply_learned_mr(0.012, 0.015)
    assert tp == 0.010  # 0.010 >= MIN_TP 0.006
    assert sl == 0.012  # 0.002 被夹到 _MR_SL_FLOOR


def test_apply_learned_mr_fallback_when_missing(monkeypatch):
    from backend.services.risk import tp_sl_grid_trainer as trainer
    from backend.services.scalp.scalp_ranging_mr import apply_learned_mr
    monkeypatch.setattr(trainer, "get_learned_pct", lambda *a, **k: None)
    assert apply_learned_mr(0.012, 0.015) == (0.012, 0.015)


def test_apply_learned_mr_fallback_invalid_tp(monkeypatch):
    from backend.services.risk import tp_sl_grid_trainer as trainer
    from backend.services.scalp.scalp_ranging_mr import apply_learned_mr
    monkeypatch.setattr(trainer, "get_learned_pct",
                        lambda tier, band=None, morph=None: {"tp_pct": 0.0, "sl_pct": 0.015})
    assert apply_learned_mr(0.012, 0.015) == (0.012, 0.015)
