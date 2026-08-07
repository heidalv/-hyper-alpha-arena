# backend/services/live_position_manager.py
"""Live 子仓位管理器。

在本地维护各 tier 的虚拟子仓位,对交易所呈现统一的净仓位操作。

核心原则
--------
1. 交易所只能看到一个币种一个净仓位(HL One-Way mode)—— 交易所端
   per-symbol per-account 只有一个净仓 + 一个杠杆档位。
2. 本地按 trade_nature 分仓跟踪 scalp / trend_follow —— 不同周期策略独立决策。
3. 下单时计算差额(净变化),只发一笔给交易所 —— 避免对冲腿互相抵消后多走流量。
4. 杠杆取 max(各 tier) —— 交易所端共享档位,取最大值确保保证金足够。

与 PositionCoordinator(paper 侧)的关系
----------------------------------------
PositionCoordinator 只做 *协调/放行* 决策(paper 侧,查询 PaperPosition),
不持有也不修改仓位。LivePositionManager 是 *执行* 侧(live 侧):它真正调用
exchange_callback 下单并维护 LiveSubPosition 账本。两者职责互补但模型分离:
paper 子仓 → PaperPosition;live 子仓 → LiveSubPosition。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List

_log = logging.getLogger(__name__)


@dataclass
class NetPositionView:
    """净仓位视图。

    用于风控/展示:聚合某 symbol 下所有 open 子仓位得到统一视图。
    """
    symbol: str
    net_side: str = "flat"          # long / short / flat
    net_size: float = 0.0           # 有符号:long 正,short 负
    unified_leverage: float = 1.0   # max(各 tier)
    sub_positions: list = field(default_factory=list)


class LivePositionManager:
    """Live 子仓位管理器。进程内单例(由模块底部 ``live_position_manager`` 提供)。"""

    def execute_order(
        self,
        db,
        account_id: int,
        symbol: str,
        side: str,            # long / short (desired sub-position side)
        size: float,          # desired sub-position notional size
        leverage: float,
        trade_nature: str,    # scalp / trend_follow
        tier: str,            # short / mid / long
        exchange_callback,
        # callable(db, symbol, order_side, net_qty, leverage) -> {order_id, fill_price}
    ) -> dict:
        """统一下单入口。

        流程:
        1. 查现有 open 子仓位(跨 nature,同 symbol/account)。
        2. 计算当前净仓位(signed)。
        3. 模拟加入新子仓位后的目标净仓位(新子仓替换同 nature 旧子仓)。
        4. 差额 = 目标净 - 当前净 → 向交易所发一笔差额订单(或 no-op 当差额≈0)。
        5. 更新本地 LiveSubPosition(关闭旧的同 nature 子仓,建新子仓)。
        6. 返回结果(含 sub_position_id)。

        参数:
            db: SQLAlchemy Session。
            account_id: 账户 ID。
            symbol: 交易对。
            side: 目标子仓位方向("long"/"short",position side)。
            size: 目标子仓位名义大小(>0)。
            leverage: 本次子仓位杠杆。
            trade_nature: 交易性质("scalp"/"trend_follow")。
            tier: 周期档位("short"/"mid"/"long")。
            exchange_callback: 实际下单回调,签名
                ``(db, symbol, order_side, net_qty, leverage) -> {"order_id", "fill_price"}``。

        返回:
            dict: {sub_position_id, order_id, fill_price, net_delta, order_side}
        """
        from backend.database.models import LiveSubPosition

        # 1. 查现有 open 子仓位(跨 nature)
        existing = db.query(LiveSubPosition).filter(
            LiveSubPosition.account_id == account_id,
            LiveSubPosition.symbol == symbol,
            LiveSubPosition.status == "open",
        ).all()

        # 2. 当前净仓位(signed:long 正,short 负)
        current_net = sum(
            (p.size if p.side == "long" else -p.size) for p in existing
        )

        # 3. 同 nature 子仓位(可能 add/dca/reverse)—— 新子仓将替换它们
        same_nature = [p for p in existing if p.trade_nature == trade_nature]
        old_sub_size = sum(
            (p.size if p.side == "long" else -p.size) for p in same_nature
        )

        # 4. 目标净:用新子仓替换同 nature 的(同 nature 语义上代表同一笔逻辑持仓)
        new_sub_signed = size if side == "long" else -size
        target_net = current_net - old_sub_size + new_sub_signed

        # 5. 差额订单
        delta = target_net - current_net
        if abs(delta) < 1e-8:
            _log.info(
                "[LPM] %s %s: no net change (delta≈0), skip exchange order",
                symbol, trade_nature,
            )
            order_side = "buy" if delta >= 0 else "sell"
            fill_price = 0.0
            order_id = None
        else:
            order_side = "buy" if delta > 0 else "sell"
            # 统一杠杆 = max(现有所有子仓杠杆, 新请求杠杆)
            all_levs = [p.leverage for p in existing] + [leverage]
            unified_lev = max(all_levs) if all_levs else leverage
            # 发给交易所
            result = exchange_callback(db, symbol, order_side, abs(delta), unified_lev)
            order_id = result.get("order_id")
            fill_price = result.get("fill_price", 0.0)

        # 6. 更新本地账本
        # 关闭旧的同 nature 子仓位(被新子仓替换)
        for p in same_nature:
            p.status = "closed"

        # 创建新子仓位(仅当 size > 0;size==0 表示该 nature 平仓后不再开新仓)
        sub_id = None
        if size > 0:
            new_sub = LiveSubPosition(
                account_id=account_id,
                symbol=symbol,
                side=side,
                trade_nature=trade_nature,
                timeframe_tier=tier,
                size=size,
                leverage=leverage,
                margin=size / max(leverage, 1.0),
                entry_price=fill_price,
                status="open",
                exchange_order_id=order_id,
            )
            db.add(new_sub)
            db.flush()
            sub_id = new_sub.id

        db.commit()

        return {
            "sub_position_id": sub_id,
            "order_id": order_id,
            "fill_price": fill_price,
            "net_delta": delta,
            "order_side": order_side,
        }

    def close_sub_position(
        self,
        db,
        account_id: int,
        symbol: str,
        trade_nature: str,
        exchange_callback,
    ) -> dict:
        """关闭指定 tier(trade_nature)的子仓位。

        流程:
        1. 查该 nature 的所有 open 子仓位。
        2. 计算 signed size → 向交易所发反向差额单(净额)。
        3. 标记本地子仓位为 closed。

        参数:
            db: SQLAlchemy Session。
            account_id: 账户 ID。
            symbol: 交易对。
            trade_nature: 要平掉的交易性质("scalp"/"trend_follow")。
            exchange_callback: 同 execute_order。

        返回:
            dict: {closed, order_id, fill_price, closed_size} 或
                  {closed: False, reason: "no open sub-position"}
        """
        from backend.database.models import LiveSubPosition

        subs = db.query(LiveSubPosition).filter(
            LiveSubPosition.account_id == account_id,
            LiveSubPosition.symbol == symbol,
            LiveSubPosition.trade_nature == trade_nature,
            LiveSubPosition.status == "open",
        ).all()

        if not subs:
            return {"closed": False, "reason": "no open sub-position"}

        # 该 nature 的 signed size
        sub_signed = sum((p.size if p.side == "long" else -p.size) for p in subs)

        # 向交易所发反向差额单(净额)
        if abs(sub_signed) < 1e-8:
            order_id = None
            fill_price = 0.0
        else:
            order_side = "sell" if sub_signed > 0 else "buy"
            result = exchange_callback(
                db, symbol, order_side, abs(sub_signed),
                max(p.leverage for p in subs),
            )
            order_id = result.get("order_id")
            fill_price = result.get("fill_price", 0.0)

        # 标记关闭
        for p in subs:
            p.status = "closed"
        db.commit()

        return {
            "closed": True,
            "order_id": order_id,
            "fill_price": fill_price,
            "closed_size": abs(sub_signed),
        }

    def get_net_position(self, db, account_id: int, symbol: str) -> NetPositionView:
        """获取某币种的净仓位视图(聚合所有 open 子仓位)。

        返回 NetPositionView:net_side / net_size(signed)/ unified_leverage(max)/
        sub_positions(摘要列表)。
        """
        from backend.database.models import LiveSubPosition

        subs = db.query(LiveSubPosition).filter(
            LiveSubPosition.account_id == account_id,
            LiveSubPosition.symbol == symbol,
            LiveSubPosition.status == "open",
        ).all()

        net = sum((p.size if p.side == "long" else -p.size) for p in subs)
        unified_lev = max((p.leverage for p in subs), default=1.0)

        return NetPositionView(
            symbol=symbol,
            net_side="long" if net > 0 else ("short" if net < 0 else "flat"),
            net_size=net,
            unified_leverage=unified_lev,
            sub_positions=[
                {
                    "trade_nature": p.trade_nature,
                    "side": p.side,
                    "size": p.size,
                    "leverage": p.leverage,
                }
                for p in subs
            ],
        )

    def reconcile(
        self, db, account_id: int, symbol: str,
        exchange_qty: float, exchange_leverage: float,
    ) -> dict:
        """对账:比较本地子仓位合计 vs 交易所实际仓位。

        参数:
            db: SQLAlchemy Session。
            account_id: 账户 ID。
            symbol: 交易对。
            exchange_qty: 交易所返回的实际净仓位(signed:long 正,short 负)。
            exchange_leverage: 交易所返回的实际杠杆(目前仅用于日志,未参与判定)。

        返回:
            dict: {matched, local, exchange, diff}
            matched=True 当 |diff| 在 1% 容差以内。
        """
        net = self.get_net_position(db, account_id, symbol)
        local_qty = net.net_size
        diff = local_qty - exchange_qty
        # 容差:1% 或 1e-6 绝对下限(避免交易所 qty≈0 时除零放大)
        threshold = max(abs(exchange_qty) * 0.01, 1e-6)

        if abs(diff) > threshold:
            _log.warning(
                "[LPM] %s reconcile MISMATCH: local=%.6f exchange=%.6f "
                "diff=%.6f (threshold=%.6f)",
                symbol, local_qty, exchange_qty, diff, threshold,
            )
            return {
                "matched": False, "local": local_qty,
                "exchange": exchange_qty, "diff": diff,
            }

        return {
            "matched": True, "local": local_qty,
            "exchange": exchange_qty, "diff": diff,
        }


# 进程内单例
live_position_manager = LivePositionManager()
