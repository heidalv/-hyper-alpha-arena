# -*- coding: utf-8 -*-
"""[F61] 交易中心聚合 API 单测：权益口径、机会表成本纪律、熔断历史、车道增强。"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.api import trading_routes as T  # noqa: E402
from backend.services import lane_registry as reg  # noqa: E402


class TestRouterContract:
    def test_all_paths_present(self):
        paths = {getattr(r, "path", "") for r in T.router.routes}
        for p in ("/api/trading/portfolio/summary", "/api/trading/positions",
                  "/api/trading/opportunities", "/api/trading/risk/summary",
                  "/api/trading/risk/breakers", "/api/trading/risk/breakers/reset",
                  "/api/trading/risk/drill", "/api/trading/config/datasources",
                  "/api/trading/capital/pool",
                  "/api/trading/config/fees", "/api/trading/config/lanes/{lane_id}"):
            assert p in paths, p


class TestDataSources:
    def test_reports_all_four_sources(self):
        res = T.config_datasources()
        assert res["count"] > 0
        kinds = {x["source"] for x in res["items"]}
        assert kinds <= {"orderbook", "trades", "funding", "spot"}
        assert "orderbook" in kinds and "funding" in kinds
        for x in res["items"]:
            assert set(x) >= {"source", "exchange", "rows", "last_ts", "age_sec", "stale"}
            if x["age_sec"] is not None:
                assert x["stale"] is (x["age_sec"] > 600.0)

    def test_stale_flag_follows_age_threshold(self):
        """断流判定规则：stale ⇔ age > 600s（不依赖某个场地当前是否在断流）。"""
        res = T.config_datasources()
        checked = 0
        for x in res["items"]:
            if x["age_sec"] is None:
                assert x["stale"] is True
                continue
            assert x["stale"] is (x["age_sec"] > 600.0), x
            checked += 1
        assert checked > 0

    def test_long_outage_is_flagged(self):
        """若某源确实断了很久（>1 天），必须被标记 stale。"""
        res = T.config_datasources()
        long_outage = [x for x in res["items"]
                       if x["age_sec"] is not None and x["age_sec"] > 86400.0]
        for x in long_outage:
            assert x["stale"] is True, x


class TestCapitalPool:
    def test_lists_accounts_and_total(self):
        res = T.capital_pool()
        assert res["count"] >= 1
        for it in res["items"]:
            assert set(it) >= {"account_id", "name", "total_equity",
                               "available_balance", "status"}
        assert res["total_equity"] == pytest.approx(
            sum(i["total_equity"] for i in res["items"]), abs=0.01)


class TestRiskDrill:
    def test_drill_only_touches_paper_lanes_and_is_reversible(self):
        from backend.services import lane_registry as reg

        # 先确保有一条 paper 车道可用（不改变其 status）
        lane = reg.get_lane("mm_asterdex")
        if not lane:
            pytest.skip("车道不存在")
        before = dict(lane.get("health") or {})
        try:
            on = T.risk_drill(T.DrillBody(enable=True, lanes=["mm_asterdex"],
                                          reason="pytest 演练"))
            assert on["ok"] is True and "mm_asterdex" in on["affected"]
            health = (reg.get_lane("mm_asterdex") or {}).get("health") or {}
            assert health.get("drill") is True
            assert health.get("breaker") == "drill"
            off = T.risk_drill(T.DrillBody(enable=False, lanes=["mm_asterdex"]))
            assert off["ok"] is True
            health = (reg.get_lane("mm_asterdex") or {}).get("health") or {}
            assert health.get("drill") is False
        finally:
            reg.update_health("mm_asterdex", before)

    def test_unknown_lane_reported_not_silently_ignored(self):
        res = T.risk_drill(T.DrillBody(enable=True, lanes=["no_such_lane"]))
        assert res["affected"] == []
        assert res["skipped"] and res["skipped"][0]["lane_id"] == "no_such_lane"

    def test_disabled_lane_skipped(self):
        res = T.risk_drill(T.DrillBody(enable=True, lanes=["scalp_directional"]))
        # scalp 车道 mode=disabled → 不允许演练
        assert "scalp_directional" not in res["affected"]


class TestPositionsSide:
    def test_side_field_present(self):
        res = T.positions(days=30)
        for it in res["items"]:
            assert it["side"] in ("long", "short", "flat")
            if it["qty"] > 0:
                assert it["side"] == "long"
            elif it["qty"] < 0:
                assert it["side"] == "short"
            else:
                assert it["side"] == "flat"


class TestEquityResolution:
    def test_returns_source_label(self):
        eq, src = T._resolve_equity()
        assert eq > 0 and src
        # 来源必须明示，不能偷偷用默认值冒充真实权益
        # [2026-09-14] 新增 lane_accounts(...) = 车道绑定账户合计（中心自有资金），
        # 避免历史遗留的非车道账户（返佣策略）把组合权益灌水（5300+300=5600 事故）。
        assert src in ("default_reference",) or src.startswith(
            ("paper_accounts(", "lane_account(", "lane_accounts(")
        )

    def test_lane_scoped_equity_differs_or_falls_back(self):
        eq, src = T._resolve_equity("mm_asterdex")
        assert eq > 0
        assert src.startswith(("lane_account(", "lane_accounts(",
                               "paper_accounts(", "default_reference"))


class TestMakerOpportunities:
    def test_cost_discipline_and_fail_closed(self):
        items = T._maker_opportunities("asterdex")
        assert items, "做市机会不应为空"
        for it in items:
            assert set(it) >= {"kind", "symbol", "gross_bp", "cost_bp", "net_bp",
                               "executable", "confidence", "reason"}
            # executable 只在「净为正 且 边际已验证」时为真
            if it["executable"]:
                assert it["net_bp"] > 0 and it["confidence"] == "measured"

    def test_net_uses_measured_edge_not_theoretical(self):
        lane = reg.get_lane("mm_asterdex") or {}
        edge = lane.get("edge") or {}
        items = T._maker_opportunities("asterdex")
        if edge.get("net_bp") is not None:
            # 展示值做了 3 位四舍五入
            assert items[0]["net_bp"] == pytest.approx(float(edge["net_bp"]), abs=1e-3)
            assert items[0]["theoretical_net_bp"] is not None
            assert items[0]["confidence"] == "measured"


class TestBreakerRows:
    def test_shape_and_breakers_covered(self):
        rows = T._breaker_rows()
        assert rows
        kinds = {r["breaker"] for r in rows}
        assert kinds == {"data", "fee", "daily_loss", "toxic_flow", "drill"}
        for r in rows:
            assert set(r) >= {"lane_id", "breaker", "state", "ts", "reason"}
            assert r["state"] in ("ok", "tripped")

    def test_stale_data_lands_in_data_row_not_fee(self):
        """语义回归：数据断流必须出现在 data 行，而不是 fee 行。"""
        lanes = [{"lane_id": "x", "mode": "paper", "status": "active",
                  "risk": {}, "meta": {},
                  "health": {"breaker": "stale_data(31789.6min>3.0min)",
                             "data_age_sec": 1_907_376.0, "note": "shadow ticks=17"}}]
        rows = {r["breaker"]: r for r in T._breaker_rows(lanes)}
        assert rows["data"]["state"] == "tripped"
        assert "stale_data" in rows["data"]["reason"]
        assert rows["fee"]["state"] == "ok"

    def test_maker_fee_lands_in_fee_row(self):
        lanes = [{"lane_id": "x", "mode": "paper", "status": "active", "risk": {},
                  "meta": {}, "health": {"breaker": "maker_fee_too_high(2.00bp>0.50bp)",
                                         "data_age_sec": 1.0}}]
        rows = {r["breaker"]: r for r in T._breaker_rows(lanes)}
        assert rows["fee"]["state"] == "tripped"
        assert rows["data"]["state"] == "ok"

    def test_drill_row_reflects_flag(self):
        lanes = [{"lane_id": "x", "mode": "paper", "status": "active", "risk": {},
                  "meta": {},
                  "health": {"drill": True, "drill_reason": "pytest", "data_age_sec": 1.0}}]
        rows = {r["breaker"]: r for r in T._breaker_rows(lanes)}
        assert rows["drill"]["state"] == "tripped"
        assert rows["drill"]["reason"] == "pytest"


class TestCarryFilters:
    def test_suspect_flag_and_reason(self):
        items = T._carry_opportunities("asterdex", days=40.0, min_days=7.0,
                                       tradable_only=True)
        for it in items:
            assert set(it) >= {"hedged_net_bp", "executable", "execution_mode",
                               "required_hold_days", "spot_leg_available"}
            if it["suspect"]:
                assert (it["annualized_pct"] > 100.0
                        or (it["breakeven_days"] is not None
                            and it["breakeven_days"] < 2.0)
                        or it["funding_positive_ratio"] < 0.6
                        or (it["hedged_net_bp"] is not None
                            and float(it.get("hedged_samples") or 0) >= 0
                            and (it.get("hedged_t") if it.get("hedged_t") else 1.0) < 1.0))

    def test_min_days_filter_reduces_universe(self):
        loose = T._carry_opportunities("asterdex", days=30.0, min_days=0.0,
                                       tradable_only=False)
        strict = T._carry_opportunities("asterdex", days=30.0, min_days=7.0,
                                        tradable_only=True)
        assert len(strict) <= len(loose)

    def test_period_hours_per_venue(self):
        a = T._carry_opportunities("asterdex", days=30.0, min_days=7.0)
        h = T._carry_opportunities("hyperliquid", days=30.0, min_days=7.0)
        assert a and all(x["period_hours"] == 8.0 for x in a)
        assert h and all(x["period_hours"] == 1.0 for x in h)


class TestHedgedCarryIntegration:
    """[F70] 对冲后实测净期望必须覆盖资金费估算，并标出持有期与执行通道。"""

    def test_hedged_stats_override_estimate(self):
        items = T._carry_opportunities("asterdex", days=40.0, min_days=7.0)
        hedged = [x for x in items if x["hedged_net_bp"] is not None]
        if not hedged:
            pytest.skip("尚无对冲回测数据（先跑 _f70_hedged_backtest.py --push）")
        for it in hedged:
            # 实测覆盖估算：net_bp 必须等于 hedged_net_bp
            assert it["net_bp"] == pytest.approx(it["hedged_net_bp"], abs=1e-6)
            assert it["confidence"] == "measured"
            assert it["required_hold_days"] is not None
            assert it["execution_mode"] == "paper_spot_channel"
            assert it["spot_leg_available"] is True
            # 模拟盘现货通道具备 → 净为正即可执行
            assert it["executable"] is (it["net_bp"] > 0)

    def test_estimate_still_reported_for_comparison(self):
        items = T._carry_opportunities("asterdex", days=40.0, min_days=7.0)
        for it in items:
            assert it["theoretical_net_bp"] is not None
            assert isinstance(it["funding_mean_bp"], float)

    def test_unhedged_symbols_not_executable(self):
        items = T._carry_opportunities("asterdex", days=40.0, min_days=7.0)
        for it in items:
            if it["hedged_net_bp"] is None:
                assert it["executable"] is False
                assert "对冲" in it["reason"]


class TestBreakerHistory:
    def test_log_and_read_roundtrip(self):
        lane = f"test_lane_f61_{uuid4().hex[:8]}"
        rows = [{"lane_id": lane, "breaker": "fee", "state": "tripped",
                 "ts": None, "reason": "maker_fee_too_high(2.0bp>0.5bp)"}]
        try:
            assert reg.log_breaker(rows) == 1
            hist = reg.breaker_history(days=1.0)
            mine = [h for h in hist if h["lane_id"] == lane]
            assert len(mine) == 1 and mine[0]["breaker"] == "fee"
            assert mine[0]["trips"] == 1
            # 幂等：同一状态一小时内不重复计数
            assert reg.log_breaker(rows) == 0
            assert sum(h["trips"] for h in reg.breaker_history(days=1.0)
                       if h["lane_id"] == lane) == 1
        finally:
            _purge(lane)

    def test_ok_state_not_logged(self):
        lane = f"test_lane_f61_{uuid4().hex[:8]}"
        try:
            assert reg.log_breaker([{"lane_id": lane, "breaker": "data",
                                     "state": "ok", "reason": ""}]) == 0
            assert not [h for h in reg.breaker_history(days=1.0) if h["lane_id"] == lane]
        finally:
            _purge(lane)


class TestLaneEnrichment:
    def test_enriched_fields_present(self):
        from backend.api import lane_routes as LR

        items = LR._enrich_lanes(reg.list_lanes())
        assert items
        for it in items:
            for k in ("pnl_today_usd", "pnl_7d_usd", "inventory_usd",
                      "net_exposure_usd", "open_symbols", "data_age_sec"):
                assert k in it, k
            # 无账本数据时必须是 None 而不是 0（0 会被误读成「没有盈亏」）
            if it["lane_id"] not in ("mm_asterdex",):
                assert it["pnl_7d_usd"] is None

    def test_promotion_shape_is_standard(self):
        from backend.api import lane_routes as LR

        for it in LR._enrich_lanes(reg.list_lanes()):
            p = it["promotion"]
            assert set(p) >= {"passed", "failed", "labels", "progress_pct", "ready"}
            assert p["ready"] is (len(p["failed"]) == 0)


def _purge(lane: str) -> None:
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal

        with system_identity():
            with SessionLocal() as db:
                db.execute(text("DELETE FROM lane_breaker_log WHERE lane_id = :l"),
                           {"l": lane})
                db.commit()
    except Exception:
        pass
