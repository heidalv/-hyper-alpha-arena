"""lane_policy — **中线 / 长线两条车道的策略真源**（轮100，2026-09-19）。

## 为什么必须存在这个模块

用户 2026-09-19 指出：

> 中线和长线周期是两个概念，但是你把它们合并为中长线一并设计，这是非常严重的错误。

**用户是对的。** 实测这个仓库的现状：

| 事实 | 数字 |
|---|---|
| 名字里带 `midlong`/`mlto` 的服务模块 | **24 个**（约 1.2 万行） |
| 这些模块里的 `tier` 引用 | **324 处** |
| 其中"长线例外"式补丁（`tier=='long'` / `_LONG`） | **22 处** |
| `MIDLONG_*` 配置键 | **195 个** |
| 带车道后缀（`_MID`/`_LONG`/`_SHORT`）的 | **仅 31 个** |
| **两条车道共用同一个键的** | **164 个** |

也就是说：**一套"中长线"策略栈 + 22 处临时打的长线例外**。
这种结构必然持续出问题 —— 每轮修复都会发现"还有一处补丁没打上"：

- 轮96：`midlong_position_manager` 的 `tighten_trailing`（2×短周期ATR ≈1%）
  对**两条车道**都生效 ⇒ 长线趋势仓被 1% 回撤收割；
- 轮99：`paper_trading_engine._run_v2_protection` 的 ATR 阶梯是**全车道共用**的
  （参数按 regime 选，没有车道维度）⇒ 长线在 +3.8% 就被分批止盈；
- 轮99：车道自己声明的 `exit_policy.tp_stages`（中线 0.8/1.6/3.0、长线 8/15/25）
  **没有任何消费者** —— 合并口径把它架空了。

而中/长线在**周期上根本不是一回事**：

    中线 mid    设计持仓 12–48h   主看 1h/4h    出场：分档TP + 保本 + 回撤 + 追踪
    长线 long   设计持仓 3–7 天   主看 4h/1d/1w  出场：规则失效 + Chandelier（+ 滚仓）

同一个"复查节奏"（`MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC=14400`=4h）对中线是
"每 1/3 生命复查一次"，对长线是"每 1/40 生命复查一次" —— 一个节奏不可能同时服务两者。

## 本模块的定位（第一阶段：先立真源，再拆实现）

**只做一件事：把两条车道的策略差异变成"可读、可测、可单点修改"的声明**，
并提供**机械化的"合并检测"**（见 `MUST_BE_INDEPENDENT` 与
`backend/tests/unit/test_lane_separation_20260918.py`）。

- **不改变任何交易行为**：所有字段解析出的值 = 当前实际生效值；
- 目前仍共用的键被显式登记在 `SHARED_KEY_ALLOWLIST` 里（棘轮：只许减不许增），
  新加共用键会让测试变红；
- 每条车道的**设计意图值**写在 `RECOMMENDED` 里并断言两车道不同 ——
  这样"中线被拉成 7 天""长线止损用中线的 1.5×ATR"这类事故有据可查。

后续阶段（需用户确认后执行）见 `reports/_轮100_中线与长线分离_架构诊断与第一阶段_20260919.md`。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# ── 车道标识 ─────────────────────────────────────────────────────────────
LANE_MID = "mid"
LANE_LONG = "long"
LANES: Tuple[str, ...] = (LANE_MID, LANE_LONG)

# 每条车道的引擎侧标签（与 position_construction.normalize_lane / lane_semantics 一致）
LANE_TIER: Dict[str, str] = {LANE_MID: "mid", LANE_LONG: "long"}
LANE_NATURES: Dict[str, Tuple[str, ...]] = {
    LANE_MID: ("swing", "intraday"),
    LANE_LONG: ("trend_follow", "position"),
}


def lane_of(*, tier: object = None, nature: object = None) -> Optional[str]:
    """把 (tier, nature) 归到 mid / long；无法判定返回 None。

    ## 判定顺序：**nature 优先**（与 `lane_semantics.resolve_lane` 同规则）

    `trade_nature` 是**执行层在开仓时写入的行为标签**，比 `timeframe_tier`（档位字段，
    历史上被多处改写）更贴近"这个仓位实际是什么"。轮63 建的报告层车道真源
    `lane_semantics.resolve_lane` 也是这个顺序（nature 命中 → 用 nature；
    nature 未命中而 tier 命中 → 用 tier）。

    实测（account 14 近 60 天 2403 行）：`tier` 与 `nature` 在车道归属上
    **100% 自洽**（只有 short/scalp、short/intraday、mid/swing、long/trend_follow、
    long/position 五种组合）⇒ 本顺序对现有数据**不产生任何改判**，
    只是把"谁优先"这件事固定下来，避免各模块各写一份。

    与 `lane_semantics` 的分工：那个是**报告/风控**层的车道真源
    （intraday / trend / research，其中 `swing` 归 intraday）；本函数只回答
    "由哪个**持仓管理器**负责"，所以 `swing → mid`、`trend_follow/position → long`。
    """
    t = str(tier or "").strip().lower()
    n = str(nature or "").strip().lower()
    # ① nature 优先
    for lane, nats in LANE_NATURES.items():
        if n in nats:
            return lane
    # ② nature 缺失/未识别 → 看 tier
    for lane, _t in LANE_TIER.items():
        if t == _t:
            return lane
    return None


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, "") or default)
    except Exception:
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(float(os.getenv(key, "") or default))
    except Exception:
        return default


# ── 每条车道的策略声明 ───────────────────────────────────────────────────
@dataclass(frozen=True)
class LanePolicy:
    """一条车道的完整策略身份（**中线与长线必须分别实例化**）。"""

    lane: str
    label: str

    # ① 归属：谁负责持仓管理 / 出场
    manager: str
    exit_stack: str

    # ② 节奏（周期概念的直接体现：中线与长线的时间尺度不同）
    review_interval_sec: int          # 持仓复查节奏
    ai_tick_sec: int                  # AI 决策 tick

    # ③ 持仓窗口
    min_hold_sec: int
    max_hold_sec: int

    # ④ 出场参数
    tp_stages: Tuple[float, ...]
    structural_stop: str              # price / chandelier
    trailing_activation_pct: Optional[float]
    trailing_callback_pct: Optional[float]

    # ⑤ 止损口径
    entry_sl_atr_mult: float          # 入场止损的 ATR 倍数基准
    init_sl_max_pct: float            # 入场止损距离上限
    tighten_min_band_pct: float       # 收紧后的最小带宽（防贴市价）
    min_lock_profit_pct: float        # 拒付追踪派生止损时的最小锁定利润

    # ⑥ 组合与准入
    max_open_positions: int
    corr_cluster_max: int
    ai_min_conf: float

    # ⑦ 加仓（滚仓）
    allow_add: bool
    add_min_pnl_pct: float

    # ⑧ 元信息：每个字段的**配置来源键**（用于机械检测"是否仍在共用"）
    sources: Dict[str, str]

    def source_of(self, field: str) -> str:
        return self.sources.get(field, "")

    def identity(self) -> Dict[str, object]:
        return {
            "lane": self.lane, "label": self.label, "manager": self.manager,
            "exit_stack": self.exit_stack,
            "review_interval_sec": self.review_interval_sec,
            "ai_tick_sec": self.ai_tick_sec,
            "min_hold_sec": self.min_hold_sec, "max_hold_sec": self.max_hold_sec,
            "tp_stages": list(self.tp_stages), "structural_stop": self.structural_stop,
            "trailing_activation_pct": self.trailing_activation_pct,
            "trailing_callback_pct": self.trailing_callback_pct,
            "entry_sl_atr_mult": self.entry_sl_atr_mult,
            "init_sl_max_pct": self.init_sl_max_pct,
            "tighten_min_band_pct": self.tighten_min_band_pct,
            "min_lock_profit_pct": self.min_lock_profit_pct,
            "max_open_positions": self.max_open_positions,
            "corr_cluster_max": self.corr_cluster_max,
            "ai_min_conf": self.ai_min_conf,
            "allow_add": self.allow_add, "add_min_pnl_pct": self.add_min_pnl_pct,
        }


# ── 仍然"回退读共用键"的字段（棘轮白名单：只许减，不许增）────────────────
# 这些字段已经**暴露了车道专属键**（`_MID` / `_LONG`），但为避免改行为，
# 在专属键未设置时仍回退到旧的共用键。测试断言此清单只减不增。
SHARED_FALLBACK_ALLOWLIST: Tuple[str, ...] = (
    "review_interval_sec",   # MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC
    "entry_sl_atr_mult",     # MIDLONG_ATR_SL_MULT
)
SHARED_FALLBACK_BASELINE = len(SHARED_FALLBACK_ALLOWLIST)

# 已废弃的旧名（保留仅为让"谁在共用"可枚举、可检测）
LEGACY_SHARED_KEYS: Tuple[str, ...] = (
    "MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC",
    "MIDLONG_ATR_SL_MULT",
)

# 必须按车道独立的字段（合并检测清单）。测试会断言：两车道的**来源键不同**、
# 且不存在"两车道解析到同一个值却本该不同"的字段。
MUST_BE_INDEPENDENT: Tuple[str, ...] = (
    "review_interval_sec", "tp_stages", "structural_stop",
    "trailing_activation_pct", "trailing_callback_pct",
    "entry_sl_atr_mult", "init_sl_max_pct", "tighten_min_band_pct",
    "min_lock_profit_pct", "min_hold_sec", "max_hold_sec", "exit_stack",
    "ai_min_conf", "corr_cluster_max",
)

# 设计意图值（**不是**当前生效值）：用于说明"两条车道应该差在哪"。
# 测试断言两车道在此表上逐项不同 —— 若有人把它们写成一样，测试变红。
RECOMMENDED: Dict[str, Dict[str, object]] = {
    LANE_MID: {
        "label": "中线",
        "review_interval_sec": 3600 * 4,        # 4h：中线 12–48h，复查须占其生命的 1/3~1/12
        "min_hold_sec": 3600 * 12,
        "max_hold_sec": 3600 * 48,              # 中线不该是 7 天（当前 .env 把它拉到了 7 天）
        "tp_stages": (0.8, 1.6, 3.0),
        "structural_stop": "price",
        "entry_sl_atr_mult": 1.5,
        "allow_add": False,                     # 中线不做金字塔滚仓
    },
    LANE_LONG: {
        "label": "长线趋势",
        "review_interval_sec": 3600 * 24,       # 24h：长线 3–7 天，日线节奏
        "min_hold_sec": 3600 * 72,
        "max_hold_sec": 3600 * 168,
        "tp_stages": (8.0, 15.0, 25.0),
        "structural_stop": "chandelier",
        "entry_sl_atr_mult": 3.0,               # Chandelier 3×ATR20(日线) 口径
        "allow_add": True,                      # 滚仓是长线的主要盈利手段
    },
}


def _mid_policy() -> LanePolicy:
    from backend.config.settings import (
        MIDLONG_AI_MIN_CONF as _mid_conf,
        MIDLONG_CORR_CLUSTER_MAX as _mid_cluster,
        MIDLONG_MAX_OPEN_POSITIONS as _mid_open,
        MIDLONG_MIN_LOCK_PROFIT_PCT_MID as _mid_lock,
        MIDLONG_SL_MAX_PCT_MID as _mid_slmax,
        MIDLONG_TIGHTEN_MIN_BAND_PCT_MID as _mid_band,
        TIER_MID_AI_TICK_SEC as _mid_tick,
        TIER_PROTECTION_PARAMS as _tpp,
    )
    from backend.services.exit.exit_policy import ExitPolicy

    pol = ExitPolicy.for_lane("mid")
    prot = _tpp.get("mid") or {}
    return LanePolicy(
        lane=LANE_MID,
        label="中线",
        manager="full_auto.midlong_position_manager",
        exit_stack="staged_tp+breakeven+drawdown+trailing",
        review_interval_sec=_env_int("MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_MID",
                                     _env_int("MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC", 14400)),
        ai_tick_sec=int(_mid_tick),
        min_hold_sec=int(prot.get("min_hold_sec") or 43200),
        max_hold_sec=int(prot.get("max_hold_sec") or 172800),
        tp_stages=tuple(pol.tp_stages or ()),
        structural_stop=str(pol.structural_stop or "price"),
        trailing_activation_pct=pol.trailing_activation_pct,
        trailing_callback_pct=pol.trailing_callback_pct,
        entry_sl_atr_mult=_env_float("MIDLONG_ATR_SL_MULT_MID",
                                     _env_float("MIDLONG_ATR_SL_MULT", 1.5)),
        init_sl_max_pct=float(_mid_slmax),
        tighten_min_band_pct=float(_mid_band),
        min_lock_profit_pct=float(_mid_lock),
        max_open_positions=int(_mid_open),
        corr_cluster_max=int(_mid_cluster),
        ai_min_conf=float(_mid_conf),
        allow_add=False,
        add_min_pnl_pct=float(_env_float("MIDLONG_POSITION_MGMT_PYRAMID_DIRECT_PNL", 0.05)),
        sources={
            "review_interval_sec": "MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_MID",
            "tp_stages": "EXIT_POLICY_MID_TP_STAGES|exit_policy.LaneDefaults.mid",
            "structural_stop": "exit_policy.LaneDefaults.mid",
            "trailing_activation_pct": "EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT",
            "trailing_callback_pct": "EXIT_POLICY_MID_TRAILING_CALLBACK_PCT",
            "entry_sl_atr_mult": "MIDLONG_ATR_SL_MULT_MID",
            "init_sl_max_pct": "MIDLONG_SL_MAX_PCT_MID",
            "tighten_min_band_pct": "MIDLONG_TIGHTEN_MIN_BAND_PCT_MID",
            "min_lock_profit_pct": "MIDLONG_MIN_LOCK_PROFIT_PCT_MID",
            "min_hold_sec": "TIER_MID_MIN_HOLD_SEC",
            "max_hold_sec": "TIER_MID_MAX_HOLD_SEC",
            "exit_stack": "lane_policy:mid",
            "ai_min_conf": "MIDLONG_AI_MIN_CONF",
            "corr_cluster_max": "MIDLONG_CORR_CLUSTER_MAX",
        },
    )


def _long_policy() -> LanePolicy:
    from backend.config.settings import (
        MIDLONG_AI_MIN_CONF_LONG as _long_conf,
        MIDLONG_CORR_CLUSTER_MAX_LONG as _long_cluster,
        MIDLONG_MAX_LONG_LANE_POSITIONS as _long_open,
        MIDLONG_MIN_LOCK_PROFIT_PCT_LONG as _long_lock,
        MIDLONG_SL_MAX_PCT_LONG as _long_slmax,
        MIDLONG_TIGHTEN_MIN_BAND_PCT_LONG as _long_band,
        TIER_LONG_AI_TICK_SEC as _long_tick,
        TIER_PROTECTION_PARAMS as _tpp,
    )
    from backend.services.exit.exit_policy import ExitPolicy

    pol = ExitPolicy.for_lane("long")
    prot = _tpp.get("long") or {}
    return LanePolicy(
        lane=LANE_LONG,
        label="长线趋势",
        manager="services.trend_e1_engine(+Chandelier) / full_auto.midlong_position_manager(仅滚仓)",
        exit_stack="rule_invalidation+chandelier",
        review_interval_sec=_env_int("MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_LONG",
                                     _env_int("MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC", 14400)),
        ai_tick_sec=int(_long_tick),
        min_hold_sec=int(prot.get("min_hold_sec") or 259200),
        max_hold_sec=int(prot.get("max_hold_sec") or 604800),
        tp_stages=tuple(pol.tp_stages or ()),
        structural_stop=str(pol.structural_stop or "chandelier"),
        trailing_activation_pct=pol.trailing_activation_pct,
        trailing_callback_pct=pol.trailing_callback_pct,
        entry_sl_atr_mult=_env_float("MIDLONG_ATR_SL_MULT_LONG",
                                     _env_float("MIDLONG_ATR_SL_MULT", 1.5)),
        init_sl_max_pct=float(_long_slmax),
        tighten_min_band_pct=float(_long_band),
        min_lock_profit_pct=float(_long_lock),
        max_open_positions=int(_long_open),
        corr_cluster_max=int(_long_cluster),
        ai_min_conf=float(_long_conf),
        allow_add=True,
        add_min_pnl_pct=float(_env_float("MIDLONG_POSITION_MGMT_PYRAMID_DIRECT_PNL", 0.05)),
        sources={
            "review_interval_sec": "MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_LONG",
            "tp_stages": "EXIT_POLICY_LONG_TP_STAGES|exit_policy.LaneDefaults.long",
            "structural_stop": "exit_policy.LaneDefaults.long",
            "trailing_activation_pct": "EXIT_POLICY_LONG_TRAILING_ACTIVATION_PCT",
            "trailing_callback_pct": "EXIT_POLICY_LONG_TRAILING_CALLBACK_PCT",
            "entry_sl_atr_mult": "MIDLONG_ATR_SL_MULT_LONG",
            "init_sl_max_pct": "MIDLONG_SL_MAX_PCT_LONG",
            "tighten_min_band_pct": "MIDLONG_TIGHTEN_MIN_BAND_PCT_LONG",
            "min_lock_profit_pct": "MIDLONG_MIN_LOCK_PROFIT_PCT_LONG",
            "min_hold_sec": "TIER_LONG_MIN_HOLD_SEC",
            "max_hold_sec": "TIER_LONG_MAX_HOLD_SEC",
            "exit_stack": "lane_policy:long",
            "ai_min_conf": "MIDLONG_AI_MIN_CONF_LONG",
            "corr_cluster_max": "MIDLONG_CORR_CLUSTER_MAX_LONG",
        },
    )


_CACHE: Dict[str, LanePolicy] = {}


def policy_for(lane: str) -> LanePolicy:
    """取某条车道的策略（带进程内缓存；配置热调请调 `clear_cache()`）。"""
    key = str(lane or "").strip().lower()
    if key not in LANES:
        raise ValueError(f"未知车道: {lane!r}（只有 {LANES}；research/short 不走本模块）")
    if key not in _CACHE:
        _CACHE[key] = _mid_policy() if key == LANE_MID else _long_policy()
    return _CACHE[key]


def resolve(*, tier: object = None, nature: object = None) -> Optional[LanePolicy]:
    """按 (tier, nature) 取车道策略；不属于 mid/long 时返回 None。"""
    lane = lane_of(tier=tier, nature=nature)
    return policy_for(lane) if lane else None


def is_long_lane(*, tier: object = None, nature: object = None) -> bool:
    """是否长线车道 —— **唯一**的长线归属判定（替代散落的 tier=='long' 判断）。"""
    return lane_of(tier=tier, nature=nature) == LANE_LONG


def clear_cache() -> None:
    _CACHE.clear()


def separation_report() -> Dict[str, object]:
    """两车道逐字段对照（供 CLI / 测试 / 报告使用）。"""
    mid, lng = policy_for(LANE_MID), policy_for(LANE_LONG)
    rows = []
    for field in MUST_BE_INDEPENDENT:
        mv, lv = mid.identity().get(field), lng.identity().get(field)
        rows.append({
            "field": field,
            "mid": mv, "long": lv,
            "same_value": (mv == lv),
            "mid_source": mid.source_of(field), "long_source": lng.source_of(field),
            "independent_keys": mid.source_of(field) != lng.source_of(field),
            "recommended_mid": RECOMMENDED[LANE_MID].get(field),
            "recommended_long": RECOMMENDED[LANE_LONG].get(field),
        })
    return {
        "rows": rows,
        "shared_keys": list(SHARED_FALLBACK_ALLOWLIST),
        "shared_key_baseline": SHARED_FALLBACK_BASELINE,
        "legacy_shared_keys": list(LEGACY_SHARED_KEYS),
        "merged_fields": [r["field"] for r in rows if r["same_value"]],
        "still_shared_keys": [r["field"] for r in rows if not r["independent_keys"]],
    }


if __name__ == "__main__":  # pragma: no cover
    import json

    rep = separation_report()
    print("=== 中线 vs 长线 逐字段对照 ===")
    for r in rep["rows"]:
        flag = "  ← 两车道同值" if r["same_value"] else ""
        keys = "独立键" if r["independent_keys"] else "**仍共用键**"
        print(f'  {r["field"]:26s} mid={str(r["mid"]):>12}  long={str(r["long"]):>12}  [{keys}]{flag}')
    print()
    print("仍共用的键（棘轮白名单）:", rep["shared_keys"])
    print("同值的字段:", rep["merged_fields"] or "无")
    print("仍共用键的字段:", rep["still_shared_keys"] or "无")
    print(json.dumps({"baseline": rep["shared_key_baseline"]}, ensure_ascii=False))
