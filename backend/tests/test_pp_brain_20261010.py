# -*- coding: utf-8 -*-
"""[2026-10-10 主脑接入高频学习] 高频入口（pp_brain）与熔断隔离的契约测试。

锁定的四件事（每条都对应一个真实会出事的机制）：

1. **只认平仓腿**：账本里 `meta_json->>'rt_bp' IS NOT NULL` 才是往返平仓腿（开仓腿
   写零）。若把开仓腿也当往返，主脑会收到一半的假样本。
2. **游标幂等且不吞数据**：游标是 `(ts, id)` 双键。同秒多笔不能互相吃掉；单笔失败
   必须**停在原行重试**，连续失败到上限才跳过——否则一笔坏数据能永久卡死入口。
3. **不挂中线/长线论题**：`tier="hft"` 是唯一手段（`thesis_store.find_latest(sym,"hft")`
   恒空）。这条如果破了，同一个币的短来回会改写中线记忆 —— 用户明确禁止。
4. **不污染短线熔断**：`record_short_tier_outcome` 是**交易侧硬闸门**。高频每几分钟
   一笔来回（实测账本 2,244 行），逐笔记进去会把那道闸彻底污染。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import pp_brain as pb  # noqa: E402
from backend.services.unified_learning_service import (  # noqa: E402
    TradeOutcome,
    _is_hft_lane,
    _skip_circuit_bookkeeping,
    hft_isolation_enabled,
    unified_learning,
)


# ── 夹具 ───────────────────────────────────────────────────────────────
def _close_row(rid: int = 101, ts: float = 1000.0, symbol: str = "ETH",
               side: str = "sell", rt_bp: float = 2.0, qty: float = 0.2,
               fill_px: float = 2500.0, entry_px: float = 2499.5,
               notional: float = 500.0, fee_usd: float = 0.0,
               hold_sec: float = 42.0, exit_path: str = "pp_exit") -> dict:
    return {
        "id": rid, "symbol": f"{symbol}USDT", "ts": ts,
        "position_id": f"mm:{symbol}:1", "notional": notional,
        "meta": {
            "side": side, "qty": qty, "fill_px": fill_px, "mid_px": fill_px,
            "rt_bp": rt_bp, "rt_entry_px": entry_px, "hold_sec_true": hold_sec,
            "exit_path": exit_path, "regime": "R2", "fee_usd": fee_usd,
        },
    }


class FakeDriver:
    """最小 driver：`fetch` 返回预置行，`process` 记录 outcome 或按需抛错。"""

    def __init__(self, rows, fail_ids=()):
        self.rows = list(rows)
        self.fail_ids = set(fail_ids)
        self.seen = []

    def fetch(self, cursor_ts, cursor_id, limit):
        out = [r for r in self.rows
               if (float(r.get("ts") or 0.0), int(r.get("id") or 0))
               > (float(cursor_ts), int(cursor_id))]
        return out[: int(limit)]

    def process(self, outcome):
        rid = int(str(outcome.metadata.get("pp_roundtrip_id")).split(":")[-1])
        if rid in self.fail_ids:
            raise RuntimeError(f"boom-{rid}")
        self.seen.append(outcome)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("MM_PP_TO_BRAIN", "MM_PP_BRAIN_SINCE", "MM_PP_BRAIN_LIMIT",
              "MM_PP_BRAIN_MAX_ATTEMPTS", "P4_HFT_ISOLATION"):
        monkeypatch.delenv(k, raising=False)
    yield


def _cursor(root: Path, ts: float = 0.0, cid: int = 0) -> dict:
    return {"cursor_ts": ts, "cursor_id": cid, "ingested": 0, "failed": 0,
            "skipped": 0, "attempts_id": -1, "attempts": 0}


# ══ 1. 账本行 → TradeOutcome ═══════════════════════════════════════════
def test_open_leg_is_not_a_roundtrip():
    """开仓腿（无 rt_bp）必须被丢弃，不能当往返送给主脑。"""
    row = {"id": 1, "symbol": "ETHUSDT", "ts": 1.0, "meta": {"side": "buy", "qty": 1.0}}
    assert pb.to_outcome(row) is None


def test_close_leg_maps_to_hft_lane_outcome():
    o = pb.to_outcome(_close_row(side="sell", rt_bp=2.0))
    assert o is not None
    assert o.strategy_id == "mm_asterdex_pp"
    assert o.tier == "hft" and o.trade_nature == "scalp"
    assert o.symbol == "ETH"
    # 平仓方向 sell ⇒ 我们原来是多头
    assert o.side == "long"
    assert o.entry_price == pytest.approx(2499.5)
    assert o.exit_price == pytest.approx(2500.0)
    # 净口径 = 名义 × rt_bp/1e4 − 吃单费
    assert o.pnl == pytest.approx(500.0 * 2.0 / 1e4)
    assert o.metadata["pp_rt_bp"] == pytest.approx(2.0)
    assert o.metadata["close_reason"] == "pp_exit"
    assert o.persist_trade is False, "高频不得写 strategy_trades（会污染中线统计）"


def test_close_leg_short_side_and_taker_fee():
    o = pb.to_outcome(_close_row(side="buy", rt_bp=-4.0, fee_usd=0.2))
    assert o.side == "short"
    assert o.pnl == pytest.approx(500.0 * (-4.0) / 1e4 - 0.2)
    assert o.metadata["close_fee"] == pytest.approx(0.2)


def test_missing_entry_px_falls_back_to_fill_px():
    row = _close_row()
    row["meta"].pop("rt_entry_px")
    o = pb.to_outcome(row)
    assert o.entry_price == pytest.approx(2500.0)


def test_zero_notional_does_not_divide_by_zero():
    o = pb.to_outcome(_close_row(notional=0.0, qty=0.0))
    assert o.pnl_pct == 0.0


# ══ 2. 游标：幂等 / 失败重试 / 上限跳过 ════════════════════════════════
def test_run_once_ingests_and_advances_cursor(tmp_path):
    drv = FakeDriver([_close_row(rid=101, ts=10.0), _close_row(rid=102, ts=11.0)])
    cur = _cursor(tmp_path)
    extra = pb.run_once(driver=drv, root=tmp_path, cursor=cur)
    assert extra["ingested"] == 2 and extra["failed"] == 0
    assert len(drv.seen) == 2
    assert cur["cursor_ts"] == 11.0 and cur["cursor_id"] == 102


def test_same_timestamp_second_row_is_not_swallowed(tmp_path):
    """同秒两笔：靠 `id` 分量区分，第二笔不能被吃掉。"""
    drv = FakeDriver([_close_row(rid=201, ts=50.0), _close_row(rid=202, ts=50.0)])
    cur = _cursor(tmp_path)
    first = pb.run_once(driver=drv, root=tmp_path, cursor=cur)
    assert first["ingested"] == 2
    second = pb.run_once(driver=drv, root=tmp_path, cursor=cur)
    assert second["scanned"] == 0 and second["ingested"] == 0


def test_open_legs_advance_cursor_without_ingest(tmp_path):
    drv = FakeDriver([{"id": 301, "symbol": "ETHUSDT", "ts": 70.0,
                       "meta": {"side": "buy", "qty": 1.0}}])
    cur = _cursor(tmp_path)
    extra = pb.run_once(driver=drv, root=tmp_path, cursor=cur)
    assert extra["ingested"] == 0 and extra["skipped_rows"] == 1
    assert cur["cursor_ts"] == 70.0 and cur["cursor_id"] == 301


def test_failed_row_keeps_cursor_and_retries(tmp_path):
    drv = FakeDriver([_close_row(rid=401, ts=90.0)], fail_ids=(401,))
    cur = _cursor(tmp_path)
    first = pb.run_once(driver=drv, root=tmp_path, cursor=cur)
    assert first["ingested"] == 0 and first["failed"] == 1
    assert cur["cursor_ts"] == 0.0 and cur["cursor_id"] == 0, "失败必须停在原行"
    assert cur["attempts"] == 1
    # 第二次仍然重试（不是静默跳过）
    pb.run_once(driver=drv, root=tmp_path, cursor=cur)
    assert cur["attempts"] == 2


def test_poison_row_skipped_after_max_attempts(tmp_path, monkeypatch):
    monkeypatch.setenv("MM_PP_BRAIN_MAX_ATTEMPTS", "2")
    drv = FakeDriver([_close_row(rid=501, ts=95.0)], fail_ids=(501,))
    cur = _cursor(tmp_path)
    pb.run_once(driver=drv, root=tmp_path, cursor=cur)
    second = pb.run_once(driver=drv, root=tmp_path, cursor=cur)
    assert second["skipped_rows"] == 1, "到上限必须跳过，否则一行坏数据永久卡死入口"
    assert cur["cursor_id"] == 501 and cur["attempts"] == 0


def test_master_switch_off_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("MM_PP_TO_BRAIN", "0")
    drv = FakeDriver([_close_row(rid=601, ts=120.0)])
    extra = pb.run_once(driver=drv, root=tmp_path)
    assert extra["scanned"] == 0 and not drv.seen
    assert extra.get("skipped") == "MM_PP_TO_BRAIN=0"


def test_cursor_roundtrip_on_disk(tmp_path):
    cur = _cursor(tmp_path, ts=123.5, cid=7)
    assert pb.save_cursor(cur, tmp_path) is True
    back = pb.load_cursor(tmp_path)
    assert back["cursor_ts"] == pytest.approx(123.5) and back["cursor_id"] == 7
    # 文件损坏 ⇒ 回落 era 起点（fail-open 到"从头补"，不静默丢弃）
    (tmp_path / pb.CURSOR_FILE).write_text("{not json", encoding="utf-8")
    broken = pb.load_cursor(tmp_path)
    assert broken["cursor_ts"] == pytest.approx(pb.era_start_ts(tmp_path))
    assert broken["cursor_id"] == 0


def test_era_start_follows_marker_file(tmp_path, monkeypatch):
    monkeypatch.delenv("MM_PP_BRAIN_SINCE", raising=False)
    p = tmp_path / pb.ERA_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("1700000000.5", encoding="utf-8")
    assert pb.era_start_ts(tmp_path) == pytest.approx(1700000000.5)
    monkeypatch.setenv("MM_PP_BRAIN_SINCE", "1700000123")
    assert pb.era_start_ts(tmp_path) == pytest.approx(1700000123.0)


def test_limit_caps_backfill(tmp_path, monkeypatch):
    """首次开启补历史必须有界（不能一个 tick 灌 2000+ 笔）。"""
    monkeypatch.setenv("MM_PP_BRAIN_LIMIT", "2")
    rows = [_close_row(rid=700 + i, ts=200.0 + i) for i in range(5)]
    drv = FakeDriver(rows)
    cur = _cursor(tmp_path)
    extra = pb.run_once(driver=drv, root=tmp_path, cursor=cur)
    assert extra["scanned"] == 2 and extra["ingested"] == 2


def test_wall_clock_budget_stops_tick(tmp_path, monkeypatch):
    """墙钟预算到点就收工，且**已成功的部分不丢**（游标按每笔推进）。

    现场事故：一次 tick 跑了 ~15 分钟（200 笔 × 慢下游），APScheduler 按
    `max_instances=1` 把后续触发全部跳过 ⇒ 入口看起来"注册了但不动"。
    """
    monkeypatch.setenv("MM_PP_BRAIN_BUDGET_SEC", "0.05")
    rows = [_close_row(rid=800 + i, ts=300.0 + i) for i in range(50)]

    class SlowDriver(FakeDriver):
        def process(self, outcome):
            import time as _t
            _t.sleep(0.02)
            super().process(outcome)

    drv = SlowDriver(rows)
    cur = _cursor(tmp_path)
    extra = pb.run_once(driver=drv, root=tmp_path, cursor=cur)
    assert extra["budget_exhausted"] is True
    assert 0 < extra["ingested"] < 50, "预算内只跑一部分"
    assert cur["cursor_id"] == 800 + extra["ingested"] - 1, "游标必须停在最后一笔成功的行上"


def test_driver_reuses_one_session_for_the_batch():
    """整批只开一个 DB session（此前每笔一个 ⇒ 连接被长期占住、LeakGuard 指向本 tick）。"""
    opened = []

    class _Sess:
        def __init__(self):
            self.committed = 0

        def close(self):
            opened.append("close")

        def rollback(self):
            opened.append("rollback")

    made = []

    def _factory():
        s = _Sess()
        made.append(s)
        return s

    drv = pb.SqlDriver(session_factory=_factory)
    drv.open()
    assert len(made) == 1
    drv.close()
    assert opened == ["close"]
    # 已有批级 session 时，process() 不再新开
    import inspect
    src = inspect.getsource(pb.SqlDriver.process)
    assert "从批级 session 走" in src or "if self._db is not None" in src


# ══ 3. 车道身份与隔离 ══════════════════════════════════════════════════
def test_hft_outcome_is_recognised_as_hft_lane():
    o = pb.to_outcome(_close_row())
    assert _is_hft_lane(o) is True
    assert _skip_circuit_bookkeeping(o) is True


def test_mid_and_short_lanes_are_untouched():
    mid = TradeOutcome(source="paper", strategy_id="mid_x", tier="mid",
                       trade_nature="swing")
    short = TradeOutcome(source="paper", strategy_id="scalp_router", tier="short",
                         trade_nature="scalp")
    assert _is_hft_lane(mid) is False and _skip_circuit_bookkeeping(mid) is False
    assert _is_hft_lane(short) is False and _skip_circuit_bookkeeping(short) is False


def test_isolation_can_be_rolled_back(monkeypatch):
    o = pb.to_outcome(_close_row())
    monkeypatch.setenv("P4_HFT_ISOLATION", "false")
    assert hft_isolation_enabled() is False
    assert _skip_circuit_bookkeeping(o) is False, "回滚必须逐字恢复旧行为"


def test_illegal_isolation_value_fails_closed():
    """非法值 fail-closed（与仓内开关约定一致：不能因为写错值就放开硬闸）。"""
    import os
    os.environ["P4_HFT_ISOLATION"] = "maybe"
    try:
        assert hft_isolation_enabled() is True
    finally:
        os.environ.pop("P4_HFT_ISOLATION", None)


# ══ 4. 主脑侧：HFT 不写 genome、不挂论题 ════════════════════════════════
class _Mem:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_adaptation_skips_hft_without_touching_genome():
    """连亏 7 次以上：HFT 只清读数，不写 AIStrategy.genome（PP 不读那三个键）。"""
    import inspect
    src = inspect.getsource(unified_learning._check_adaptation_needed)
    assert "_skip_circuit_bookkeeping(outcome)" in src
    # 真实调用路径：没有任何 outcome.strategy_id 时也必须能跑（纯内存，不打库）
    o = pb.to_outcome(_close_row())
    del o.strategy_id  # 触发 `key = outcome.strategy_id` 的 getattr 语义
    unified_learning._loss_streaks = {"mm_asterdex_pp": 9}
    unified_learning._check_adaptation_needed(None, pb.to_outcome(_close_row()))
    assert unified_learning._loss_streaks.get("mm_asterdex_pp") == 0


def test_short_tier_breaker_is_wired_to_the_guard():
    """结构性断言：两处短线熔断记账都必须带隔离闸，否则高频会污染交易侧硬闸。"""
    import inspect

    from backend.services import unified_learning_service as uls
    src = inspect.getsource(uls.UnifiedLearningService.process_outcome)
    assert "_skip_circuit_bookkeeping(outcome)" in src
    src2 = inspect.getsource(uls.UnifiedLearningService._process_global_feedback)
    assert "_skip_circuit_bookkeeping(outcome)" in src2
    # 连亏 genome 路径同样受闸（HFT 只清读数不写库）
    src3 = inspect.getsource(uls.UnifiedLearningService._check_adaptation_needed)
    assert "_skip_circuit_bookkeeping(outcome)" in src3


def test_hft_strategy_has_placeholder_parent_prefix():
    """`mm_asterdex_pp` 必须在占位父行白名单里，否则策略记忆/教训静默丢失。"""
    import inspect
    src = inspect.getsource(unified_learning._resolve_strategy_id_for_fk)
    assert "mm_asterdex_pp" in src


def test_hft_placeholder_declares_hft_tier_explicitly():
    """占位行必须显式声明 tier/symbol —— 模型默认值是 mid/BTC。

    实测来源：高频第一笔的 `strategy_regime_scores` 落进了 `auto_mid_BTC`
    （`_update_regime_score` 用 `auto_{nature}_{symbol}` 合成分组键，
    而占位行 `timeframe_tier` 取了 server_default="mid"、`primary_symbol` 取了
    default="BTC"）⇒ 等于用高频污染了中线分组。
    """
    import inspect
    src = inspect.getsource(unified_learning._resolve_strategy_id_for_fk)
    assert '"mm_asterdex_pp": {"timeframe_tier": "hft", "primary_symbol": ""}' in src
    assert "cur_row.timeframe_tier = _fix[" in src, "旧占位行要能就地自愈"


# ══ 5. tick 接线 ═══════════════════════════════════════════════════════
def test_learning_loop_registers_hft_brain_tick():
    from backend.services.learning_loop_service import (
        DEFAULT_INTERVALS, JOB_HFT_BRAIN, JOB_TRIGGER_ALIASES, LearningLoopService,
    )
    assert JOB_HFT_BRAIN in DEFAULT_INTERVALS
    assert DEFAULT_INTERVALS[JOB_HFT_BRAIN] > 0
    svc = LearningLoopService()
    assert JOB_HFT_BRAIN in svc._last_tick_at
    assert callable(getattr(svc, "_tick_hft_brain", None))
    # [2026-10-10 前端升级] 手动触发的别名表是**唯一来源**（路由从这里派生），
    # 所以这里查别名表而不是查 `trigger_job` 的源码文本。
    assert "hft_brain" in JOB_TRIGGER_ALIASES
    assert JOB_TRIGGER_ALIASES["hft_brain"] == "_tick_hft_brain"


def test_hft_brain_tick_records_metrics(monkeypatch, tmp_path):
    """tick 必须走 `_record_tick`（否则 health 面板看不到这条闭环）。"""
    from backend.services.learning_loop_service import JOB_HFT_BRAIN, LearningLoopService
    svc = LearningLoopService()
    svc._paused = False
    calls = []
    monkeypatch.setattr(svc, "_record_tick",
                        lambda job, t0, ok, extra: calls.append((job, ok, extra)))
    monkeypatch.setattr(pb, "run_once", lambda: {"ok": True, "scanned": 3,
                                                 "ingested": 3, "failed": 0})
    svc._tick_hft_brain()
    assert calls and calls[0][0] == JOB_HFT_BRAIN and calls[0][1] is True
    assert calls[0][2]["ingested"] == 3
