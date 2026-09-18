# -*- coding: utf-8 -*-
"""轮99 回归：长线趋势仓必须按**趋势**对待，不能套用日内单的 ATR 止盈阶梯。

## 用户反馈（2026-09-18）

> 深度检查和修复止盈止损，尤其是长线单，他按照日内单对待，这样完全发挥不出长线趋势的作用。
> 长线趋势是滚仓盈利为目的的。

## 取证（account 14 近 30 天，`timeframe_tier='long'` 共 48 笔）

| 指标 | 实测 | 车道设计 |
|---|---|---|
| 中位持仓 | **13.56h** | 24–168h（`settings.TIER_PROTECTION_PARAMS["long"]`） |
| 中位实现 | **+0.53%** | 档位 8/15/25%、止损 −3%~−16%（Chandelier） |
| 平均加仓次数 | **0.06** | 滚仓（pyramid）是趋势车道的主要盈利手段 |
| 出场事件 | `staged_tp1` 在 **+3.83%**、`staged_tp2` 在 **+5.80%** | 声明档位 `tp_stages=[8,15,25]` |

## 根因：一条"长线跳过日内保护"的规则**从未生效**

`paper_trading_engine` 里写着（注释）：

    if _v2_unified_on and not _v2_long_managed:
        # [2026-08-24 long_trend_v2] V2 长线仓跳过统一分段止盈/保本/硬软回撤/追踪

而判定是：

    _v2_long_managed = long_v2_enabled() and (nature 是长线 or tier == long)

`long_v2_enabled()` 读的是 **入场闸** `LONG_TREND_V2`（`.env` 现为 **0**），
且主脑接管（`MIDLONG_BRAIN_MODE`）时会**再否决一次** ⇒ 两个条件都指向 False
⇒ "长线跳过"对任何长线仓均不成立 ⇒ 长线仓照样跑中短线口径：

1. ATR(1h) 阶梯分批止盈：`tp1_mult=2.0` / `tp2_mult=3.0`（`REGIME_TP_PARAMS`，**按 regime 而非车道**）；
2. 每档后把止损拖到 `entry + 0.8×ATR`（≈成本上方 1%~2%）；
3. TP1 后软回撤 `>2×ATR` 直接全平；TP3 后 `peak − 2×ATR` 追踪；
4. 名义 < $30 时更狠：**TP1 触发即全仓清掉**（自动降档）。

⇒ 一次 2% 级正常回撤就把整笔趋势仓按"微利"清掉（4 笔 long 收在
+1.14%/+2.05%/+2.11%/+4.33%，`reason=breakeven_tp`），
而车道声明的 8%/15%/25% 永远到不了。

## 修法

把"是否属趋势车道"与**入场闸开关彻底解耦**：车道成员身份只由仓位自身
`(tier, nature, entry_source)` 决定；成员 ⇒ 跳过全部中短线口径保护，
只留硬层（liquidation / failsafe SL·TP / max_hold）与车道自己的
Chandelier / 规则失效退出。回滚：`EXIT_TREND_LANE_SKIP_INTRADAY=false`。
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.paper_trading_engine import paper_engine  # noqa: E402

_PTE = ROOT / "backend" / "services" / "paper_trading_engine.py"


class _Pos:
    def __init__(self, tier=None, nature=None, es="{}", pid=1, symbol="ETH", side="long"):
        self.id = pid
        self.symbol = symbol
        self.side = side
        self.timeframe_tier = tier
        self.trade_nature = nature
        self.exit_state_json = es


def _live(src: str) -> str:
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


# ══════════════════════════════════════════════════════════════════
# 车道成员判定（核心）
# ══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("tier,nature,expected", [
    ("long", "trend_follow", True),
    ("long", "position", True),
    ("long", None, True),
    (None, "trend_follow", True),
    ("mid", "trend_follow", True),          # nature 说了算（历史脏数据）
    ("mid", "swing", False),
    ("short", "scalp", False),
    ("research", "pair_research", False),
    (None, None, False),
])
def test_trend_lane_membership_matrix(tier, nature, expected):
    assert paper_engine._is_trend_lane_member(_Pos(tier, nature)) is expected


def test_e1_position_is_member_regardless_of_labels():
    """E1 契约（唯一出场 = Chandelier）优先于 tier/nature 字段。"""
    p = _Pos("mid", "swing", '{"entry_source": "trend_e1"}')
    assert paper_engine._is_trend_lane_member(p) is True


def test_membership_does_not_depend_on_entry_gate_switch(monkeypatch):
    """**这条是本轮缺陷的回归护栏**：

    旧实现用 `long_v2_enabled()`（读 `LONG_TREND_V2` 入场闸 + 主脑否决）当车道判据，
    于是 `LONG_TREND_V2=0` 时长线仓被判成"非长线"，日内阶梯照跑。

    断言：把入场闸相关的开关全部置为**关闭**，长线仓**仍然**是趋势车道成员。
    """
    monkeypatch.setenv("LONG_TREND_V2", "0")
    monkeypatch.setenv("MIDLONG_BRAIN_MODE", "on")
    p = _Pos("long", "trend_follow")
    assert paper_engine._is_trend_lane_member(p) is True
    assert paper_engine._should_run_unified_staged_tp(p) is False

    # 反向：确认在**当前部署配置**（LONG_TREND_V2=0）下，入场闸确实是关的
    from backend.services.long_trend_v2 import long_v2_enabled
    assert long_v2_enabled() is False, (
        "若此断言失败说明 .env 改了 LONG_TREND_V2 —— 正是它当年让长线被按日内对待"
    )


def test_source_does_not_use_entry_gate_for_lane_membership():
    """源码守卫：车道判定不得再引用 `long_v2_enabled`。

    用 AST 找**真实代码引用**（`Name`/`Attribute` 的标识符），而不是文本匹配 ——
    该函数的 docstring 里正引用着旧写法来解释根因，文本匹配会命中说明文字
    （本项目已多次踩过这个坑）。
    """
    tree = ast.parse(_PTE.read_text(encoding="utf-8"))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_is_trend_lane_member"),
        None,
    )
    assert fn is not None, "找不到 _is_trend_lane_member"
    idents: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            idents.append(node.id)
        elif isinstance(node, ast.Attribute):
            idents.append(node.attr)
    assert not any("long_v2_enabled" in i for i in idents), (
        f"车道成员判定又被绑回入场闸了（引用: {[i for i in idents if 'long_v2' in i]}）"
        " —— 入场闸是入场闸，车道是车道"
    )


# ══════════════════════════════════════════════════════════════════
# 闸门接线
# ══════════════════════════════════════════════════════════════════


def test_trend_lane_skips_intraday_staged_tp():
    assert paper_engine._should_run_unified_staged_tp(_Pos("long", "trend_follow")) is False
    assert paper_engine._should_run_unified_staged_tp(_Pos("mid", "swing")) is True
    assert paper_engine._should_run_unified_staged_tp(_Pos("short", "scalp")) is True


def test_ladder_gate_uses_the_helper():
    live = _live(_PTE.read_text(encoding="utf-8"))
    assert "if _v2_unified_on and self._should_run_unified_staged_tp(pos):" in live, (
        "统一分段止盈的闸门必须走统一判定，不得再写成别的变量"
    )


def test_other_intraday_blocks_are_gated_by_lane_membership():
    """另外 5 处"长线跳过"也必须挂在同一个判定上（否则又是一半跳一半不跳）。"""
    live = _live(_PTE.read_text(encoding="utf-8"))
    assert live.count("not _v2_long_managed") >= 4
    assert "_v2_long_managed = self._is_trend_lane_member(pos, _pos_tier, _pos_nature)" in live
    # ExitPolicy 层 / profit_manager / DSM / 固定TP / PEO 各自都在 gate 之内
    for anchor in ("if not _v2_long_managed:",
                   "if _min_hold_ok and not _v2_long_managed:",
                   "if pos.tp_price and not _v2_long_managed:",
                   "if not _use_peo and not _v2_long_managed:"):
        assert anchor in live, f"缺少 gate: {anchor}"


def test_hard_layer_is_not_gated():
    """硬层（爆仓 / 强平超时）**不得**被车道跳过 —— 趋势仓也必须有兜底。

    否则"跳过日内保护"会变成"没有任何保护"。
    """
    live = _live(_PTE.read_text(encoding="utf-8"))
    seg = live[live.index("def _run_v2_protection"):]
    _tail = seg.find("def _run_unified_staged_tp")
    if _tail > 0:
        seg = seg[:_tail]
    # 爆仓兜底：以代码锚点（`hit_liq`）定位，不用注释文字
    liq_idx = seg.index("hit_liq")
    assert "liquidation" in seg[liq_idx: liq_idx + 900]
    # 强平超时在最前面调用（不受车道判定影响）
    assert "_enforce_max_hold_timeout(db, pos)" in seg
    assert "_v2_long_managed" not in seg[: seg.index("_enforce_max_hold_timeout(db, pos)")], (
        "硬层不得位于车道跳过之后"
    )


def test_skip_is_logged_once_per_position(caplog):
    """跳过必须留日志：否则"按契约交给 Chandelier"与"保护失效"无法区分。"""
    import logging
    paper_engine._TREND_SKIP_LOGGED.clear()
    p = _Pos("long", "trend_follow", pid=424242)
    with caplog.at_level(logging.INFO, logger="backend.services.paper_trading_engine"):
        paper_engine._log_trend_lane_skip_once(p, True)
        paper_engine._log_trend_lane_skip_once(p, True)
    hits = [r.message for r in caplog.records if "趋势车道" in r.message]
    assert len(hits) == 1, f"应只记一次，实际 {len(hits)}"
    assert "Chandelier" in hits[0]
    paper_engine._TREND_SKIP_LOGGED.clear()


def test_rollback_switch_restores_old_behaviour(monkeypatch):
    """回滚开关关掉 ⇒ 所有车道共享 ATR 阶梯（旧行为）。"""
    from backend.config import settings as _st
    monkeypatch.setattr(_st, "EXIT_TREND_LANE_SKIP_INTRADAY", False, raising=False)
    p = _Pos("long", "trend_follow")
    assert paper_engine._is_trend_lane_member(p) is False
    assert paper_engine._should_run_unified_staged_tp(p) is True


def test_rollback_switch_is_declared_and_registered():
    from backend.config import settings as _st
    from backend.config import env_registry as er
    assert hasattr(_st, "EXIT_TREND_LANE_SKIP_INTRADAY")
    assert _st.EXIT_TREND_LANE_SKIP_INTRADAY is True, "默认必须是「趋势车道跳过日内保护」"
    assert "EXIT_TREND_LANE_SKIP_INTRADAY" in er.KNOWN_FLAGS


# ══════════════════════════════════════════════════════════════════
# 口径一致性：车道声明 vs 实际执行的阶梯
# ══════════════════════════════════════════════════════════════════


def test_long_lane_declared_stages_are_trend_scale():
    """车道声明的档位必须是趋势量级（≥5%），证明 ATR 阶梯（≈1%~4%）与声明脱节。"""
    from backend.services.exit.exit_policy import ExitPolicy
    pol = ExitPolicy.for_lane("long")
    assert pol.tp_stages, "长线车道必须声明分档"
    assert min(pol.tp_stages) >= 5.0, f"长线首档 {min(pol.tp_stages)}% 太小，不是趋势量级"
    assert pol.trailing_activation_pct is None, "长线车道声明里不应有 trailing"
    assert pol.structural_stop == "chandelier"


def test_atr_ladder_is_lane_agnostic_by_design_hence_must_not_run_on_trend():
    """ATR 阶梯的参数是**按 regime** 选的（不是按车道）——这正是它不能用在趋势车道的原因。"""
    params = paper_engine.REGIME_TP_PARAMS
    assert set(params.keys()) == {"trending", "ranging", "extreme"}, (
        f"REGIME_TP_PARAMS 竟然带了车道维度？那本测试的前提需要重写: {sorted(params)}"
    )
    for regime, p in params.items():
        assert p["tp1_mult"] <= 3.0, f"{regime} tp1_mult={p['tp1_mult']} 是日内量级（ATR 倍数）"


def test_probe_numbers_recorded_in_code():
    """把实测口径写进代码，避免下一个人又把它当"正常分批止盈"。"""
    src = _PTE.read_text(encoding="utf-8")
    assert "13.56h" in src and "+0.53%" in src and "+3.83%" in src, (
        "轮99 的实测数据必须留在注释里（否则结论会被时间冲掉）"
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
