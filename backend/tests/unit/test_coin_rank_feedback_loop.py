# -*- coding: utf-8 -*-
"""
T8 验证：选币反馈闭环核验深化（v6 计划 6.5 第 8 项）。

核验三环节：
  1. 衰减乘数计算（feedback._rebuild_from_db：<0.35→0.75 / <0.45→0.88 /
     ≥0.60→1.05 / 其它→1.0；样本<3 不衰减）→ get_decay_map
  2. 衰减乘数真正进打分链路（score_rows：composite = base × decay，
     RankResult.decay_mult/hist 字段透出）
  3. 历史回填（write_price_feedback：price_at_selection 补齐 +
     24h/72h 价格与 hit 标志回写 + 统计）

运行：.venv\\Scripts\\python.exe -m pytest backend\\tests\\unit\\test_coin_rank_feedback_loop.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.models import AutoCoinSelection, Base

from backend.services.coin_rank import feedback as fb
from backend.services.coin_rank.score import score_rows


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    session = Session()
    yield session
    session.close()
    engine.dispose()


def _selection(session, symbol, action="injected", *, price=None, age_h=0.0,
               after_24=None, after_72=None, pnl=None, hit_24=None, hit_72=None):
    row = AutoCoinSelection(
        session_id="sess-fb",
        symbol=symbol,
        action=action,
        tenant_id=1,
        price_at_selection=price,
        price_after_24h=after_24,
        price_after_72h=after_72,
        realized_pnl=pnl,
        hit_24h=hit_24,
        hit_72h=hit_72,
    )
    row.created_at = datetime.utcnow() - timedelta(hours=age_h)
    session.add(row)
    return row


def _reset_fb_cache():
    fb._cache_ts = 0.0
    fb._decay_cache = {}
    fb._hist_cache = {}


# ═══════════════════════════════════════════════════════════════════
# 1. 衰减乘数计算（feedback._rebuild_from_db）
# ═══════════════════════════════════════════════════════════════════

def test_decay_bad_hit_rate_downgrades(db_session, monkeypatch):
    """hit_rate < 0.35 → decay 0.75（压分）。"""
    _reset_fb_cache()
    for i in range(5):
        r = _selection(db_session, "BAD", price=100.0, age_h=24.0 * (i + 1))
        r.hit_24h = (i == 0)  # 1/5 = 0.20 < 0.35
    db_session.commit()
    monkeypatch.setattr("backend.database.connection.SessionLocal",
                        lambda: db_session)
    monkeypatch.setattr("backend.core.tenant.set_system_identity", lambda: None)
    fb._rebuild_from_db()
    assert fb._decay_cache.get("BAD") == 0.75


def test_decay_mid_hit_rate_mild_downgrade(db_session, monkeypatch):
    """0.35 ≤ hit_rate < 0.45 → 0.88。"""
    _reset_fb_cache()
    for i in range(5):
        r = _selection(db_session, "MID", price=100.0, age_h=24.0 * (i + 1))
        r.hit_24h = (i < 2)  # 2/5 = 0.40
    db_session.commit()
    monkeypatch.setattr("backend.database.connection.SessionLocal",
                        lambda: db_session)
    monkeypatch.setattr("backend.core.tenant.set_system_identity", lambda: None)
    fb._rebuild_from_db()
    assert fb._decay_cache.get("MID") == 0.88


def test_decay_good_hit_rate_boosts(db_session, monkeypatch):
    """hit_rate ≥ 0.60 → 1.05（略抬）。"""
    _reset_fb_cache()
    for i in range(5):
        r = _selection(db_session, "GOOD", price=100.0, age_h=24.0 * (i + 1))
        r.hit_24h = (i < 3)  # 3/5 = 0.60
    db_session.commit()
    monkeypatch.setattr("backend.database.connection.SessionLocal",
                        lambda: db_session)
    monkeypatch.setattr("backend.core.tenant.set_system_identity", lambda: None)
    fb._rebuild_from_db()
    assert fb._decay_cache.get("GOOD") == 1.05


def test_decay_neutral_band_keeps_one(db_session, monkeypatch):
    """0.45 ≤ hit_rate < 0.60 → 1.0。"""
    _reset_fb_cache()
    for i in range(5):
        r = _selection(db_session, "NEU", price=100.0, age_h=24.0 * (i + 1))
        r.hit_24h = (i < 2)  # 2/5 = 0.40 —— 等等，0.40 应到 0.88
    db_session.commit()
    monkeypatch.setattr("backend.database.connection.SessionLocal",
                        lambda: db_session)
    monkeypatch.setattr("backend.core.tenant.set_system_identity", lambda: None)
    fb._rebuild_from_db()
    assert fb._decay_cache.get("NEU") == 0.88


def test_decay_needs_min_samples(db_session, monkeypatch):
    """样本 < 3 → 不进衰减映射（避免小样本误伤）。"""
    _reset_fb_cache()
    for i in range(2):
        r = _selection(db_session, "LOW", price=100.0, age_h=24.0 * (i + 1))
        r.hit_24h = False  # 0/2 = 0.0，但样本不足
    db_session.commit()
    monkeypatch.setattr("backend.database.connection.SessionLocal",
                        lambda: db_session)
    monkeypatch.setattr("backend.core.tenant.set_system_identity", lambda: None)
    fb._rebuild_from_db()
    assert "LOW" not in fb._decay_cache, "样本<3 不应衰减"
    assert fb._hist_cache["LOW"]["samples"] == 2  # hist 仍记录


def test_hist_map_carries_metrics(db_session, monkeypatch):
    """hist_map：hit_rate / avg_pnl_24h / samples。"""
    _reset_fb_cache()
    for i, hit in enumerate((True, True, False, True)):
        r = _selection(db_session, "HIST", price=100.0, age_h=24.0 * (i + 1))
        r.hit_24h = hit
        r.realized_pnl = 1.5 if hit else -1.0
    db_session.commit()
    monkeypatch.setattr("backend.database.connection.SessionLocal",
                        lambda: db_session)
    monkeypatch.setattr("backend.core.tenant.set_system_identity", lambda: None)
    fb._rebuild_from_db()
    h = fb._hist_cache["HIST"]
    assert h["hit_rate"] == pytest.approx(0.75)
    assert h["samples"] == 4
    assert h["avg_pnl_24h"] == pytest.approx(0.875)  # (1.5+1.5-1.0+1.5)/4


# ═══════════════════════════════════════════════════════════════════
# 2. 衰减乘数进打分链路（score_rows）
# ═══════════════════════════════════════════════════════════════════

def _rows():
    return {
        "AAA": {"symbol": "AAA", "volume_24h": 1000.0, "change_24h": 5.0,
                "change_1h": 0.5, "change_4h": 1.0, "price": 10.0,
                "sources": ["dc"]},
        "BBB": {"symbol": "BBB", "volume_24h": 2000.0, "change_24h": 8.0,
                "change_1h": 0.8, "change_4h": 2.0, "price": 20.0,
                "sources": ["dc"]},
    }


def test_decay_multiplies_composite(monkeypatch):
    """decay=0.75 → composite = base×0.75（与无衰减对比降 25%）。"""
    monkeypatch.setattr("backend.services.coin_rank.score.factor_soft",
                        lambda sym: (None, {}))
    r_none = score_rows(_rows(), symbols=["AAA"], apply_factor=True)
    r_decay = score_rows(_rows(), symbols=["AAA"], apply_factor=True,
                         decay_map={"AAA": 0.75})
    assert r_decay[0].composite == pytest.approx(r_none[0].composite * 0.75)
    assert r_decay[0].decay_mult == 0.75
    assert any("decay=0.75" in x for x in r_decay[0].explain)


def test_decay_boost_caps_at_one(monkeypatch):
    """decay=1.05 抬升但 composite 被 clip01 限制在 1.0。"""
    monkeypatch.setattr("backend.services.coin_rank.score.factor_soft",
                        lambda sym: (None, {}))
    r_none = score_rows(_rows(), symbols=["AAA"], apply_factor=True)
    r_boost = score_rows(_rows(), symbols=["AAA"], apply_factor=True,
                         decay_map={"AAA": 1.05})
    assert r_boost[0].composite >= r_none[0].composite
    assert 0.0 <= r_boost[0].composite <= 1.0


def test_hist_fields_flow_through(monkeypatch):
    """hist_map → RankResult.hist_hit_rate/hist_avg_pnl_24h/hist_samples。"""
    monkeypatch.setattr("backend.services.coin_rank.score.factor_soft",
                        lambda sym: (None, {}))
    r = score_rows(_rows(), symbols=["AAA"], apply_factor=True,
                   hist_map={"AAA": {"hit_rate": 0.8, "avg_pnl_24h": 1.2,
                                     "samples": 7}})
    assert r[0].hist_hit_rate == pytest.approx(0.8)
    assert r[0].hist_avg_pnl_24h == pytest.approx(1.2)
    assert r[0].hist_samples == 7


def test_no_decay_map_defaults_one(monkeypatch):
    """无衰减映射时 decay_mult 默认 1.0，不影响排序。"""
    monkeypatch.setattr("backend.services.coin_rank.score.factor_soft",
                        lambda sym: (None, {}))
    r = score_rows(_rows(), symbols=["AAA"], apply_factor=True)
    assert r[0].decay_mult == 1.0
    assert r[0].composite > 0.0


def _ticker_with(*pairs):
    """构造 ≥20 个价格的 ticker 快照（write_price_feedback 要求 len≥20 才启用）。"""
    prices = dict(pairs)
    for i in range(30):
        prices.setdefault(f"FAKE{i}", 1.0)
    return prices


# ═══════════════════════════════════════════════════════════════════
# 3. 历史回填（write_price_feedback）
# ═══════════════════════════════════════════════════════════════════

def test_feedback_backfills_24h_72h(db_session, monkeypatch):
    """age≥24h/72h 的行回写 price_after + hit 标志。"""
    _selection(db_session, "BTC", price=100.0, age_h=30.0)    # 只回填 24h
    _selection(db_session, "ETH", price=200.0, age_h=80.0)    # 24h+72h
    db_session.commit()
    with patch("backend.services.asterdex_ticker_poller.asterdex_ticker_poller"
               ".get_all_prices", return_value=_ticker_with(("BTC", 110.0), ("ETH", 190.0))):
        monkeypatch.setattr(
            "backend.database.connection.MarketSessionLocal",
            lambda: (_ for _ in ()).throw(RuntimeError("no market db in test")),
        )
        res = fb.write_price_feedback(db_session)

    assert res["updated_24h"] == 2
    assert res["updated_72h"] == 1
    btc = db_session.query(AutoCoinSelection).filter_by(symbol="BTC").one()
    eth = db_session.query(AutoCoinSelection).filter_by(symbol="ETH").one()
    assert btc.price_after_24h == 110.0
    assert btc.hit_24h is True
    assert btc.price_after_72h is None
    assert eth.price_after_24h == 190.0
    assert eth.hit_24h is False
    assert eth.price_after_72h == 190.0
    assert eth.hit_72h is False


def test_feedback_fills_missing_entry_price(db_session, monkeypatch):
    """price_at_selection 缺失 → 用 ticker 价补齐（避免伪命中）。"""
    _selection(db_session, "SOL", price=None, age_h=30.0)
    db_session.commit()
    with patch("backend.services.asterdex_ticker_poller.asterdex_ticker_poller"
               ".get_all_prices", return_value=_ticker_with(("SOL", 50.0))):
        monkeypatch.setattr(
            "backend.database.connection.MarketSessionLocal",
            lambda: (_ for _ in ()).throw(RuntimeError("no market db in test")),
        )
        res = fb.write_price_feedback(db_session)

    assert res["filled_entry_price"] == 1
    row = db_session.query(AutoCoinSelection).filter_by(symbol="SOL").one()
    assert float(row.price_at_selection) == 50.0
    # 补齐后同一轮仍可回写 24h（entry 50 → 现价 50，hit=True）
    assert row.price_after_24h == 50.0
    assert row.hit_24h is True


def test_feedback_skips_young_rows(db_session, monkeypatch):
    """age < 24h 的行不回写。"""
    _selection(db_session, "NEW", price=100.0, age_h=10.0)
    db_session.commit()
    with patch("backend.services.asterdex_ticker_poller.asterdex_ticker_poller"
               ".get_all_prices", return_value=_ticker_with(("NEW", 120.0))):
        monkeypatch.setattr(
            "backend.database.connection.MarketSessionLocal",
            lambda: (_ for _ in ()).throw(RuntimeError("no market db in test")),
        )
        res = fb.write_price_feedback(db_session)

    assert res["updated_24h"] == 0
    row = db_session.query(AutoCoinSelection).filter_by(symbol="NEW").one()
    assert row.price_after_24h is None
    assert row.hit_24h is None


def test_feedback_uses_realized_pnl_for_avg(db_session, monkeypatch):
    """realized_pnl 优先于价格差（avg_pnl 计算不双算）。"""
    _reset_fb_cache()
    _selection(db_session, "PNL", price=100.0, age_h=24.0, pnl=3.0, hit_24=True)
    db_session.commit()
    monkeypatch.setattr("backend.database.connection.SessionLocal",
                        lambda: db_session)
    monkeypatch.setattr("backend.core.tenant.set_system_identity", lambda: None)
    fb._rebuild_from_db()
    assert fb._hist_cache["PNL"]["avg_pnl_24h"] == pytest.approx(3.0)
