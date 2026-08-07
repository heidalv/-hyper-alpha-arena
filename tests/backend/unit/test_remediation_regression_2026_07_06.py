"""2026-07-06 整改回归测试。

锁定本轮"无兼容尾巴"整改中最容易被后续重构悄悄破坏的三类不变量：

1. 多频率硬约束链（H1-H5）：strategy_coordinator._apply_multi_freq_constraints
   —— 4h/1h/15m 方向冲突时必须置 constraint_violated=True 并返回 False。
2. constraint_violated 门禁传导（P0-6）：unified_gate.evaluate_entry
   —— market_data.constraint_violated=True 时必须 BLOCK（此前只写日志不拦截）。
3. MLTO 并发占位（#1 调度空转 / #3 双循环重复开仓）：
   —— "检查+占位"必须在同一把锁 + 同一个共享 set 上原子完成，
      并发下同一 key 只能被占位一次，且不丢条目（不再用"集合|{key}"整体替换）。
"""
import threading

import pytest

pytestmark = pytest.mark.unit


# ────────────────────────────────────────────────────────────────────────────
# 1. 多频率硬约束链 H1-H5
# ────────────────────────────────────────────────────────────────────────────
def _bars(start: float, step: float, n: int):
    """生成单调 K 线（dict 形态），用于稳定驱动 EMA 方向。"""
    return [
        {"close": start + step * i, "high": start + step * i + 1, "low": start + step * i - 1}
        for i in range(n)
    ]


def _make_env():
    from backend.services.strategy_coordinator import MarketEnvironment

    env = MarketEnvironment()
    # 关闭 VPVR 分支干扰：poc_price=0 时不进入价值区约束
    env.current_in_va = True
    env.poc_price = 0
    return env


def test_multi_freq_conflict_sets_violation():
    """4h 看多 + 1h 看空 → 硬约束违反，返回 False。"""
    from backend.services.strategy_coordinator import StrategyCoordinator

    sc = StrategyCoordinator(db=None)
    up_4h = _bars(100, 0.6, 60)     # 稳定上行 → 4h 看多
    down_1h = _bars(130, -0.7, 40)  # 稳定下行 → 1h 看空
    env = _make_env()

    ok = sc._apply_multi_freq_constraints(env, [], down_1h, up_4h)

    assert ok is False
    assert env.constraint_violated is True
    assert env.freq_4h_direction == 1
    assert env.freq_1h_direction == -1
    assert env.constraint_reason  # 必须有可读原因供门禁透传


def test_multi_freq_aligned_passes():
    """4h/1h/15m 同向看多 → 通过约束，constraint_violated=False。"""
    from backend.services.strategy_coordinator import StrategyCoordinator

    sc = StrategyCoordinator(db=None)
    up_4h = _bars(100, 0.6, 60)
    up_1h = _bars(100, 0.5, 40)
    up_15m = _bars(100, 0.4, 40)
    env = _make_env()

    ok = sc._apply_multi_freq_constraints(env, up_15m, up_1h, up_4h)

    assert ok is True
    assert env.constraint_violated is False
    assert env.freq_4h_direction == 1
    assert env.freq_1h_direction == 1
    assert env.freq_15m_direction == 1


# ────────────────────────────────────────────────────────────────────────────
# 2. constraint_violated 门禁传导（P0-6）
# ────────────────────────────────────────────────────────────────────────────
def test_unified_gate_blocks_on_constraint_violated():
    """market_data.constraint_violated=True → evaluate_entry 必须 BLOCK。"""
    from backend.services.decision_core.unified_gate import evaluate_entry

    r = evaluate_entry(
        db=None, account_id=1, symbol="BTC", action="buy",
        confidence=90, tier="short", trade_nature="swing",
        tp_pct=0.02, sl_pct=0.01,
        market_data={"constraint_violated": True, "constraint_reason": "1h方向与4h方向冲突"},
        mode="paper",
    )
    assert r.allowed is False
    assert r.rule == "multi_freq_constraint"
    assert "1h" in r.reason or "H1-H5" in r.reason


