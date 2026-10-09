"""统一套利开关语义（Phase 3）。

2026-07-06 新增：厘清"两套开关"，根治"开了套利但 V3 不动"的排查困惑。

套利中心是双引擎 Hub，两条独立链路、各自的开关，此前语义分散在多处：

  ┌─ V3 统计套利（资金费率/跨所价差/基差）
  │    运行条件 = 环境变量 FUNDING_ARB_ENABLED=true  且  会话级 arb_enabled=true
  │    （二者与关系；默认都关，必须显式开）
  │
  └─ Rebate 刷积分 / delta-neutral（S*/SDN）
       运行条件 = rebate_config.engine.paper_mode（Paper 恒可扫描/模拟）
       是否自动开仓 = rebate_config.engine.auto_execute（默认关）
       实盘下单 = Phase 5 未启用（本次全程 Paper）

本模块提供**单一事实来源**：查询两条链路的开关状态与"是否可运行"，供 tick、
API、前端统一读取，避免各处自行拼装、语义漂移。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ArbSwitchStatus:
    """两条套利链路的开关快照。"""

    # V3 统计套利
    v3_env_enabled: bool
    v3_session_enabled: bool
    v3_runnable: bool
    # Rebate / delta-neutral 刷积分
    rebate_paper_mode: bool
    rebate_auto_execute: bool
    rebate_scan_runnable: bool     # 是否可扫描/模拟评估（Paper 下恒 True）
    rebate_auto_open: bool         # 是否会自动开仓
    # 实盘（Phase 5）
    live_trading_enabled: bool     # 恒 False（本次全程 Paper）

    def to_dict(self) -> dict:
        return {
            "v3_statistical_arb": {
                "env_enabled": self.v3_env_enabled,
                "session_enabled": self.v3_session_enabled,
                "runnable": self.v3_runnable,
                "note": "需 FUNDING_ARB_ENABLED=true 且 会话 arb_enabled=true（与关系）",
            },
            "rebate_points_arb": {
                "paper_mode": self.rebate_paper_mode,
                "auto_execute": self.rebate_auto_execute,
                "scan_runnable": self.rebate_scan_runnable,
                "auto_open": self.rebate_auto_open,
                # [F327] note 必须反映**当前**裁决。此前恒写"Paper 下恒可扫描"，
                # 而该结论已被总开关（ARBITRAGE_CENTER_ENABLED）取代 ⇒ 会误导排查。
                "note": ("需 ARBITRAGE_CENTER_ENABLED=true 才可扫描/模拟"
                         if not self.rebate_scan_runnable
                         else "可扫描/模拟")
                        + ("；auto_execute=true 才会自动开仓"
                           if not self.rebate_auto_execute else "；自动开仓已开"),
            },
            "live_trading": {
                "enabled": self.live_trading_enabled,
                "note": "Phase 5 未启用，本次全程 Paper，无真实下单",
            },
        }


def get_arb_switch_status(session_arb_enabled: Optional[bool] = None) -> ArbSwitchStatus:
    """汇总两条套利链路的开关状态（单一事实来源）。

    Args:
        session_arb_enabled: 会话级 arb_enabled（V3 需要）。None 时按 False 处理。
    """
    # V3
    try:
        from backend.config import settings

        v3_env = bool(getattr(settings, "FUNDING_ARB_ENABLED", False))
    except Exception:
        v3_env = False
    v3_session = bool(session_arb_enabled)
    v3_runnable = v3_env and v3_session

    # Rebate
    try:
        from backend.config.rebate_config_loader import rebate_config

        paper_mode = bool(rebate_config.engine.paper_mode)
        auto_execute = bool(rebate_config.engine.auto_execute)
    except Exception:
        paper_mode, auto_execute = True, False

    # Paper 下扫描/模拟恒可运行；自动开仓需 auto_execute。
    #
    # [F327 2026-09-17] **套利中心总开关**：用户要求「完全停止整个套利中心的运行」。
    # 此前这里是**硬编码 True**（注释：Paper 下恒可运行），于是：
    #   · `/api/rebate/status` 与 `/api/arbitrage/status` **永远报 engine_enabled=true**
    #     （前者此处恒真，后者在 arbitrage_routes 里也是硬编码 True）；
    #   · 前端据此显示"运行中"，即使用户已经把全部车道停掉 ——
    #     两套状态（车道注册表 / 返佣引擎）互不知情。
    #   ⇒ 现在由 `ARBITRAGE_CENTER_ENABLED`（默认 **false** = 停止）统一裁决。
    #
    # 语义：
    #   · `ARBITRAGE_CENTER_ENABLED` 未设置或非真值 ⇒ `rebate_scan_runnable=False`
    #     ⇒ 引擎状态如实报 false、前端不再显示"运行中"；
    #   · 设 `ARBITRAGE_CENTER_ENABLED=1` ⇒ 恢复旧行为（Paper 下可扫描）。
    #
    # 为什么不改动 `rebate_config`：那是**策略参数**配置；本开关是**运行裁决**，
    # 与车道注册表的 `status` 同层，放在这里才能被 `/api/rebate/arb-switches`
    # 与所有消费方**一致地**读到。
    _center_on = os.getenv("ARBITRAGE_CENTER_ENABLED", "false").strip().lower() in (
        "1", "true", "yes", "on")
    rebate_scan_runnable = bool(_center_on)
    rebate_auto_open = bool(auto_execute) and bool(_center_on)

    return ArbSwitchStatus(
        v3_env_enabled=v3_env,
        v3_session_enabled=v3_session,
        v3_runnable=v3_runnable,
        rebate_paper_mode=paper_mode,
        rebate_auto_execute=auto_execute,
        rebate_scan_runnable=rebate_scan_runnable,
        rebate_auto_open=rebate_auto_open,
        live_trading_enabled=False,
    )


def is_v3_arb_runnable(session_arb_enabled: Optional[bool] = None) -> bool:
    """V3 统计套利是否应运行（环境 AND 会话）。"""
    return get_arb_switch_status(session_arb_enabled).v3_runnable


def is_rebate_arb_scan_runnable() -> bool:
    """Rebate/delta-neutral 是否可扫描/模拟（Paper 下恒 True）。"""
    return get_arb_switch_status().rebate_scan_runnable
