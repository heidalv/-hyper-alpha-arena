# -*- coding: utf-8 -*-
"""OMS：实盘订单管理（v3 方向 2，p2-oms-exec）。

这一层解决实盘执行里三个**会直接亏钱**的缺口：

  1. **重复下单**：此前下单不带 client_order_id，网络超时后无法安全重试——重试可能变双倍仓位，
     不重试又可能漏单。现在**先落库拿到幂等键、再用它当 `newClientOrderId` 下单**，
     交易所侧天然去重，超时可以放心重试。
  2. **订单状态黑洞**：此前只有 `live_sub_positions` 的 open/closed 子仓账本，
     订单从发出到成交之间没有任何记录，进程崩溃后无从得知那笔单到底发出去没有。
     现在有完整状态机，重启后能把 `submitted` 悬挂单捞回来查证。
  3. **吃 taker 费**：入场几乎全是市价单。ExecutionAlgo 用 post-only 限价追价，
     追不到再按配置回退市价，把 maker/taker 的选择变成可调策略而不是硬编码。

  order_store.py     `live_orders` 表 + 状态机（唯一的状态写入口）
  client_id.py       幂等键生成与格式适配
  execution_algo.py  maker 追价执行器
  reconcile.py       每日订单对账：本地 vs 交易所
  bridge.py          与 `_place_order_fresh_client` 的零侵入桥接
  jobs.py            定时任务注册

**安全默认**：
  - `OMS_RECORD_ORDERS=true`：现有路径落账本（不改下单方式）
  - `EXEC_ALGO_ENABLED=false`：不接管下单
  - `OMS_SHADOW=true`：algo 路径只走状态机不发真单
  - `OMS_RECONCILE_AUTO_FIX=false`：对账只报告
"""
from backend.services.oms.client_id import (  # noqa: F401
    is_ours,
    new_client_order_id,
    sanitize_for_exchange,
)
from backend.services.oms.order_store import (  # noqa: F401
    TERMINAL_STATUSES,
    OrderStatus,
    ensure_schema,
    get_order,
    list_orders,
    record_intent,
    shadow_mode,
    transition,
)
