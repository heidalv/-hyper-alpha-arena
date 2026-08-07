"""
爆仓预警监控器 — LiquidationMonitor

实时监控所有持仓的爆仓风险，多级阈值预警与强制平仓触发。

设计依据：见《001Alpha重构修订方案》§3 新增模块。
预警级别：
  - SAFE:    距爆仓 > 15%
  - WARNING: 距爆仓 10%~15%
  - DANGER:  距爆仓 5%~10%
  - CRITICAL: 距爆仓 < 5% → 触发紧急平仓
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class LiquidationRiskLevel(str, Enum):
    SAFE = "safe"
    WARNING = "warning"
    DANGER = "danger"
    CRITICAL = "critical"    # 触发紧急平仓


@dataclass
class PositionRisk:
    """单个持仓的爆仓风险评估"""
    symbol: str
    side: str                       # "long" / "short"
    entry_price: float
    mark_price: float
    liquidation_price: float
    leverage: float
    unrealized_pnl: float
    distance_to_liq_pct: float      # 距爆仓价格的百分比距离（越小越危险）
    risk_level: LiquidationRiskLevel
    account_id: int
    position_value: float
    margin_used: float
    timestamp: float = field(default_factory=lambda: time.time())


@dataclass
class LiquidationAlert:
    """爆仓预警事件"""
    account_id: int
    symbol: str
    risk_level: LiquidationRiskLevel
    distance_to_liq_pct: float
    mark_price: float
    liquidation_price: float
    message: str
    triggered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    action_taken: str = "none"     # "none" / "alert" / "emergency_close"


class LiquidationMonitor:
    """
    爆仓风险实时预警服务。

    功能：
    1. 定期扫描所有持仓的爆仓风险
    2. 多级阈值预警（WARNING / DANGER / CRITICAL）
    3. CRITICAL 级别时触发紧急平仓回调
    4. 记录预警历史
    """

    # 距爆仓距离阈值（百分比）
    SAFE_THRESHOLD: float = 15.0
    WARNING_THRESHOLD: float = 10.0
    DANGER_THRESHOLD: float = 5.0
    CRITICAL_THRESHOLD: float = 3.0   # 低于此距离触发紧急平仓

    SCAN_INTERVAL: float = 30.0   # 每30秒扫描一次

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._position_risks: Dict[str, PositionRisk] = {}   # key: f"{account_id}_{symbol}"
        self._alert_history: List[LiquidationAlert] = []
        self._emergency_close_callbacks: List[Any] = []
        logger.info("[LiquidationMonitor] 初始化完成")

    def start(self):
        """启动后台监控线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="liquidation-monitor"
        )
        self._thread.start()
        logger.info("[LiquidationMonitor] 监控线程已启动")

    def stop(self):
        """停止监控"""
        self._running = False
        logger.info("[LiquidationMonitor] 监控线程已停止")

    def register_emergency_close_callback(self, callback):
        """注册紧急平仓回调函数。
        
        callback(account_id: int, symbol: str, risk: PositionRisk) -> None
        """
        self._emergency_close_callbacks.append(callback)

    def _monitor_loop(self):
        while self._running:
            try:
                self._scan_all_positions()
            except Exception as e:
                logger.error(f"[LiquidationMonitor] 扫描异常: {e}", exc_info=True)
            time.sleep(self.SCAN_INTERVAL)

    def _scan_all_positions(self):
        """扫描所有账户的持仓风险"""
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import Account
            from backend.services.hyperliquid_trading_client import get_hyperliquid_client

            db = SessionLocal()
            try:
                accounts = db.query(Account).filter(Account.is_active == "true").all()
                for account in accounts:
                    try:
                        self._scan_account_positions(db, account)
                    except Exception as e:
                        logger.debug(f"[LiquidationMonitor] 账户 {account.id} 扫描失败: {e}")
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[LiquidationMonitor] _scan_all_positions error: {e}")

    def _scan_account_positions(self, db, account):
        """扫描单个账户的所有持仓"""
        try:
            from backend.services.hyperliquid_trading_client import get_hyperliquid_client
            client = get_hyperliquid_client(db, account.id)
            if not client:
                return

            positions = client.get_positions(db)
            for pos in positions:
                risk = self._evaluate_position_risk(account.id, pos)
                if risk:
                    key = f"{account.id}_{risk.symbol}"
                    with self._lock:
                        self._position_risks[key] = risk
                    self._handle_risk_level(risk)
        except Exception as e:
            logger.debug(f"[LiquidationMonitor] 账户 {account.id} 持仓扫描失败: {e}")

    def _evaluate_position_risk(self, account_id: int, pos: dict) -> Optional[PositionRisk]:
        """评估单个持仓的爆仓风险"""
        try:
            symbol = pos.get("coin") or pos.get("symbol", "")
            szi = float(pos.get("szi") or pos.get("size") or 0)
            if abs(szi) < 1e-9:
                return None  # 空仓跳过

            side = "long" if szi > 0 else "short"
            entry_price = float(pos.get("entryPx") or pos.get("entry_price") or 0)
            mark_price = float(pos.get("markPx") or pos.get("mark_price") or 0)
            liq_price = float(pos.get("liquidationPx") or pos.get("liquidation_price") or 0)
            leverage = float(pos.get("leverage") or 1)
            unrealized_pnl = float(pos.get("unrealizedPnl") or pos.get("unrealized_pnl") or 0)
            position_value = abs(szi) * mark_price
            margin_used = position_value / leverage if leverage > 0 else position_value

            if liq_price <= 0 or mark_price <= 0:
                return None

            # 距爆仓距离
            distance_pct = abs(mark_price - liq_price) / mark_price * 100

            if distance_pct >= self.SAFE_THRESHOLD:
                level = LiquidationRiskLevel.SAFE
            elif distance_pct >= self.WARNING_THRESHOLD:
                level = LiquidationRiskLevel.WARNING
            elif distance_pct >= self.DANGER_THRESHOLD:
                level = LiquidationRiskLevel.DANGER
            else:
                level = LiquidationRiskLevel.CRITICAL

            return PositionRisk(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                mark_price=mark_price,
                liquidation_price=liq_price,
                leverage=leverage,
                unrealized_pnl=unrealized_pnl,
                distance_to_liq_pct=round(distance_pct, 2),
                risk_level=level,
                account_id=account_id,
                position_value=round(position_value, 2),
                margin_used=round(margin_used, 2),
            )
        except Exception as e:
            logger.debug(f"[LiquidationMonitor] _evaluate_position_risk error: {e}")
            return None

    def _handle_risk_level(self, risk: PositionRisk):
        """根据风险级别触发相应动作"""
        if risk.risk_level == LiquidationRiskLevel.SAFE:
            return

        action = "alert"
        msg = (
            f"[{risk.risk_level.value.upper()}] {risk.symbol} "
            f"{risk.side} 距爆仓 {risk.distance_to_liq_pct:.1f}% "
            f"(标价={risk.mark_price:.4f}, 爆仓价={risk.liquidation_price:.4f})"
        )

        if risk.risk_level == LiquidationRiskLevel.CRITICAL:
            logger.critical(f"[LiquidationMonitor] 爆仓极危: {msg}")
            action = "emergency_close"
            self._trigger_emergency_close(risk)
        elif risk.risk_level == LiquidationRiskLevel.DANGER:
            logger.warning(f"[LiquidationMonitor] 爆仓危险: {msg}")
        else:
            logger.warning(f"[LiquidationMonitor] 爆仓预警: {msg}")

        # ── 飞书通知：爆仓预警 ──
        if risk.risk_level in (LiquidationRiskLevel.DANGER, LiquidationRiskLevel.CRITICAL):
            try:
                import asyncio
                from backend.services.openclaw_notify import notify_liquidation_warning
                _coro = notify_liquidation_warning(
                    symbol=risk.symbol, side=risk.side,
                    distance_pct=risk.distance_to_liq_pct,
                    risk_level=risk.risk_level.value,
                )
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(_coro)
                except RuntimeError:
                    asyncio.run(_coro)
            except Exception as _nf_err:
                logger.debug(f"[LiquidationMonitor] 通知发送失败(非致命): {_nf_err}")

        alert = LiquidationAlert(
            account_id=risk.account_id,
            symbol=risk.symbol,
            risk_level=risk.risk_level,
            distance_to_liq_pct=risk.distance_to_liq_pct,
            mark_price=risk.mark_price,
            liquidation_price=risk.liquidation_price,
            message=msg,
            action_taken=action,
        )
        with self._lock:
            self._alert_history.append(alert)
            if len(self._alert_history) > 500:
                self._alert_history = self._alert_history[-500:]

        # 深挖第 3 轮 (2026-05-08)：把 DANGER / CRITICAL 事件统一落盘
        if risk.risk_level in (LiquidationRiskLevel.DANGER, LiquidationRiskLevel.CRITICAL):
            try:
                from backend.database.connection import SessionLocal as _SL
                from backend.services.unified_risk_gate import record_guard_block
                _db = _SL()
                try:
                    record_guard_block(
                        _db, account_id=risk.account_id,
                        guard_name="liquidation_monitor",
                        symbol=risk.symbol, side=risk.side,
                        reason=msg,
                        extra={
                            "risk_level": risk.risk_level.value,
                            "distance_to_liq_pct": risk.distance_to_liq_pct,
                            "mark_price": risk.mark_price,
                            "liquidation_price": risk.liquidation_price,
                            "action_taken": action,
                        },
                    )
                    _db.commit()
                finally:
                    _db.close()
            except Exception as _evt_err:
                logger.debug(f"[LiquidationMonitor] 落盘事件失败(非致命): {_evt_err}")

    def _trigger_emergency_close(self, risk: PositionRisk):
        """触发紧急平仓回调"""
        for cb in self._emergency_close_callbacks:
            try:
                cb(risk.account_id, risk.symbol, risk)
            except Exception as e:
                logger.error(f"[LiquidationMonitor] 紧急平仓回调失败: {e}")

    def get_position_risks(self) -> List[PositionRisk]:
        """获取当前所有持仓风险列表（不含 SAFE）"""
        with self._lock:
            return [r for r in self._position_risks.values()
                    if r.risk_level != LiquidationRiskLevel.SAFE]

    def get_alert_history(self, limit: int = 50) -> List[dict]:
        """获取最近的预警记录"""
        with self._lock:
            recent = self._alert_history[-limit:]
            return [
                {
                    "account_id": a.account_id,
                    "symbol": a.symbol,
                    "risk_level": a.risk_level.value,
                    "distance_pct": a.distance_to_liq_pct,
                    "mark_price": a.mark_price,
                    "liq_price": a.liquidation_price,
                    "message": a.message,
                    "triggered_at": a.triggered_at,
                    "action": a.action_taken,
                }
                for a in reversed(recent)
            ]

    def get_status(self) -> dict:
        with self._lock:
            counts = {lv.value: 0 for lv in LiquidationRiskLevel}
            for r in self._position_risks.values():
                counts[r.risk_level.value] += 1
        return {
            "running": self._running,
            "positions_monitored": len(self._position_risks),
            "risk_counts": counts,
            "alerts_total": len(self._alert_history),
        }


# 模块级单例
liquidation_monitor = LiquidationMonitor()
