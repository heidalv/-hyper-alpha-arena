# -*- coding: utf-8 -*-
"""[2026-09-14 F92] 区间成交桶取数窗口契约（重大漏单根因）。

现场：成交桶按 15s 网格存储、时间戳=**桶起点**，实测「桶结束 + ~1s」落库。
此前 `fetch_market` 的下界取 `last_tick_ts`（**墙钟**，落在桶中间），SQL 用
`timestamp > 下界` ⇒ 系统性排除「标签 ≤ 下界 < 标签+15s」的那个桶。
实测后果：同窗口同参数，实盘 8.9 笔/小时 vs 回放 101 笔/小时（**只吃到 9%**），
实盘因此长期接近不成交、盈亏被极少数样本主导。

修：窗口两端锚在**快照桶标签**（与回放同一时间网格）：
  - `lo` = 该币已消费到的最大桶标签（冷启动回退一个桶）；
  - `hi` = 当前快照标签（只消费到本次决策依据的快照那一桶，不把未来成交算进来）；
  - 桶 `[L, L+15)` 仅当 `L + 15s > 挂单时刻` 才参与成交判定（防幻影成交）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402

B = mmrunner.SEG_BUCKET_MS


def test_bucket_ms_is_15s():
    assert B == 15000, "成交桶/快照网格是 15s；改变它必须同时改回放口径"


def test_cold_start_falls_back_one_bucket():
    """冷启动（无水位）⇒ 回退一个桶，覆盖「上一 tick 所在桶」的尾巴。"""
    snap = 1_700_000_100_000          # 任意 15s 对齐标签
    lo, hi, _ = mmrunner.seg_window(0, snap)
    assert lo == snap - B, "冷启动下界必须回退一个桶（否则丢掉当前桶的成交）"
    assert hi == snap, "上界 = 当前快照标签"


def test_normal_advance_covers_the_next_bucket_inclusive():
    """水位 = 上一快照标签 ⇒ 本次恰好消费「下一个桶」，且上界闭区间。

    这是与回放 `[left,right]` 语义对齐的关键：桶标签 == 水位时必须被消费
    （旧实现 `timestamp > 墙钟` 正是把它排除掉的）。
    """
    prev_snap = 1_700_000_100_000
    snap = prev_snap + B
    lo, hi, _ = mmrunner.seg_window(prev_snap, snap)
    assert lo == prev_snap, "下界 = 已消费标签（开区间排除它本身）"
    assert hi == snap, "上界 = 当前快照标签（闭区间包含它）"
    assert lo < hi and (hi - lo) == B, "恰好前进一个桶"


def test_wall_clock_mid_bucket_is_never_used_as_lower_bound():
    """核心回归：下界必须落在桶网格上（不能是墙钟中间值）。

    旧实现下界 = last_tick_ts（例如 1_700_000_107_300），会把标签
    1_700_000_100_000 的桶（覆盖它）排除 ⇒ 每 tick 漏一个桶。
    """
    snap = 1_700_000_115_000
    for prev in (0, snap - B, snap - 3 * B):
        lo, hi, _ = mmrunner.seg_window(prev, snap)
        assert lo % B == 0, f"下界必须桶对齐，实际 {lo}"


def test_upper_bound_never_exceeds_snapshot_label():
    """上界不得超过当前快照标签（否则会把「快照之后」的成交算进本次判定）。"""
    snap = 1_700_000_100_000
    lo, hi, _ = mmrunner.seg_window(snap - 5 * B, snap)
    assert hi == snap and lo <= hi


def test_stale_watermark_is_not_pulled_forward_by_snapshot():
    """水位落后（桶落库延迟）时窗口自动变宽补收，绝不跳过中间桶。"""
    snap = 1_700_000_100_000
    lo, hi, _ = mmrunner.seg_window(snap - 4 * B, snap)
    assert lo == snap - 4 * B and hi == snap
    assert (hi - lo) // B == 4, "落后 4 个桶就一次补收 4 个桶（无空洞）"


def test_quote_lifetime_gate_uses_quote_ts():
    """成交判定门槛 = 挂单时刻（ms）；桶结束时刻必须晚于它。"""
    snap = 1_700_000_100_000
    qts = 1_700_000_098.5            # 挂单于快照前 1.5s
    _, _, qts_ms = mmrunner.seg_window(0, snap, qts)
    assert qts_ms == 1_700_000_098_500
    # 桶 [snap-B, snap)：结束 = snap ⇒ snap > qts_ms ⇒ 参与判定
    assert (snap - B) + B > qts_ms
    # 更老的桶 [snap-2B, snap-B)：结束 = snap-B < qts_ms ⇒ 只消费不判定
    assert (snap - 2 * B) + B < qts_ms


def test_no_quote_means_no_lifetime_gate():
    """无挂单（quote_ts=0）⇒ 门槛为 0，所有桶都可判定（空仓时窗口照常推进）。"""
    _, _, qts_ms = mmrunner.seg_window(0, 1_700_000_100_000, 0.0)
    assert qts_ms == 0


def test_symbol_state_persists_seg_label():
    """已消费标签必须随运行态持久化（重启后不漏桶）。"""
    st = mmrunner.SymbolState(symbol="BTC")
    assert st.last_seg_ms == 0
    st.last_seg_ms = 1_700_000_100_000
    d = st.to_dict()
    assert d["last_seg_ms"] == 1_700_000_100_000
    st2 = mmrunner.SymbolState.from_dict(d)
    assert st2.last_seg_ms == 1_700_000_100_000


def test_fetch_market_uses_snapshot_anchored_window():
    """源码契约：fetch_market 必须用 seg_window + 闭区间上界 + 挂单存续期过滤。"""
    import inspect
    src = inspect.getsource(mmrunner.ShadowRunner.fetch_market)
    assert "seg_window(" in src, "窗口必须由 seg_window 统一计算（可单测）"
    assert "timestamp > :lo AND timestamp <= :hi" in src, "上界必须闭区间且锚在快照"
    assert "FILTER (WHERE timestamp + :bk > :qts)" in src, "必须有挂单存续期过滤"
    assert "last_seg_ms" in src, "水位必须落在持久化运行态上"