def test_unified_gate_no_false_block_without_constraint():
    """未设置 constraint_violated 时，不因该规则误拦（放行到后续门槛判断）。"""
    from backend.services.decision_core.unified_gate import evaluate_entry

    r = evaluate_entry(
        db=None, account_id=1, symbol="BTC", action="buy",
        confidence=90, tier="short", trade_nature="scalp",
        tp_pct=0.02, sl_pct=0.01,
        market_data={"constraint_violated": False},
        mode="paper",
    )
    # 结果可能因其它门槛被拦，但绝不能命中 multi_freq_constraint 规则
    assert r.rule != "multi_freq_constraint"


# ────────────────────────────────────────────────────────────────────────────
# 3. MLTO 并发占位不变量（#1 / #3）
# ────────────────────────────────────────────────────────────────────────────
class _HandledStub:
    """复刻 full_auto_trading_service 中 _mlto_handled_keys + _mlto_handled_lock
    的"检查+占位"契约（见 full_auto_trading_service.py 4898-4904 / 5258-5267）：
    - 共享同一个 set 对象（原地 add，绝不用 `旧集合 | {key}` 整体替换）
    - 共享同一把锁保证跨线程可见
    - reserve(key) 原子：同一 key 只有第一个线程成功占位
    """

    def __init__(self):
        self._mlto_handled_keys = set()
        self._mlto_handled_lock = threading.Lock()
        self.processed = []  # 记录"实际执行了业务"的 key（应无重复）
        self._proc_lock = threading.Lock()

    def _reserve_key(self, key: str) -> bool:
        with self._mlto_handled_lock:
            if key in self._mlto_handled_keys:
                return False
            self._mlto_handled_keys.add(key)
            return True

    def try_process(self, key: str) -> None:
        if not self._reserve_key(key):
            return
        # 模拟"跑完 LLM 分析并开仓"这段耗时业务
        with self._proc_lock:
            self.processed.append(key)


def test_mlto_reserve_key_atomic_single_winner():
    """N 个线程抢同一个 key → 只有 1 个占位成功，业务只执行 1 次。"""
    stub = _HandledStub()
    winners = []
    lock = threading.Lock()

    def worker():
        won = stub._reserve_key("BTC:mid")
        if won:
            with lock:
                winners.append(1)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(winners) == 1
    assert stub._mlto_handled_keys == {"BTC:mid"}


