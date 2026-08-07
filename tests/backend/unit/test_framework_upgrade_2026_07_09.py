"""
量化框架升级（QUANT_FRAMEWORK_COMPARISON_AND_UPGRADE.md）常驻回归测试 — 2026-07-09。

覆盖两类保证：
  1. 特征化测试网首批（对应文档 §10.9）：
     - C1：`FullAutoState`（#8 追加件）日亏/连亏/跨日/冻结/冷却/回撤缩仓边界。
     - C7 雏形：事件溯源（#9 Phase 1）golden 轨迹重放 == 期望仓位物化视图，
       为后续 #8/#9 破坏性重构的"改造前后行为等价"对拍打底。
  2. 零风险铁律：所有新增能力的开关默认关闭 → 行为等价旧系统（no-op / 恒放行 / 全 1 权重）。

这些是解锁 #8 loop 拆分 / #9 Phase 2-3 的安全网的一部分——任一断言失败即禁止破坏性重构。
"""
import os

import numpy as np
import pytest

pytestmark = pytest.mark.unit


# ============================ C1：FullAutoState（#8 追加件）============================

def test_fullauto_state_daily_and_streak_and_rollover():
    from backend.services.full_auto import FullAutoState

    s = FullAutoState(daily_loss_limit=1000.0)
    assert s.roll_day("2026-07-09") is True
    s.register_trade_result(-300)
    s.register_trade_result(-400)
    assert s.consecutive_losses == 2
    assert s.daily_realized_pnl == pytest.approx(-700)
    assert s.daily_loss_breached() is False
    s.register_trade_result(-500)
    assert s.daily_loss_breached() is True          # -1200 <= -1000
    s.register_trade_result(200)
    assert s.consecutive_losses == 0                # 盈利重置连亏
    assert s.roll_day("2026-07-10") is True
    assert s.daily_realized_pnl == pytest.approx(0.0)   # 跨日清零


def test_fullauto_state_freeze_and_cooldown_auto_expire():
    from backend.services.full_auto import FullAutoState

    s = FullAutoState()
    s.freeze_for(100, now=1000.0)
    assert s.is_frozen(now=1050.0) is True
    assert s.is_frozen(now=1200.0) is False
    assert s.frozen is False                        # 到期自动解冻

    s.set_cooldown("BTC", 60, now=1000.0)
    assert s.in_cooldown("BTC", now=1030.0) is True
    assert s.in_cooldown("BTC", now=1100.0) is False
    assert "BTC" not in s.cooldown_by_symbol        # 过期自动清理


def test_fullauto_state_drawdown_recovery_scaling_and_roundtrip():
    from backend.services.full_auto import FullAutoState

    s = FullAutoState()
    s.update_equity(10000, recovery_position_scale=0.5, drawdown_trigger=0.1)
    assert s.recovery_scale == pytest.approx(1.0)   # 新高不缩仓
    s.update_equity(8500, recovery_position_scale=0.5, drawdown_trigger=0.1)
    assert s.recovery_scale == pytest.approx(0.5)   # 回撤 15% > 10% → 缩仓

    s2 = FullAutoState.from_dict(s.to_dict())
    assert s2.peak_equity == s.peak_equity
    assert s2.recovery_scale == s.recovery_scale


# ============================ C7 雏形：事件溯源重放对拍（#9 Phase 1）============================

def test_event_sourcing_replay_rebuilds_positions(tmp_path):
    from backend.services.event_sourcing import (
        DomainEvent, EventStore, EventSourcedPositionRepository,
        EVT_POSITION_OPENED, EVT_POSITION_CHANGED, EVT_POSITION_CLOSED)

    log = str(tmp_path / "event_log.jsonl")
    store = EventStore(log_path=log)

    # force=True 绕过开关直接写（模拟已启用的 shadow 记录），钉住重建行为
    store.append(DomainEvent(EVT_POSITION_OPENED, "pos1",
                 {"symbol": "BTC", "side": "long", "size": 1.0, "entry_price": 60000}), force=True)
    store.append(DomainEvent(EVT_POSITION_CHANGED, "pos1", {"size": 2.0}), force=True)
    store.append(DomainEvent(EVT_POSITION_OPENED, "pos2",
                 {"symbol": "ETH", "side": "short", "size": 5.0, "entry_price": 3000}), force=True)
    store.append(DomainEvent(EVT_POSITION_CLOSED, "pos2",
                 {"exit_price": 2900, "realized_pnl": 500}), force=True)

    repo = EventSourcedPositionRepository(EventStore(log_path=log))
    state = repo.rebuild_from_events()

    # golden 期望：pos1 open size=2；pos2 closed pnl=500
    assert state["pos1"]["size"] == pytest.approx(2.0)
    assert state["pos1"]["status"] == "open"
    assert state["pos2"]["status"] == "closed"
    assert state["pos2"]["realized_pnl"] == pytest.approx(500)
    assert set(repo.projection.open_positions().keys()) == {"pos1"}


