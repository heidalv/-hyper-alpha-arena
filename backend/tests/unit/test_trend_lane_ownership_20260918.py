# -*- coding: utf-8 -*-
"""轮102 阶段3 回归：长线车道的**决策归属**必须只有一个定义处。

## 背景（用户 2026-09-19）

> 中线和长线周期是两个概念，但是你把它们合并为中长线一并设计，这是非常严重的错误。

轮100 立了车道真源（`config/lane_policy.py`）、轮101 做了值分离；
本轮的**归属分离**解决"长线允许做什么"散落在 2091 行 `midlong_position_manager`
（95 处 tier 引用、4 处长线例外）里的问题 —— 那条路径靠 `if tier == "long"` 逐处打补丁，
而每漏一处就是一次事故：

    漏 tighten_trailing → 4 笔趋势仓被 1% 回撤收割（轮96）
    漏 reduce           → #4659 被减 6 次、size 只剩 2.7%（轮99 取证）
    漏统一分段止盈       → 长线 +3.83% 就被分批止盈（轮99）

现在"长线允许做什么"由 `full_auto/trend_lane_manager.py` 的纯函数 `decide()` 唯一决定。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.full_auto import trend_lane_manager as tlm  # noqa: E402

_MPM = ROOT / "backend" / "services" / "full_auto" / "midlong_position_manager.py"
_TLM = ROOT / "backend" / "services" / "full_auto" / "trend_lane_manager.py"


def _live(src: str) -> str:
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


# ══════════════════════════════════════════════════════════════════
# 决策本身
# ══════════════════════════════════════════════════════════════════


def test_rule_invalidation_is_the_only_price_based_exit():
    d = tlm.decide(thesis_reason="thesis_invalidation")
    assert d.action == tlm.ACT_THESIS_EXIT and d.allowed is True
    assert d.detail.get("rule_based") is True


def test_discretionary_close_must_pass_the_confirm_gate():
    """LLM 裁量的 should_close 不能直接放行 —— 长线最短持仓 72h，必须过 F39 闸。"""
    d = tlm.decide(thesis_reason="thesis_should_close")
    assert d.action == tlm.ACT_THESIS_EXIT
    assert d.detail.get("discretionary") is True
    assert d.detail.get("requires_confirm_gate") is True


@pytest.mark.parametrize("action", ["tighten_trailing", "reduce"])
def test_mid_lane_tools_are_forbidden_on_the_trend_lane(action):
    """这两个动作正是 2026-09-18 事故的直接机制，必须硬禁止。"""
    d = tlm.decide(review_action=action)
    assert d.action == tlm.ACT_HOLD
    assert d.allowed is False
    assert d.detail.get("rejected_action") == action
    assert tlm.is_forbidden(action) is True


def test_pyramid_is_allowed_and_is_the_profit_engine():
    """用户明确要求：长线趋势是滚仓盈利为目的的。"""
    assert tlm.decide(pyramid_action="add", pnl_pct=0.02).action == tlm.ACT_PYRAMID
    assert tlm.decide(rule_passthrough=True, pnl_pct=0.06).action == tlm.ACT_PYRAMID
    # 浮亏不加仓（滚仓是"顺势"，不是摊平）
    assert tlm.decide(pyramid_action="add", pnl_pct=-0.01).action == tlm.ACT_HOLD
    # 未达阈值的规则直通不加仓
    assert tlm.decide(rule_passthrough=True, pnl_pct=0.01,
                      pyramid_min_pnl_pct=0.05).action == tlm.ACT_HOLD


def test_dca_is_not_a_trend_lane_tool():
    d = tlm.decide(dca_action="dca")
    assert d.action == tlm.ACT_HOLD and d.allowed is False


def test_forbidden_wins_over_pyramid():
    """同时给出禁止动作与加仓信号时，禁止优先（安全侧）。"""
    d = tlm.decide(review_action="tighten_trailing", pyramid_action="add", pnl_pct=0.10)
    assert d.action == tlm.ACT_HOLD and d.allowed is False


def test_default_is_hold_not_exit():
    """没有任何信号时必须持有 —— 长线的默认不是"找理由平仓"。"""
    d = tlm.decide()
    assert d.action == tlm.ACT_HOLD and d.allowed is True


def test_allowed_action_set_is_documented():
    assert set(tlm.allowed_actions()) == {"hold", "pyramid", "thesis_exit"}


def test_decide_is_pure():
    """纯函数：同样输入必须同样输出（无时间/随机/DB）。"""
    for _ in range(3):
        assert tlm.decide(pyramid_action="add", pnl_pct=0.03) == \
            tlm.decide(pyramid_action="add", pnl_pct=0.03)


def test_module_has_no_db_or_broker_imports():
    """决策模块不得直接碰 DB / 下单（执行仍在既有 helper 里）。"""
    tree = ast.parse(_TLM.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    banned = [m for m in imported
              if any(k in m for k in ("sqlalchemy", "database", "paper_trading_engine",
                                      "live_executor", "session"))]
    assert not banned, f"决策模块引入了执行层依赖: {banned}"


# ══════════════════════════════════════════════════════════════════
# 接线：长线的归属必须走真源
# ══════════════════════════════════════════════════════════════════


def test_manager_consults_the_trend_lane_decision_module():
    live = _live(_MPM.read_text(encoding="utf-8"))
    assert "trend_lane_manager" in live, "长线决策未走归属模块"
    assert "from backend.services.full_auto.trend_lane_manager import decide" in live


def test_manager_no_longer_hardcodes_the_forbidden_actions():
    """`manage_position` 里的长线禁令必须来自归属模块，而不是又写一遍字面量判断。

    允许保留的：调用点自身的 gate（`_trend_lane_pos`），
    但**禁止**在长线分支里再出现一套独立的 action 白/黑名单。
    """
    live = _live(_MPM.read_text(encoding="utf-8"))
    seg = live[live.index("def manage_position("):]
    seg = seg[: seg.index("def ", 10)] if "\ndef " in seg else seg
    # 归属模块的禁止集合必须来自模块，不得在本地再定义一份
    assert "FORBIDDEN_ACTIONS" not in seg, (
        "本地又定义了一份长线禁令 —— 应当 import trend_lane_manager"
    )
    # 而 lane 相关的 gate 仍应存在（它们控制"是否跳过中线口径"，与本模块互补）
    assert "_trend_lane_pos" in seg


def test_decision_module_is_importable_without_side_effects():
    """纯决策模块必须能在没有 DB/交易所的环境下直接 import。"""
    import importlib
    mod = importlib.import_module("backend.services.full_auto.trend_lane_manager")
    assert hasattr(mod, "decide")


# ══════════════════════════════════════════════════════════════════
# 24 个 midlong_* 模块的归属**棘轮**（只许减不许增）
# ══════════════════════════════════════════════════════════════════


MERGED_MODULES_BASELINE = 16   # 2026-09-19 实测：24 个模块里 16 个"两车道共用"
MIDLONG_MODULES_BASELINE = 24
MERGED_LINES_BASELINE = 10870  # 那 16 个模块的行数合计


def _midlong_modules():
    import re
    out = []
    for p in (ROOT / "backend" / "services").rglob("*.py"):
        if "__pycache__" in str(p):
            continue
        if re.search(r"midlong|mid_long|mlto", p.name):
            out.append(p)
    return sorted(out)


def test_midlong_module_count_does_not_grow():
    mods = _midlong_modules()
    assert len(mods) <= MIDLONG_MODULES_BASELINE, (
        f"名字带 midlong/mlto 的模块增至 {len(mods)}（基线 {MIDLONG_MODULES_BASELINE}）—— "
        f"新的车道专属逻辑应当放进 mid_*/trend_* 模块，不要再挂到合并命名空间下"
    )


def test_merged_module_count_does_not_grow():
    """棘轮：真正"两车道共用"的模块数只许减。

    判据：模块里同时存在 tier 分支与"长线例外"补丁 —— 那就是"一套栈 + 打补丁"的形态。
    """
    import re
    merged = []
    for p in _midlong_modules():
        src = p.read_text(encoding="utf-8", errors="ignore")
        tier = len(re.findall(r"timeframe_tier|_tier_of\(|\btier\b", src))
        adhoc = len(re.findall(
            r'tier\s*==\s*["\']long["\']|tier\s*in\s*\([^)]*long|_LONG\b', src))
        if tier > 0 and adhoc > 0:
            merged.append(p.name)
    assert len(merged) <= MERGED_MODULES_BASELINE, (
        f"「两车道共用 + 长线补丁」的模块增至 {len(merged)}（基线 {MERGED_MODULES_BASELINE}）: {merged}"
    )


def test_ownership_audit_is_recorded():
    """24 个模块的归属判定必须留档（否则下一轮又要重新数一遍）。"""
    rep = ROOT / "reports" / "_轮102_车道归属分离与trend_lane_manager_20260919.md"
    assert rep.is_file(), "缺阶段3 的归属判定报告"
    text = rep.read_text(encoding="utf-8")
    for name in ("midlong_position_manager.py", "midlong_helpers.py",
                 "midlong_executor.py", "mlto_cycle.py"):
        assert name in text, f"归属报告缺模块 {name}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
