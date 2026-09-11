"""组合级风险预算单测（v6 计划 阶段1 第4项）。"""
import numpy as np
import pytest

from backend.services.risk_management.portfolio_budget import (
    PortfolioBudget,
    _is_strategy_pos,
    _pos_notional,
)

LONG = {"symbol": "BTC", "side": "long", "size": 1.0,
        "mark_price": 50000.0, "trade_nature": "trend_follow", "timeframe_tier": "long"}
SHORT = {"symbol": "ETH", "side": "short", "size": 10.0,
         "mark_price": 3000.0, "trade_nature": "scalp", "timeframe_tier": "short"}


@pytest.fixture
def budget(monkeypatch):
    # [2026-09-02] 显式打开冻结开关 + 停掉修复流水线。
    #
    # 本文件多数用例测的就是「冻结机制」本身（写冻结戳 / 冻结后拒单 / 冷却时长），
    # 但测试进程会加载仓库 .env，那里的生产实况是 PB_FREEZE_ENABLED=false
    # （08-16 用户指令：「亏一笔就冻结」整体删除，改走累计口径的修复流水线）。
    # 不显式设置的话，这些用例实际测的是「关掉冻结后什么都不发生」，与用例名
    # 声称的正相反 —— 它们此前一直被前面的死锁挡着没真正跑过，所以没人发现。
    #
    # _spawn_repair 会起后台线程跑因子挖掘/回测（连 DB、吃 CPU），单测一律 stub：
    # 修复流水线本身有独立用例覆盖，不该在每个预算用例里被真的拉起来。
    monkeypatch.setenv("PB_FREEZE_ENABLED", "true")
    # [2026-09-11 用户指令] 本文件测的是「闸本身」，须显式声明非模拟账户前提：
    # 生产里 PB_PAPER_SKIP=true + mode=paper 会整段跳过（纸面账户不接组合预算闸），
    # 不 pin 的话所有拦截用例都会因"跳过"而恒放行。
    monkeypatch.setenv("PB_PAPER_SKIP", "false")
    b = PortfolioBudget()
    b._spawn_repair = lambda *a, **k: None

    # 冻结的实际落点是**模块级单例**：evaluate_open → _freeze_via_coordinator →
    # freeze_coordinator.freeze，而后者按设计红线（冻结只能一处发出）固定调用
    # `portfolio_budget` 单例的 _freeze。生产里 self 就是那个单例、行为自洽；
    # 单测用的是新实例，不把单例换成它的话，冻结戳会写到单例上，用例断言自己
    # 实例的 _key_frozen_until 必然落空。
    # 同时清空协调器的全局台账：残留的未过期条目会让 freeze() 走幂等分支直接
    # 返回，后一个用例就冻结不上（用例间互相污染）。
    import backend.services.risk_management.portfolio_budget as _pb_mod
    from backend.services.risk_management import freeze_coordinator as _fc

    monkeypatch.setattr(_pb_mod, "portfolio_budget", b)
    _fc._FREEZES.clear()

    # 默认全放行数据源（单测聚焦逻辑，K线/DB 由 patch 注入）
    b._daily_returns = lambda sym: np.array([0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.01, -0.02, 0.01, 0.02,
                                             0.01, -0.01, 0.02, -0.02, 0.01, 0.03, -0.02, 0.01, -0.01, 0.02,
                                             0.01, 0.02, -0.03, 0.01, -0.01, 0.02, 0.01, -0.02, 0.01, 0.01] * 3)
    b._strategy_drawdown_metric = lambda *a, **kw: None
    return b


def _dd(ratio: float, *, age_hours: float = 1.0, n: int = 168) -> dict:
    """[P17] 回撤判据 metric 的形状（样本默认"新鲜"，避免触发 stale 自愈规则）。"""
    return {
        "ratio": round(float(ratio), 4), "sigma": 9.87, "drawdown": 187.47,
        "peak": 41.19, "last_value": -146.28, "n_samples": n,
        "last_sample_ts": "2026-09-10 10:08:29", "age_hours": age_hours,
    }

def test_pos_notional():
    assert _pos_notional(LONG) == pytest.approx(50000.0)
    assert _pos_notional({"symbol": "X", "side": "long", "margin": 100, "leverage": 10}) == pytest.approx(1000.0)
    assert _pos_notional({}) == 0.0


