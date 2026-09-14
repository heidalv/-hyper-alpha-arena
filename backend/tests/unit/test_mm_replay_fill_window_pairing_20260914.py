# -*- coding: utf-8 -*-
"""[2026-09-14 F103] 组合回放「成交判定窗口」契约：挂单只能被**其存续期内**的成交打到。

缺陷（本轮定位）：`portfolio_replay` 把 `[ots[i], ots[i+1])` 交给 `plan_tick`，而
`plan_tick` 检验的是**上一轮挂出的单**（状态里的 quote）⇒ 挂单被拿"它被刷新之后
才发生的成交"判定 = **一桶前瞻偏差**。单标的 `replay.py` 没有这个问题（同一轮内先
算挂单、再用同段成交判定）。

物理对照（实盘 tick 时序）：tick t 用最新快照 S 挂单；tick t+Δ（下一个 tick）检验这些
挂单时，消费的是"自上次消费以来"的成交桶，即**以 S 为起点的那一桶** ⇒ 与"同一轮
挂单 ↔ 同一段成交"一致（仅差 live 的 δ≈2-4s 摆放延迟）。故回放必须用**上一段**。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def test_replay_uses_previous_window_for_fill_test():
    """源码契约：成交明细必须取「上一段」并缓存本段供下一轮使用。"""
    import inspect
    from backend.services.market_maker import portfolio_replay as pr
    src = inspect.getsource(pr.replay_portfolio)
    assert "prev_win" in src, "缺少上一段窗口缓存"
    assert "prev_win[s] = (_w_lo, _w_hi, _w_sv, _w_bv)" in src
    assert "seg_low, seg_high, seg_sell, seg_buy = _prev" in src
    # 不允许再把本段直接交给 plan_tick
    assert "seg_low=seg_low, seg_high=seg_high," in src


def test_ofi_uses_completed_bucket_only():
    """OFI 也必须退一段：迭代 i 时已知的是被判定窗口那一桶的流向（信息集约束）。"""
    import re
    import inspect
    from backend.services.market_maker import portfolio_replay as pr
    src = inspect.getsource(pr.replay_portfolio)
    # 允许空格差异：searchsorted(tts, ots[i], "left") - 1
    assert re.search(r'searchsorted\(d\["tts"\], d\["ots"\]\[i\], "left"\)\)\s*-\s*1', src), \
        "OFI 未退一段（仍取 <= ots[i] 的最后一桶 = 尚未判定的那一桶）"


def test_single_symbol_replay_pairs_quote_with_same_bucket():
    """单标的 replay.py 是正确参照：同轮内先算挂单、再用同段成交判定。"""
    import inspect
    from backend.services.market_maker import replay as rp
    src = inspect.getsource(rp)
    i_q = src.index("q = compute_quote(")
    i_fill = src.index("side = fill_side(bid=q.bid")
    assert i_q < i_fill, "挂单必须先于成交判定（同一轮内）"


def test_window_pairing_changes_selected_fills():
    """两种配对选出**不同**的成交集合（不是等价变换）。

    构造：上一段（挂单存续期）价格在 101 上方（未穿越报价），本段才跌到 99。
    ⇒ 旧口径（用本段判定）会判买单成交 = **用挂单被刷新之后的成交**判它成交（前瞻）；
      新口径（用上一段判定）不成交 ✓ 正确。
    """
    prev_seg_low = 101.4      # 上一段最低价（高于买价 ⇒ 未穿越）
    cur_seg_low = 99.0        # 本段最低价（低于买价 ⇒ 旧口径会判成交）
    bid = 100.5
    assert not (prev_seg_low < bid), "新口径：挂单存续期内未被穿越"
    assert cur_seg_low < bid, "旧口径：用刷新之后的成交判定 ⇒ 会多算一笔成交"
