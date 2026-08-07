"""Monolith Phase2 瘦身：迁出模块与 shim 转发一致性。"""
from __future__ import annotations

from backend.services.full_auto.execution_gates import (
    decision_price_consistency_ok,
    orchestrator_blocks_open,
)
from backend.services.full_auto.market_summary_helpers import (
    MarketSummaryContext,
    bootstrap_market_summary,
    sanitize_market_summary_for_qaa,
)
from backend.services.full_auto.orchestrator_ui_helpers import (
    backfill_dec_confidence_from_orch,
    normalize_orchestrator_for_ui,
    tier_confidence_pct,
)
from backend.services.full_auto.paper_risk_helpers import (
    get_account_risk_score,
    tiny_close_allowed_by_hardfact,
)
from backend.services.full_auto.strategy_binding import active_exchange
from backend.services.full_auto.tp_sl_prices import compute_initial_tp_sl_prices
from backend.services.full_auto.db_session_helpers import deferred_signal_key as dsk
from backend.services.full_auto_trading_service import FullAutoTradingService


def test_tp_sl_shim_matches_extracted_module():
    direct = compute_initial_tp_sl_prices("mid", "buy", 100.0, atr_pct=0.02, sym="BTCUSDT")
    shim = FullAutoTradingService._compute_initial_tp_sl_prices(
        "mid", "buy", 100.0, atr_pct=0.02, sym="BTCUSDT",
    )
    assert direct == shim


def test_orchestrator_ui_shim_matches_extracted_module():
    info = {"orchestrator": {"long_confidence": 0.72, "mid_confidence": 55}}
    normalize_orchestrator_for_ui(info)
    info2 = {"orchestrator": {"long_confidence": 0.72, "mid_confidence": 55}}
    FullAutoTradingService._normalize_orchestrator_for_ui(info2)
    assert info == info2

    assert tier_confidence_pct(tier="long", orch={"long_confidence": 0.8}) == (
        FullAutoTradingService._tier_confidence_pct(tier="long", orch={"long_confidence": 0.8})
    )

    dec_a = {"confidence": 0}
    dec_b = {"confidence": 0}
    ms = {"ETHUSDT": {"orchestrator": {"mid_confidence": 42}}}
    pct_a = backfill_dec_confidence_from_orch(dec_a, sym="ETHUSDT", market_summary=ms, tier="mid")
    pct_b = FullAutoTradingService._backfill_dec_confidence_from_orch(
        dec_b, sym="ETHUSDT", market_summary=ms, tier="mid",
    )
    assert pct_a == pct_b == 42
    assert dec_a["confidence"] == dec_b["confidence"] == 42


def test_paper_risk_helpers_callable():
    assert get_account_risk_score(1) >= 0
    allow, detail = tiny_close_allowed_by_hardfact(
        1,
        {"tier": "mid", "entry_price": 100, "mark_price": 99, "margin": 10, "unrealized_pnl": -1},
        reasoning="test",
    )
    assert isinstance(allow, bool)
    assert isinstance(detail, str)


def test_execution_gates_shim_matches_extracted():
    ms = {"BTC": {"orchestrator": {"action": "frozen", "reasoning": "risk"}}}
    svc = FullAutoTradingService.get_instance()
    assert orchestrator_blocks_open("BTC", "buy", ms) == (
        svc._orchestrator_blocks_open("BTC", "buy", ms)
    )
    assert decision_price_consistency_ok("BTC", {}, None, "paper") == (
        FullAutoTradingService._decision_price_consistency_ok("BTC", {}, None, "paper")
    )


def test_market_summary_bootstrap_from_cache():
    ctx = MarketSummaryContext(
        market_scan_cache={"BTC": {"current_price": 50000.0, "data_source": "cache"}},
    )
    out = bootstrap_market_summary(["BTC"], ctx)
    assert out["BTC"]["current_price"] == 50000.0


def test_sanitize_and_deferred_key_shims():
    raw = {"BTC": {"current_price": 1.0, "df": object()}}
    assert FullAutoTradingService._sanitize_market_summary_for_qaa(raw) == (
        sanitize_market_summary_for_qaa(raw)
    )
    assert FullAutoTradingService._deferred_signal_key(1, "btc", "buy", "mid") == dsk(1, "btc", "buy", "mid")
    assert active_exchange() == FullAutoTradingService._active_exchange()


def test_qaa_health_stats_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("QAA_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("QAA_KNOWLEDGE_BACKEND", "jsonl")
    monkeypatch.setenv("QAA_KNOWLEDGE_DIR", str(tmp_path / "qaa_knowledge"))

    import backend.services.qaa_trade_memory_bridge as bridge

    bridge._RAG = None
    stats = bridge.get_qaa_trade_memory_stats()
    assert stats.get("embedding_backend") == "hash"
    assert stats.get("active_embedding_backend") == "hash"
    assert "active_store_class" in stats
    assert stats.get("neural_active") is False