def test_is_strategy_pos():
    assert _is_strategy_pos(LONG, "midlong")
    assert not _is_strategy_pos(SHORT, "midlong")
    assert _is_strategy_pos(SHORT, "scalp")
    assert not _is_strategy_pos(LONG, "scalp")


def test_concentration_block(budget, monkeypatch):
    """单币集中度超限 → 拒绝。"""
    monkeypatch.setenv("PB_MAX_SYMBOL_EXPOSURE_PCT", "0.30")
    monkeypatch.setenv("PB_MIDLONG_MAX_SYMBOL_EXPOSURE_PCT", "0.30")
    positions = [LONG]  # BTC 名义 50000
    d = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=10000.0, equity=100000.0,
        strategy="midlong", mode="paper", positions=positions,
    )
    # 60000/100000 = 60% > 30% → 拒绝
    assert not d.allowed
    assert any("concentration" in r for r in d.reasons)


def test_midlong_concentration_raised_without_freeze(budget, monkeypatch):
    """中长线与短线同币并存时名义常 >80%；应拒过大单但不冻结。"""
    monkeypatch.setenv("PB_MIDLONG_MAX_SYMBOL_EXPOSURE_PCT", "2.0")
    budget._spawn_repair = lambda *a, **k: None
    # 名义约 108% 权益 → 低于 2.0 放行
    d_ok = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=50.0, equity=440.0,
        strategy="midlong", mode="paper",
        positions=[{"symbol": "BTC", "side": "long", "size": 0.007, "mark_price": 64000.0}],
        account_id=14,
    )
    assert d_ok.allowed
    # 名义 250% → 拒绝但不冻结
    d_block = budget.evaluate_open(
        symbol="SOL", action="buy", notional_usd=1100.0, equity=440.0,
        strategy="midlong", mode="paper", positions=[], account_id=14,
    )
    assert not d_block.allowed
    assert any("concentration" in r for r in d_block.reasons)
    assert not budget._key_frozen_until


def test_midlong_drawdown_sigma_raised(budget, monkeypatch):
    """中长线纸盘回撤常用 >3σ；PB_MIDLONG_DRAWDOWN_SIGMA 放宽后不误杀。"""
    monkeypatch.setenv("PB_MIDLONG_DRAWDOWN_SIGMA", "10")
    budget._strategy_drawdown_metric = lambda *a, **kw: _dd(7.54)
    d = budget.evaluate_open(
        symbol="ETH", action="buy", notional_usd=50.0, equity=440.0,
        strategy="midlong", mode="paper", positions=[], account_id=14,
    )
    assert d.allowed


def test_concentration_pass(budget):
    positions = [LONG]
    d = budget.evaluate_open(
        symbol="ETH", action="sell", notional_usd=5000.0, equity=100000.0,
        strategy="midlong", mode="paper", positions=positions,
    )
    assert d.allowed


def test_scalp_concentration_allows_raised_size_without_freeze(budget, monkeypatch):
    """短线抬仓后名义可达权益 60%+；应拒过大单但不冻结账户。"""
    monkeypatch.setenv("PB_SCALP_MAX_SYMBOL_EXPOSURE_PCT", "1.5")
    budget._spawn_repair = lambda *a, **k: None
    # 名义 50% 权益 → 低于 1.5 放行
    d_ok = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=220.0, equity=442.0,
        strategy="scalp", mode="paper", positions=[], account_id=14,
    )
    assert d_ok.allowed
    # 名义 200% 权益 → 拒绝但不写入冻结表
    d_block = budget.evaluate_open(
        symbol="ETH", action="buy", notional_usd=900.0, equity=442.0,
        strategy="scalp", mode="paper", positions=[], account_id=14,
    )
    assert not d_block.allowed
    assert any("concentration" in r for r in d_block.reasons)
    assert not budget._key_frozen_until


