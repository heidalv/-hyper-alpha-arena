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
                "note": "Paper 下恒可扫描/模拟；auto_execute 才会自动开仓",
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
    rebate_scan_runnable = True
    rebate_auto_open = auto_execute

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
