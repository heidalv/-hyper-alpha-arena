"""lane_semantics — 交易车道语义单一真源（轮63 重构，2026-09-18）。

## 为什么需要这个模块

用户反馈（2026-09-18）：
> 周期报告存在很多问题，中线日内和长线趋势全部都乱了，哪个是哪个都分不出来。

根因不是渲染 bug，是**全库并存 7 套互相打架的周期词表**，同一个标签在不同层指不同的东西：

| 来源 | 词表 | 「日内波段」被归到 |
|---|---|---|
| `tp_sl_authority.TIER_TO_NATURE` | short/mid/long | mid=swing |
| `sub_position_manager.NATURE_TO_TIER` | scalp/intraday/swing/... | **short** ← 打架 |
| `sub_position_manager.NATURE_RULES` 标签 | — | scalp 与 intraday **都叫「日内」** ← 打架 |
| `position_construction.normalize_lane` | short/mid/long/research/arb | **short** ← 打架 |
| `agent-monitor/page.tsx` | 日内波段 / 长线趋势 | mid ✓ |
| 报告层 `period_daily_report._horizon_of` | scalp/midlong/long | midlong ✓ |
| `cycle_semantics`（因子层） | intraday/trend | midlong→trend ← 另一层，见下 |

后果实测（`paper_positions` 全库）：
    scalp        + short    3041 笔
    intraday     + short       3 笔      ← nature 叫 intraday，却和 scalp 同 tier
    swing        + mid       288 笔
    trend_follow + long       74 笔
    position     + long        7 笔

`scalp` 与 `intraday` 的 `NATURE_RULES` 标签**都是「日内」**，而 `swing` 的标签是「波段」——
于是「日内波段」这个词在代码里没有唯一所指，UI 说的「日内波段=中线」在引擎层根本对不上号。

## 本模块的定位

**交易的归交易，因子的归因子。**

- 本模块 = **持仓/报告/风控**层的车道真源（tier ↔ nature ↔ lane ↔ 展示名）。
- `backend/config/cycle_semantics.py` = **K线周期/因子前瞻**层的真源（15m/1h/4h/1d ↔ intraday/trend）。
  两者刻意分开：因子层的 `midlong` 指 4h 前瞻 24h，而持仓层的 `mid` 档实测中位持仓仅 4h，
  强行合并会把「因子前瞻期」与「实际持仓期」再次混为一谈（轮44 已量化该错配 ≈7.5×）。
  本模块通过 `report_timeframe` / `confirm_timeframes` 与 cycle_semantics 的周期档保持同一套周期词。

## 唯一口径（用户 2026-09-18 确认）

    车道 1  intraday  日内（含中线槽位）  primary 1h  confirm 15m/4h  ≤24h（实测中位 4.0h）
    车道 2  trend     长线趋势            primary 4h  confirm 1d/1w  24h–3d（设计 168h）

`scalp` 不再是独立车道：短线车道已停（2026-09-17），存量 scalp 仓位归入 `intraday`
（实测 scalp 中位持仓 0.95h，本就是日内行为）。`research` 是研究车道，不进交易报告。

## 兼容

`horizon` 这个旧名保留为 `lane` 的**别名**，历史枚举映射（读入方向）：
    scalp → intraday, midlong → intraday, long → trend,
    intraday → intraday, trend → trend, research → research
写入方向一律使用 `LANE_*` 规范值。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple

# ── 规范车道名（唯一真源）────────────────────────────────────────────────
LANE_INTRADAY = "intraday"   # 日内（含中线槽位）
LANE_TREND = "trend"         # 长线趋势
LANE_RESEARCH = "research"   # 研究车道（pair_research / research），不进交易报告

# 交易报告展示的车道（有序：日内在前）
REPORT_LANES: Tuple[str, ...] = (LANE_INTRADAY, LANE_TREND)
# 全部已知车道（含非交易车道）
ALL_LANES: Tuple[str, ...] = (LANE_INTRADAY, LANE_TREND, LANE_RESEARCH)


# ── 车道定义 ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class LaneSpec:
    """一条车道的完整身份。报告/UI 的「分不清」由此消除：身份随数据一起传给前端。"""

    lane: str
    label: str                    # 报告/卡片主标题
    label_full: str               # 带槽位的完整名（避免与旧「中线=长线」混淆）
    engine_lane: str              # position_construction.normalize_lane 的对应值
    tier: str                     # 权威 timeframe_tier
    nature: str                   # 权威 trade_nature
    # 该车道实际消费的 K 线周期（与 cycle_semantics 同一套周期词）
    report_timeframe: str         # 主看周期（报告标题里标出来）
    confirm_timeframes: Tuple[str, ...]
    expected_hold_hours: float    # 设计期望持仓
    hold_bracket_hours: Tuple[float, float]  # 期望持仓区间，报告用它做偏离告警
    actual_hold_hours: float      # 实测中位持仓（留痕，供报告对照）
    match_priority: int           # 解析歧义时的优先级（大者优先）

    def identity(self) -> Dict[str, object]:
        """序列化给报告 payload / API，让前端无需猜测周期身份。"""
        return {
            "lane": self.lane,
            "label": self.label,
            "label_full": self.label_full,
            "engine_lane": self.engine_lane,
            "tier": self.tier,
            "nature": self.nature,
            "report_timeframe": self.report_timeframe,
            "confirm_timeframes": list(self.confirm_timeframes),
            "expected_hold_hours": self.expected_hold_hours,
            "hold_bracket_hours": list(self.hold_bracket_hours),
            "actual_hold_hours": self.actual_hold_hours,
        }


LANE_SPECS: Dict[str, LaneSpec] = {
    LANE_INTRADAY: LaneSpec(
        lane=LANE_INTRADAY,
        label="日内",
        label_full="日内波段（中线槽位）",
        engine_lane="mid",
        tier="mid",
        nature="swing",
        report_timeframe="1h",
        confirm_timeframes=("15m", "4h"),
        expected_hold_hours=24.0,
        hold_bracket_hours=(0.5, 24.0),
        actual_hold_hours=4.0,
        match_priority=30,
    ),
    LANE_TREND: LaneSpec(
        lane=LANE_TREND,
        label="长线趋势",
        label_full="长线趋势",
        engine_lane="long",
        tier="long",
        nature="trend_follow",
        report_timeframe="4h",
        confirm_timeframes=("1d", "1w"),
        expected_hold_hours=168.0,
        hold_bracket_hours=(24.0, 168.0),
        actual_hold_hours=23.3,
        match_priority=20,
    ),
    LANE_RESEARCH: LaneSpec(
        lane=LANE_RESEARCH,
        label="研究",
        label_full="研究车道（非交易）",
        engine_lane="research",
        tier="research",
        nature="research",
        report_timeframe="1h",
        confirm_timeframes=(),
        expected_hold_hours=0.0,
        hold_bracket_hours=(0.0, 0.0),
        actual_hold_hours=0.0,
        match_priority=10,
    ),
}

# 默认车道：无法识别时的兜底。
# 选择理由：历史无标签仓位（trade_nature/timeframe_tier 双 NULL）绝大多数属短线/日内体系，
# 且报错方向应为「更短周期」而非把日内行为伪装成长期趋势（那正是本轮要根治的错）。
DEFAULT_LANE = LANE_INTRADAY


# ── 标签 → 车道 ─────────────────────────────────────────────────────────
# 存量/新增的一切 tier 与 nature 写法都在此收敛。新增写法只加在这里，其余模块不许再维护 if/elif。
NATURE_TO_LANE: Dict[str, str] = {
    "scalp": LANE_INTRADAY,        # 存量短线（车道已停，行为上仍是日内）
    "intraday": LANE_INTRADAY,
    "daytrade": LANE_INTRADAY,
    "day_trade": LANE_INTRADAY,
    "swing": LANE_INTRADAY,        # 中线槽位实测中位 4.0h ⇒ 日内
    "mid": LANE_INTRADAY,
    "midlong": LANE_INTRADAY,      # 旧报告枚举名
    "trend_follow": LANE_TREND,
    "position": LANE_TREND,        # 长线子类
    "trend": LANE_TREND,
    "long": LANE_TREND,
    "research": LANE_RESEARCH,
    "pair_research": LANE_RESEARCH,
    # 其它旧轨的叫法（原先各自散落在 position_construction / 各 executor 的 if/elif 里）
    "shortterm": LANE_INTRADAY,
    "short_term": LANE_INTRADAY,
    "midterm": LANE_INTRADAY,
    "mid_term": LANE_INTRADAY,
    "medium": LANE_INTRADAY,
    "longterm": LANE_TREND,
    "long_term": LANE_TREND,
    "trendfollow": LANE_TREND,
}

TIER_TO_LANE: Dict[str, str] = {
    "short": LANE_INTRADAY,
    "mid": LANE_INTRADAY,
    "long": LANE_TREND,
    "research": LANE_RESEARCH,
    "arb": LANE_RESEARCH,          # 套利中心已停，不进交易报告
    "arbitrage": LANE_RESEARCH,
    # 有些旧轨把 nature 名写进 tier
    "scalp": LANE_INTRADAY,
    "intraday": LANE_INTRADAY,
    "swing": LANE_INTRADAY,
    "trend_follow": LANE_TREND,
    "position": LANE_TREND,
    # `position_construction.normalize_lane` 支持的旧别名，收进来以便全库只此一份
    "shortterm": LANE_INTRADAY,
    "short_term": LANE_INTRADAY,
    "midterm": LANE_INTRADAY,
    "mid_term": LANE_INTRADAY,
    "medium": LANE_INTRADAY,
    "longterm": LANE_TREND,
    "long_term": LANE_TREND,
    "trend": LANE_TREND,
}

# 旧报告 horizon 枚举 → 新车道（读入兼容；写入一律用 LANE_*）
LEGACY_HORIZON_TO_LANE: Dict[str, str] = {
    "scalp": LANE_INTRADAY,
    "midlong": LANE_INTRADAY,
    "long": LANE_TREND,
    "short": LANE_INTRADAY,
    "mid": LANE_INTRADAY,
    "intraday": LANE_INTRADAY,
    "trend": LANE_TREND,
    "research": LANE_RESEARCH,
}

# 展示名 → 车道（前端 / 中文查询参数）
LABEL_TO_LANE: Dict[str, str] = {
    "日内": LANE_INTRADAY,
    "日内波段": LANE_INTRADAY,
    "中线": LANE_INTRADAY,
    "中线日内": LANE_INTRADAY,
    "短线": LANE_INTRADAY,
    "长线": LANE_TREND,
    "长线趋势": LANE_TREND,
    "趋势": LANE_TREND,
    "研究": LANE_RESEARCH,
}


def _norm(raw: object) -> str:
    return str(raw or "").strip().lower().replace("-", "_").replace(" ", "")


# 旧轨的 tier 写法 → 规范引擎车道名（short/mid/long/research/arb）。
# 注意方向：这是**引擎车道**，不是报告车道 —— `short` 含短线存量 + 日内，不等于报告层的「日内」。
# 存在原因：`position_construction.normalize_lane` 等旧轨把 nature 名写进 tier 字段，
# 这里就是那张「nature 名当成 tier 用」时的换算表。
LEGACY_TIER_ALIAS_TO_ENGINE_LANE: Dict[str, str] = {
    "scalp": "short",
    "intraday": "short",
    "shortterm": "short",
    "short_term": "short",
    "swing": "mid",
    "midterm": "mid",
    "mid_term": "mid",
    "medium": "mid",
    "trend": "long",
    "longterm": "long",
    "long_term": "long",
    "position": "long",
}

# 旧轨的 nature 写法 → 规范引擎车道名。
LEGACY_NATURE_ALIAS_TO_ENGINE_LANE: Dict[str, str] = {
    "scalp": "short",
    "intraday": "short",
    "swing": "mid",
    "trend_follow": "long",
    "position": "long",
    "research": "research",
    "pair_research": "research",
}


def normalize_engine_lane(tier: object = None, nature: object = None,
                          *, lanes: Tuple[str, ...] = ("short", "mid", "long", "research", "arb"),
                          default: str = "mid") -> str:
    """把任意旧写法归到**引擎车道名**（short/mid/long/research/arb）。

    判定顺序与原 `position_construction.normalize_lane` 完全一致（tier 优先于 nature），
    因此可以直接替换而不改变任何既有仓位权重/风控行为：
    规范车道名 → 套利别名 → tier 别名 → nature 别名 → default。

    要「日内 / 长线趋势」报告车道请用 `resolve_lane()`，不要用本函数。
    """
    t = _norm(tier)
    n = _norm(nature)
    if n == "arbitrage" or t in ("arb", "arbitrage"):
        return "arb"
    if t in lanes:
        return t
    if t in LEGACY_TIER_ALIAS_TO_ENGINE_LANE:
        return LEGACY_TIER_ALIAS_TO_ENGINE_LANE[t]
    if n in LEGACY_NATURE_ALIAS_TO_ENGINE_LANE:
        return LEGACY_NATURE_ALIAS_TO_ENGINE_LANE[n]
    return default


# ── 解析 API（永不抛异常：报告路径不能因一条脏数据整段消失）────────────────
def lane_for_nature(raw: object) -> Optional[str]:
    """trade_nature → 车道；无法识别返回 None。"""
    key = _norm(raw)
    if not key:
        return None
    return NATURE_TO_LANE.get(key)


def lane_for_tier(raw: object) -> Optional[str]:
    """timeframe_tier → 车道；无法识别返回 None。"""
    key = _norm(raw)
    if not key:
        return None
    return TIER_TO_LANE.get(key)


def lane_for_label(raw: object) -> Optional[str]:
    """展示名 / 旧 horizon 枚举 → 车道；无法识别返回 None。"""
    s = str(raw or "").strip()
    if not s:
        return None
    if s in LABEL_TO_LANE:
        return LABEL_TO_LANE[s]
    key = s.lower().replace("-", "_")
    return LEGACY_HORIZON_TO_LANE.get(key) or NATURE_TO_LANE.get(key) or TIER_TO_LANE.get(key)


def resolve_lane(
    trade_nature: object = None,
    timeframe_tier: object = None,
    *,
    default: str = DEFAULT_LANE,
) -> str:
    """把一条持仓/交易的 (trade_nature, timeframe_tier) 归到唯一车道。

    优先级（实测 tier/nature 全库 100% 自洽，冲突只可能来自脏数据）：
    1. nature 命中 → 用 nature（nature 是执行层写入的语义标签，比 tier 更贴近行为）
    2. nature 未命中而 tier 命中 → 用 tier
    3. 两者都未命中 → default

    与旧实现的关键差别：**不再把 `mid`/`swing` 与 `long`/`trend_follow` 混在一起判断**，
    也不再让 `intraday` 掉进「既非 mid 也非 long 就当 scalp」的兜底洞里。
    """
    by_nature = lane_for_nature(trade_nature)
    if by_nature is not None:
        return by_nature
    by_tier = lane_for_tier(timeframe_tier)
    if by_tier is not None:
        return by_tier
    return default


def resolve_lane_for_position(pos: object) -> str:
    """按 ORM 对象（PaperPosition 等）解析车道，属性缺失安全。"""
    return resolve_lane(
        getattr(pos, "trade_nature", None),
        getattr(pos, "timeframe_tier", None),
    )


def lane_mismatch(
    trade_nature: object = None,
    timeframe_tier: object = None,
) -> Optional[str]:
    """返回 tier 与 nature 相互矛盾时的说明；自洽或信息不足时返回 None。

    供报告输出数据质量提示 —— 静默把矛盾仓位塞进某条车道正是「分不清」的来源之一。
    """
    by_nature = lane_for_nature(trade_nature)
    by_tier = lane_for_tier(timeframe_tier)
    if by_nature is None or by_tier is None:
        return None
    if by_nature == by_tier:
        return None
    return (
        f"nature={_norm(trade_nature)!r} 指向 {by_nature}，"
        f"tier={_norm(timeframe_tier)!r} 指向 {by_tier}"
    )


# ── 车道元数据读取 ──────────────────────────────────────────────────────
def get_spec(lane: object) -> LaneSpec:
    """取车道定义；输入可以是车道名、旧 horizon、tier、nature 或中文展示名。"""
    resolved = lane_for_label(lane)
    if resolved is None:
        resolved = resolve_lane(lane, lane, default=DEFAULT_LANE)
    return LANE_SPECS[resolved]


def lane_identity(lane: object) -> Dict[str, object]:
    """车道身份字典（写入报告 payload，供前端如实展示周期身份）。"""
    return get_spec(lane).identity()


def report_lanes() -> Tuple[str, ...]:
    """交易报告使用的车道（有序）。"""
    return REPORT_LANES


def is_report_lane(lane: object) -> bool:
    return str(lane or "").strip().lower() in REPORT_LANES


def expected_hold_hours(lane: object) -> float:
    return get_spec(lane).expected_hold_hours


def hold_bracket_hours(lane: object) -> Tuple[float, float]:
    return get_spec(lane).hold_bracket_hours


def match_priority(lane: object) -> int:
    return get_spec(lane).match_priority


def hold_bracket_label(lane: object) -> str:
    """人读的期望持仓区间，例如 `≤24h` / `24h–168h`。"""
    lo, hi = hold_bracket_hours(lane)
    if hi <= 0:
        return "n/a"
    if lo <= 0:
        return f"≤{hi:g}h"
    return f"{lo:g}h–{hi:g}h"


# ── 车道 ↔ tier/nature（写回方向）────────────────────────────────────────
def lane_to_tier(lane: object) -> str:
    """车道 → 权威 timeframe_tier（写库存档用）。"""
    return get_spec(lane).tier


def lane_to_nature(lane: object) -> str:
    """车道 → 权威 trade_nature（开仓/归一用）。"""
    return get_spec(lane).nature


def lanes_in_scope(scope: str = "trade") -> Tuple[str, ...]:
    """`trade` → 两条交易车道；`all` → 含研究车道。"""
    return REPORT_LANES if str(scope).lower() != "all" else ALL_LANES


__all__: List[str] = [
    # 车道常量
    "LANE_INTRADAY", "LANE_TREND", "LANE_RESEARCH",
    "REPORT_LANES", "ALL_LANES", "DEFAULT_LANE",
    # 类型与规格
    "LaneSpec", "LANE_SPECS",
    # 映射表
    "NATURE_TO_LANE", "TIER_TO_LANE", "LEGACY_HORIZON_TO_LANE", "LABEL_TO_LANE",
    "LEGACY_TIER_ALIAS_TO_ENGINE_LANE", "LEGACY_NATURE_ALIAS_TO_ENGINE_LANE",
    # 解析
    "lane_for_nature", "lane_for_tier", "lane_for_label",
    "resolve_lane", "resolve_lane_for_position", "lane_mismatch",
    "normalize_engine_lane",
    # 元数据
    "get_spec", "lane_identity", "report_lanes", "is_report_lane",
    "expected_hold_hours", "hold_bracket_hours", "hold_bracket_label",
    "match_priority", "lanes_in_scope",
    # 写回
    "lane_to_tier", "lane_to_nature",
]
