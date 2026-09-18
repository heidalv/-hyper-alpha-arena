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


def test_split_keys_keep_the_legacy_fallback():
    """拆键必须保留"回退旧共用键"的语义（未迁移的部署行为不变）。

    轮100 立键时两车道默认值都等于旧共用值；轮101 阶段2 **有意**把长线的
    入场止损倍数改成 3.0 —— 所以这里不再断言"两车道默认相等"，
    改为断言**回退机制仍在**：settings 里的表达式必须是
    `os.getenv("..._LANE") or os.getenv("共用键")`。
    """
    src = (ROOT / "backend" / "config" / "settings.py").read_text(encoding="utf-8")
    for key in ("MIDLONG_ATR_SL_MULT_MID", "MIDLONG_ATR_SL_MULT_LONG",
                "MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_MID",
                "MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_LONG"):
        seg = src[src.index(f"{key}: "):][:400]        # 固定窗口，别按 ")" 切（会切在半截）
        assert f'os.getenv("{key}")' in seg, f"{key} 未优先读车道专属键"
        assert ('os.getenv("MIDLONG_ATR_SL_MULT"' in seg
                or 'os.getenv("MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC"' in seg), (
            f"{key} 丢了旧共用键回退 ⇒ 未迁移部署会静默改行为"
        )


def test_mid_side_value_is_unchanged_by_stage2():
    """阶段2 只该改长线（与中线）；中线两项维持原值，避免顺手改到中线。"""
    import os
    from backend.config import settings as _st
    assert _st.MIDLONG_ATR_SL_MULT_MID == pytest.approx(
        float(os.getenv("MIDLONG_ATR_SL_MULT", "1.5")))
    assert _st.MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_MID == int(
        os.getenv("MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC", "14400"))


def test_separation_report_shape():
    rep = lp.separation_report()
    assert len(rep["rows"]) == len(lp.MUST_BE_INDEPENDENT)
    assert rep["still_shared_keys"] == [], f"仍有字段共用来源键: {rep['still_shared_keys']}"
    # 报告要能回答"哪些字段两车道当前同值"（这是调参清单）
    assert isinstance(rep["merged_fields"], list)
    assert rep["shared_key_baseline"] == lp.SHARED_FALLBACK_BASELINE


# ══════════════════════════════════════════════════════════════════
# 阶段2：**值分离**（轮101）
# ══════════════════════════════════════════════════════════════════


def test_stage2_values_are_lane_specific():
    """三处最荒谬的共用值必须已按车道分开（`.env` 层面）。"""
    mid, lng = lp.policy_for(lp.LANE_MID), lp.policy_for(lp.LANE_LONG)
    # ① 入场止损 ATR 倍数：中线 1.5 / 长线 3.0（与 Chandelier 同口径）
    assert mid.entry_sl_atr_mult == pytest.approx(1.5)
    assert lng.entry_sl_atr_mult == pytest.approx(3.0)
    # ② 中线恢复自己的时间尺度（原被 .env 拉成与长线相同的 7 天）
    assert mid.max_hold_sec == 48 * 3600, f"中线 max_hold={mid.max_hold_sec/3600}h，应恢复 48h"
    assert lng.max_hold_sec == 168 * 3600
    assert mid.max_hold_sec != lng.max_hold_sec


def test_stage2_keys_declared_in_env_file():
    env = (ROOT / ".env").read_text(encoding="utf-8", errors="replace")
    for key, val in (("MIDLONG_ATR_SL_MULT_MID", "1.5"),
                     ("MIDLONG_ATR_SL_MULT_LONG", "3.0"),
                     ("MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_MID", "14400"),
                     ("MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_LONG", "14400"),
                     ("TIER_MID_MAX_HOLD_SEC", "172800")):
        m = re.search(rf"^{key}=(\S+)", env, re.M)
        assert m, f"{key} 未在 .env 声明"
        assert m.group(1) == val, f"{key}={m.group(1)}，期望 {val}"


def test_long_lane_gets_a_wider_stop_than_mid():
    """结构止损位置必须按车道不同（长线更宽，给趋势呼吸空间）。"""
    from backend.services.mlto.midlong_trade_design import apply_structure_atr_floor
    mid_sl, _ = apply_structure_atr_floor(sl_pct=0.06, atr_1d_pct=0.03, tier="mid")
    lng_sl, _ = apply_structure_atr_floor(sl_pct=0.06, atr_1d_pct=0.03, tier="long")
    assert mid_sl == pytest.approx(0.06), "中线不该被抬升（地板 4.5% < 6%）"
    assert lng_sl == pytest.approx(0.09), f"长线应抬到 3×ATR=9%，实得 {lng_sl:.2%}"
    assert lng_sl > mid_sl


def test_per_trade_risk_is_lane_invariant():
    """**关键不变式**：止损位置可以按车道不同，但每笔风险必须同量级。

    这条是我在轮101 差点做错的地方：最初把 `atr_size_multiplier` 的风险标尺
    也一起按车道放大（ref = 3×ATR），于是宽止损换不来缩仓 ——
    长线每笔风险 9%、中线 4.5%，**翻倍**。风险标尺必须独立于结构止损倍数。
    """
    from backend.services.mlto.midlong_trade_design import (
        apply_structure_atr_floor,
        atr_size_multiplier,
    )
    risks = {}
    for tier in ("mid", "long"):
        sl, _ = apply_structure_atr_floor(sl_pct=0.06, atr_1d_pct=0.03, tier=tier)
        mult, _ = atr_size_multiplier(sl_pct=sl, atr_1d_pct=0.03, tier=tier)
        risks[tier] = sl * mult
    assert risks["mid"] == pytest.approx(risks["long"], rel=1e-6), (
        f"两车道每笔风险不一致: {risks}（宽止损必须换来缩仓）"
    )
    assert risks["mid"] == pytest.approx(0.045, rel=1e-6)


def test_risk_yardstick_does_not_follow_the_lane():
    """源码守卫：风险标尺不得按车道取值。

    若有人把 `atr_size_multiplier` 里的 `risk_ref_atr_mult()` 又改成
    `atr_sl_mult_for(lane)`，本测试变红 —— 那正是会让风险翻倍的改动。
    """
    src = (ROOT / "backend" / "services" / "mlto" / "midlong_trade_design.py").read_text(encoding="utf-8")
    body = src[src.index("def atr_size_multiplier("):]
    body = body[: body.index("def apply_structure_atr_floor(")]
    live = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
    assert "risk_ref_atr_mult()" in live
    assert "atr_sl_mult_for(" not in live, (
        "风险标尺跟了车道的结构止损倍数 ⇒ 宽止损换不来缩仓，风险随止损膨胀"
    )


def test_risk_yardstick_key_is_registered():
    from backend.config import settings as _st
    from backend.config import env_registry as er
    assert hasattr(_st, "MIDLONG_RISK_REF_ATR_MULT")
    assert "MIDLONG_RISK_REF_ATR_MULT" in er.KNOWN_FLAGS


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
