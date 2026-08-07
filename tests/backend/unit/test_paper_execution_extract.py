
def test_finalize_open_tp_sl_enforces_min_ratio():
    from backend.services.full_auto.paper_tp_sl import finalize_open_tp_sl

    # SL 3%, TP 3% -> ratio 1.0, should expand TP to 2.5x
    sl, tp = finalize_open_tp_sl(
        symbol="BTC",
        trade_nature="swing",
        side="buy",
        price=100.0,
        plan_sl=97.0,
        plan_tp=103.0,
        is_auto_coin=False,
    )
    assert sl == 97.0
    assert abs(tp - 107.5) < 0.01


def test_paper_execution_shim_delegates(monkeypatch):
    from backend.services.full_auto import paper_execution as pe
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}

    def _fake_execute(db, session, strat, decision, host):
        called["ok"] = True
        called["host_type"] = type(host).__name__
        return True

    monkeypatch.setattr(pe, "execute_paper_trade", _fake_execute)
    monkeypatch.setattr(pe, "build_paper_execution_host", lambda svc: pe.PaperExecutionHost(
        market_scan_cache={},
        template_recent_opens={},
        recovery_until={},
        recovery_position_scale=0.5,
        valid_trade_natures=set(),
        sub_mgr=None,
        ensure_bound_strategy=lambda *a, **k: None,
        get_trading_account_id=lambda *a, **k: 1,
        extract_ai_position_pct=lambda *a, **k: None,
        apply_auto_coin_position_scale=lambda *a, **k: None,
        append_event=lambda *a, **k: None,
        get_today_realized_pnl=lambda *a, **k: 0.0,
        get_validated_trade_nature=lambda *a, **k: "swing",
        recover_db_session=lambda *a, **k: None,
        is_unified_executor_on=lambda: False,
    ))

    svc = FullAutoTradingService.get_instance()
    assert svc._execute_paper_trade(None, None, None, {}) is True
    assert called.get("ok") is True
    assert called.get("host_type") == "PaperExecutionHost"
