"""AI 中线候选：看板 midlong approve 主源 + 兜底。"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_midlong_board_approve_filters_fixed_and_min_conf():
    """[调研轮16 2026-09-16 更新] 桩需返回**真实查询的 5 列**。

    轮10 把候选口径改成"窗口内每个 symbol 最近一次判定"，SQL 返回
    `(sym, confidence, verdict, liquidity, created_at)`；旧桩只给 2 列，
    于是生产代码读 `r[2]`（verdict）时 IndexError —— 桩比 SQL 旧，
    属于"测试没跟上口径"，不是生产缺陷。同时补上轮10 新增的两条语义
    （verdict 候选集 + 流动性下限）的断言。
    """
    from backend.services import auto_coin_selector as m

    db = MagicMock()
    # 顺序 = SQL：upper(symbol), confidence, verdict, liquidity, created_at
    db.execute.return_value.all.return_value = [
        ("BTC", 0.90, "approve", 0.99, None),   # 固定币 → 剔除
        ("TON", 0.80, "approve", 0.85, None),   # 保留（conf 最高）
        ("XMR", 0.65, "watch", 0.70, None),     # watch ∈ 候选集（轮10）
        ("AAA", 0.55, "approve", 0.90, None),   # conf < min_conf → 剔除
        ("JUNK", 0.95, "approve", 0.20, None),  # 流动性 0.20 < 0.5 → 剔除
        ("REJ", 0.95, "reject", 0.95, None),    # verdict ∉ 候选集 → 剔除
    ]
    out = m._midlong_board_approve_candidates(
        db, fixed={"BTC", "ETH"}, min_conf=0.60, min_liquidity=0.5,
    )
    syms = [s for s, _ in out]
    assert "BTC" not in syms, "固定币必须被剔除"
    assert syms[0] == "TON", "按 confidence 降序，最高分在前"
    assert "XMR" in syms, "watch 属于候选集（只认 approve = 永久空池）"
    assert "JUNK" not in syms, "流动性低于下限的标的不该进候选"
    assert "REJ" not in syms, "reject 不进候选池"
    assert set(syms) == {"TON", "XMR"}


def test_force_adopt_ai_mid_writes_sticky(tmp_path, monkeypatch):
    from backend.services import auto_coin_selector as m

    monkeypatch.setattr(m, "_ai_mid_sticky_path", lambda sid: str(tmp_path / f"{sid}.json"))
    monkeypatch.setattr(
        m,
        "get_session_mid_ai_config",
        lambda sid, db=None: {"enabled": True, "max_slots": 3},
    )
    monkeypatch.setattr(
        m,
        "get_fixed_symbols_for_session",
        lambda sid, db=None, tier=None: {"BTC", "ETH"},
    )

    r = m.force_adopt_ai_mid_symbol("fa_test", "TON", max_slots=3)
    assert r["success"] is True
    assert r["ai_mid_watch"][0] == "TON"

    sticky = m._load_ai_mid_sticky("fa_test")
    assert sticky["symbols"][0] == "TON"
    assert "manual_adopt" in sticky["reason"]

    # fixed mid: skip occupying AI mid slot
    r2 = m.force_adopt_ai_mid_symbol("fa_test", "BTC", max_slots=3)
    assert r2["success"] is True
    assert r2.get("skipped") == "already_fixed_mid"


def test_get_ai_mid_prefers_board_over_stale_auto_coin_sticky(tmp_path, monkeypatch):
    from backend.services import auto_coin_selector as m

    monkeypatch.setattr(m, "_ai_mid_sticky_path", lambda sid: str(tmp_path / f"{sid}.json"))
    # 旧 sticky：来自短线池，即使未过期也应失效
    m._save_ai_mid_sticky(
        "fa_test", ["DOGE", "ENA"],
        reason="resample age>=10800s from auto_coin",
    )
    monkeypatch.setattr(
        m,
        "get_session_mid_ai_config",
        lambda sid, db=None: {"enabled": True, "max_slots": 3},
    )
    monkeypatch.setattr(
        m,
        "get_fixed_symbols_for_session",
        lambda sid, db=None, tier=None: {"BTC"},
    )
    monkeypatch.setattr(
        m, "count_open_ai_mid_positions",
        lambda db=None, account_id=None, exclude_symbols=None, include_symbols=None: 0,
    )
    # [2026-09-27 R4 修测试桩②] 09-18 起落盘前多了一道 `filter_tradeable_ai_symbols`
    # （单一事实源：以"写过滤后实际落盘的集合"为准返回）。单测环境没有目录/DB，
    # 该过滤器会把整池清空（sticky 里留下 `kept=0 evicted=[...] added=[...]`），
    # 于是断言恒空。这里把它替换成恒等函数 —— 本测试要验的是**看板主源胜出**，
    # 不是目录过滤（目录过滤另有专测）。
    import backend.services.ai_coin_unified as _acu

    monkeypatch.setattr(_acu, "filter_tradeable_ai_symbols", lambda syms: list(syms))

    class _FakeResult:
        def __init__(self, rows=None, scalar=None, first=None):
            self._rows = rows or []
            self._scalar = scalar
            self._first = first

        def all(self):
            return self._rows

        def first(self):
            return self._first

        def scalar(self):
            return self._scalar

    def _execute(sql, params=None):
        q = str(sql)
        # [2026-09-27 R4 修测试桩] 轮123（2026-09-20 提交 19ff62c）起，代码用候选表
        # `MAX(created_at)` 判「看板换代」。本桩写于 09-16，未给该查询标量 ⇒
        # `float(None)` → TypeError → 被外层 except 吞掉 → 候选恒空、本测试自 09-20 起**假红**。
        # 这里补上标量：看板时间晚于 sticky ⇒ 触发"换代即重采"，正是本测试要断言的路径。
        if "EXTRACT(EPOCH" in q:
            return _FakeResult(scalar=time.time() + 60)
        if "paper_account_id" in q:
            return _FakeResult(first=(14,))
        if "timeframe_tier = 'mid'" in q and "DISTINCT" in q:
            return _FakeResult(rows=[])
        if "coin_select_candidates" in q:
            # [调研轮16 2026-09-16] 列数必须与真实 SQL 一致：
            # upper(symbol), confidence, verdict, liquidity, created_at
            # （旧的 2 列桩会在读 verdict 时 IndexError，异常被吞成"查询失败"→ 空候选）
            return _FakeResult(rows=[("TON", 0.7, "approve", 0.90, None),
                                     ("XMR", 0.65, "watch", 0.80, None)])
        return _FakeResult()

    db = MagicMock()
    db.execute.side_effect = _execute

    picked = m.get_ai_mid_candidates_for_session("fa_test", db=db, max_slots=3)
    assert picked == ["TON", "XMR"]
    sticky = m._load_ai_mid_sticky("fa_test")
    assert "midlong_board" in sticky["reason"]
    # [调研轮8] 落盘前会经 AiCoinUnified 目录过滤（不可交易的 symbol 会被丢弃，
    # 实测日志：`[AiCoinUnified] drop not-in-catalog: ['XMR']`）。因此只断言
    # **看板主源胜出**（TON 在列且来源正确），不再要求被目录过滤掉的 XMR 也在 sticky 里。
    assert "TON" in sticky["symbols"]
