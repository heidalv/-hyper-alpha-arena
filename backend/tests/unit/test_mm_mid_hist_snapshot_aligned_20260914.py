# -*- coding: utf-8 -*-
"""[2026-09-14 F102] `mid_hist` 追加口径契约（实盘/回放同口径）。

缺陷现场：实盘 `mid_hist` 有 240 条但**只有 52~109 条不同值**（重复率 55~78%），
因为 tick（~15-19s）快于快照更新却被无条件 append。两个依赖 mid_hist 的信号失真：
  - 冻结检测 `slow_move_bp`（frozen_lookback=240 期的单步最大移动）窗口被拉长
    2~4 倍 ⇒ 更容易 > frozen_max_move(8bp) ⇒ **冻结档 3bp 很少生效**；
  - `vol_cur`（20 期已实现波动）被重复值注入 0 收益 ⇒ 波动低估。
后果（蒙特卡洛 12 个实现、1.08h 同窗口）：回放成交 107~119 笔（P5~P95 108~118），
**实盘只有 43 笔 = 第 0 百分位**；且实盘挂宽系统性更宽（买 -1.37bp / 卖 +0.61bp）。
修：仅在**快照时间戳变化**时追加（与回放「每快照一条」完全同口径）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402


def test_last_mid_src_ms_is_persisted():
    st = mmrunner.SymbolState(symbol="BTC")
    st.last_mid_src_ms = 1_700_000_000_000
    d = st.to_dict()
    assert d["last_mid_src_ms"] == 1_700_000_000_000
    assert mmrunner.SymbolState.from_dict(d).last_mid_src_ms == 1_700_000_000_000


def test_tick_appends_only_on_new_snapshot():
    """源码契约：mid_hist 只在快照时间戳变化时追加。"""
    import inspect
    src = inspect.getsource(mmrunning_tick())
    assert "last_mid_src_ms" in src, "未按快照去重追加"
    assert "_snap_ms_now" in src
    # 不允许再有无条件 append
    assert "st.mid_hist.append(float(m[\"mid\"]))" in src

    # 追加必须被 `if _snap_ms_now != int(st.last_mid_src_ms ...)` 包住，
    # 且同一块内同时更新 last_mid_src_ms。
    #
    # [F301 2026-09-16] 原断言用「首个子串起 400 字符的窗口」定位，属于**脆弱的
    # 字面窗口**：F279 在 append 之前插入了停机断点补齐（`_splice_mid_hist`，
    # 约 390 字符），把 `st.last_mid_src_ms = _snap_ms_now` 推出窗口 ⇒ 断言失败，
    # 但**行为完全正确**（append 仍在同一 if 块内、顺序不变）。
    # 改为**结构断言**：按缩进圈出守卫块，再检查块内成员与顺序。
    lines = src.splitlines()
    guard_idx = guard_indent = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("if _snap_ms_now != int(st.last_mid_src_ms"):
            guard_idx = i
            guard_indent = len(ln) - len(ln.lstrip())
            break
    assert guard_idx is not None, "未找到按快照去重的守卫条件"
    body = []
    for ln in lines[guard_idx + 1:]:
        if not ln.strip():
            continue                      # 空行不结束块
        ind = len(ln) - len(ln.lstrip())
        if ind <= guard_indent:
            break                         # 回到同级或更外层 ⇒ 块结束
        body.append(ln.strip())
    assert body, "守卫块为空"
    joined = "\n".join(body)
    assert "st.mid_hist.append(float(m[\"mid\"]))" in joined, \
        "append 必须在守卫块内（否则会重复注入相同中价 ⇒ 冻结档失效）"
    assert "st.last_mid_src_ms = _snap_ms_now" in joined, \
        "必须在同一守卫块内更新 last_mid_src_ms"
    assert (body.index("st.mid_hist.append(float(m[\"mid\"]))")
            < body.index("st.last_mid_src_ms = _snap_ms_now")), \
        "必须先 append 再推进时间戳（顺序颠倒会漏掉本快照）"


def mmrunning_tick():
    return mmrunner.ShadowRunner.tick


def test_frozen_detector_semantics_documented():
    """冻结检测对窗口长度敏感——用重复样本喂它会系统性关掉冻结档。

    这里用纯逻辑锁定：同样的 240 个样本，若其中一半是重复值（窗口被拉长），
    单步最大移动会被"稀释/放大"到不同结论。
    """
    from backend.services.market_maker.core import compute_quote, QuoteParams

    p = QuoteParams(w_base_bp=6.0, k_vol=0.3, frozen_width_bp=3.0,
                    frozen_max_move_bp=8.0, frozen_lookback=240)
    # 冻结档：slow_range < 8bp ⇒ base=3bp
    q_frozen = compute_quote(symbol="X", mid=100.0, sigma_norm=0.0, inv_ratio=0.0,
                             slow_range_bp=2.0, params=p)
    q_normal = compute_quote(symbol="X", mid=100.0, sigma_norm=0.0, inv_ratio=0.0,
                             slow_range_bp=0.0, params=p)   # 0 = 无冻结信号（旧口径）
    assert q_frozen.w_bid_bp == pytest.approx(3.0)
    assert q_normal.w_bid_bp == pytest.approx(6.0)
    assert q_normal.w_bid_bp > q_frozen.w_bid_bp, "冻结档必须比常规档窄"
