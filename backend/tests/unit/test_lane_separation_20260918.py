# -*- coding: utf-8 -*-
"""轮100 回归：**中线与长线必须被当作两条车道**（用户 2026-09-19 指出的架构错误）。

## 用户原话

> 中线和长线周期是两个概念，但是你把它们合并为中长线一并设计，这是非常严重的错误。

## 实测的"合并"规模

| 事实 | 数字 |
|---|---|
| 名字带 `midlong`/`mlto` 的服务模块 | **24 个**（约 1.2 万行） |
| 这些模块里的 `tier` 引用 | **324 处** |
| 其中"长线例外"式补丁（`tier=='long'` / `_LONG`） | **22 处** |
| `MIDLONG_*` 配置键 | **195 个**（带车道后缀仅 31 个） |

结构与周期上两者根本不是一回事：

    中线 mid    12–48h   1h/4h     出场：分档TP + 保本 + 回撤 + 追踪
    长线 long   3–7 天   4h/1d/1w  出场：规则失效 + Chandelier（+ 滚仓）

"一套栈 + 22 处长线例外"必然持续漏补丁 —— 本轮前两轮的事故就是漏出来的：
`tighten_trailing`（轮96）与 ATR 分段止盈阶梯（轮99）都曾**同时**作用于两条车道。

## 本测试守什么

1. `lane_policy` 是唯一的车道策略真源，且**两车道的策略字段来源键不同**；
2. 设计意图（`RECOMMENDED`）里，两车道在周期类字段上**必须不同**；
3. **棘轮**：仍回退读共用键的字段只许减不许增（新增共用键 ⇒ 测试红）；
4. 消费方（`paper_trading_engine` / `midlong_position_manager`）必须走真源，
   不得再各自维护一份 `(tier, nature)` 元组；
5. 行为不变：本轮只立真源与拆键，**不改变任何生效值**。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import lane_policy as lp  # noqa: E402

_PTE = ROOT / "backend" / "services" / "paper_trading_engine.py"
_MPM = ROOT / "backend" / "services" / "full_auto" / "midlong_position_manager.py"


def _live(src: str) -> str:
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


# ══════════════════════════════════════════════════════════════════
# 真源本身
# ══════════════════════════════════════════════════════════════════


def test_lane_of_maps_the_two_lanes_only():
    assert lp.lane_of(tier="mid") == lp.LANE_MID
    assert lp.lane_of(nature="swing") == lp.LANE_MID
    assert lp.lane_of(tier="long") == lp.LANE_LONG
    assert lp.lane_of(nature="trend_follow") == lp.LANE_LONG
    assert lp.lane_of(nature="position") == lp.LANE_LONG
    # 短线/研究不走本模块（它们有自己的车道语义）
    assert lp.lane_of(nature="scalp") is None
    assert lp.lane_of(tier="short") is None
    assert lp.lane_of() is None


def test_ordering_is_nature_first_like_lane_semantics():
    """判定顺序必须与报告层真源 `lane_semantics.resolve_lane` 一致（nature 优先）。

    这条在轮100 接线时**真的抓到过一次回归**：`lane_of` 最初写成 tier 优先，
    于是 `(tier=mid, nature=trend_follow)` 被判成中线（而轮99 的用例明确要求
    它算长线 —— "nature 说了算"）。顺序不统一 = 同一个仓位在不同模块归属不同。
    """
    from backend.config.lane_semantics import resolve_lane
    # nature 命中时压过 tier
    assert lp.lane_of(tier="mid", nature="trend_follow") == lp.LANE_LONG
    assert lp.lane_of(tier="long", nature="swing") == lp.LANE_MID
    # 与 lane_semantics 的"nature 优先"同向（它是报告车道，值域不同，只比"谁说了算"）
    for tier, nature in (("mid", "swing"), ("long", "trend_follow"),
                         ("mid", "trend_follow"), ("long", "position")):
        by_nature = lp.lane_of(nature=nature)
        combined = lp.lane_of(tier=tier, nature=nature)
        assert combined == by_nature, f"{tier}/{nature} 的顺序与 nature 优先不一致"
        assert resolve_lane(nature, tier) == resolve_lane(nature, None), (
            f"lane_semantics 对该组合不是 nature 优先：{tier}/{nature}"
        )


def test_is_long_lane_is_the_single_membership_predicate():
    assert lp.is_long_lane(tier="long") is True
    assert lp.is_long_lane(nature="trend_follow") is True
    assert lp.is_long_lane(tier="mid", nature="swing") is False
    assert lp.is_long_lane() is False


def test_policy_rejects_unknown_lane():
    with pytest.raises(ValueError):
        lp.policy_for("scalp")


def test_mid_and_long_policies_are_distinct_objects():
    mid, lng = lp.policy_for(lp.LANE_MID), lp.policy_for(lp.LANE_LONG)
    assert mid is not lng
    assert mid.lane != lng.lane
    assert mid.exit_stack != lng.exit_stack, "两条车道的出场栈必须是不同的东西"
    assert mid.min_hold_sec != lng.min_hold_sec


# ══════════════════════════════════════════════════════════════════
# 必须独立的字段：来源键不同 + 推荐值不同
# ══════════════════════════════════════════════════════════════════


def test_every_must_be_independent_field_has_lane_specific_source():
    """**核心护栏**：这些字段的来源键必须按车道分开 —— 同键 = 又合并了。"""
    mid, lng = lp.policy_for(lp.LANE_MID), lp.policy_for(lp.LANE_LONG)
    bad = []
    for field in lp.MUST_BE_INDEPENDENT:
        ms, ls = mid.source_of(field), lng.source_of(field)
        if not ms or not ls or ms == ls:
            bad.append((field, ms, ls))
    assert not bad, f"以下字段两车道共用同一个配置来源（又合并了）: {bad}"


def test_recommended_design_intent_differs_per_lane():
    """设计意图表必须体现"周期是两个概念"：两车道在周期类字段上不同。"""
    mid, lng = lp.RECOMMENDED[lp.LANE_MID], lp.RECOMMENDED[lp.LANE_LONG]
    for field in ("review_interval_sec", "min_hold_sec", "max_hold_sec",
                  "tp_stages", "structural_stop", "entry_sl_atr_mult", "allow_add"):
        assert field in mid and field in lng, f"设计意图表缺字段 {field}"
        assert mid[field] != lng[field], (
            f"设计意图里 {field} 两车道相同（{mid[field]}）—— 周期概念被抹平了"
        )
    # 关键量级关系：长线的复查节奏必须明显慢于中线、持仓窗口必须明显更长
    assert lng["review_interval_sec"] > mid["review_interval_sec"]
    assert lng["min_hold_sec"] > mid["min_hold_sec"]
    assert lng["max_hold_sec"] > mid["max_hold_sec"]


def test_long_lane_is_the_only_one_allowed_to_add():
    """滚仓是长线的盈利手段；中线不做金字塔（设计意图层）。"""
    assert lp.RECOMMENDED[lp.LANE_LONG]["allow_add"] is True
    assert lp.RECOMMENDED[lp.LANE_MID]["allow_add"] is False
    assert lp.policy_for(lp.LANE_LONG).allow_add is True
    assert lp.policy_for(lp.LANE_MID).allow_add is False


# ══════════════════════════════════════════════════════════════════
# 棘轮：共用键只许减不许增
# ══════════════════════════════════════════════════════════════════


def test_shared_fallback_list_does_not_grow():
    assert len(lp.SHARED_FALLBACK_ALLOWLIST) <= lp.SHARED_FALLBACK_BASELINE, (
        f"仍回退读共用键的字段增至 {len(lp.SHARED_FALLBACK_ALLOWLIST)}"
        f"（基线 {lp.SHARED_FALLBACK_BASELINE}）—— 新加共用键就是重新合并两条车道"
    )


def test_shared_fallbacks_are_declared_and_lane_keys_exist():
    """仍在共用的字段必须①在白名单里②同时暴露车道专属键（迁移路径明确）。"""
    from backend.config import settings as _st
    mid, lng = lp.policy_for(lp.LANE_MID), lp.policy_for(lp.LANE_LONG)
    for field in lp.SHARED_FALLBACK_ALLOWLIST:
        key_mid = mid.source_of(field)
        key_long = lng.source_of(field)
        assert key_mid.endswith("_MID") and key_long.endswith("_LONG"), (
            f"{field} 的车道键命名不规范: {key_mid} / {key_long}"
        )
        assert hasattr(_st, key_mid) and hasattr(_st, key_long), (
            f"{field} 的车道专属键未在 settings 声明 ⇒ env 覆盖静默失效"
        )


def test_new_lane_keys_are_registered_in_env_registry():
    from backend.config import env_registry as er
    for k in ("MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_MID",
              "MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_LONG",
              "MIDLONG_ATR_SL_MULT_MID",
              "MIDLONG_ATR_SL_MULT_LONG"):
        assert k in er.KNOWN_FLAGS, f"{k} 未登记 KNOWN_FLAGS"


# ══════════════════════════════════════════════════════════════════
# 行为不变（第一阶段只立真源，不动生效值）
# ══════════════════════════════════════════════════════════════════


def test_split_keys_default_to_the_legacy_shared_values():
    """拆键不能顺手改行为：专属键未设置时 = 原共用键的当前值。"""
    import os
    from backend.config import settings as _st
    legacy_iv = int(os.getenv("MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC", "14400"))
    legacy_atr = float(os.getenv("MIDLONG_ATR_SL_MULT", "1.5"))
    assert _st.MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_MID == legacy_iv
    assert _st.MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_LONG == legacy_iv
    assert _st.MIDLONG_ATR_SL_MULT_MID == pytest.approx(legacy_atr)
    assert _st.MIDLONG_ATR_SL_MULT_LONG == pytest.approx(legacy_atr)


def test_separation_report_shape():
    rep = lp.separation_report()
    assert len(rep["rows"]) == len(lp.MUST_BE_INDEPENDENT)
    assert rep["still_shared_keys"] == [], f"仍有字段共用来源键: {rep['still_shared_keys']}"
    # 报告要能回答"哪些字段两车道当前同值"（这是调参清单）
    assert isinstance(rep["merged_fields"], list)
    assert rep["shared_key_baseline"] == lp.SHARED_FALLBACK_BASELINE


# ══════════════════════════════════════════════════════════════════
# 消费方必须走真源（不得各自维护一份车道元组）
# ══════════════════════════════════════════════════════════════════


def _uses_lane_policy(path: Path, func_name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == func_name), None)
    assert fn is not None, f"{path.name} 缺函数 {func_name}"
    src = ast.unparse(fn)
    return "lane_policy" in src or "is_long_lane" in src


def test_paper_engine_membership_uses_the_source_of_truth():
    assert _uses_lane_policy(_PTE, "_is_trend_lane_member"), (
        "paper_trading_engine 又自己写了一份 (tier, nature) 车道判断"
    )


def test_midlong_manager_uses_the_source_of_truth():
    src = _live(_MPM.read_text(encoding="utf-8"))
    seg = src[src.index("def manage_position("):]
    assert "lane_policy" in seg, "midlong_position_manager 未走车道真源"
    assert "is_long_lane" in seg


def test_cadence_is_lane_aware():
    """复查节奏必须按车道取（这是"周期是两个概念"最直接的落点）。"""
    src = _live(_MPM.read_text(encoding="utf-8"))
    seg = src[src.index("_llm_interval = _cfg_int"):]
    seg = seg[: seg.index("_last_llm = _last_llm_run_ts")]
    assert "lane_policy" in seg and "review_interval_sec" in seg, (
        "复查节奏仍在读两车道共用的 MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC"
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
