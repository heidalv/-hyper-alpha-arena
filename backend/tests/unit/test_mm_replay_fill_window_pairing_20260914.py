# -*- coding: utf-8 -*-
"""[2026-09-14 F107] 成交判定分片口径契约：每个成交桶**恰好判定一次**。

历史两版都是错的，且都让"实盘 vs 模型"系统性分叉：
  - v1 `[ots[i], ots[i+1]]`：**前瞻**——用"挂单被刷新之后才发生的成交"判定该挂单；
  - v2（F103）`[ots[i-1], ots[i]]`：**两端闭合** ⇒ 每个桶同时落在相邻两轮分片里，
    同一批成交获得**两次**撞单机会。实测：该口径 65.4 笔/h vs 实盘 36 笔/h，
    单侧命中率 2.2×、空区间 0% vs 实盘 52%。
  - v3（F107，本契约）：下界**半开**（水位）、上界闭合、可选可见性过滤。与实盘
    `runner.fetch_market` 的 `timestamp > :lo AND timestamp <= :hi` 逐条同构。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

B = 15_000


def test_each_bucket_judged_exactly_once():
    """稀疏网格上连续推进：每个已落库的桶**恰好**被消费一次（不重不漏）。"""
    from backend.services.market_maker.core import seg_slice

    labels = [0, 2 * B, 3 * B, 7 * B]          # 稀疏：1B/4B/5B/6B 没有桶
    seen: list[int] = []
    wm = -B
    for snap in range(0, 9 * B, B):            # 快照每 15s 一个
        i0, i1 = seg_slice(labels, wm, snap)
        seen += [int(x) for x in labels[i0:i1]]
        if i1 > i0:
            wm = int(labels[i1 - 1])
    assert seen == labels, f"每个桶必须恰好一次，实际 {seen}"
    assert len(seen) == len(set(seen)), "出现重复判定（= 多算成交机会）"


def test_empty_span_keeps_watermark_and_catches_up():
    """空分片不丢成交量：水位不动，下一轮补收被跳过的标签。"""
    from backend.services.market_maker.core import seg_slice

    labels = [5 * B]
    wm = 0
    i0, i1 = seg_slice(labels, wm, 2 * B)
    assert (i0, i1) == (0, 0), "2B 时刻还没到 5B 的桶"
    i0, i1 = seg_slice(labels, wm, 5 * B)
    assert [int(x) for x in labels[i0:i1]] == [5 * B], "水位不动 ⇒ 下一轮必须补收"


def test_upper_bound_is_inclusive_and_no_lookahead():
    """上界闭合：标签正好等于快照的桶算进来；晚于快照的绝不进来。"""
    from backend.services.market_maker.core import seg_slice

    labels = [1 * B, 2 * B, 3 * B]
    i0, i1 = seg_slice(labels, 0, 2 * B)
    assert [int(x) for x in labels[i0:i1]] == [1 * B, 2 * B]
    assert 3 * B not in [int(x) for x in labels[i0:i1]], "不得前瞻到快照之后的桶"


def test_visibility_filter_matches_live_data_defect():
    """可见性：当时还没落库的桶不能用于判定（否则用了实盘看不到的成交）。

    实测数据层事实：成交桶按**落库时刻**分桶、**空桶不落行** ⇒ 15s 网格填充率仅
    47.5%、落库滞后 1~13s。实盘 tick 只能看到已落库的桶。
    """
    from backend.services.market_maker.core import seg_slice

    labels = [1 * B, 2 * B]
    created = [1 * B + 2_000, 2 * B + 12_000]     # 第二个桶 12s 后才落库
    # tick 在 2B 快照、滞后 8.8s（= 实测数据龄中位）⇒ 第二个桶还没落库
    i0, i1 = seg_slice(labels, 0, 2 * B, created_ms=created, tick_wall_ms=2 * B + 8_800)
    assert [int(x) for x in labels[i0:i1]] == [1 * B]
    # 再滞后 3.2s（下一轮）就该能看到它
    i0, i1 = seg_slice(labels, 1 * B, 2 * B, created_ms=created,
                       tick_wall_ms=2 * B + 12_000)
    assert [int(x) for x in labels[i0:i1]] == [2 * B]


def test_portfolio_replay_uses_shared_seg_slice():
    """源码契约：回放必须调用共用的 `seg_slice`，且不再有 `prev_win`（闭合双桶）。"""
    import inspect
    from backend.services.market_maker import portfolio_replay as pr

    src = inspect.getsource(pr.replay_portfolio)
    assert "seg_slice(" in src, "必须使用共用分片函数（否则实盘/回放口径会再次分叉）"
    assert "prev_win" not in src, "旧的『上一段缓存（两端闭合）』口径必须彻底移除"
    assert "seg_wm" in src, "必须有按币的成交桶水位（半开下界）"
    assert "created_ms=" in src and "tick_wall_ms=" in src, "必须有可见性过滤"


def test_live_runner_window_is_same_shape():
    """实盘窗口同构：`timestamp > :lo AND timestamp <= :hi`（半开下界、闭合上界）。"""
    import inspect
    from backend.services.market_maker import runner as mmrunner

    _tgt = mmrunner.ShadowRunner.fetch_market
    src = inspect.getsource(_tgt)
    assert "timestamp > :lo AND timestamp <= :hi" in src, "实盘窗口必须半开下界"
    assert "FILTER (WHERE timestamp + :bk > :qts)" in src, "必须有挂单存续期过滤"
    assert "last_seg_ms" in src, "水位必须落在运行态里"


def test_single_symbol_replay_pairs_quote_with_same_bucket():
    """单标的 replay.py 是正确参照：同轮内先算挂单、再用同段成交判定。"""
    import inspect
    from backend.services.market_maker import replay as rp

    src = inspect.getsource(rp)
    i_q = src.index("q = compute_quote(")
    i_fill = src.index("side = fill_side(bid=q.bid")
    assert i_q < i_fill, "挂单必须先于成交判定（同一轮内）"