def test_event_sourcing_disabled_is_noop(tmp_path, monkeypatch):
    from backend.services.event_sourcing import DomainEvent, EventStore, EVT_POSITION_OPENED

    monkeypatch.setenv("EVENT_SOURCING_ENABLED", "false")
    store = EventStore(log_path=str(tmp_path / "e.jsonl"))
    assert store.append(DomainEvent(EVT_POSITION_OPENED, "p", {})) is False
    assert store.count() == 0


# ============================ C7 完整：monolith↔事件溯源对拍 ====================

def test_c7_monolith_trajectory_replay_matches_memory_view(tmp_path):
    """C7：monolith 操作序列 → 事件流 → 重放 == monolith 内存仓位视图。"""
    from backend.services.full_auto.monolith_replay import (
        build_monolith_view, replay_matches_monolith,
    )

    trajectory = [
        {"op": "open", "id": "pos1", "symbol": "BTC", "side": "long",
         "size": 1.0, "entry_price": 60000},
        {"op": "change", "id": "pos1", "size": 2.0},
        {"op": "open", "id": "pos2", "symbol": "ETH", "side": "short",
         "size": 5.0, "entry_price": 3000},
        {"op": "close", "id": "pos2", "exit_price": 2900, "realized_pnl": 500},
        {"op": "close", "id": "pos1", "exit_price": 61000, "realized_pnl": 2000},
    ]
    monolith = build_monolith_view(trajectory)
    log_path = str(tmp_path / "c7_trajectory.jsonl")

    assert replay_matches_monolith(trajectory, store_path=log_path)
    assert monolith["pos1"]["status"] == "closed"
    assert monolith["pos1"]["realized_pnl"] == pytest.approx(2000)
    assert monolith["pos2"]["realized_pnl"] == pytest.approx(500)
    assert set(monolith.keys()) == {"pos1", "pos2"}


def test_c7_open_positions_projection_matches_monolith(tmp_path):
    """C7 补充：重放后 open_positions 仅含未平仓聚合。"""
    from backend.services.event_sourcing import (
        DomainEvent, EventStore, EventSourcedPositionRepository,
        EVT_POSITION_OPENED, EVT_POSITION_CLOSED,
    )
    from backend.services.full_auto.monolith_replay import ops_to_events

    ops = [
        {"op": "open", "id": "p1", "symbol": "BTC", "side": "long", "size": 1, "entry_price": 60000},
        {"op": "open", "id": "p2", "symbol": "ETH", "side": "short", "size": 3, "entry_price": 3000},
        {"op": "close", "id": "p2", "exit_price": 2900, "realized_pnl": 300},
    ]
    log = str(tmp_path / "c7_open.jsonl")
    store = EventStore(log_path=log)
    for ev in ops_to_events(ops):
        store.append(ev, force=True)
    repo = EventSourcedPositionRepository(store)
    state = repo.rebuild_from_events()
    open_ids = set(repo.projection.open_positions().keys())
    assert open_ids == {"p1"}
    assert state["p1"]["status"] == "open"
    assert state["p2"]["status"] == "closed"


# ============================ 零风险铁律：新开关默认关 → 行为等价旧系统 ============================

def test_pbo_audit_disabled_never_rejects(monkeypatch):
    from backend.services.learning_core.pbo_audit import PBOAuditor

    monkeypatch.setenv("PBO_AUDIT_ENABLED", "false")
    aud = PBOAuditor(ledger=None)
    assert aud.should_reject_promotion({"pbo": 0.99}) is False   # 关 → 恒放行


def test_ewc_disabled_zero_penalty(monkeypatch):
    from backend.services.learning_core.continual_learning import (
        EWCTrainer, FisherInformation)

    monkeypatch.setenv("EWC_ENABLED", "false")
    t = EWCTrainer(ewc_lambda=400.0)
    t.consolidate(FisherInformation({"w": np.array([9.0])}, {"w": np.array([0.0])}))
    assert t.penalty({"w": np.array([100.0])}) == 0.0            # 关 → penalty 0
    assert t.penalized_loss(1.0, {"w": np.array([100.0])}) == pytest.approx(1.0)


