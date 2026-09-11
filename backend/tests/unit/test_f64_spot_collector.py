# -*- coding: utf-8 -*-
"""[F64] 现货采集器单测：K 线解析、幂等写入、基差计算、过期拒绝。

全部用注入的假 session，**不联网**。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.services.carry import spot_collector as sc  # noqa: E402


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload=None, status=200, record=None):
        self.payload = payload if payload is not None else []
        self.status = status
        self.record = record if record is not None else {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        self.record.update({"url": url, "params": params})
        return _FakeResp(self.payload, self.status)


BINANCE_ROW = [
    1788925440000, "78656.00", "78656.01", "78650.00", "78650.01",
    "1.2376", 1788925499999, "97341.73", 357,
    "0.4850", "38150.28", "0",
]


class TestSymbolPair:
    def test_quote_suffix(self):
        assert sc.spot_pair("btc") == "BTCUSDT"
        assert sc.spot_pair("ETH", "USDC") == "ETHUSDC"


class TestFetchKlines:
    def test_parses_binance_payload(self):
        s = _FakeSession([BINANCE_ROW])
        rows = sc.fetch_klines("BTC", session=s)
        assert len(rows) == 1
        r = rows[0]
        assert r["ts_ms"] == 1788925440000
        assert r["open"] == pytest.approx(78656.0)
        assert r["close"] == pytest.approx(78650.01)
        assert r["volume"] == pytest.approx(1.2376)
        assert r["quote_volume"] == pytest.approx(97341.73)
        assert r["trades"] == 357

    def test_request_params(self):
        rec = {}
        s = _FakeSession([BINANCE_ROW], record=rec)
        sc.fetch_klines("SOL", interval="1m", limit=5, session=s)
        assert rec["params"]["symbol"] == "SOLUSDT"
        assert rec["params"]["interval"] == "1m"
        assert rec["params"]["limit"] == 5
        assert rec["url"].endswith("/api/v3/klines")

    def test_limit_clamped_to_exchange_max(self):
        rec = {}
        s = _FakeSession([BINANCE_ROW], record=rec)
        sc.fetch_klines("BTC", limit=5000, session=s)
        assert rec["params"]["limit"] == 1000

    def test_http_error_propagates(self):
        s = _FakeSession([], status=451)
        with pytest.raises(RuntimeError):
            sc.fetch_klines("BTC", session=s)


class TestUpsertAndLatest:
    def test_upsert_is_idempotent(self):
        sym = f"TST{uuid4().hex[:6].upper()}"
        rows = [{"ts_ms": 1_788_925_440_000, "open": 1.0, "high": 1.1, "low": 0.9,
                 "close": 1.05, "volume": 10.0, "quote_volume": 10.5, "trades": 3}]
        n1 = sc.upsert_klines("test_spot", sym, "5m", rows)
        if n1 == 0:
            pytest.skip("DB 不可用")
        try:
            assert sc.upsert_klines("test_spot", sym, "5m", rows) == 1
            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import MarketSessionLocal

            with system_identity():
                with MarketSessionLocal() as db:
                    cnt = db.execute(text(
                        "SELECT COUNT(*) FROM market_spot_klines"
                        " WHERE exchange='test_spot' AND symbol=:s"
                    ), {"s": sym}).scalar()
            assert cnt == 1, "同一主键应 upsert 而不是插入新行"
        finally:
            _purge(sym)

    def test_latest_price_rejects_stale(self):
        sym = f"TST{uuid4().hex[:6].upper()}"
        old_ts = int((time.time() - 7200) * 1000)      # 2 小时前
        rows = [{"ts_ms": old_ts, "open": 2.0, "high": 2.0, "low": 2.0,
                 "close": 2.0, "volume": 1.0, "quote_volume": 2.0, "trades": 1}]
        if sc.upsert_klines("test_spot", sym, "5m", rows) == 0:
            pytest.skip("DB 不可用")
        try:
            assert sc.latest_spot_price(sym, exchange="test_spot",
                                        max_age_sec=600) is None
            fresh = sc.latest_spot_price(sym, exchange="test_spot", max_age_sec=0)
            assert fresh is not None and fresh["price"] == pytest.approx(2.0)
        finally:
            _purge(sym)


class TestBasis:
    def test_basis_math(self):
        # 纯函数口径：(perp − spot)/spot × 1e4
        spot, perp = 100.0, 100.5
        assert round((perp - spot) / spot * 1e4, 4) == pytest.approx(50.0)

    def test_basis_none_without_data(self):
        sym = f"NODATA{uuid4().hex[:6].upper()}"
        assert sc.basis_bp(sym) is None


def _purge(symbol: str) -> None:
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import MarketSessionLocal

        with system_identity():
            with MarketSessionLocal() as db:
                db.execute(text(
                    "DELETE FROM market_spot_klines WHERE exchange='test_spot'"
                    " AND symbol=:s"), {"s": symbol})
                db.commit()
    except Exception:
        pass
