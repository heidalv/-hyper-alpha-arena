# -*- coding: utf-8 -*-
"""[P3 / §70 执行 2026-09-10] PnL 口径统一：**净口径 = 毛盈亏 − 手续费 − 资金费**。

背景（§43.2/§43.3/§44.1 实证）：逐笔 `pnl`（`trade_facts.pnl`、`paper_positions.unrealized_pnl`）
是**毛口径**（不含交易费），而手续费真实存在且不小：
  * `paper_orders.fee` 近 7 天合计 **$42.35**（246 笔）；
  * `paper_funding_ledger.payment` 记录资金费；
  * 75 天全链净口径 ≈ **−$163** vs 毛口径 **−$100.6** —— 差 ~62%，足以改变"策略是否正期望"的结论。

口径约定（本模块是唯一真相源）：
  * `gross`：价差盈亏（含已实现/未实现），**不含费用**；
  * `fees`：交易手续费（开/平/部分平仓累计）；
  * `funding`：资金费净支出（正数=支出，负数=收取）；
  * `net = gross − fees − funding`。

任何对外展示/写报告的数字**必须**标注口径；本模块提供 `label()` 以便调用方在输出里带上 `basis=net`。
"""
from __future__ import annotations

from typing import Any, Dict

#: 当前对外口径（P3 决策：以净口径为准）
PNL_BASIS = "net"


def net_pnl(gross: float, fees: float = 0.0, funding: float = 0.0) -> float:
    """净盈亏 = 毛 − 手续费 − 资金费。缺失值按 0 处理（None 不当作"无费用"以外的含义）。"""
    g = float(gross or 0.0)
    f = float(fees or 0.0)
    d = float(funding or 0.0)
    return g - f - d


def label() -> str:
    """口径标签（写进日志/报告/接口，避免"这个 pnl 是毛还是净"再次含糊）。"""
    return f"basis={PNL_BASIS}(gross-fees-funding)"


def describe(gross: float, fees: float = 0.0, funding: float = 0.0) -> Dict[str, Any]:
    """一次性给出四个数字 + 口径标签，便于直接塞进返回体。"""
    net = net_pnl(gross, fees, funding)
    return {
        "pnl_gross": round(float(gross or 0.0), 4),
        "fees": round(float(fees or 0.0), 4),
        "funding": round(float(funding or 0.0), 4),
        "pnl_net": round(net, 4),
        "pnl": round(net, 4),  # 兼容字段：P3 起 `pnl` 即**净**口径
        "pnl_basis": PNL_BASIS,
        "cost_ratio": (round((float(fees or 0.0) + float(funding or 0.0)) / abs(float(gross)), 4)
                       if abs(float(gross or 0.0)) > 1e-9 else None),
    }
