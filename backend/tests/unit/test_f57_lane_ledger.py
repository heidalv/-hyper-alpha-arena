# -*- coding: utf-8 -*-
"""[F57] 六维归因账本单测：纯函数口径 + DB 往返 + 路由契约。"""
import sys
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services import lane_ledger as L  # noqa: E402


class TestNetBp:
    def test_sum_of_five(self):
        assert L.net_bp(5.0, 1.0, -2.0, 0.0, -0.5) == 3.5

    def test_empty(self):
        assert L.net_bp() == 0.0


class TestFillDimensions:
    def test_maker_buy_captures_half_spread(self):
        """挂宽 5bp 的买单成交：捕获 +5bp，maker 费 0。"""
        d = L.compute_fill_dimensions(side="buy", fill_px=99.95, mid_px=100.0, fee_rate=0.0)
        assert d["spread_bp"] == pytest.approx(5.0, abs=1e-3)
        assert d["fee_bp"] == 0.0
        assert L.net_bp(**d) == pytest.approx(5.0, abs=1e-3)

    def test_maker_sell_captures_half_spread(self):
        d = L.compute_fill_dimensions(side="sell", fill_px=100.05, mid_px=100.0, fee_rate=0.0)
        assert d["spread_bp"] == pytest.approx(5.0, abs=1e-3)

    def test_taker_pays_fee(self):
        """taker 4bp：即使价格等于中间价，净也是 −4bp。"""
        d = L.compute_fill_dimensions(side="buy", fill_px=100.0, mid_px=100.0, fee_rate=0.0004)
        assert d["spread_bp"] == 0.0
        assert d["fee_bp"] == pytest.approx(-4.0, abs=1e-3)
        assert L.net_bp(**d) == pytest.approx(-4.0, abs=1e-3)

    def test_adverse_slippage_counted_only(self):
        # 买单成交价高于委托价 → 不利 → 负
        d = L.compute_fill_dimensions(side="buy", fill_px=100.02, mid_px=100.0,
                                      order_px=100.0, fee_rate=0.0)
        assert d["slippage_bp"] == pytest.approx(-2.0, abs=1e-3)
        # 买单成交价低于委托价 → 有利 → 不计（0）
        d2 = L.compute_fill_dimensions(side="buy", fill_px=99.98, mid_px=100.0,
                                       order_px=100.0, fee_rate=0.0)
        assert d2["slippage_bp"] == 0.0

    def test_funding_and_price_passthrough(self):
        d = L.compute_fill_dimensions(side="buy", fill_px=100.0, mid_px=100.0,
                                      funding_bp=1.4, price_bp=-0.8)
        assert d["funding_bp"] == 1.4 and d["price_bp"] == -0.8

    def test_zero_mid_is_safe(self):
        d = L.compute_fill_dimensions(side="buy", fill_px=100.0, mid_px=0.0)
        assert all(v == 0.0 for v in d.values())


class TestRouterContract:
    def test_new_routes_present(self):
        from backend.api.lane_routes import router

        paths = {getattr(r, "path", "") for r in router.routes}
        for p in ("/api/trading/portfolio/attribution",
                  "/api/trading/portfolio/series",
                  "/api/trading/ledger/fill"):
            assert p in paths, p


class TestDbRoundTrip:
    """真实 DB 往返（需可用 Postgres；失败则跳过）。

    注意：测试写入的是**生产库**，因此必须用唯一 lane_id 并在结束后清理，
    否则历史行会污染 `attribution` 聚合（曾导致本测试第二次运行即失败）。
    """

    @staticmethod
    def _purge(lane: str) -> None:
        try:
            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    db.execute(text("DELETE FROM lane_ledger WHERE lane_id = :l"), {"l": lane})
                    db.commit()
        except Exception:
            pass

    def test_record_and_aggregate(self):
        # 每次用唯一 lane_id：账本按 (lane, 时间窗) 聚合，固定 id 会累积历史行导致不幂等
        lane = f"test_lane_f57_{uuid4().hex[:8]}"
        ok = L.record_fill(lane_id=lane, symbol="BTC", side="buy", qty=0.01,
                           fill_px=99.95, mid_px=100.0, fee_rate=0.0)
        if not ok:
            pytest.skip("DB 不可用")
        try:
            assert L.record_fill(lane_id=lane, symbol="BTC", side="sell", qty=0.01,
                                 fill_px=100.05, mid_px=100.0, fee_rate=0.0)
            agg = L.attribution(days=1.0, lane_id=lane)
            assert agg["total"]["n"] == 2
            assert agg["total"]["spread_bp"] == pytest.approx(5.0, abs=0.01)
            assert agg["total"]["net_bp"] == pytest.approx(5.0, abs=0.01)
            # 美元口径 = net_bp × notional / 1e4
            assert agg["total"]["net_usd"] > 0
            series = L.daily_series(lane, days=1.0)
            assert series and series[0]["n"] == 2
            # 持仓重建：一买一卖后应为平仓，且账本自带 side/qty/fill_px
            pos = L.open_positions(lane_id=lane, days=1.0, marks={"BTC": 100.0})
            assert len(pos) == 1 and abs(pos[0]["qty"]) < 1e-9
            assert pos[0]["fills"] == 2
            assert pos[0]["net_bp"] == pytest.approx(5.0, abs=0.01)
            # 逐标的归因（影子期报告与前端都依赖它）
            by_sym = agg.get("by_symbol") or []
            assert by_sym and by_sym[0]["symbol"] == "BTC"
            assert by_sym[0]["n"] == 2
            assert by_sym[0]["net_bp"] == pytest.approx(5.0, abs=0.01)
        finally:
            self._purge(lane)