def test_mlto_no_duplicate_processing_under_concurrency():
    """主循环与独立循环并发处理同一批 key → 每个 key 只被业务处理一次，且不丢。"""
    stub = _HandledStub()
    keys = [f"SYM{i}:mid" for i in range(20)]

    def loop():
        for k in keys:
            stub.try_process(k)

    threads = [threading.Thread(target=loop) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 无重复 + 无遗漏
    assert sorted(stub.processed) == sorted(keys)
    assert len(stub.processed) == len(set(stub.processed)) == len(keys)


def test_mlto_handled_set_is_mutated_in_place():
    """占位必须原地 add（set 对象身份不变），杜绝"整体替换导致引用分叉"回归。"""
    stub = _HandledStub()
    original = stub._mlto_handled_keys
    stub._reserve_key("ETH:long")
    stub._reserve_key("SOL:mid")
    assert stub._mlto_handled_keys is original  # 同一对象
    assert stub._mlto_handled_keys == {"ETH:long", "SOL:mid"}


# ────────────────────────────────────────────────────────────────────────────
# 4. K线批量加载路由（P1 #14）
# ────────────────────────────────────────────────────────────────────────────
def _mk_bars(n: int, base_ts: int = 1_700_000_000):
    return [
        {"timestamp": base_ts + i * 900, "open": 1.0, "high": 1.1,
         "low": 0.9, "close": 1.0 + i * 0.01, "volume": 100.0}
        for i in range(n)
    ]


def test_batch_loader_routing_cache_batch_fallback(monkeypatch):
    """校验 get_klines_batch_from_db 三条路由：
    - A: 缓存命中 → 直接取缓存，不进批量查询
    - B: 未缓存 + 批量返回足量新鲜数据 → 走批量结果
    - C: 未缓存 + 批量返回过薄 → 回退单标的 get_klines_from_db（含降级）
    """
    from backend.services import kline_data_service as kds

    svc = kds.kline_service
    cache_full = _mk_bars(60)
    batch_fresh = _mk_bars(40)
    fallback_bars = _mk_bars(30)

    def fake_cache_get(sym, period, exchange=None):
        return cache_full if sym == "A" else None

    captured = {"batch_input": None, "fallback_syms": []}

    def fake_query_batch(symbols, period, count, exchange):
        captured["batch_input"] = list(symbols)
        return {"B": batch_fresh}  # C 缺失 → 过薄

    def fake_single(sym, period, count=500, exchange=None):
        captured["fallback_syms"].append(sym)
        return fallback_bars

    monkeypatch.setattr(kds.kline_cache, "get_klines", fake_cache_get)
    monkeypatch.setattr(kds.kline_cache, "set_klines", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_query_klines_batch", fake_query_batch)
    monkeypatch.setattr(svc, "get_klines_from_db", fake_single)
    # 新鲜度恒真：只让"过薄"触发回退，隔离 stale 因素
    monkeypatch.setattr(kds, "_klines_are_fresh", lambda k, p: True)

    out = svc.get_klines_batch_from_db(["A", "B", "C"], "15m", count=40, exchange="asterdex")

    # A 命中缓存，不应出现在批量查询入参里
    assert "A" not in captured["batch_input"]
    assert set(captured["batch_input"]) == {"B", "C"}
    # C 过薄 → 走单标的回退；B 不回退
    assert captured["fallback_syms"] == ["C"]
    # 三个 symbol 都有结果
    assert set(out.keys()) == {"A", "B", "C"}
    # A 走缓存，返回最新 count 根（cache_full 有 60 根，count=40 → 取后 40）
    assert out["A"] == cache_full[-40:]
    assert out["B"] == batch_fresh
    assert out["C"] == fallback_bars


def test_batch_loader_empty_symbols():
    from backend.services import kline_data_service as kds

    assert kds.kline_service.get_klines_batch_from_db([], "15m") == {}


# ────────────────────────────────────────────────────────────────────────────
# 5. 时点快照消费（P1 #12/#13 落地部分）
# ────────────────────────────────────────────────────────────────────────────
def _kbars(base, n=40):
    return [
        {"timestamp": 1_700_000_000 + i * 60, "open": base, "high": base + 1,
         "low": base - 1, "close": base + i * 0.1, "volume": 100.0}
        for i in range(n)
    ]


def test_load_env_klines_consumes_snapshot(monkeypatch):
    """快照已提供且 ≥20 根的周期直接复用，缺失周期回退 _get_fresh_klines。"""
    from backend.services.strategy_coordinator import StrategyCoordinator

    sc = StrategyCoordinator(db=None)
    # 只提供 15m/1h/4h 快照，故意不给 1d → 1d 必须回退拉取
    snapshot = {"15m": _kbars(100), "1h": _kbars(200), "4h": _kbars(300)}
    fetched = []

    def fake_fresh(symbol, period, lookback, now_ts, exchange):
        fetched.append(period)
        return _kbars(999)

    monkeypatch.setattr(sc, "_get_fresh_klines", fake_fresh)

    k15, k1h, k4h, k1d = sc._load_env_klines("BTC", 1_700_000_000, "asterdex", snapshot)

    assert fetched == ["1d"]           # 仅 1d 回退，其余走快照
    assert k15 is snapshot["15m"]
    assert k1h is snapshot["1h"]
    assert k4h is snapshot["4h"]
    assert len(k1d) == 40              # 来自回退


def test_load_env_klines_snapshot_too_thin_falls_back(monkeypatch):
    """快照某周期不足 20 根 → 视为无效，回退实时拉取。"""
    from backend.services.strategy_coordinator import StrategyCoordinator

    sc = StrategyCoordinator(db=None)
    snapshot = {"15m": _kbars(100, n=10)}  # 只有 10 根 < 20
    fetched = []
    monkeypatch.setattr(
        sc, "_get_fresh_klines",
        lambda symbol, period, lookback, now_ts, exchange: fetched.append(period) or _kbars(1),
    )

    sc._load_env_klines("BTC", 1_700_000_000, "asterdex", snapshot)

    assert "15m" in fetched  # 太薄 → 回退


def test_load_env_klines_no_snapshot_pulls_all(monkeypatch):
    """不传快照（None）时四周期全部实时拉取，行为与旧版一致。"""
    from backend.services.strategy_coordinator import StrategyCoordinator

    sc = StrategyCoordinator(db=None)
    fetched = []
    monkeypatch.setattr(
        sc, "_get_fresh_klines",
        lambda symbol, period, lookback, now_ts, exchange: fetched.append(period) or [],
    )

    sc._load_env_klines("BTC", 1_700_000_000, "asterdex", None)

    assert set(fetched) == {"15m", "1h", "4h", "1d"}


# ────────────────────────────────────────────────────────────────────────────
# 6. ReplayHarness 三周期全覆盖（P2）：run_batch 聚合不变量
#    —— 审查报告指出 MVP "覆盖面窄：单 symbol + mid tier"；run_batch 必须
#       覆盖 short/mid/long，且聚合口径 = 各子报告之和（不重不漏）。
# ────────────────────────────────────────────────────────────────────────────
def _replay_bars(n: int = 30):
    """生成带动量的合成 K 线，稳定触发规则 proposer（change>1.5% → buy）。"""
    return [{"close": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i} for i in range(n)]


def test_replay_run_batch_covers_three_tiers(monkeypatch):
    from backend.services.replay.replay_harness import ReplayHarness, BatchReplayReport

    h = ReplayHarness()
    # 隔离 DB：_load_bars 直接返回合成 K 线（evaluate_proposal 允许 db=None，
    # 与 verify_gap_closure 自检一致）
    monkeypatch.setattr(h, "_load_bars", lambda symbol, tier: _replay_bars())

    batch = h.run_batch(["BTC"], ["short", "mid", "long"])

    assert isinstance(batch, BatchReplayReport)
    # 覆盖三个 tier，每 tier 一份子报告
    assert set(batch.per_tier.keys()) == {"short", "mid", "long"}
    assert set(batch.reports.keys()) == {"BTC:short", "BTC:mid", "BTC:long"}


def test_replay_run_batch_aggregate_equals_sum(monkeypatch):
    """聚合口径必须严格等于各子报告之和（不重不漏），且 allow+block=proposals。"""
    from backend.services.replay.replay_harness import ReplayHarness

    h = ReplayHarness()
    monkeypatch.setattr(h, "_load_bars", lambda symbol, tier: _replay_bars())

    batch = h.run_batch(["BTC", "ETH"], ["short", "mid"])

    sum_prop = sum(r.proposals for r in batch.reports.values())
    sum_allow = sum(r.allowed for r in batch.reports.values())
    sum_block = sum(r.blocked for r in batch.reports.values())

    assert batch.total_proposals == sum_prop
    assert batch.total_allowed == sum_allow
    assert batch.total_blocked == sum_block
    assert batch.total_allowed + batch.total_blocked == batch.total_proposals
    # 4 组合（2 symbol × 2 tier）
    assert len(batch.reports) == 4


def test_replay_run_batch_empty_symbols(monkeypatch):
    """空标的列表 → 空聚合，不抛异常。"""
    from backend.services.replay.replay_harness import ReplayHarness

    h = ReplayHarness()
    batch = h.run_batch([], ["mid"])
    assert batch.total_proposals == 0
    assert batch.reports == {}
    d = batch.to_dict()
    assert d["allow_rate"] == 0.0  # 分母兜底 max(1, ...) 不除零


# ────────────────────────────────────────────────────────────────────────────
# 7. unified_data_pool 全量整合 · 灰度切片：
#    UnifiedDataPool.klines_for_coordinator 纯转换器不变量
#    —— 快照 DataFrame → coordinator 消费的 {period:[dict]}；
#       不足 min_bars 的周期跳过；无快照返回 {}（消费端自动回退，向后兼容）。
# ────────────────────────────────────────────────────────────────────────────
def test_klines_for_coordinator_converts_snapshot(monkeypatch):
    import pandas as pd
    from backend.services.unified_data_pool import UnifiedSnapshot, unified_data_pool

    def _df(n):
        return pd.DataFrame(
            [{"timestamp": i, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}
             for i in range(n)]
        )

    snap = UnifiedSnapshot()
    snap.klines[("BTC", "15m")] = _df(100)
    snap.klines[("BTC", "1h")] = _df(50)
    snap.klines[("BTC", "4h")] = _df(30)
    snap.klines[("BTC", "1d")] = _df(10)   # <20 → 应跳过

    out = unified_data_pool.klines_for_coordinator("btc", snap, min_bars=20)

    assert set(out.keys()) == {"15m", "1h", "4h"}     # 1d 因过薄被跳过
    assert isinstance(out["15m"], list) and isinstance(out["15m"][0], dict)
    assert out["15m"][0]["close"] == 1.5
    assert len(out["1h"]) == 50


def test_klines_for_coordinator_no_snapshot_returns_empty(monkeypatch):
    """无快照 → 返回 {}，调用方行为与不传快照一致（向后兼容护栏）。"""
    from backend.services.unified_data_pool import unified_data_pool

    monkeypatch.setattr(unified_data_pool, "get_snapshot", lambda *a, **k: None)
    assert unified_data_pool.klines_for_coordinator("BTC", None) == {}


def test_klines_for_coordinator_missing_symbol_periods():
    """快照存在但无该 symbol 周期 → 该周期跳过，不抛异常。"""
    from backend.services.unified_data_pool import UnifiedSnapshot, unified_data_pool

    snap = UnifiedSnapshot()  # 空 klines
    assert unified_data_pool.klines_for_coordinator("ETH", snap) == {}


# ────────────────────────────────────────────────────────────────────────────
# 8. unified_data_pool 全量整合 · 下游切片：AI prompt K 线优先复用快照
#    —— agent_deep_context._fetch_klines_for_prompt：
#       开关关 → 走 DB（向后兼容）；开关开且快照足够 → 复用快照 tail(count)；
#       开关开但快照过薄 → 回退 DB。数据源可切换但指标窗口口径不变。
# ────────────────────────────────────────────────────────────────────────────
def _patch_db_klines(monkeypatch, sentinel):
    import backend.services.kline_data_service as kds
    monkeypatch.setattr(kds.kline_service, "get_klines_from_db",
                        lambda symbol, tf, count=200: sentinel)


def test_fetch_klines_for_prompt_flag_off_uses_db(monkeypatch):
    from backend.services import agent_deep_context as adc

    monkeypatch.delenv("COORDINATOR_CONSUME_SNAPSHOT_KLINES", raising=False)
    db_sentinel = [{"close": 1, "high": 1, "low": 1, "open": 1, "volume": 1}]
    _patch_db_klines(monkeypatch, db_sentinel)

    assert adc._fetch_klines_for_prompt("BTC", "4h", 30) is db_sentinel


def test_fetch_klines_for_prompt_flag_on_uses_snapshot(monkeypatch):
    import pandas as pd
    from backend.services import agent_deep_context as adc
    from backend.services.unified_data_pool import UnifiedSnapshot, unified_data_pool

    monkeypatch.setenv("COORDINATOR_CONSUME_SNAPSHOT_KLINES", "true")
    # DB 兜底给一个可辨识的 sentinel；若被用到说明没走快照
    _patch_db_klines(monkeypatch, [{"close": -999}])

    snap = UnifiedSnapshot()
    snap.klines[("BTC", "4h")] = pd.DataFrame(
        [{"timestamp": i, "open": 1.0, "high": 2.0, "low": 0.5, "close": float(i), "volume": 5.0}
         for i in range(100)]
    )
    monkeypatch.setattr(unified_data_pool, "get_snapshot", lambda *a, **k: snap)

    out = adc._fetch_klines_for_prompt("BTC", "4h", 30)
    assert len(out) == 30                     # tail(count)，指标窗口口径不变
    assert out[-1]["close"] == 99.0           # 来自快照，非 DB sentinel
    assert all(r.get("close") != -999 for r in out)


def test_fetch_klines_for_prompt_thin_snapshot_falls_back(monkeypatch):
    import pandas as pd
    from backend.services import agent_deep_context as adc
    from backend.services.unified_data_pool import UnifiedSnapshot, unified_data_pool

    monkeypatch.setenv("COORDINATOR_CONSUME_SNAPSHOT_KLINES", "true")
    db_sentinel = [{"close": 7, "high": 7, "low": 7, "open": 7, "volume": 7}]
    _patch_db_klines(monkeypatch, db_sentinel)

    snap = UnifiedSnapshot()
    snap.klines[("BTC", "4h")] = pd.DataFrame(  # 只有 10 根 < count=30 → 回退 DB
        [{"timestamp": i, "open": 1.0, "high": 2.0, "low": 0.5, "close": float(i), "volume": 5.0}
         for i in range(10)]
    )
    monkeypatch.setattr(unified_data_pool, "get_snapshot", lambda *a, **k: snap)

    assert adc._fetch_klines_for_prompt("BTC", "4h", 30) is db_sentinel


# ────────────────────────────────────────────────────────────────────────────
# 9. unified_data_pool 全量整合 · MLTO 切片：
#    _analyze_mid_term 的"市场状态确认"regime 块必须复用传入 snapshot 的 K 线，
#    不再无视 snapshot 直接打 DB（与同文件 _inject_regime 既有约定一致）。
# ────────────────────────────────────────────────────────────────────────────
def test_mlto_mid_regime_prefers_snapshot_klines(monkeypatch):
    import pandas as pd
    import backend.services.kline_data_service as kds
    import backend.services.market_regime as mr
    import backend.services.data_readiness_gate as drg
    from backend.services.unified_data_pool import UnifiedSnapshot
    from backend.services.multi_timeframe_orchestrator import mt_orchestrator

    # DB 取数若被调用即判失败（本用例快照已足量，不该回退 DB）
    def _boom(*a, **k):
        raise AssertionError("regime 块不应在快照充足时回退 get_klines_from_db")
    monkeypatch.setattr(kds.kline_service, "get_klines_from_db", _boom)

    # 放行 strict-data-gate，确保能走到 regime 子块
    monkeypatch.setattr(drg, "indicators_are_real", lambda *a, **k: True)

    # 隔离 regime 块之前的两处情报注入网络调用（crypto_alpha / intel），保持用例快且确定
    import backend.services.crypto_alpha_signals as cas
    monkeypatch.setattr(cas.crypto_alpha, "get_bundle",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stub")))
    monkeypatch.setattr(mt_orchestrator, "_get_intel_signal", lambda *a, **k: None)

    # regime 分类桩：记录被调用 + 返回 trending
    calls = {"classify": 0}

    class _FakeRegime:
        value = "trending_up"

    class _FakeCls:
        regime = _FakeRegime()
        confidence = 0.8

    class _FakeMRC:
        def classify(self, df):
            calls["classify"] += 1
            assert len(df) >= 50  # 证明拿到的是快照的足量 K 线
            return _FakeCls()

    monkeypatch.setattr(mr, "MarketRegimeClassifier", _FakeMRC)

    snap = UnifiedSnapshot()
    snap.indicators["BTC"] = {"rsi_4h": 65.0, "macd_4h": 0.5, "rsi": 65.0, "macd": 0.5}
    snap.klines[("BTC", "1h")] = pd.DataFrame(
        [{"timestamp": i, "open": 1.0, "high": 2.0, "low": 0.5, "close": float(100 + i), "volume": 5.0}
         for i in range(60)]
    )

    view = mt_orchestrator._analyze_mid_term("BTC", snap)

    # 关键不变量：regime 分类确实执行了（→ 走到了子块），且全程未回退 DB（_boom 未触发）
    assert calls["classify"] >= 1
    assert view is not None


# ────────────────────────────────────────────────────────────────────────────
# 10. unified_data_pool 全量整合 · 因子引擎/scalp 切片：
#     快照 5m DataFrame → scalp 路径消费的 list[dict]（tail(100)），
#     形态与 get_klines_from_db 一致（下游 pd.DataFrame(...) + len()>20 校验可用）。
# ────────────────────────────────────────────────────────────────────────────
def test_snapshot_5m_shape_matches_scalp_consumer():
    import pandas as pd
    from backend.services.unified_data_pool import UnifiedSnapshot

    snap = UnifiedSnapshot()
    snap.klines[("BTC", "5m")] = pd.DataFrame(
        [{"timestamp": i, "open": 1.0, "high": 2.0, "low": 0.5, "close": float(i), "volume": 3.0}
         for i in range(120)]
    )

    # 复刻 scalp 路径的取用与校验：tail(100).to_dict("records") → list[dict]
    df5 = snap.klines.get(("BTC", "5m"))
    raw = df5.tail(100).to_dict("records")

    assert isinstance(raw, list) and isinstance(raw[0], dict)
    assert len(raw) == 100 and len(raw) > 20        # 满足 scalp 的 >20 与 <30 跳过口径
    # 下游 pd.DataFrame(raw) 必须能重建且列齐全（factor_engine 依赖 close/high/low/volume）
    rebuilt = pd.DataFrame(raw)
    for col in ("open", "high", "low", "close", "volume"):
        assert col in rebuilt.columns
    assert float(rebuilt.iloc[-1]["close"]) == 119.0


# ────────────────────────────────────────────────────────────────────────────
# 11. 执行侧决策价一致性门禁：FullAutoTradingService._decision_price_consistency_ok
#     —— 默认关；开启后决策价 vs 下单前实时价偏离超阈值则拦截；
#        取不到实时价/无决策价基准/异常一律 fail-open（不误杀、不卡死链路）。
# ────────────────────────────────────────────────────────────────────────────
def _patch_realtime_price(monkeypatch, price):
    import backend.services.strategy_coordinator as sc
    monkeypatch.setattr(sc.StrategyCoordinator, "_get_realtime_price_robust",
                        staticmethod(lambda symbol, exchange: price))
    import backend.services.exchange_config as ec
    monkeypatch.setattr(ec, "get_active_exchange", lambda *a, **k: "asterdex")


def test_decision_price_gate_off_by_default(monkeypatch):
    from backend.services.full_auto_trading_service import FullAutoTradingService as F

    monkeypatch.delenv("DECISION_PRICE_GATE_ENABLED", raising=False)
    # 即便价格已暴走，开关关 → 放行
    ok, reason = F._decision_price_consistency_ok("BTC", {"current_price": 100.0}, None, "live")
    assert ok is True and reason == ""


def test_decision_price_gate_blocks_on_large_deviation(monkeypatch):
    from backend.services.full_auto_trading_service import FullAutoTradingService as F

    monkeypatch.setenv("DECISION_PRICE_GATE_ENABLED", "true")
    monkeypatch.setenv("DECISION_PRICE_MAX_DEVIATION_PCT_LIVE", "0.005")
    _patch_realtime_price(monkeypatch, 101.0)  # 决策 100 → 现价 101 = 1% > 0.5%

    ok, reason = F._decision_price_consistency_ok("BTC", {"current_price": 100.0}, None, "live")
    assert ok is False and "decision_price_stale" in reason


def test_decision_price_gate_allows_small_deviation(monkeypatch):
    from backend.services.full_auto_trading_service import FullAutoTradingService as F

    monkeypatch.setenv("DECISION_PRICE_GATE_ENABLED", "true")
    monkeypatch.setenv("DECISION_PRICE_MAX_DEVIATION_PCT_PAPER", "0.010")
    _patch_realtime_price(monkeypatch, 100.3)  # 0.3% < 1.0%(paper)

    ok, reason = F._decision_price_consistency_ok("BTC", {"current_price": 100.0}, None, "paper")
    assert ok is True and reason == ""


def test_decision_price_gate_fail_open_when_no_live_price(monkeypatch):
    from backend.services.full_auto_trading_service import FullAutoTradingService as F

    monkeypatch.setenv("DECISION_PRICE_GATE_ENABLED", "true")
    _patch_realtime_price(monkeypatch, 0.0)  # 取不到实时价 → fail-open

    ok, reason = F._decision_price_consistency_ok("BTC", {"current_price": 100.0}, None, "live")
    assert ok is True and reason == ""


def test_decision_price_gate_fail_open_when_no_decision_price(monkeypatch):
    from backend.services.full_auto_trading_service import FullAutoTradingService as F

    monkeypatch.setenv("DECISION_PRICE_GATE_ENABLED", "true")
    _patch_realtime_price(monkeypatch, 999.0)  # 有现价但无决策价基准 → 不误杀

    ok, reason = F._decision_price_consistency_ok("BTC", {}, None, "live")
    assert ok is True and reason == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
