# -*- coding: utf-8 -*-
"""事件数据层（v3 p0-event-data，2026-09-03）。

免费源优先的事件数据接入：
  market_events_store      事件总线表 market_events（所有采集器统一写入；RiskEngine/Agent 统一读取）
  exchange_announcements   交易所公告（Binance CMS / OKX / Bybit：上新、下架、监控标签、空投…）
  liquidation_stream       多所逐笔清算 WebSocket（Binance/Aster forceOrder、OKX liquidation-orders、Bybit allLiquidation）→ liquidation_ticks
  position_structure       Binance /futures/data/* 持仓结构（OI、多空比、大户持仓比、主动买卖比）
  funding_universe         全币池资金费：/fapi/v1/fundingRate 结算费率一年回填 + 极端费率扫描
  bridge                   既有 news_events / whale_activities / macro_events → market_events 桥接
  collectors               统一注册入口（DC 数据中心进程 / 主进程 均可挂载）

设计约束：
  - 绝不造数：任何源离线 → 本轮 0 行、留诊断，不写占位。
  - 幂等：所有表带 dedupe/唯一约束，重复采集不产生重复行。
  - 单向依赖：本包只依赖 database.connection / ops.job_registry / ops.alerts，不反向依赖策略层。
"""
