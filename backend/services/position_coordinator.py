# backend/services/position_coordinator.py
"""多周期仓位协调器。

在 place_order 之前协调各 tier 的开仓请求:
- 查询同币种所有 tier 的子仓位
- 计算净仓位和统一杠杆(max)
- 判断跨 tier 冲突(允许对冲,不允许同 tier 反向)

设计原则:
1. 同 tier(trade_nature)内:不允许反向开仓(必须先平)—— 子仓位语义上
   代表同一笔逻辑持仓,反向必须走 close 路径,而非开新反向仓。
2. 跨 tier:允许反向(scalp long + trend short = 对冲,合法)—— 不同周期
   策略可以对同一币种持有相反观点。
3. 统一杠杆 = max(所有现有子仓位的杠杆, 新请求杠杆)—— 交易所端 per-symbol
   per-account 只有一个杠杆档位,跨 tier 共享,取最大值确保保证金足够。
4. 净暴露 = 所有子仓位的 signed size 之和 —— 用于风控/展示,本模块只计算不阻断。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

_log = logging.getLogger(__name__)


@dataclass
class CoordinationResult:
    """协调结果。"""
    allowed: bool
    reason: str = ""
    unified_leverage: float = 1.0  # 交易所端杠杆(各 tier max)
    net_exposure: float = 0.0      # 净暴露(signed 名义价值;long 正,short 负)
    existing_sub_positions: list = field(default_factory=list)  # 现有子仓位摘要


class PositionCoordinator:
    """多周期仓位协调器。进程内单例(由模块底部 `position_coordinator` 提供)。"""

    def coordinate_open(
        self,
        db,
        account_id: int,
        symbol: str,
        side: str,            # "long" / "short" (position side)
        order_side: str,      # "buy" / "sell" (order side)
        leverage: float,
        tier: Optional[str] = None,         # "short" / "mid" / "long"
        trade_nature: Optional[str] = None,  # "scalp" / "swing" / "trend_follow"
    ) -> CoordinationResult:
        """协调开仓请求。

        参数:
            db: SQLAlchemy Session。
            account_id: 账户 ID。
            symbol: 交易对。
            side: 目标仓位方向("long"/"short",position side)。
            order_side: 订单方向("buy"/"sell",order side)—— 仅用于日志一致性。
            leverage: 本次开仓请求杠杆。
            tier: 周期档位("short"/"mid"/"long");为推断 trade_nature 的备选入参。
            trade_nature: 交易性质("scalp"/"swing"/"trend_follow");优先于 tier。

        规则:
        1. 同 tier(trade_nature)内:不允许反向开仓(必须先平)。
        2. 跨 tier:允许反向(scalp long + trend short = 对冲,合法)。
        3. 统一杠杆 = max(所有现有子仓位的杠杆, 新请求杠杆)。
        4. 净暴露 = 所有子仓位的 signed size 之和。
        """
        from backend.database.models import PaperPosition

        # 查询同币种所有 open 子仓位(跨 tier)
        try:
            existing = (
                db.query(PaperPosition)
                .filter(PaperPosition.account_id == account_id)
                .filter(PaperPosition.symbol == symbol)
                .filter(PaperPosition.status == "open")
                .all()
            )
        except Exception as e:
            # 查询失败不阻断下单(避免协调器成为单点故障),回退到保守放行
            _log.warning("PositionCoordinator query failed (allowing): %s", e)
            return CoordinationResult(
                allowed=True,
                reason=f"coordinator_query_failed: {e}",
                unified_leverage=float(leverage or 1.0),
                net_exposure=0.0,
                existing_sub_positions=[],
            )

        # 1. 同 tier 反向检查(跨 tier 反向 = 合法对冲,放行)
        nature = trade_nature or self._tier_to_nature(tier)
        for pos in existing:
            pos_nature = getattr(pos, "trade_nature", None) or ""
            pos_side = getattr(pos, "side", "")
            # 仅在 trade_nature 一致时检查反向 —— 同一逻辑持仓不允许反向叠加
            if nature and pos_nature == nature and pos_side and pos_side != side:
                return CoordinationResult(
                    allowed=False,
                    reason=(
                        f"same-tier direction conflict: existing {pos_nature} "
                        f"{pos_side} vs new {side}"
                    ),
                    unified_leverage=float(leverage or 1.0),
                    net_exposure=0.0,
                    existing_sub_positions=self._summarize(existing),
                )

        # 2. 统一杠杆(max)—— 交易所端 per-symbol 共享一个杠杆档位
        all_leverages = [
            float(getattr(p, "leverage", 1.0) or 1.0) for p in existing
        ]
        all_leverages.append(float(leverage or 1.0))
        unified_lev = max(all_leverages) if all_leverages else float(leverage or 1.0)

        # 3. 净暴露(signed 名义价值;long 正,short 负)
        net = 0.0
        for p in existing:
            p_side = getattr(p, "side", "")
            p_size = abs(
                float(getattr(p, "size", 0) or getattr(p, "quantity", 0) or 0)
            )
            if p_side == "long":
                net += p_size
            elif p_side == "short":
                net -= p_size

        return CoordinationResult(
            allowed=True,
            unified_leverage=unified_lev,
            net_exposure=net,
            existing_sub_positions=self._summarize(existing),
        )

    def _tier_to_nature(self, tier: Optional[str]) -> str:
        """tier → trade_nature 单一映射(权威源在 tp_sl_authority)。"""
        from backend.services.tp_sl_authority import TIER_TO_NATURE
        if tier and tier in TIER_TO_NATURE:
            return TIER_TO_NATURE[tier]
        return "scalp"

    def _summarize(self, positions) -> list:
        return [
            {
                "trade_nature": getattr(p, "trade_nature", "") or "",
                "side": getattr(p, "side", "") or "",
                "size": float(getattr(p, "size", 0) or getattr(p, "quantity", 0) or 0),
                "leverage": float(getattr(p, "leverage", 1.0) or 1.0),
            }
            for p in positions
        ]


# 进程内单例
position_coordinator = PositionCoordinator()