def test_daily_var_block(budget):
    """高波动序列（单日 -8%）→ 组合 95% VaR 超 5% 权益预算 → 拒绝。"""
    budget._daily_returns = lambda sym: np.array([-0.08, 0.05, -0.07, 0.06, -0.09] * 20)
    positions = [{"symbol": "BTC", "side": "long", "size": 1.0, "mark_price": 100.0}]
    d = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=100.0, equity=1000.0,
        strategy="scalp", mode="paper", positions=positions,
    )
    assert not d.allowed
    assert any("daily_var" in r for r in d.reasons)


def test_daily_var_pass_low_vol(budget):
    """低波动 → VaR 预算内放行。"""
    budget._daily_returns = lambda sym: np.array([0.001, -0.002] * 50)
    positions = [{"symbol": "BTC", "side": "long", "size": 1.0, "mark_price": 100.0}]
    d = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=100.0, equity=1000.0,
        strategy="scalp", mode="paper", positions=positions,
    )
    assert d.allowed


def test_daily_var_fail_open_insufficient_data(budget):
    """收益数据不足 → VaR 项 fail-open，其余规则仍生效。"""
    budget._daily_returns = lambda sym: np.array([0.01] * 10)  # <30 根
    positions = [LONG]
    d = budget.evaluate_open(
        symbol="ETH", action="sell", notional_usd=5000.0, equity=100000.0,
        strategy="midlong", mode="paper", positions=positions,
    )
    assert d.allowed


def test_drawdown_sigma_circuit(budget):
    """策略回撤 3σ 熔断：连亏序列回撤远超 3σ → 只冻结该 (账户,策略,交易对) key（止血不杀死）。"""
    budget._strategy_drawdown_metric = lambda *a, **kw: _dd(5.2)
    d = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=1000.0, equity=100000.0,
        strategy="scalp", mode="paper", positions=[],
    )
    assert not d.allowed
    assert any("drawdown" in r for r in d.reasons)
    # 触发后该 (账户,策略,交易对) 组合冻结（key 级，不冻整策略）
    assert budget._key_frozen_until.get((0, "scalp", "BTC"), 0) > 0


def test_freeze_signal_blocks_and_unfreeze(budget):
    """冻结信号：触发后同组合新单全拒（其他 symbol 不受影响）；手动解冻后恢复。"""
    budget._strategy_drawdown_metric = lambda *a, **kw: _dd(6.0)
    d1 = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=1000.0, equity=100000.0,
        strategy="scalp", mode="paper", positions=[],
    )
    assert not d1.allowed
    # 冻结期内同组合再开：即使数据全部正常也拒（冻结优先）
    budget._strategy_drawdown_metric = lambda *a, **kw: None
    budget._daily_returns = lambda sym: np.array([0.001] * 60)
    d2 = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=1000.0, equity=100000.0,
        strategy="scalp", mode="paper", positions=[],
    )
    assert not d2.allowed
    assert any("frozen" in r for r in d2.reasons)
    # 同策略其他 symbol 不受冻结影响（最小粒度）
    d2b = budget.evaluate_open(
        symbol="ETH", action="buy", notional_usd=1000.0, equity=100000.0,
        strategy="scalp", mode="paper", positions=[],
    )
    assert d2b.allowed
    # 手动解冻该组合 → 放行
    budget.manual_unfreeze(account_id=0, strategy="scalp", symbol="BTC")
    d3 = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=1000.0, equity=100000.0,
        strategy="scalp", mode="paper", positions=[],
    )
    assert d3.allowed


def test_freeze_strategy_isolation(budget):
    """熔断按策略隔离：scalp 熔断不影响 midlong。"""
    budget._strategy_drawdown_metric = lambda *a, **kw: _dd(8.0)
    d1 = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=1000.0, equity=100000.0,
        strategy="scalp", mode="paper", positions=[],
    )
    assert not d1.allowed
    budget._strategy_drawdown_metric = lambda *a, **kw: None
    d2 = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=1000.0, equity=100000.0,
        strategy="midlong", mode="paper", positions=[],
    )
    assert d2.allowed


def test_exception_paper_fail_open(budget):
    """异常时 paper fail-open。"""
    def boom(*a, **kw):
        raise RuntimeError("data source down")
    budget._daily_returns = boom
    d = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=1000.0, equity=100000.0,
        strategy="scalp", mode="paper", positions=[],
    )
    assert d.allowed
    assert any("fail_open" in r for r in d.reasons)


