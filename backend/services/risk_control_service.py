"""
Risk Control Service - 风控服务模块

统一管理所有风险控制检查：
1. 单币种最大仓位限制 (默认30%权益)
2. 日亏损熔断机制 (默认5%日亏损停止交易)
3. 总仓位限制 (默认不超过权益的N倍)
4. 保证金使用率检查
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Any, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text, func
from dataclasses import dataclass
from enum import Enum

from backend.services.strategy_params_registry import RISK_LIMITS

logger = logging.getLogger(__name__)


class RiskCheckResult(Enum):
    """风控检查结果"""
    PASSED = "passed"
    BLOCKED = "blocked"
    WARNING = "warning"


@dataclass
class RiskControlConfig:
    """风控配置参数 — 默认值从统一注册表 RISK_LIMITS 读取"""
    # 单币种最大仓位比例 (占总权益) — 注册表 max_symbol_notional_pct / 100
    max_single_symbol_ratio: float = RISK_LIMITS["max_symbol_notional_pct"].default / 100.0

    # 日亏损熔断阈值 — 注册表 max_daily_loss_pct / 100
    daily_loss_limit_ratio: float = RISK_LIMITS["max_daily_loss_pct"].default / 100.0

    # 总仓位限制 (总仓位价值 / 总权益 的最大倍数)
    max_total_position_multiple: float = RISK_LIMITS["max_total_position_multiple"].default

    # 保证金使用率限制
    max_margin_usage_ratio: float = RISK_LIMITS["max_margin_usage_pct"].default / 100.0

    # 熔断冷却时间 (小时)
    circuit_breaker_cooldown_hours: int = int(RISK_LIMITS["circuit_breaker_cooldown_hours"].default)

    # 是否启用各项风控
    enable_single_symbol_limit: bool = True
    enable_daily_loss_breaker: bool = True
    enable_total_position_limit: bool = True
    enable_margin_check: bool = True

    # ── Phase 3B §修复⑤：新增风控规则 ──
    # 单笔最大亏损 — 注册表 max_loss_per_trade_pct / 100
    max_loss_per_trade_ratio: float = RISK_LIMITS["max_loss_per_trade_pct"].default / 100.0
    # 单笔最大仓位 — 注册表 max_position_per_trade_pct / 100
    max_position_per_trade_ratio: float = RISK_LIMITS["max_position_per_trade_pct"].default / 100.0
    # 最大杠杆
    max_leverage: int = int(RISK_LIMITS["max_leverage"].default)
    # 日内交易次数极端安全网
    max_daily_trades: int = int(RISK_LIMITS["max_daily_trades"].default)
    # 连续亏损缩减阈值
    consecutive_loss_reduce_threshold: int = int(RISK_LIMITS["consecutive_loss_reduce"].default)
    # 连续亏损暂停阈值
    consecutive_loss_pause_threshold: int = int(RISK_LIMITS["consecutive_loss_pause"].default)
    # 单 symbol 24小时内最多开仓次数
    max_symbol_entries_per_day: int = 3


@dataclass
class RiskCheckResponse:
    """风控检查响应"""
    result: RiskCheckResult
    check_name: str
    message: str
    details: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.details is None:
            self.details = {}


class RiskControlService:
    """风控服务"""
    
    def __init__(self, config: Optional[RiskControlConfig] = None):
        self.config = config or RiskControlConfig()
        self._circuit_breaker_cache: Dict[int, datetime] = {}  # account_id -> breaker_time
        self._restore_circuit_breakers_from_db()

    def _restore_circuit_breakers_from_db(self):
        """P0-4: 从 DB 恢复熔断状态（防止重启丢失）"""
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import FullAutoSession
            from datetime import timezone as _tz
            db = SessionLocal()
            try:
                now = datetime.now(_tz.utc)
                sessions = db.query(FullAutoSession).filter(
                    FullAutoSession.circuit_breaker_until.isnot(None),
                    FullAutoSession.circuit_breaker_until > now,
                ).all()
                for s in sessions:
                    self._circuit_breaker_cache[s.account_id] = (
                        s.circuit_breaker_until.replace(tzinfo=_tz.utc)
                    )
                if self._circuit_breaker_cache:
                    logger.info(
                        f"[RiskControl] 从DB恢复了 {len(self._circuit_breaker_cache)} 个熔断状态"
                    )
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[RiskControl] 恢复熔断状态失败（首次启动或表未创建）: {e}")

    def load_config_from_db(self, db: Session, account_id: int) -> None:
        """从数据库加载账户特定的风控配置"""
        try:
            result = db.execute(
                text("""
                    SELECT * FROM risk_control_configs 
                    WHERE account_id = :account_id AND is_active = 'true'
                    LIMIT 1
                """),
                {"account_id": account_id}
            )
            row = result.fetchone()
            
            if row:
                row_dict = dict(row._mapping) if hasattr(row, '_mapping') else dict(zip(result.keys(), row))
                self.config = RiskControlConfig(
                    max_single_symbol_ratio=float(row_dict.get('max_single_symbol_ratio', 0.30)),
                    daily_loss_limit_ratio=float(row_dict.get('daily_loss_limit_ratio', 0.05)),
                    max_total_position_multiple=float(row_dict.get('max_total_position_multiple', 3.0)),
                    max_margin_usage_ratio=float(row_dict.get('max_margin_usage_ratio', 0.70)),
                    circuit_breaker_cooldown_hours=int(row_dict.get('circuit_breaker_cooldown_hours', 24)),
                    enable_single_symbol_limit=row_dict.get('enable_single_symbol_limit', 'true') == 'true',
                    enable_daily_loss_breaker=row_dict.get('enable_daily_loss_breaker', 'true') == 'true',
                    enable_total_position_limit=row_dict.get('enable_total_position_limit', 'true') == 'true',
                    enable_margin_check=row_dict.get('enable_margin_check', 'true') == 'true',
                )
                logger.info(f"Loaded risk control config for account {account_id}")
        except Exception as e:
            # [fix] rollback 避免 InFailedSqlTransaction 污染调用方 session
            try:
                db.rollback()
            except Exception:
                pass
            logger.warning(f"Failed to load risk control config for account {account_id}: {e}, using defaults")
    
    def check_all(
        self,
        db: Session,
        account_id: int,
        symbol: str,
        operation: str,  # "buy", "sell", "close"
        order_value: float,
        total_equity: float,
        available_balance: float,
        positions: List[Dict[str, Any]],
        margin_usage_percent: float = 0,
        order_margin: Optional[float] = None,
    ) -> Tuple[bool, List[RiskCheckResponse]]:
        """
        执行所有风控检查
        
        Returns:
            Tuple[bool, List[RiskCheckResponse]]: (是否通过, 检查结果列表)
        """
        responses: List[RiskCheckResponse] = []
        all_passed = True
        
        # 平仓操作不受大部分风控限制
        if operation == "close":
            responses.append(RiskCheckResponse(
                result=RiskCheckResult.PASSED,
                check_name="close_operation",
                message="平仓操作不受风控限制"
            ))
            return True, responses
        
        # 1. 日亏损熔断检查 (最高优先级)
        if self.config.enable_daily_loss_breaker:
            response = self.check_daily_loss_breaker(db, account_id, total_equity)
            responses.append(response)
            if response.result == RiskCheckResult.BLOCKED:
                all_passed = False
                logger.warning(f"[RISK] Account {account_id} blocked by daily loss breaker: {response.message}")
                return False, responses  # 熔断状态下直接返回
        
        # 2. 保证金使用率检查
        if self.config.enable_margin_check:
            response = self.check_margin_usage(margin_usage_percent)
            responses.append(response)
            if response.result == RiskCheckResult.BLOCKED:
                all_passed = False
        
        # 3. 单币种最大仓位检查（保证金基准 — 与 deterministic_risk_gate Rule 1 一致）
        # 2026-05-08 深挖第 4 轮 修复：原用 notional 基准在 10x 杠杆下等于 2.5% margin，过严
        if self.config.enable_single_symbol_limit:
            _basis = order_margin if (order_margin and order_margin > 0) else order_value
            response = self.check_single_symbol_limit(
                symbol, _basis, total_equity, positions, basis_label=("保证金" if order_margin else "敞口")
            )
            responses.append(response)
            if response.result == RiskCheckResult.BLOCKED:
                all_passed = False
        
        # 4. 总仓位限制检查
        if self.config.enable_total_position_limit:
            response = self.check_total_position_limit(
                order_value, total_equity, positions
            )
            responses.append(response)
            if response.result == RiskCheckResult.BLOCKED:
                all_passed = False

        # 5. 单笔最大仓位（保证金基准 — 与 single_symbol_limit 同源）
        # 2026-05-08 深挖第 4 轮 修复：原用 notional 基准在 10x 杠杆下等于 2% margin，过严
        _per_trade_basis = order_margin if (order_margin and order_margin > 0) else order_value
        _per_trade_label = "保证金" if order_margin else "敞口"
        _per_trade_max = total_equity * self.config.max_position_per_trade_ratio
        if _per_trade_basis > _per_trade_max:
            responses.append(RiskCheckResponse(
                result=RiskCheckResult.BLOCKED,
                check_name="max_position_per_trade",
                message=f"单笔{_per_trade_label} ${_per_trade_basis:.2f} 超过账户 {self.config.max_position_per_trade_ratio*100:.0f}% 上限 ${_per_trade_max:.2f}",
                details={
                    "order_basis": _per_trade_basis,
                    "max_allowed": _per_trade_max,
                    "basis": _per_trade_label,
                }
            ))
            all_passed = False

        # 6. 日内交易次数极端安全网（V5_DAILY_TRADE_CAP_ENABLED=false 时跳过）
        try:
            from backend.config.settings import (
                V5_DAILY_TRADE_CAP_ENABLED,
                V5_MAX_SYMBOL_TRADES_PER_DAY,
            )
        except Exception:
            V5_DAILY_TRADE_CAP_ENABLED = False
            V5_MAX_SYMBOL_TRADES_PER_DAY = 0

        if V5_DAILY_TRADE_CAP_ENABLED:
            daily_trades = self._count_daily_trades(db, account_id)
            # 单一来源：日交易上限统一取 runtime_tuning（与 V5 unified_gate 同源），
            # 避免此前 risk_control 与 V5 各持一个不同的 max_daily_trades。
            # V5 gate 是「主门」（按 cap 拦截）；此处作为更宽的极端安全网（cap×1.5），
            # 仅在主门被绕过/失效时兜底，不与主门重复在同一数值上拦截。
            try:
                from backend.services.runtime_tuning_store import get_tuning_int
                _primary_cap = get_tuning_int("max_daily_trades", int(self.config.max_daily_trades))
            except Exception:
                _primary_cap = int(self.config.max_daily_trades)
            _safety_net = max(_primary_cap + 1, int(_primary_cap * 1.5 + 0.5))
            if daily_trades >= _safety_net:
                responses.append(RiskCheckResponse(
                    result=RiskCheckResult.BLOCKED,
                    check_name="max_daily_trades",
                    message=f"今日交易 {daily_trades} 笔已触发极端安全网（{_safety_net}=主门{_primary_cap}×1.5），系统保护暂停",
                    details={"daily_trades": daily_trades, "max": _safety_net, "primary_cap": _primary_cap}
                ))
                all_passed = False

            # 7. 单币种日入场次数限制 (F0-3)
            symbol_cap = V5_MAX_SYMBOL_TRADES_PER_DAY or self.config.max_symbol_entries_per_day
            symbol_entries = self._count_symbol_daily_entries(db, account_id, symbol)
            if symbol_cap > 0 and symbol_entries >= symbol_cap:
                responses.append(RiskCheckResponse(
                    result=RiskCheckResult.BLOCKED,
                    check_name="max_symbol_entries_per_day",
                    message=f"{symbol} 今日入场 {symbol_entries} 次已达上限({symbol_cap})",
                    details={"symbol": symbol, "daily_entries": symbol_entries, "max": symbol_cap}
                ))
                all_passed = False

        # 8. Phase 3B §修复⑤：连续亏损熔断
        try:
            from backend.config.settings import CONSECUTIVE_LOSS_PROTECTION_ENABLED
            _loss_prot = bool(CONSECUTIVE_LOSS_PROTECTION_ENABLED)
        except Exception:
            _loss_prot = True
        if _loss_prot:
            consecutive_losses = self._count_consecutive_losses(db, account_id)
            if consecutive_losses >= self.config.consecutive_loss_pause_threshold:
                responses.append(RiskCheckResponse(
                    result=RiskCheckResult.BLOCKED,
                    check_name="consecutive_loss_pause",
                    message=f"连续亏损 {consecutive_losses} 次（≥{self.config.consecutive_loss_pause_threshold}），暂停交易24小时",
                    details={"consecutive_losses": consecutive_losses}
                ))
                all_passed = False
            elif consecutive_losses >= self.config.consecutive_loss_reduce_threshold:
                responses.append(RiskCheckResponse(
                    result=RiskCheckResult.WARNING,
                    check_name="consecutive_loss_reduce",
                    message=f"连续亏损 {consecutive_losses} 次（≥{self.config.consecutive_loss_reduce_threshold}），建议仓位缩减50%",
                    details={"consecutive_losses": consecutive_losses, "suggestion": "reduce_position_50pct"}
                ))

        # 9. 积分/返利套利全局敞口限制
        try:
            from backend.services.rebate_arb.capital_coordinator import capital_coordinator
            rebate_status = capital_coordinator.get_status()
            rebate_used = rebate_status.used.get("rebate_points_arb", 0.0)
            rebate_alloc = rebate_status.allocations.get("rebate_points_arb", 0.0)
            rebate_utilization = rebate_used / max(rebate_alloc, 1.0)
            if rebate_utilization > 0.9:
                responses.append(RiskCheckResponse(
                    result=RiskCheckResult.WARNING,
                    check_name="rebate_arb_capital_limit",
                    message=f"积分套利资金池使用率 {rebate_utilization:.0%}，已接近上限",
                    details={"utilization": rebate_utilization, "used": rebate_used, "allocated": rebate_alloc}
                ))
        except ImportError:
            pass  # 积分套利模块未安装时跳过

        return all_passed, responses
    
    def check_single_symbol_limit(
        self,
        symbol: str,
        new_order_value: float,
        total_equity: float,
        positions: List[Dict[str, Any]],
        basis_label: str = "敞口",
    ) -> RiskCheckResponse:
        """
        检查单币种最大仓位限制

        2026-05-08 深挖第 4 轮 修复：
        - 当 caller 通过 check_all 提供了 order_margin 时，使用保证金基准（更合理）
        - 兼容旧 caller：默认仍按传入值（可能是名义敞口）做限制
        """
        if total_equity <= 0:
            return RiskCheckResponse(
                result=RiskCheckResult.BLOCKED,
                check_name="single_symbol_limit",
                message="总权益为0，无法开仓"
            )

        max_allowed = total_equity * self.config.max_single_symbol_ratio

        # 计算该币种当前仓位（保证金基准时累加 margin，敞口基准时累加 notional）
        current_symbol_value = 0.0
        _use_margin_basis = (basis_label == "保证金")
        for pos in positions:
            pos_symbol = pos.get('coin') or pos.get('symbol', '')
            if pos_symbol.upper() == symbol.upper():
                if _use_margin_basis:
                    current_symbol_value = abs(float(pos.get('margin', 0) or 0))
                else:
                    current_symbol_value = abs(
                        float(pos.get('position_value', 0) or pos.get('value', 0) or 0)
                    )
                break

        projected_value = current_symbol_value + new_order_value
        usage_ratio = projected_value / total_equity

        if projected_value > max_allowed:
            return RiskCheckResponse(
                result=RiskCheckResult.BLOCKED,
                check_name="single_symbol_limit",
                message=f"单币种{symbol}{basis_label}${projected_value:.2f}超过限制${max_allowed:.2f} ({self.config.max_single_symbol_ratio*100:.0f}%权益)",
                details={
                    "symbol": symbol,
                    "current_value": current_symbol_value,
                    "new_order_value": new_order_value,
                    "projected_value": projected_value,
                    "max_allowed": max_allowed,
                    "total_equity": total_equity,
                    "usage_ratio": usage_ratio,
                    "basis": basis_label,
                }
            )

        if usage_ratio > self.config.max_single_symbol_ratio * 0.8:
            return RiskCheckResponse(
                result=RiskCheckResult.WARNING,
                check_name="single_symbol_limit",
                message=f"单币种{symbol}{basis_label}接近限制 ({usage_ratio*100:.1f}%/{self.config.max_single_symbol_ratio*100:.0f}%)",
                details={"usage_ratio": usage_ratio, "basis": basis_label}
            )

        return RiskCheckResponse(
            result=RiskCheckResult.PASSED,
            check_name="single_symbol_limit",
            message=f"单币种{basis_label}检查通过 ({usage_ratio*100:.1f}%/{self.config.max_single_symbol_ratio*100:.0f}%)",
            details={"usage_ratio": usage_ratio, "basis": basis_label}
        )
    
    def check_daily_loss_breaker(
        self,
        db: Session,
        account_id: int,
        current_equity: float,
    ) -> RiskCheckResponse:
        """
        检查日亏损熔断机制
        
        如果当日亏损超过阈值，触发熔断，停止交易直到冷却期结束
        """
        # 检查是否在熔断冷却期
        if account_id in self._circuit_breaker_cache:
            breaker_time = self._circuit_breaker_cache[account_id]
            cooldown_end = breaker_time + timedelta(hours=self.config.circuit_breaker_cooldown_hours)
            if datetime.now(timezone.utc) < cooldown_end:
                remaining_hours = (cooldown_end - datetime.now(timezone.utc)).total_seconds() / 3600
                return RiskCheckResponse(
                    result=RiskCheckResult.BLOCKED,
                    check_name="daily_loss_breaker",
                    message=f"熔断冷却中，剩余{remaining_hours:.1f}小时",
                    details={
                        "breaker_time": breaker_time.isoformat(),
                        "cooldown_end": cooldown_end.isoformat(),
                        "remaining_hours": remaining_hours,
                    }
                )
            else:
                # 冷却期结束，清除记录
                del self._circuit_breaker_cache[account_id]
        
        # 获取当日初始权益 (从快照表)
        try:
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            
            # 尝试从 hyperliquid_account_snapshots 获取当日初始权益
            result = db.execute(
                text("""
                    SELECT total_equity 
                    FROM hyperliquid_account_snapshots 
                    WHERE account_id = :account_id 
                    AND snapshot_time >= :today_start
                    ORDER BY snapshot_time ASC
                    LIMIT 1
                """),
                {"account_id": account_id, "today_start": today_start}
            )
            row = result.fetchone()
            
            if row:
                start_equity = float(row[0])
            else:
                # 没有当日快照，尝试获取昨日收盘
                result = db.execute(
                    text("""
                        SELECT total_equity 
                        FROM hyperliquid_account_snapshots 
                        WHERE account_id = :account_id 
                        AND snapshot_time < :today_start
                        ORDER BY snapshot_time DESC
                        LIMIT 1
                    """),
                    {"account_id": account_id, "today_start": today_start}
                )
                row = result.fetchone()
                if row:
                    start_equity = float(row[0])
                else:
                    # 没有历史快照，使用当前权益作为基准 (首次运行)
                    logger.info(f"No historical snapshot for account {account_id}, using current equity as baseline")
                    return RiskCheckResponse(
                        result=RiskCheckResult.PASSED,
                        check_name="daily_loss_breaker",
                        message="首次运行，无历史数据，跳过日亏损检查"
                    )
            
            # 计算日亏损
            if start_equity <= 0:
                return RiskCheckResponse(
                    result=RiskCheckResult.PASSED,
                    check_name="daily_loss_breaker",
                    message="初始权益为0，跳过日亏损检查"
                )
            
            daily_pnl = current_equity - start_equity
            daily_loss_ratio = -daily_pnl / start_equity if daily_pnl < 0 else 0
            
            if daily_loss_ratio >= self.config.daily_loss_limit_ratio:
                # 触发熔断
                breaker_time = datetime.now(timezone.utc)
                self._circuit_breaker_cache[account_id] = breaker_time

                # P0-4: 持久化熔断状态到 DB，防止重启丢失
                try:
                    from backend.database.models import FullAutoSession
                    _session = db.query(FullAutoSession).filter(
                        FullAutoSession.account_id == account_id,
                        FullAutoSession.status.in_(["running", "defensive"]),
                    ).order_by(FullAutoSession.started_at.desc()).first()
                    if _session:
                        _session.circuit_breaker_until = (
                            breaker_time + timedelta(hours=self.config.circuit_breaker_cooldown_hours)
                        )
                        db.commit()
                        logger.info(f"[RiskControl] 熔断状态已持久化: account={account_id}, "
                                    f"until={_session.circuit_breaker_until}")
                except Exception as _e:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    logger.debug(f"[RiskControl] 持久化熔断状态失败: {_e}")

                # 记录熔断事件
                self._record_circuit_breaker_event(db, account_id, start_equity, current_equity, daily_loss_ratio)
                
                return RiskCheckResponse(
                    result=RiskCheckResult.BLOCKED,
                    check_name="daily_loss_breaker",
                    message=f"触发日亏损熔断！当日亏损{daily_loss_ratio*100:.2f}% 超过阈值{self.config.daily_loss_limit_ratio*100:.0f}%",
                    details={
                        "start_equity": start_equity,
                        "current_equity": current_equity,
                        "daily_pnl": daily_pnl,
                        "daily_loss_ratio": daily_loss_ratio,
                        "limit_ratio": self.config.daily_loss_limit_ratio,
                        "cooldown_hours": self.config.circuit_breaker_cooldown_hours,
                    }
                )
            
            # 警告级别：亏损超过80%阈值
            if daily_loss_ratio >= self.config.daily_loss_limit_ratio * 0.8:
                return RiskCheckResponse(
                    result=RiskCheckResult.WARNING,
                    check_name="daily_loss_breaker",
                    message=f"日亏损接近熔断阈值 ({daily_loss_ratio*100:.2f}%/{self.config.daily_loss_limit_ratio*100:.0f}%)",
                    details={
                        "daily_pnl": daily_pnl,
                        "daily_loss_ratio": daily_loss_ratio,
                    }
                )
            
            return RiskCheckResponse(
                result=RiskCheckResult.PASSED,
                check_name="daily_loss_breaker",
                message=f"日亏损检查通过 (当日盈亏: ${daily_pnl:.2f}, {daily_loss_ratio*100:.2f}%)",
                details={
                    "daily_pnl": daily_pnl,
                    "daily_loss_ratio": daily_loss_ratio,
                }
            )
            
        except Exception as e:
            # [fix] rollback 避免 InFailedSqlTransaction 污染调用方 session
            try:
                db.rollback()
            except Exception:
                pass
            logger.error(f"Failed to check daily loss breaker: {e}", exc_info=True)
            # F1-7: 尝试从内存cache获取上次已知的熔断状态
            cached_breaker = self._circuit_breaker_cache.get(account_id)
            if cached_breaker and cached_breaker > datetime.now(timezone.utc):
                return RiskCheckResponse(
                    result=RiskCheckResult.BLOCKED,
                    check_name="daily_loss_breaker",
                    message=f"日亏损熔断中(cache), until={cached_breaker.isoformat()}",
                    details={"cached": True}
                )
            # 如果cache为空，仍允许通过但标ERROR（比WARNING更醒目）
            logger.error(f"[RiskCtrl] 日亏损检查DB故障且无cache, 降级放行(需人工检查)")
            return RiskCheckResponse(
                result=RiskCheckResult.WARNING,
                check_name="daily_loss_breaker",
                message=f"日亏损检查异常(DOWNGRADE): {str(e)[:100]}",
                details={"error": str(e), "downgraded": True}
            )
    
    def check_total_position_limit(
        self,
        new_order_value: float,
        total_equity: float,
        positions: List[Dict[str, Any]],
    ) -> RiskCheckResponse:
        """
        检查总仓位限制
        
        确保总仓位价值不超过总权益的指定倍数 (默认3倍)
        """
        if total_equity <= 0:
            return RiskCheckResponse(
                result=RiskCheckResult.BLOCKED,
                check_name="total_position_limit",
                message="总权益为0，无法开仓"
            )
        
        max_allowed = total_equity * self.config.max_total_position_multiple
        
        # 计算当前总仓位价值
        current_total_value = 0.0
        for pos in positions:
            pos_value = abs(float(pos.get('position_value', 0) or 0))
            current_total_value += pos_value
        
        # 计算新增后的总仓位
        projected_value = current_total_value + new_order_value
        usage_multiple = projected_value / total_equity
        
        if projected_value > max_allowed:
            return RiskCheckResponse(
                result=RiskCheckResult.BLOCKED,
                check_name="total_position_limit",
                message=f"总仓位${projected_value:.2f}超过限制${max_allowed:.2f} ({self.config.max_total_position_multiple:.1f}x权益)",
                details={
                    "current_total_value": current_total_value,
                    "new_order_value": new_order_value,
                    "projected_value": projected_value,
                    "max_allowed": max_allowed,
                    "total_equity": total_equity,
                    "usage_multiple": usage_multiple,
                }
            )
        
        # 警告级别：超过80%限制
        if usage_multiple > self.config.max_total_position_multiple * 0.8:
            return RiskCheckResponse(
                result=RiskCheckResult.WARNING,
                check_name="total_position_limit",
                message=f"总仓位接近限制 ({usage_multiple:.2f}x/{self.config.max_total_position_multiple:.1f}x)",
                details={"usage_multiple": usage_multiple}
            )
        
        return RiskCheckResponse(
            result=RiskCheckResult.PASSED,
            check_name="total_position_limit",
            message=f"总仓位检查通过 ({usage_multiple:.2f}x/{self.config.max_total_position_multiple:.1f}x)",
            details={"usage_multiple": usage_multiple}
        )
    
    def check_margin_usage(self, margin_usage_percent: float) -> RiskCheckResponse:
        """
        检查保证金使用率
        """
        max_usage = self.config.max_margin_usage_ratio * 100
        
        if margin_usage_percent >= max_usage:
            return RiskCheckResponse(
                result=RiskCheckResult.BLOCKED,
                check_name="margin_usage",
                message=f"保证金使用率{margin_usage_percent:.1f}%超过限制{max_usage:.0f}%",
                details={
                    "margin_usage_percent": margin_usage_percent,
                    "max_usage_percent": max_usage,
                }
            )
        
        # 警告级别：超过80%限制
        if margin_usage_percent >= max_usage * 0.85:
            return RiskCheckResponse(
                result=RiskCheckResult.WARNING,
                check_name="margin_usage",
                message=f"保证金使用率较高 ({margin_usage_percent:.1f}%/{max_usage:.0f}%)",
                details={"margin_usage_percent": margin_usage_percent}
            )
        
        return RiskCheckResponse(
            result=RiskCheckResult.PASSED,
            check_name="margin_usage",
            message=f"保证金检查通过 ({margin_usage_percent:.1f}%/{max_usage:.0f}%)",
            details={"margin_usage_percent": margin_usage_percent}
        )
    
    def _count_daily_trades(self, db: Session, account_id: int) -> int:
        """统计今日已执行交易次数（Phase 3B §修复⑤）"""
        try:
            from sqlalchemy import text as _t
            result = db.execute(
                _t("""
                    SELECT COUNT(*) FROM ai_decision_logs
                    WHERE account_id = :aid
                      AND executed = 'true'
                      AND operation IN ('buy', 'sell')
                      AND decision_time >= CURRENT_DATE
                """),
                {"aid": account_id}
            )
            row = result.fetchone()
            return int(row[0]) if row else 0
        except Exception as e:
            # [fix] rollback 避免 InFailedSqlTransaction 污染调用方 session
            try:
                db.rollback()
            except Exception:
                pass
            logger.debug(f"[RiskCtrl] _count_daily_trades error: {e}")
            return 0

    def _count_symbol_daily_entries(self, db: Session, account_id: int, symbol: str) -> int:
        """统计今日单币种已执行入场次数 (F0-3)"""
        try:
            from sqlalchemy import text as _t
            result = db.execute(
                _t("""
                    SELECT COUNT(*) FROM ai_decision_logs
                    WHERE account_id = :aid
                      AND symbol = :sym
                      AND executed = 'true'
                      AND operation IN ('buy', 'sell')
                      AND decision_time >= CURRENT_DATE
                """),
                {"aid": account_id, "sym": symbol}
            )
            row = result.fetchone()
            return int(row[0]) if row else 0
        except Exception as e:
            logger.debug(f"[RiskCtrl] _count_symbol_daily_entries error: {e}")
            return 0

    def _count_consecutive_losses(self, db: Session, account_id: int) -> int:
        """统计连续亏损次数（Phase 3B §修复⑤）"""
        try:
            from sqlalchemy import text as _t
            result = db.execute(
                _t("""
                    SELECT realized_pnl FROM ai_decision_logs
                    WHERE account_id = :aid
                      AND operation IN ('buy', 'sell', 'close')
                      AND executed = 'true'
                      AND realized_pnl IS NOT NULL
                    ORDER BY decision_time DESC
                    LIMIT 50
                """),
                {"aid": account_id}
            )
            rows = result.fetchall()
            count = 0
            for row in rows:
                pnl = float(row[0]) if row[0] is not None else 0
                if pnl < 0:
                    count += 1
                else:
                    break
            return count
        except Exception as e:
            # [fix] rollback 避免 InFailedSqlTransaction 污染调用方 session
            try:
                db.rollback()
            except Exception:
                pass
            logger.debug(f"[RiskCtrl] _count_consecutive_losses error: {e}")
            return 0

    def check_strategy_consecutive_losses(
        self, db: Session, strategy_id: str, symbol: str,
    ) -> RiskCheckResponse:
        """F1-2: 检测单策略单币种连续亏损, 建议暂停30分钟"""
        try:
            from backend.config.settings import CONSECUTIVE_LOSS_PROTECTION_ENABLED
            if not CONSECUTIVE_LOSS_PROTECTION_ENABLED:
                return RiskCheckResponse(
                    result=RiskCheckResult.PASSED,
                    check_name="strategy_consecutive_losses",
                    message="连亏保护已关闭",
                )
        except Exception:
            pass
        try:
            from sqlalchemy import text as _t
            from backend.database.dialect import dialect
            result = db.execute(
                _t("""
                    SELECT realized_pnl FROM ai_decision_logs
                    WHERE strategy_id = :sid
                      AND symbol = :sym
                      AND executed = 'true'
                      AND realized_pnl IS NOT NULL
                      AND decision_time >= """ + dialect.datetime_now_minus(7) + """
                    ORDER BY decision_time DESC
                    LIMIT 50
                """),
                {"sid": strategy_id, "sym": symbol}
            )
            rows = result.fetchall()
            consecutive_count = 0
            for row in rows:
                pnl = float(row[0]) if row[0] is not None else 0
                if pnl < 0:
                    consecutive_count += 1
                else:
                    break

            if consecutive_count >= 3:
                return RiskCheckResponse(
                    result=RiskCheckResult.WARNING,
                    check_name="strategy_consecutive_losses",
                    message=f"策略 {strategy_id} {symbol} 连续亏损 {consecutive_count} 次, 建议暂停30分钟",
                    details={"strategy_id": strategy_id, "symbol": symbol,
                             "consecutive_losses": consecutive_count}
                )
            return RiskCheckResponse(
                result=RiskCheckResult.PASSED,
                check_name="strategy_consecutive_losses",
                message=f"策略连续亏损检查通过 ({consecutive_count}次)"
            )
        except Exception as e:
            # [fix] rollback 避免 InFailedSqlTransaction 污染调用方 session
            try:
                db.rollback()
            except Exception:
                pass
            logger.debug(f"[RiskCtrl] check_strategy_consecutive_losses error: {e}")
            return RiskCheckResponse(
                result=RiskCheckResult.PASSED,
                check_name="strategy_consecutive_losses",
                message=f"检查异常: {e}"
            )

    def _record_circuit_breaker_event(
        self,
        db: Session,
        account_id: int,
        start_equity: float,
        current_equity: float,
        loss_ratio: float,
    ) -> None:
        """记录熔断事件到数据库"""
        try:
            db.execute(
                text("""
                    INSERT INTO risk_control_events 
                    (account_id, event_type, event_time, details)
                    VALUES (:account_id, 'circuit_breaker', NOW(), :details)
                """),
                {
                    "account_id": account_id,
                    "details": f'{{"start_equity": {start_equity}, "current_equity": {current_equity}, "loss_ratio": {loss_ratio}}}'
                }
            )
            db.commit()
            logger.warning(f"[RISK] Circuit breaker triggered for account {account_id}: loss_ratio={loss_ratio*100:.2f}%")
        except Exception as e:
            # [fix] rollback 避免 InFailedSqlTransaction 污染调用方 session
            try:
                db.rollback()
            except Exception:
                pass
            logger.error(f"Failed to record circuit breaker event: {e}")
    
    def get_risk_summary(
        self,
        db: Session,
        account_id: int,
        total_equity: float,
        positions: List[Dict[str, Any]],
        margin_usage_percent: float,
    ) -> Dict[str, Any]:
        """
        获取账户风控摘要
        """
        # 计算各项风控指标
        total_position_value = sum(
            abs(float(pos.get('position_value', 0) or 0))
            for pos in positions
        )
        
        # 单币种最大仓位
        max_symbol_value = 0.0
        max_symbol = ""
        for pos in positions:
            pos_value = abs(float(pos.get('position_value', 0) or 0))
            if pos_value > max_symbol_value:
                max_symbol_value = pos_value
                max_symbol = pos.get('coin') or pos.get('symbol', '')
        
        # 检查熔断状态
        is_circuit_breaker_active = account_id in self._circuit_breaker_cache
        breaker_end_time = None
        if is_circuit_breaker_active:
            breaker_time = self._circuit_breaker_cache[account_id]
            breaker_end_time = breaker_time + timedelta(hours=self.config.circuit_breaker_cooldown_hours)
        
        return {
            "account_id": account_id,
            "total_equity": total_equity,
            "total_position_value": total_position_value,
            "position_equity_ratio": total_position_value / total_equity if total_equity > 0 else 0,
            "max_single_symbol": max_symbol,
            "max_single_symbol_value": max_symbol_value,
            "max_single_symbol_ratio": max_symbol_value / total_equity if total_equity > 0 else 0,
            "margin_usage_percent": margin_usage_percent,
            "is_circuit_breaker_active": is_circuit_breaker_active,
            "circuit_breaker_end_time": breaker_end_time.isoformat() if breaker_end_time else None,
            "config": {
                "max_single_symbol_ratio": self.config.max_single_symbol_ratio,
                "daily_loss_limit_ratio": self.config.daily_loss_limit_ratio,
                "max_total_position_multiple": self.config.max_total_position_multiple,
                "max_margin_usage_ratio": self.config.max_margin_usage_ratio,
            }
        }


# 全局风控服务实例
_risk_control_service: Optional[RiskControlService] = None


def get_risk_control_service() -> RiskControlService:
    """获取风控服务单例"""
    global _risk_control_service
    if _risk_control_service is None:
        _risk_control_service = RiskControlService()
    return _risk_control_service


def check_risk_before_trade(
    db: Session,
    account_id: int,
    symbol: str,
    operation: str,
    order_value: float,
    total_equity: float,
    available_balance: float,
    positions: List[Dict[str, Any]],
    margin_usage_percent: float = 0,
) -> Tuple[bool, str]:
    """
    交易前风控检查的便捷函数
    
    Returns:
        Tuple[bool, str]: (是否允许交易, 拒绝原因或通过消息)
    """
    service = get_risk_control_service()
    service.load_config_from_db(db, account_id)
    
    passed, responses = service.check_all(
        db=db,
        account_id=account_id,
        symbol=symbol,
        operation=operation,
        order_value=order_value,
        total_equity=total_equity,
        available_balance=available_balance,
        positions=positions,
        margin_usage_percent=margin_usage_percent,
    )
    
    # 收集所有消息
    blocked_messages = []
    warning_messages = []
    
    for response in responses:
        if response.result == RiskCheckResult.BLOCKED:
            blocked_messages.append(response.message)
        elif response.result == RiskCheckResult.WARNING:
            warning_messages.append(response.message)
    
    if not passed:
        reason = " | ".join(blocked_messages)
        logger.warning(f"[RISK] Trade blocked for account {account_id}, symbol {symbol}: {reason}")
        return False, reason
    
    if warning_messages:
        logger.info(f"[RISK] Trade allowed with warnings for account {account_id}: {' | '.join(warning_messages)}")
    
    return True, "风控检查通过"
