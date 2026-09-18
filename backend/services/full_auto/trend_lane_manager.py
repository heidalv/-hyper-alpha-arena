"""trend_lane_manager — **长线趋势车道的持仓决策归属**（轮102，阶段3 第一步）。

## 为什么要有这个模块

用户 2026-09-19 指出「中线和长线周期是两个概念，你却把它合并为中长线一并设计」。
轮100/101 已把**车道真源与值**分开；本模块解决**归属**问题 ——
在此之前，"长线仓允许做什么"散落在 `midlong_position_manager`（2091 行、95 处 tier 引用、
4 处长线例外）里，与中线共用同一套六维决策栈，靠 `if tier == "long"` 逐处打补丁。
实测的代价（见 `reports/_轮96_*.md`、`_轮99_*.md`）：

| 漏补丁的位置 | 后果 |
|---|---|
| `tighten_trailing` | 长线止损被拉到「现价 − 2×短周期ATR」(≈1%) ⇒ 4 笔趋势仓被 1% 回撤收割 |
| `reduce` 裁量减仓 | #4659 BTC 被减 6 次、size 只剩 **2.7%**；#4660 BNB 剩 4.5% |
| 统一分段止盈（ATR 阶梯） | 长线在 **+3.83%** 就被分批止盈（车道声明档位是 8/15/25%） |

## 本模块的边界（**只做决策，不做执行**）

- **输入**：仓位的事实（车道、浮盈、持仓时长、论题命中、LLM 复查给出的动作）；
- **输出**：`TrendDecision`（允许做什么 + 理由）；
- **不做**：任何 DB 访问、任何下单 —— 执行仍由 `midlong_position_manager` 的既有
  helper（`_exec_pyramid` / `_exec_close` …）完成，避免一次搬 700 行代码。

这样"长线允许做什么"就只有一个定义处；将来要把执行也搬进来，直接在本模块加 `run()` 即可，
调用方只需换一行。

## 长线车道的契约（来自 `trend_e1_engine` 的模块文档 + 用户裁决）

    出场 = 规则失效（论题失效价被击穿） 或 Chandelier 结构止损
    盈利 = **滚仓**（在趋势确认处加仓），而不是早早分批止盈
    禁止 = 收紧追踪止损、裁量减仓（那是中线的工具，用在长线上就是把趋势仓降级成日内仓）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# 决策动作
ACT_HOLD = "hold"
ACT_PYRAMID = "pyramid"
ACT_THESIS_EXIT = "thesis_exit"
ACT_SKIP = "skip"

# 论题退出的两类原因：`invalidation` 是**规则**（价格击穿失效价）；
# `should_close` 是 LLM **裁量**标志 —— 必须再过 F39 反转确认闸（含 min_hold）。
RULE_THESIS_REASONS = ("thesis_invalidation",)
DISCRETIONARY_THESIS_REASONS = ("thesis_should_close",)

# 长线禁止的复查动作（中线的工具）
FORBIDDEN_ACTIONS = ("tighten_trailing", "reduce")


@dataclass(frozen=True)
class TrendDecision:
    """长线趋势仓该做什么（纯数据，可直接断言）。"""

    action: str
    reason: str
    allowed: bool = True
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"action": self.action, "reason": self.reason,
                "allowed": self.allowed, "detail": dict(self.detail)}


def decide(
    *,
    thesis_reason: Optional[str] = None,
    review_action: Optional[str] = None,
    pyramid_action: Optional[str] = None,
    pnl_pct: float = 0.0,
    pyramid_min_pnl_pct: float = 0.05,
    rule_passthrough: bool = False,
    dca_action: Optional[str] = None,
) -> TrendDecision:
    """长线趋势仓的决策（**优先规则失效，其次滚仓，其余一律持有**）。

    顺序与理由：

    1. `thesis_invalidation`（规则失效：价格击穿论题失效价）→ **允许全平**。
       这是车道契约里唯一的价格型出场，与 Chandelier 并列。
    2. `thesis_should_close`（LLM 裁量）→ **交给 F39 反转确认闸**（调用方执行），
       本模块只标记 `discretionary=True`，不在这里放行 ——
       长线最短持仓 72h，裁量平仓必须过闸。
    3. 复查给出 `tighten_trailing` / `reduce` → **拒绝**，返回 hold。
       这两条是 2026-09-18 事故的直接机制（见模块文档的表格）。
    4. 复查给出 `add`，或规则直通（浮盈 ≥ 阈值且方向仍强）→ **滚仓**。
       用户明确要求"长线趋势是滚仓盈利为目的的"。
    5. 其余 → hold。

    纯函数：无 DB、无时间、无随机，便于把"长线允许做什么"写成测试。
    """
    # ① 规则失效（价格型出场）
    if thesis_reason in RULE_THESIS_REASONS:
        return TrendDecision(ACT_THESIS_EXIT, f"规则失效({thesis_reason})",
                             detail={"thesis_reason": thesis_reason, "rule_based": True})

    # ② LLM 裁量平仓 → 交闸（本模块不放行，也不拦死）
    if thesis_reason in DISCRETIONARY_THESIS_REASONS:
        return TrendDecision(ACT_THESIS_EXIT, f"裁量平仓({thesis_reason})，需过 F39 反转确认闸",
                             detail={"thesis_reason": thesis_reason, "discretionary": True,
                                     "requires_confirm_gate": True})

    # ③ 禁止中线的收紧/减仓工具
    if review_action in FORBIDDEN_ACTIONS:
        return TrendDecision(ACT_HOLD, f"长线禁止 {review_action}（中线工具）",
                             allowed=False, detail={"rejected_action": review_action})

    # ④ 滚仓（长线的主要盈利手段）
    if pyramid_action == "add" and pnl_pct > 0:
        return TrendDecision(ACT_PYRAMID, "LLM 判 add 且浮盈",
                             detail={"trigger": "llm_add", "pnl_pct": pnl_pct})
    if rule_passthrough and pnl_pct > float(pyramid_min_pnl_pct or 0):
        return TrendDecision(ACT_PYRAMID, "规则直通（浮盈达阈值）",
                             detail={"trigger": "rule_passthrough", "pnl_pct": pnl_pct,
                                     "threshold": pyramid_min_pnl_pct})

    # ⑤ 其余持有（DCA 逆势补仓不属趋势车道：那是震荡车道的工具）
    if dca_action == "dca":
        return TrendDecision(ACT_HOLD, "长线不做逆势补仓（DCA 属中线/震荡工具）",
                             allowed=False, detail={"rejected_action": "dca"})
    return TrendDecision(ACT_HOLD, "无允许的动作，继续持有")


def is_forbidden(action: Optional[str]) -> bool:
    """该复查动作在长线车道上是否被禁止（供其它模块复用同一口径）。"""
    return str(action or "").strip().lower() in FORBIDDEN_ACTIONS


def allowed_actions() -> tuple:
    """长线车道允许的动作集合（文档化用途，测试会钉住）。"""
    return (ACT_HOLD, ACT_PYRAMID, ACT_THESIS_EXIT)
