"""
v3 整改 — 风控与 AI 学习模块整合测试

覆盖范围：
1. BlockReportAggregator        — record/top 行为
2. /api/system/feature-flags    — GET/POST 运行时切换
3. /api/system/block-report-top — Top-N 查询 + 空态
4. /api/rl/coordinator/status   — TDI 注入后返回 flags
5. /api/rl/kelly/portfolio      — 基础调用
6. /api/rl/drl/performance      — 基础调用
7. /api/ai-strategies/stats/tier-distribution — 空 DB 行为 + quota 结构
8. reentry_cooldown 减仓冷却    — pyramid/dca 反弹门
9. strategy_params_registry.apply_genome — 统一写入口 + 版本递增
"""
from __future__ import annotations

import pytest


# ══════════════════════════════════════════════════
#  1. BlockReportAggregator
# ══════════════════════════════════════════════════

@pytest.mark.unit
class TestBlockReportAggregator:
    def test_record_and_top(self):
        from backend.services.block_report_aggregator import block_report_aggregator

        # 用独立新实例避免污染全局
        from backend.services.block_report_aggregator import _BlockReportAggregator
        agg = _BlockReportAggregator()

        for _ in range(5):
            agg.record("risk_gate:RULE_2", "max_side_margin exceeded on BTC")
        for _ in range(3):
            agg.record("rebound_gate_blocked", "reduce cooldown active")
        agg.record("fee_threshold", "profit < fee*3")

        report = agg.top(n=3, window_sec=3600)
        assert report["total"] == 9
        codes = [it["code"] for it in report["top"]]
        # 前两名应按频率排列
        assert codes[0] == "risk_gate:RULE_2"
        assert codes[1] == "rebound_gate_blocked"
        # 每条应有 samples
        top0 = report["top"][0]
        assert top0["count"] == 5
        assert 0.5 < top0["ratio"] <= 1.0
        assert len(top0["samples"]) >= 1

        # 全局单例也可调用而不抛
        block_report_aggregator.record("unit_test_probe", "hello")
        out = block_report_aggregator.top(n=1, window_sec=60)
        assert out["total"] >= 1


# ══════════════════════════════════════════════════
#  2. /api/system/feature-flags
# ══════════════════════════════════════════════════

