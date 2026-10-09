# -*- coding: utf-8 -*-
"""[整顿轮·T16 2026-10-05] `maker_risk` 的触发比例必须可配，且关闭后仍有出口。

病根（实测，`lane_ledger`，T9 之后）`maker_risk` **全部 6 条腿**：

    ts        symbol     notional  spread_bp
    16:07:44  BTW            15.0     -64.42
    16:21:06  PLAY          988.5      +0.00
    16:48:28  PLAY          984.8      -5.19
    16:53:31  AAVE          977.8      -6.47
    17:13:22  LYN            15.1     -10.73
    17:27:05  MARSCOIN     1009.1     -15.87

⇒ **4/6 条成交在中价的错误一侧**，均 net_bp **-17.11**，净 **-$2.86**
（占同窗口总负贡献 39%）。

机理：该层在「浮亏达 stop×0.5」时挂对手价抢平，而**只有中价继续穿过挂单价
时才会成交** ⇒ 成交本身证明又多吃了 5~16bp 逆向移动。
即：**这一层只在被逆向选择时成交**。

锁定三条契约：
  1. `MM_MAKER_EXIT_FRAC` 可覆盖比例（A/B 与回滚）
  2. `=0` ⇒ 该层不触发，但仓位**仍有出口**（走 `maker_edge`，不是被卡住）
  3. 默认（不设 env）行为与历史一致（0.5）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FLOW_RULES = ROOT / "backend/services/market_maker/flow_rules.py"


def _choose(**over):
    from backend.services.market_maker.flow_rules import choose_exit
    kw = dict(qty=1.0, entry_px=100.0, bid=99.9, ask=100.1, now_ts=2000.0,
              opened_ts=1900.0, max_hold_sec=90.0, mu=-1.0, vol_300s_bp=10.0,
              regime="R1", book_stale=False, stop_floor_bp=15.0,
              stop_cap_bp=40.0, tp_bp=60.0)
    kw.update(over)
    return choose_exit(**kw)


class TestMakerExitFracTunable:
    def test_source_reads_env(self):
        src = FLOW_RULES.read_text(encoding="utf-8", errors="replace")
        assert "MM_MAKER_EXIT_FRAC" in src, "必须提供可配比例（A/B 与回滚依赖它）"

    def test_flow_rules_imports_os(self):
        """首版忘了 `import os` ⇒ `NameError`（与 T13 同型错误，已犯两次）。"""
        src = FLOW_RULES.read_text(encoding="utf-8", errors="replace")
        tree_lines = src.splitlines()
        has_os = any(ln.strip() == "import os" for ln in tree_lines[:40])
        assert has_os, "flow_rules.py 顶部必须 `import os`（否则 getenv 抛 NameError）"

    def test_default_behaviour_unchanged(self, monkeypatch):
        monkeypatch.delenv("MM_MAKER_EXIT_FRAC", raising=False)
        # stop = clamp(2*10,15,40) = 20bp；浮亏 -14bp 已越过 0.5*20=10bp
        assert _choose() == "maker_risk", "默认比例 0.5 的行为必须与历史一致"

    def test_off_does_not_block_exit(self, monkeypatch):
        """关闭该层 ⇒ 仓位必须仍能离场（走 maker_edge），不得卡住。"""
        monkeypatch.setenv("MM_MAKER_EXIT_FRAC", "0")
        got = _choose()
        assert got != "maker_risk", "=0 时该层不应触发"
        assert got in ("maker_edge", "maker_time", "make_take", "maker_take"), (
            f"关闭后必须有其它挂单出口，实际 {got!r} —— 不得把仓位卡死")

    def test_full_stop_threshold(self, monkeypatch):
        """=1.0 ⇒ 只有浮亏达**满止损**(20bp) 才挂单抢平。"""
        monkeypatch.setenv("MM_MAKER_EXIT_FRAC", "1.0")
        assert _choose() != "maker_risk", "浮亏 -14bp 未达满止损 20bp，不应触发"
        assert _choose(bid=99.7) == "maker_risk", "浮亏 -34bp 已越满止损，应触发"

    def test_bad_env_value_falls_back(self, monkeypatch):
        """脏 env 值不得让函数崩（退化为传入的默认参数）。"""
        monkeypatch.setenv("MM_MAKER_EXIT_FRAC", "not-a-number")
        assert _choose() in ("maker_risk", "maker_edge")
