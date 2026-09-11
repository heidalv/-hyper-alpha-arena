# -*- coding: utf-8 -*-
"""LIVE_KILL_SWITCH —— 全局急停（v3 方向 7）。三个入口，任一命中即生效：

  1. 环境变量  LIVE_KILL_SWITCH=true            （部署级；改 .env 需重启或热读）
  2. 文件      backend/data/risk/KILL_SWITCH      （运维级；文件存在即生效，内容=原因；
                                                    可用任何工具/脚本/飞书机器人落盘）
  3. API/命令  POST /api/ops/risk/kill 或飞书命令 /kill  → 写同一文件（与 2 同源）

语义：
  - 作用域 LIVE_KILL_SWITCH_SCOPE=live（默认）：拦截所有**实盘**新开仓/加仓；paper 不受影响。
    =all：paper 也一并拦截（演练/极端情况）。
  - 急停不阻止平仓/减仓（降风险动作永远放行）。
  - 可选动作 close_all：撤全部挂单 + 平全部仓（见 playbook.kill_close_all）。
  - 释放：删除文件 + env 非真。若 env 为真，文件释放无效（部署级更高）。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from backend.services.risk.trading_state import RISK_DATA_DIR

logger = logging.getLogger(__name__)

KILL_FILE = os.path.join(RISK_DATA_DIR, "KILL_SWITCH")


def _env_true(name: str) -> bool:
    return str(os.getenv(name, "") or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class KillSwitchStatus:
    engaged: bool
    scope: str            # live | all
    source: str           # env | file | none
    reason: str
    since: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engaged": self.engaged, "scope": self.scope, "source": self.source,
            "reason": self.reason, "since": self.since,
        }

    def blocks(self, *, live: bool) -> bool:
        """给定订单是否被急停拦截（仅对开仓/加仓调用）。"""
        if not self.engaged:
            return False
        if self.scope == "all":
            return True
        return bool(live)


def kill_switch_scope() -> str:
    v = str(os.getenv("LIVE_KILL_SWITCH_SCOPE", "live") or "live").strip().lower()
    return "all" if v == "all" else "live"


def kill_switch_status() -> KillSwitchStatus:
    scope = kill_switch_scope()
    if _env_true("LIVE_KILL_SWITCH"):
        return KillSwitchStatus(True, scope, "env", "LIVE_KILL_SWITCH=true (.env)", None)
    try:
        if os.path.isfile(KILL_FILE):
            reason = ""
            try:
                with open(KILL_FILE, "r", encoding="utf-8") as f:
                    reason = f.read().strip()[:300]
            except Exception:
                pass
            try:
                since = os.path.getmtime(KILL_FILE)
            except Exception:
                since = None
            return KillSwitchStatus(True, scope, "file", reason or "kill file present", since)
    except Exception as exc:  # pragma: no cover
        logger.debug("[KillSwitch] 文件检查异常: %s", exc)
    return KillSwitchStatus(False, scope, "none", "", None)


def kill_switch_engaged() -> bool:
    return kill_switch_status().engaged


def engage(reason: str, *, source: str = "api") -> KillSwitchStatus:
    """落盘 KILL 文件 + 状态机切 HALTED（不自动过期）+ P0 告警。"""
    os.makedirs(RISK_DATA_DIR, exist_ok=True)
    text = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] source={source} reason={str(reason or 'manual')[:200]}"
    tmp = KILL_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, KILL_FILE)
    try:
        from backend.services.risk.trading_state import get_state_store, TradingState
        get_state_store().set_state(
            TradingState.HALTED, reason=f"kill_switch: {reason}", source="kill_switch",
        )
    except Exception as exc:
        logger.error("[KillSwitch] 切换 HALTED 失败: %s", exc)
    _alert(f"🛑 LIVE_KILL_SWITCH 已启用\n来源: {source}\n原因: {reason}", level="critical")
    logger.critical("[KillSwitch] ENGAGED source=%s reason=%s", source, reason)
    return kill_switch_status()


def release(*, source: str = "api", reason: str = "") -> KillSwitchStatus:
    """删除 KILL 文件；若 env 仍为真则仍处于急停（返回状态告知）。状态机回 ACTIVE。"""
    try:
        if os.path.isfile(KILL_FILE):
            os.remove(KILL_FILE)
    except Exception as exc:
        logger.error("[KillSwitch] 删除 KILL 文件失败: %s", exc)
    st = kill_switch_status()
    if not st.engaged:
        try:
            from backend.services.risk.trading_state import get_state_store, TradingState
            store = get_state_store()
            if store.state() == TradingState.HALTED and str(store.snapshot().source) == "kill_switch":
                store.set_state(TradingState.ACTIVE, reason=f"kill_switch released: {reason}", source=source)
        except Exception as exc:
            logger.error("[KillSwitch] 恢复 ACTIVE 失败: %s", exc)
        _alert(f"✅ LIVE_KILL_SWITCH 已释放\n来源: {source} {reason}", level="warning")
        logger.warning("[KillSwitch] RELEASED source=%s", source)
    else:
        logger.warning("[KillSwitch] 文件已删但 env LIVE_KILL_SWITCH 仍为真，急停继续生效")
    return st


def _alert(text: str, *, level: str = "critical") -> None:
    """统一告警出口（飞书 + Telegram + webhook）；critical→P0，其余→P1。"""
    try:
        from backend.services.ops.alerts import send_alert
        send_alert("P0" if level == "critical" else "P1", "风控急停", text,
                   dedupe_key=f"kill_switch:{level}", source="kill_switch")
    except Exception as exc:  # pragma: no cover
        logger.debug("[KillSwitch] 告警发送失败: %s", exc)