def test_exception_live_fail_closed(budget, monkeypatch):
    """异常时 live fail-closed（默认）。"""
    monkeypatch.setenv("PB_FAIL_CLOSED_LIVE", "true")

    def boom(*a, **kw):
        raise RuntimeError("data source down")
    budget._daily_returns = boom
    d = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=1000.0, equity=100000.0,
        strategy="scalp", mode="live", positions=[],
    )
    assert not d.allowed
    assert any("fail_closed" in r for r in d.reasons)


def test_disabled(monkeypatch, budget):
    """PB_ENABLED=false 全放行。"""
    monkeypatch.setenv("PB_ENABLED", "false")
    d = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=999999.0, equity=1000.0,
        strategy="scalp", mode="live", positions=[LONG],
    )
    assert d.allowed


def test_scalp_freeze_cooldown_shorter(budget, monkeypatch):
    """P2-1: 短线冻结默认 ≤900s（长于最低 180）。"""
    monkeypatch.setenv("PB_SCALP_FREEZE_COOLDOWN_SEC", "900")
    budget._spawn_repair = lambda *a, **k: None  # 不启真实进化
    budget._strategy_drawdown_metric = lambda *a, **kw: _dd(5.0)
    import time
    t0 = time.time()
    budget.evaluate_open(
        symbol="SOL", action="buy", notional_usd=1000.0, equity=100000.0,
        strategy="scalp", mode="paper", positions=[],
    )
    until = budget._key_frozen_until.get((0, "scalp", "SOL"), 0)
    assert until > t0
    assert until - t0 <= 900.0 + 2.0


def test_repair_fail_cooldown_skips_respawn(budget):
    """P2-1: 修复失败冷却期内不再占并发闸。"""
    import time
    key = (1, "scalp", "SOL")
    budget._repair_fail_until[key] = time.time() + 600
    called = []
    budget._repair_locks = {}
    budget._repair_running = 0

    # 直接测 _spawn_repair 早退
    budget._spawn_repair(1, "scalp", "SOL", "test", "key")
    assert budget._repair_running == 0


# ── [2026-09-11 用户指令] 模拟账户不接组合预算闸 ──
# 原话：「模拟账户交易还配置全局冻结？」——纸面亏损=训练数据，冻结只是停摆样本。
# 契约：mode=paper + PB_PAPER_SKIP=true → 直接放行，且**不**写任何冻结台账；
#       mode=live 或 PB_PAPER_SKIP=false → 行为与原来完全一致。

def test_paper_mode_skips_budget_gate(budget, monkeypatch):
    monkeypatch.setenv("PB_PAPER_SKIP", "true")
    budget._strategy_drawdown_metric = lambda *a, **kw: _dd(99.0)   # 极端回撤
    d = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=999999.0, equity=1000.0,
        strategy="scalp", mode="paper", positions=[LONG],
    )
    assert d.allowed, d.reasons
    assert d.metrics.get("paper_skip") is True
    assert budget._key_frozen_until == {}, "模拟账户不得写冻结台账"
    assert budget._strategy_frozen_until == {}
    assert budget._account_frozen_until == {}


def test_paper_skip_off_restores_gate(budget, monkeypatch):
    """PB_PAPER_SKIP=false 时 paper 仍走闸（回滚档）。"""
    monkeypatch.setenv("PB_PAPER_SKIP", "false")
    budget._strategy_drawdown_metric = lambda *a, **kw: _dd(99.0)
    d = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=100.0, equity=100000.0,
        strategy="scalp", mode="paper", positions=[],
    )
    assert not d.allowed


def test_live_mode_still_checked_when_paper_skip(budget, monkeypatch):
    """PB_PAPER_SKIP=true 不得放行 live。"""
    monkeypatch.setenv("PB_PAPER_SKIP", "true")
    budget._strategy_drawdown_metric = lambda *a, **kw: _dd(99.0)
    d = budget.evaluate_open(
        symbol="BTC", action="buy", notional_usd=100.0, equity=100000.0,
        strategy="scalp", mode="live", positions=[],
    )
    assert not d.allowed
    assert d.metrics.get("paper_skip") is None
