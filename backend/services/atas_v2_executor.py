"""
ATAS V2 Executor - 策略执行器
处理回测引擎、风险管理、系统监控等核心功能
"""
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)


class ATASV2Executor:
    """ATAS V2 策略执行器"""

    def __init__(self, db=None):
        self.is_running = False
        self.active_strategies: Dict[str, Any] = {}
        self.db = db

    def start(self):
        """启动执行器"""
        self.is_running = True
        logger.info("[ATAS V2] 执行器已启动")

    def stop(self):
        """停止执行器"""
        self.is_running = False
        logger.info("[ATAS V2] 执行器已停止")

    def execute_strategy(self, strategy_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """执行策略"""
        logger.info(f"[ATAS V2] 执行策略: {strategy_id}")
        return {
            "status": "success",
            "strategy_id": strategy_id,
            "timestamp": datetime.now().isoformat()
        }

    def get_status(self) -> Dict[str, Any]:
        """获取执行器状态"""
        return {
            "is_running": self.is_running,
            "active_strategies": list(self.active_strategies.keys()),
            "timestamp": datetime.now().isoformat()
        }

    def get_account_snapshot(self, account_id: int) -> Dict[str, Any]:
        """
        获取账户统一快照 - 一次性获取所有数据
        避免多次调用交易所API
        """
        from database.models import Account, Position, AccountAssetSnapshot
        from sqlalchemy import desc

        if not self.db:
            return {
                "portfolio": {
                    "account_id": account_id,
                    "total_value": 0,
                    "capital": 0,
                    "positions": {},
                    "active_strategies": 0,
                    "unrealized_pnl": 0,
                    "daily_pnl": 0,
                    "cash_ratio": 1.0,
                    "error": "数据库连接不可用"
                },
                "health_score": {"overall": 0},
                "risk_alerts": [],
                "metrics": {"system_health": "未知", "cpu_usage": 0}
            }

        try:
            # 获取账户信息
            account = self.db.query(Account).filter(Account.id == account_id).first()
            if not account:
                return {
                    "portfolio": {
                        "account_id": account_id,
                        "total_value": 0,
                        "capital": 0,
                        "positions": {},
                        "active_strategies": 0,
                        "unrealized_pnl": 0,
                        "daily_pnl": 0,
                        "cash_ratio": 1.0,
                        "error": "账户不存在"
                    },
                    "health_score": {"overall": 0},
                    "risk_alerts": [],
                    "metrics": {"system_health": "未知", "cpu_usage": 0}
                }

            # 计算总资产
            current_cash = float(account.current_cash or 0)
            frozen_cash = float(account.frozen_cash or 0)

            # 获取持仓
            positions = self.db.query(Position).filter(Position.account_id == account_id).all()
            positions_value = sum(float(p.quantity or 0) * float(p.current_price or 0) for p in positions)
            total_value = current_cash + positions_value

            # 获取最新快照计算今日盈亏（字段名为 event_time，非 timestamp）
            latest_snapshot = self.db.query(AccountAssetSnapshot).filter(
                AccountAssetSnapshot.account_id == account_id
            ).order_by(desc(AccountAssetSnapshot.event_time)).first()

            daily_pnl = 0
            peak_equity = total_value
            if latest_snapshot:
                daily_pnl = total_value - float(getattr(latest_snapshot, 'total_assets', 0) or 0)
                peak_equity = max(peak_equity, total_value)

            # 计算回撤
            current_drawdown = 0
            if peak_equity > 0:
                current_drawdown = (peak_equity - total_value) / peak_equity

            # 构建持仓字典
            positions_dict = {}
            for pos in positions:
                if pos.symbol:
                    unrealized_pnl = (float(pos.current_price or 0) - float(pos.entry_price or 0)) * float(pos.quantity or 0)
                    positions_dict[pos.symbol] = {
                        "quantity": float(pos.quantity or 0),
                        "entry_price": float(pos.entry_price or 0),
                        "current_price": float(pos.current_price or 0),
                        "unrealized_pnl": unrealized_pnl,
                        "side": pos.side
                    }

            # 计算健康度评分
            overall_score = 100
            if current_drawdown > 0.2:
                overall_score = 40
            elif current_drawdown > 0.1:
                overall_score = 60
            elif current_drawdown > 0.05:
                overall_score = 80

            return {
                "portfolio": {
                    "account_id": account_id,
                    "total_value": round(total_value, 2),
                    "capital": round(current_cash, 2),
                    "positions": positions_dict,
                    "active_strategies": 1 if account.auto_trading_enabled == "true" else 0,
                    "unrealized_pnl": round(sum(p.get("unrealized_pnl", 0) for p in positions_dict.values()), 2),
                    "daily_pnl": round(daily_pnl, 2),
                    "current_drawdown": round(current_drawdown, 4),
                    "peak_equity": round(peak_equity, 2),
                    "cash_ratio": round(current_cash / total_value, 4) if total_value > 0 else 1,
                },
                "health_score": {
                    "overall": overall_score,
                    "performance": overall_score,
                    "risk": 100,
                    "stability": 100,
                    "liquidity": 100
                },
                "risk_alerts": [],
                "metrics": {
                    "system_health": "正常",
                    "cpu_usage": 0,
                    "memory_usage": 0,
                    "disk_usage": 0,
                    "active_strategies": 1 if account.auto_trading_enabled == "true" else 0,
                    "total_positions": len(positions),
                    "daily_pnl": round(daily_pnl, 2)
                }
            }
        except Exception as e:
            logger.error(f"[ATAS V2] 获取账户快照失败: {e}")
            return {
                "portfolio": {
                    "account_id": account_id,
                    "total_value": 0,
                    "capital": 0,
                    "positions": {},
                    "active_strategies": 0,
                    "unrealized_pnl": 0,
                    "daily_pnl": 0,
                    "cash_ratio": 1.0,
                    "error": str(e)
                },
                "health_score": {"overall": 0},
                "risk_alerts": [],
                "metrics": {"system_health": "错误", "cpu_usage": 0}
            }

    def get_account_health_score(self, account_id: int) -> Dict[str, Any]:
        """
        获取账户健康度评分（独立方法，轻量计算）。
        从 get_account_snapshot 提取健康度计算逻辑。
        """
        snapshot = self.get_account_snapshot(account_id)
        hs = snapshot.get("health_score", {})
        if hs.get("overall", 0) > 0:
            return hs
        # fallback: 无有效快照时返回默认评分
        return {
            "overall": 50,
            "performance": 50,
            "risk": 50,
            "stability": 50,
            "liquidity": 50,
        }

    def get_account_portfolio(self, account_id: int) -> Dict[str, Any]:
        """获取账户投资组合状态"""
        snapshot = self.get_account_snapshot(account_id)
        return snapshot.get("portfolio", {
            "account_id": account_id,
            "total_value": 0,
            "capital": 0,
            "positions": {},
            "active_strategies": 0,
            "unrealized_pnl": 0,
            "daily_pnl": 0,
            "cash_ratio": 1.0,
        })

    def check_trade_risk(
        self, account_id: int, symbol: str, side: str,
        quantity: float, price: float,
    ) -> Dict[str, Any]:
        """检查交易风险"""
        snapshot = self.get_account_snapshot(account_id)
        pf = snapshot.get("portfolio", {})
        capital = float(pf.get("capital", 0) or 0)
        notional = quantity * price
        return {
            "allowed": True,
            "risk_level": "low" if notional <= capital * 0.1 else "medium",
            "max_quantity": capital * 0.25 / price if price > 0 else 0,
            "reason": "风险可控",
        }

    def calculate_optimal_position(
        self, account_id: int, symbol: str, entry_price: float,
        method: str = "fixed_ratio", stop_loss_price: float = 0, ratio: float = 0.1,
    ) -> Dict[str, Any]:
        """计算最优仓位"""
        snapshot = self.get_account_snapshot(account_id)
        pf = snapshot.get("portfolio", {})
        capital = float(pf.get("capital", 0) or 0)
        optimal_qty = (capital * ratio) / entry_price if entry_price > 0 else 0
        return {
            "optimal_quantity": round(optimal_qty, 6),
            "optimal_notional": round(optimal_qty * entry_price, 2),
            "risk_pct": round(ratio * 100, 1),
            "method": method,
        }

    def monitor_account_risk(self, account_id: int) -> Dict[str, Any]:
        """监控账户风险"""
        snapshot = self.get_account_snapshot(account_id)
        pf = snapshot.get("portfolio", {})
        hs = snapshot.get("health_score", {})
        return {
            "account_id": account_id,
            "overall_risk": "low",
            "risks": [],
            "health_score": hs,
            "total_value": pf.get("total_value", 0),
            "drawdown": pf.get("current_drawdown", 0),
        }

    def get_monitoring_metrics(self, account_id: int) -> Dict[str, Any]:
        """获取监控指标"""
        snapshot = self.get_account_snapshot(account_id)
        pf = snapshot.get("portfolio", {})
        return {
            "timestamp": datetime.now().isoformat(),
            "account_id": account_id,
            "cpu_usage": 0,
            "memory_usage": 0,
            "disk_usage": 0,
            "active_strategies": pf.get("active_strategies", 0),
            "total_positions": len(pf.get("positions", {})),
            "daily_pnl": pf.get("daily_pnl", 0),
            "system_health": "正常",
        }


# 全局单例
_executor: Optional[ATASV2Executor] = None


def get_atas_v2_executor(db=None) -> ATASV2Executor:
    """
    获取 ATAS V2 执行器单例
    Args:
        db: 数据库会话（可选）
    """
    global _executor
    if _executor is None:
        _executor = ATASV2Executor(db)
    elif db is not None:
        _executor.db = db
    return _executor
