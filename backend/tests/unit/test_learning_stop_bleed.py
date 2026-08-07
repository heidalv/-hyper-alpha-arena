# -*- coding: utf-8 -*-
"""
T9 验证：学习止血（v6 计划 8.3 阶段 1 第 1-3 项）。

三项验收：
  1. 静默→告警：learning_loop_service 全量 logger.debug 改 logger.warning
     （heartbeat 失败 / 单笔回填失败 / 路由降级 / 落库失败 / 广播失败 …），
     并新增 last_tick_map() 供健康看板判定每条闭环超时标红。
  2. job 接线核验：trigger_job 5 个 job（outcome_batch / paper_outcome_backfill /
     kelly_portfolio / coordinator / heartbeat）消费端数据流存在性静态核验。
  3. 禁用假进化：HERMES_L3_AUTO_ACCEPT_PAPER 默认 false（settings + .env），
     reconcile_implemented_paper / auto_accept_pending_paper 门控后自动跳过。

运行：.venv\\Scripts\\python.exe -m pytest backend\\tests\\unit\\test_learning_stop_bleed.py -q
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.services.learning_loop_service import (
    JOB_COORDINATOR,
    JOB_HEARTBEAT,
    JOB_KELLY_PORTFOLIO,
    JOB_OUTCOME_BATCH,
    JOB_PAPER_OUTCOME_BACKFILL,
    learning_loop,
)

# ═══════════════════════════════════════════════════════════════════
# 1. 静默→告警：全量 debug 消除 + heartbeat 记录
# ═══════════════════════════════════════════════════════════════════

_SERVICE_PATH = Path(__file__).resolve().parents[2] / "services" / "learning_loop_service.py"


def test_no_logger_debug_left_in_service():
    """T9 核心验收：learning_loop_service 不再有任何 logger.debug（全量→warning）。"""
    src = _SERVICE_PATH.read_text(encoding="utf-8")
    assert "logger.debug" not in src, "仍存在 logger.debug，静默→告警改造不完整"


def test_heartbeat_failure_logs_warning(monkeypatch, caplog):
    """心跳失败 → WARNING 日志（原为 debug 静默）。"""
    def _boom():
        raise RuntimeError("ws down")

    monkeypatch.setattr(learning_loop, "_broadcast_coord_status", _boom)
    with caplog.at_level(logging.WARNING, logger="backend.services.learning_loop_service"):
        learning_loop._tick_heartbeat()
    assert any("heartbeat" in r.message for r in caplog.records), caplog.text


def test_heartbeat_records_tick(monkeypatch):
    """心跳成功后记录 last_tick（健康看板数据源）。"""
    monkeypatch.setattr(learning_loop, "_broadcast_coord_status", lambda: None)
    monkeypatch.setattr(learning_loop, "_last_tick_at", {
        k: None for k in (JOB_OUTCOME_BATCH, JOB_PAPER_OUTCOME_BACKFILL,
                          JOB_KELLY_PORTFOLIO, JOB_COORDINATOR, JOB_HEARTBEAT)
    })
    learning_loop._tick_heartbeat()
    assert learning_loop.last_tick_map().get(JOB_HEARTBEAT) is not None


def test_last_tick_map_thread_safe_snapshot():
    """last_tick_map 返回 ISO 字符串快照，未运行 job 为 None。"""
    m = learning_loop.last_tick_map()
    assert set(m) == {JOB_OUTCOME_BATCH, JOB_PAPER_OUTCOME_BACKFILL,
                      JOB_KELLY_PORTFOLIO, JOB_COORDINATOR, JOB_HEARTBEAT}
    assert all(v is None or isinstance(v, str) for v in m.values())


def test_record_tick_updates_last_activity():
    """_record_tick 后 last_tick_map 对应 job 有活动时间。"""
    before = learning_loop.last_tick_map().get(JOB_OUTCOME_BATCH)
    learning_loop._record_tick(JOB_OUTCOME_BATCH, time.time(), True, {"n": 1})
    after = learning_loop.last_tick_map().get(JOB_OUTCOME_BATCH)
    assert after is not None
    assert after != before or before is not None


# ═══════════════════════════════════════════════════════════════════
# 2. trigger_job 5 job 消费端接线核验（静态数据流）
# ═══════════════════════════════════════════════════════════════════

_BACKEND = Path(__file__).resolve().parents[2]


def _contains(rel_path: str, pattern: str) -> bool:
    return pattern in (_BACKEND / rel_path).read_text(encoding="utf-8")


def test_trigger_job_unknown_returns_error():
    res = learning_loop.trigger_job("no_such_job")
    assert res["ok"] is False
    assert "unknown job" in res["error"]


def test_trigger_job_routes_all_four(monkeypatch):
    """4 个手动 job 全部路由到对应 tick 并返回耗时。"""
    called: list[str] = []

    def _mk(name: str):
        def _fn():
            called.append(name)
        return _fn

    monkeypatch.setattr(learning_loop, "_tick_outcome_batch", _mk("outcome_batch"))
    monkeypatch.setattr(learning_loop, "_tick_paper_outcome_backfill", _mk("paper_outcome_backfill"))
    monkeypatch.setattr(learning_loop, "_tick_kelly_portfolio", _mk("kelly_portfolio"))
    monkeypatch.setattr(learning_loop, "_tick_coordinator", _mk("coordinator"))
    for job in ("outcome_batch", "paper_outcome_backfill", "kelly_portfolio", "coordinator"):
        res = learning_loop.trigger_job(job)
        assert res["ok"] is True
        assert res["elapsed_ms"] >= 0
    assert called == ["outcome_batch", "paper_outcome_backfill", "kelly_portfolio", "coordinator"]


def test_trigger_job_heartbeat_not_manual():
    """heartbeat 是 30s 自动 tick，不进手动触发 mapping（显式文档）。"""
    res = learning_loop.trigger_job("heartbeat")
    assert res["ok"] is False


def test_job_consumer_endpoints_exist():
    """8.3 第 2 项：5 job 消费端数据流全部存在（断链显式暴露）。"""
    # outcome_batch / paper_outcome_backfill → UnifiedLearning.process_outcome
    assert _contains("services/unified_learning_service.py", "def process_outcome")
    # kelly_portfolio → SystemCoordinator.update_kelly_from_outcomes → multi_symbol_kelly 表
    assert _contains("services/rl/system_coordinator.py", "def update_kelly_from_outcomes")
    # coordinator → SystemCoordinator.check_and_coordinate → coordinator_actions 表
    assert _contains("services/rl/system_coordinator.py", "def check_and_coordinate")
    # heartbeat → WS coordinator_status 广播
    assert _contains("services/ws_broadcast.py", "def broadcast_coordinator_status")
    # 消费端表（health 已查询，双保险）
    assert _contains("database/models.py", "class MultiSymbolKelly")
    assert _contains("database/models.py", "class CoordinatorAction")


# ═══════════════════════════════════════════════════════════════════
# 3. 健康看板接线：learning_health 含 5 条闭环项 + 超时标红
# ═══════════════════════════════════════════════════════════════════

def _no_db(*_a, **_k):
    raise RuntimeError("no db in test")


def _build_health(monkeypatch, loop_map):
    """隔离 DB 后构建 health（旧项全部 dead，重点验证 loop 项）。"""
    monkeypatch.setattr("backend.database.connection.AnalyticsSessionLocal", _no_db)
    monkeypatch.setattr("backend.database.connection.SessionLocal", _no_db)
    monkeypatch.setattr(learning_loop, "last_tick_map", lambda: loop_map)
    from backend.services.learning_health_service import build_learning_health
    return build_learning_health()


def _loop_items(health) -> dict:
    return {it["name"]: it for it in health["items"] if it["name"].startswith("loop_")}


def test_health_has_five_loop_items(monkeypatch):
    """看板含 5 条闭环项（outcome/paper/kelly/coordinator/heartbeat）。"""
    now = datetime.now(timezone.utc)
    loop_map = {
        JOB_OUTCOME_BATCH: now.isoformat(),
        JOB_PAPER_OUTCOME_BACKFILL: now.isoformat(),
        JOB_KELLY_PORTFOLIO: now.isoformat(),
        JOB_COORDINATOR: now.isoformat(),
        JOB_HEARTBEAT: now.isoformat(),
    }
    items = _loop_items(_build_health(monkeypatch, loop_map))
    assert set(items) == {"loop_outcome_batch", "loop_paper_backfill",
                          "loop_kelly", "loop_coordinator", "loop_heartbeat"}


def test_health_loop_fresh_ok(monkeypatch):
    """全部闭环刚活动 → ok。"""
    now = datetime.now(timezone.utc)
    loop_map = {k: now.isoformat() for k in
                (JOB_OUTCOME_BATCH, JOB_PAPER_OUTCOME_BACKFILL, JOB_KELLY_PORTFOLIO,
                 JOB_COORDINATOR, JOB_HEARTBEAT)}
    items = _loop_items(_build_health(monkeypatch, loop_map))
    assert all(v["status"] == "ok" for v in items.values())


def test_health_loop_timeout_marks_dead(monkeypatch):
    """coordinator 9h 未活动（阈值 4h，dead>2×阈值）→ dead；heartbeat 20min → dead。"""
    now = datetime.now(timezone.utc)
    loop_map = {
        JOB_OUTCOME_BATCH: now.isoformat(),
        JOB_PAPER_OUTCOME_BACKFILL: now.isoformat(),
        JOB_KELLY_PORTFOLIO: now.isoformat(),
        JOB_COORDINATOR: (now - timedelta(hours=9)).isoformat(),   # > 2×4h → dead
        JOB_HEARTBEAT: (now - timedelta(minutes=20)).isoformat(),  # > 2×0.1h → dead
    }
    items = _loop_items(_build_health(monkeypatch, loop_map))
    assert items["loop_coordinator"]["status"] == "dead"
    assert items["loop_heartbeat"]["status"] == "dead"
    assert items["loop_outcome_batch"]["status"] == "ok"


def test_health_loop_never_ran_dead(monkeypatch):
    """闭环从未运行（last_tick 全 None）→ 全部 dead（瘫痪可视化）。"""
    loop_map = {k: None for k in
                (JOB_OUTCOME_BATCH, JOB_PAPER_OUTCOME_BACKFILL, JOB_KELLY_PORTFOLIO,
                 JOB_COORDINATOR, JOB_HEARTBEAT)}
    items = _loop_items(_build_health(monkeypatch, loop_map))
    assert all(v["status"] == "dead" for v in items.values())


def test_health_snapshot_wiring(monkeypatch):
    """health_snapshot 的 _fetch_learning_health 实际调用 build_learning_health。"""
    import backend.services.health_snapshot_service as hss
    fake = {"items": [{"name": "loop_outcome_batch", "status": "ok"}], "overall": "ok"}
    monkeypatch.setattr(
        "backend.services.learning_health_service.build_learning_health",
        lambda: fake,
    )
    assert hss._fetch_learning_health() == fake


# ═══════════════════════════════════════════════════════════════════
# 4. 禁用假进化：HERMES_L3_AUTO_ACCEPT_PAPER=false
# ═══════════════════════════════════════════════════════════════════

def test_hermes_l3_auto_accept_disabled_in_settings_and_env():
    """settings 属性与 .env 均显式 false（双保险，防误开启）。"""
    from backend.config import settings
    assert settings.HERMES_L3_AUTO_ACCEPT_PAPER is False
    env_file = _BACKEND.parent / ".env"
    assert env_file.exists(), "需要 .env 用于显式禁用"
    content = env_file.read_text(encoding="utf-8")
    assert "HERMES_L3_AUTO_ACCEPT_PAPER=false" in content


def test_reconcile_skips_when_disabled(monkeypatch):
    """禁用时 reconcile_implemented_paper 直接返回 skipped，不碰数据库。"""
    from backend.config import settings
    from backend.services import hermes_architecture_evolution_engine as eng
    monkeypatch.setattr(settings, "HERMES_L3_AUTO_ACCEPT_PAPER", False)
    monkeypatch.setattr(eng, "hermes_fetchall", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("不应查询数据库")))
    res = eng.ArchitectureEvolutionEngine().reconcile_implemented_paper(limit=10)
    assert res.get("skipped") == "not paper"
    assert res.get("implemented") == 0


def test_paper_auto_accept_gate_two_factors(monkeypatch):
    """门控=HERMES_L3_AUTO_ACCEPT_PAPER × paper profile，任一关即跳过。"""
    from backend.services import hermes_architecture_evolution_engine as eng
    from backend.config import settings

    class _FakeLock:
        def __init__(self, disable: bool):
            self.disable = disable

        def get_profile(self, _name):
            return type("_P", (), {"disable_loss_locks": self.disable})()

    def _lock(disable: bool):
        monkeypatch.setattr(
            "backend.services.lock_strength_service.get_lock_strength_service",
            lambda: _FakeLock(disable),
        )

    monkeypatch.setattr(settings, "HERMES_L3_AUTO_ACCEPT_PAPER", False)
    _lock(True)
    assert eng.ArchitectureEvolutionEngine._paper_auto_accept_enabled() is False

    monkeypatch.setattr(settings, "HERMES_L3_AUTO_ACCEPT_PAPER", True)
    _lock(False)
    assert eng.ArchitectureEvolutionEngine._paper_auto_accept_enabled() is False

    _lock(True)
    assert eng.ArchitectureEvolutionEngine._paper_auto_accept_enabled() is True