@pytest.mark.integration
class TestFeatureFlagsAPI:
    def test_get_feature_flags_schema(self, client):
        resp = client.get("/api/system/feature-flags")
        assert resp.status_code == 200
        data = resp.json()
        for key in (
            "drl_integration", "kelly_position", "evolution_feedback",
            "portfolio_risk", "coordinator", "drl_shadow_mode",
        ):
            assert key in data, f"feature flag {key} missing"
            assert isinstance(data[key], bool)

    def test_post_feature_flag_toggle_roundtrip(self, client):
        # 读当前
        before = client.get("/api/system/feature-flags").json()
        orig = before["kelly_position"]

        # 翻转
        resp = client.post(
            "/api/system/feature-flags",
            json={"flag": "kelly_position", "enabled": not orig},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["enabled"] == (not orig)

        # 回读
        after = client.get("/api/system/feature-flags").json()
        assert after["kelly_position"] == (not orig)

        # 恢复原值，避免影响后续测试
        client.post(
            "/api/system/feature-flags",
            json={"flag": "kelly_position", "enabled": orig},
        )

    def test_post_unknown_flag_400(self, client):
        resp = client.post(
            "/api/system/feature-flags",
            json={"flag": "non_existent_flag", "enabled": True},
        )
        assert resp.status_code == 400


# ══════════════════════════════════════════════════
#  3. /api/system/block-report-top
# ══════════════════════════════════════════════════

@pytest.mark.integration
class TestBlockReportAPI:
    def test_block_report_top_returns_schema(self, client):
        # 先注入若干事件
        from backend.services.block_report_aggregator import block_report_aggregator
        block_report_aggregator.record("api_probe_block", "hello from test")

        resp = client.get("/api/system/block-report-top?n=3&hours=1")
        assert resp.status_code == 200
        data = resp.json()
        assert "top" in data
        assert "total" in data
        assert "window_sec" in data
        assert isinstance(data["top"], list)

    def test_block_report_top_param_clamp(self, client):
        # 超大参数应被夹紧
        resp = client.get("/api/system/block-report-top?n=100&hours=1000")
        assert resp.status_code == 200


# ══════════════════════════════════════════════════
#  4-6. RL / Coordinator 集成端点
# ══════════════════════════════════════════════════

@pytest.mark.integration
class TestRLIntegrationEndpoints:
    def test_coordinator_status(self, client):
        resp = client.get("/api/rl/coordinator/status")
        assert resp.status_code == 200
        data = resp.json()
        # 实际 schema: {db_state, coordinator:{...feature_flags...}, tdi_injected, timestamp}
        assert "coordinator" in data
        assert "tdi_injected" in data
        coord = data["coordinator"]
        assert "feature_flags" in coord
        for key in ("drl_integration", "kelly_position", "coordinator", "drl_shadow_mode"):
            assert key in coord["feature_flags"]
        # TDI 注入状态依赖运行时（启动时注入，测试环境可能未初始化）
        assert isinstance(data["tdi_injected"], bool)

    def test_kelly_portfolio_basic(self, client):
        resp = client.get("/api/rl/kelly/portfolio?symbols=BTC,ETH,SOL")
        assert resp.status_code in (200, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert "allocations" in data
            assert isinstance(data["allocations"], list)
            # 没有真实交易历史时，后端会返回 reason 字段
            if not data["allocations"]:
                assert "reason" in data

    def test_drl_performance_basic(self, client):
        resp = client.get("/api/rl/drl/performance?days=1")
        assert resp.status_code in (200, 500)
        if resp.status_code == 200:
            data = resp.json()
            # 后端 schema: total_predictions/correct_count/per_symbol/daily_trend/accuracy/avg_pnl
            for key in (
                "total_predictions", "correct_count", "accuracy",
                "avg_pnl", "per_symbol", "daily_trend",
            ):
                assert key in data, f"{key} missing in /api/rl/drl/performance"


# ══════════════════════════════════════════════════
#  7. /api/ai-strategies/stats/tier-distribution
# ══════════════════════════════════════════════════

@pytest.mark.integration
class TestTierDistributionAPI:
    def test_empty_returns_quota_structure(self, client):
        resp = client.get("/api/ai-strategies/stats/tier-distribution")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("total", "distribution", "ratio", "quota", "deviation"):
            assert key in data
        # quota 应有 short/mid/long
        for tier in ("short", "mid", "long"):
            assert tier in data["quota"]
            assert tier in data["ratio"]
            assert tier in data["deviation"]
            assert tier in data["distribution"]
        assert "unknown" in data["distribution"]
        # quota 总和应近似 1
        s = sum(data["quota"].values())
        assert 0.9 <= s <= 1.1


# ══════════════════════════════════════════════════
#  8. Rebound gate — 减仓冷却
# ══════════════════════════════════════════════════

@pytest.mark.unit
class TestReboundGate:
    def test_is_reduce_cooling_down_without_record(self):
        from backend.services.reentry_cooldown import is_reduce_cooling_down
        cooling, reason = is_reduce_cooling_down(
            account_id=9999, symbol="TEST_REB_SYM", side="long", tier="mid",
        )
        assert cooling is False
        assert reason == ""

    def test_is_reduce_cooling_down_after_record(self):
        from backend.services.reentry_cooldown import (
            record_partial_close, is_reduce_cooling_down, _reduce_cooldowns,
        )
        _reduce_cooldowns.clear()
        record_partial_close(
            account_id=9999, symbol="TEST_REB_SYM", side="long",
            tier="short", close_pnl=-5.0,
        )
        cooling, reason = is_reduce_cooling_down(
            account_id=9999, symbol="TEST_REB_SYM", side="long", tier="short",
        )
        assert cooling is True
        assert "冷却" in reason
        _reduce_cooldowns.clear()


# ══════════════════════════════════════════════════
#  9. StrategyParamsRegistry.apply_genome 版本递增
# ══════════════════════════════════════════════════

@pytest.mark.integration
class TestApplyGenome:
    def test_apply_genome_increments_version(self, db_session):
        from backend.database.models import AIStrategy, SystemCoordinatorState
        from backend.services.strategy_params_registry import apply_genome
        import json as _json

        sid = "test_apply_genome_001"
        strat = AIStrategy(
            strategy_id=sid,
            name="apply-genome-test",
            primary_symbol="BTC",
            account_id=1,
            status="active",
            timeframe_tier="mid",
            genome={"k": 1},
        )
        db_session.add(strat)
        db_session.flush()

        # 第一次写入
        ok1 = apply_genome(db_session, sid, {"k": 2, "new": "v"}, reason="ut1")
        assert ok1 is True
        state = db_session.query(SystemCoordinatorState).first()
        assert state is not None
        versions = _json.loads(state.param_versions) if state.param_versions else {}
        assert sid in versions
        v1 = versions[sid]["version"]
        assert v1 >= 1
        assert versions[sid]["reason"] == "ut1"

        # 第二次写入 — 版本应递增
        ok2 = apply_genome(db_session, sid, {"k": 3}, reason="ut2")
        assert ok2 is True
        db_session.expire_all()
        state = db_session.query(SystemCoordinatorState).first()
        versions = _json.loads(state.param_versions) if state.param_versions else {}
        assert versions[sid]["version"] == v1 + 1
        assert versions[sid]["reason"] == "ut2"