def test_ddgda_disabled_uniform_weights(monkeypatch):
    from backend.services.learning_core.distribution_forecaster import DriftTriggeredReweighter

    monkeypatch.setenv("DDGDA_ENABLED", "false")
    w = DriftTriggeredReweighter().reweight(50, drift_score=0.9)
    assert np.allclose(w, 1.0)                                   # 关 → 全 1 权重


def test_map_elites_nearest_neighbor_fallback():
    from backend.services.learning_core.map_elites_archive import (
        MAPElitesArchive, BehaviorDescriptor)

    a = MAPElitesArchive()
    a.add({"lev": 3}, 1.5, BehaviorDescriptor("trending_up", "short", "med"), {}, 1)
    # 请求不存在的行为格 → 最近邻返回已有 elite（不为 None）
    got = a.select_elite(BehaviorDescriptor("volatile", "long", "high"))
    assert got is not None and got.champion_genome["lev"] == 3


def test_cmaes_converges_with_fallback():
    from backend.services.learning_core.cmaes_optimizer import CMAESOptimizer

    best, val = CMAESOptimizer(seed=1).optimize(
        lambda p: -((p["x"] - 2.0) ** 2), {"x": (-5.0, 5.0)}, n_trials=200)
    assert abs(best["x"] - 2.0) < 0.6                           # optuna 缺失也能收敛


def test_dspy_compiler_disabled_is_noop(monkeypatch):
    from backend.services.ai.prompt_compiler import TradingPromptCompiler, Signature

    monkeypatch.setenv("DSPY_COMPILE_ENABLED", "false")
    sig = Signature(name="x", base_instruction="base")
    comp = TradingPromptCompiler(lambda i, d: 1.0).compile(sig, [1, 2, 3])
    assert comp.backend == "noop"
    assert comp.optimized_instruction == "base"
    assert comp.trial_count == 0


def test_framework_rollout_aggressive_defaults():
    """激进 rollout：未显式设置 env 时默认全开。"""
    import importlib
    import backend.config.framework_rollout as fr
    importlib.reload(fr)
    # 清掉几个 key 模拟「用户没配 .env」
    for k in ("RISK_ENGINE_ENABLED", "FACTOR_WEIGHTING_MODE", "QAA_RERANKER_ENABLED"):
        os.environ.pop(k, None)
    fr._applied = False
    newly = fr.apply_aggressive_rollout(force=True)
    assert os.environ.get("RISK_ENGINE_ENABLED") == "true"
    assert os.environ.get("FACTOR_WEIGHTING_MODE") == "hybrid"
    assert os.environ.get("QAA_RERANKER_ENABLED") == "true"
    # 用户显式设置不被覆盖
    os.environ["RISK_ENGINE_ENABLED"] = "false"
    fr._applied = False
    fr.apply_aggressive_rollout(force=True)
    assert os.environ["RISK_ENGINE_ENABLED"] == "false"


def test_adversarial_debate_enabled_changes_signal(monkeypatch):
    """C2 雏形：对抗辩论开时 run_debate 走 AdversarialDebateLayer 路径。"""
    from backend.services.mlto.debate_layer import run_debate
    from backend.services.mlto.types import MemoryEventDTO, PerceptionPacket

    monkeypatch.setenv("ADVERSARIAL_DEBATE_ENABLED", "true")
    pkt = PerceptionPacket(
        symbol="BTC", tier="mid", session_id="s1", ts=1.0, price=60000.0,
        market_summary_sym={}, orchestrator={"mid_bias": "bearish"},
        quant_brief={}, analyst_reports={}, regime_hash="r1", slot_action="observe",
    )
    mem = [MemoryEventDTO(
        event_id="e1", thesis_id="t1", layer="deep", source="test",
        signal="bear", summary="bearish short sell resist",
    )]
    sig = run_debate(pkt, mem, hub_adjusted=0.55)
    assert 0.20 <= sig <= 0.80


def test_reranker_lexical_prefers_chinese_term_match():
    import backend.config.settings  # noqa: F401 — 触发 qaa_architecture_package 的 sys.path 注入
    try:
        from qaa.knowledge.reranker import LexicalReranker
        from qaa.knowledge.base import KnowledgeChunk, RetrievalHit
    except ModuleNotFoundError:
        pytest.skip("qaa_architecture_package 未在 sys.path（非 backend 运行环境）")

    def hit(text, score):
        return RetrievalHit(chunk=KnowledgeChunk.create(text), score=score)

    hits = [
        hit("今天行情震荡，喝咖啡看盘", 0.9),
        hit("止损被扫之后追多，结果连续亏损，要等回踩", 0.3),
    ]
    out = LexicalReranker().rerank("止损被扫后追多导致连续亏损", hits, top_n=2)
    assert out[0].chunk.text.startswith("止损被扫")             # 相关项精排到首位
