"""BudgetService — Layer/Tier 预算的**唯一事实来源**（Single Source of Truth）。

2026-07-06 整改（P2 BudgetService 统一）：此前存在"双账本"——
`layer_budget_manager.LayerBudgetManager` 与本类各自持有一份层分配比例，且本类
反过来依赖旧类（get_used_margin / tier_to_layer fallback 调它，甚至调其私有方法
`_get_layer_used_margin`），等于"新壳套老核"，并未真正替代。本次把旧类的核心逻辑
（nature→layer 映射、层已用保证金 DB 聚合查询）全部收编进本类，旧模块随之删除，
预算相关配置与查询从此只有这一处定义，消除双写与潜在不一致。
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# tier / nature → 两层（scalp/trend）的唯一映射。
# 中长线合并收尾（2026-07）：mid/swing 已并入 long/trend，不再是独立层。
# swing 仅作为向后兼容的"重定向"入口——历史 DB 仓位 trade_nature='swing' 仍会
# 经 nature_to_layer 映射到 trend，从 trend 池分配预算，不会报错也不会落空。
# tier 与 nature 语义在此统一收口，其它模块不再各自维护映射表。
TIER_TO_LAYER: Dict[str, str] = {
    "short": "scalp",
    "mid": "trend",      # 中长线合并：mid → trend（原 swing 层已并入 trend）
    "long": "trend",
    "scalp": "scalp",
    "swing": "trend",    # 向后兼容：旧调用传 swing → 走 trend 池
    "trend": "trend",
}

NATURE_TO_LAYER: Dict[str, str] = {
    "scalp": "scalp", "intraday": "scalp",
    "swing": "trend",    # 中长线合并：swing 重定向到 trend（不报错）
    "trend_follow": "trend", "position": "trend",
}


class BudgetService:
    """两层预算(scalp/trend) + tier/nature 映射，预算体系的单一事实来源。"""

    _instance: Optional["BudgetService"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def layer_allocations(self) -> Dict[str, float]:
        """两层资金分配比例（实时读 env，唯一定义处）。

        中长线合并收尾（2026-07）：原 swing 层（mid）已并入 trend（long），预算由三层
        收敛为两层 scalp / trend。原 swing 默认 0.45 直接并入 trend：
          - 激活态：scalp 0.25 / trend 0.75（原 scalp 0.25 / swing 0.45 / trend 0.30）
          - 未激活：scalp 0.40 / trend 0.60（原 scalp 0.40 / swing 0.45 / trend 0.15）
        向后兼容：若仍显式设置了 `LAYER_BUDGET_SWING` env，其值会叠加进 trend，避免旧部署
        的 env 配置静默失效；历史 DB 仓位 trade_nature='swing' 经 nature_to_layer 重定向
        到 trend，从 trend 池分配预算。
        """
        try:
            from backend.config.settings import MIDLONG_ACTIVATION_ENABLED
            _act = bool(MIDLONG_ACTIVATION_ENABLED)
        except Exception:
            _act = False
        _def_scalp = "0.25" if _act else "0.40"
        _def_trend = "0.75" if _act else "0.60"
        _scalp = float(os.getenv("LAYER_BUDGET_SCALP", _def_scalp))
        _trend = float(os.getenv("LAYER_BUDGET_TREND", _def_trend))
        # 兼容旧 env：LAYER_BUDGET_SWING 叠加进 trend（不再作为独立层）
        _swing_legacy = os.getenv("LAYER_BUDGET_SWING")
        if _swing_legacy not in (None, ""):
            try:
                _trend += float(_swing_legacy)
            except (TypeError, ValueError):
                pass
        return {
            "scalp": _scalp,
            "trend": _trend,
        }

    # ── 映射 ──────────────────────────────────────────────
    def nature_to_layer(self, nature: str) -> str:
        """trade_nature → layer（scalp/trend），未知默认 trend。

        中长线合并后 swing 不再是独立层，未知 nature 统一回退到 trend（原 swing 的
        兜底语义已并入 trend）。
        """
        return NATURE_TO_LAYER.get((nature or "").lower(), "trend")

    def tier_to_layer(self, tier: str) -> str:
        """tier/nature → layer；先查 tier 表，再退到 nature 映射。"""
        t = (tier or "mid").lower()
        if t in TIER_TO_LAYER:
            return TIER_TO_LAYER[t]
        return self.nature_to_layer(t)

    # ── 已用保证金（DB 聚合，唯一实现处）──────────────────
    def _query_layer_used_margin(self, layer: str) -> float:
        """查询该层当前已用保证金：聚合所有 open 仓位、按 trade_nature 归到 layer。

        （原 LayerBudgetManager._get_layer_used_margin 迁入，为本类唯一实现。）
        """
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import PaperPosition
            _db = SessionLocal()
            try:
                positions = _db.query(PaperPosition).filter(
                    PaperPosition.status == "open"
                ).all()
                total = 0.0
                for p in positions:
                    if self.nature_to_layer((p.trade_nature or "").lower()) == layer:
                        total += float(p.margin or 0)
                return total
            finally:
                _db.close()
        except Exception:
            return 0.0

    def get_used_margin(self, layer: str, mode: str = "paper") -> float:
        return self._query_layer_used_margin(layer)

    # ── 额度 / 预算 ────────────────────────────────────────
    def get_layer_cap(self, layer: str, total_equity: float) -> float:
        # 中长线合并：未知/空 layer 兜底 trend（原 swing 兜底已并入 trend）
        alloc = self.layer_allocations.get((layer or "trend").lower(), 0.75)
        return max(0.0, float(total_equity or 0) * alloc)

    def get_tier_cap(self, tier: str, total_equity: float) -> float:
        """tier 级可用额度：层预算与 tier 上限（settings）取更严的一个。

        注：TIER_BUDGET_ALLOCATION / TIER_MAX_MARGIN_PCT 是"tier 维度上限"，与
        layer 预算是两个正交维度，仍由 settings 单独定义，此处只做取交集消费。
        """
        layer = self.tier_to_layer(tier)
        cap = self.get_layer_cap(layer, total_equity)
        try:
            from backend.config.settings import TIER_BUDGET_ALLOCATION, TIER_MAX_MARGIN_PCT
            tier_l = (tier or "mid").lower()
            if tier_l in ("short", "scalp"):
                tier_l = "short"
            elif tier_l in ("long", "trend"):
                tier_l = "long"
            else:
                tier_l = "mid"
            alloc = float(TIER_BUDGET_ALLOCATION.get(tier_l, 0.3))
            max_pct = float(TIER_MAX_MARGIN_PCT.get(tier_l, 1.0))
            tier_cap = float(total_equity or 0) * min(alloc, max_pct)
            return min(cap, tier_cap) if cap > 0 else tier_cap
        except Exception:
            return cap

    def get_layer_budget(self, layer: str, total_equity: float, mode: str = "paper") -> float:
        allocated = self.get_layer_cap(layer, total_equity)
        used = self.get_used_margin(layer, mode)
        return max(0.0, allocated - used)

    def can_open(
        self,
        tier: str,
        required_margin: float,
        total_equity: float,
        mode: str = "paper",
    ) -> bool:
        layer = self.tier_to_layer(tier)
        budget = self.get_layer_budget(layer, total_equity, mode)
        ok = budget >= float(required_margin or 0)
        if not ok:
            logger.info(
                "[BudgetService] %s/%s 预算不足: need=%.0f avail=%.0f equity=%.0f",
                tier, layer, required_margin, budget, total_equity,
            )
        return ok

    def get_budget_utilization(self, total_equity: float, mode: str = "paper") -> Dict[str, Dict]:
        """各层预算利用率快照（阶段二 B1 可观测性）。

        返回每层的 {alloc, cap, used, utilization, idle_pct}，用于中长线健康视图判断
        "预算是否闲置"。中长线合并后只剩 scalp/trend 两层；若 trend 层利用率长期接近 0，
        说明开仓侧仍被门槛/信号卡住，而非预算不足。
        """
        out: Dict[str, Dict] = {}
        allocs = self.layer_allocations
        eq = float(total_equity or 0)
        for layer in ("scalp", "trend"):
            cap = self.get_layer_cap(layer, eq)
            used = self.get_used_margin(layer, mode)
            util = round(used / cap, 4) if cap > 0 else 0.0
            out[layer] = {
                "alloc": round(allocs.get(layer, 0.0), 4),
                "cap": round(cap, 2),
                "used": round(used, 2),
                "utilization": util,
                "idle_pct": round(max(0.0, 1.0 - util), 4),
            }
        return out

    def scale_factor_for_layer(self, tier: str, total_equity: float, mode: str = "paper") -> float:
        """层预算使用 >90% 时缩仓，非新 gate。"""
        layer = self.tier_to_layer(tier)
        cap = self.get_layer_cap(layer, total_equity)
        if cap <= 0:
            return 1.0
        used = self.get_used_margin(layer, mode)
        usage = used / cap
        if usage >= 1.0:
            return 0.0
        if usage >= 0.9:
            return 0.7
        return 1.0


budget_service = BudgetService()


def nature_to_layer(nature: str) -> str:
    """模块级便捷封装：trade_nature → layer（委托单例）。

    中长线合并后 swing→trend（重定向，不报错）；未知 nature 兜底 trend。
    """
    return budget_service.nature_to_layer(nature)
