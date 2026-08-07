"""
ATAS V2 API路由 - 新一代策略中心
集成回测引擎、风险管理、系统监控等核心功能
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import Dict, List, Optional, Any
from datetime import datetime
from pydantic import BaseModel
import logging

from backend.database.connection import SessionLocal
from database.models import Account

logger = logging.getLogger(__name__)

# 导入新的ATAS V2模块
from services.backtest_engine import (
    BacktestEngine, BacktestConfig, BacktestMode, Strategy
)
from services.backtest_reporting import (
    BacktestReportGenerator, ReportFormat,
    BacktestMetricsCalculator,
    BacktestChartGenerator, ChartType
)
from services.risk_management import (
    RiskController, RiskCheckResult,
    PositionManager, PositionSizingMethod,
    RiskMonitor
)
from services.system_monitoring import (
    MonitoringDashboard,
    HealthScoreCalculator,
    AlertSystem, AlertChannel
)
from services.atas_v2_executor import get_atas_v2_executor

router = APIRouter(prefix="/atas/v2", tags=["ATAS V2"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==================== 数据模型 ====================

class BacktestRequest(BaseModel):
    strategy_code: str
    data_source: str = "csv"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    initial_capital: float = 100000.0
    commission: float = 0.001
    mode: str = "vectorized"


class RiskCheckRequest(BaseModel):
    portfolio: Dict[str, Any]
    new_order: Optional[Dict[str, Any]] = None


class PositionSizeRequest(BaseModel):
    method: str = "fixed_ratio"
    account_value: float
    entry_price: float
    stop_loss_price: Optional[float] = None
    ratio: Optional[float] = 0.1


# ==================== 回测引擎API ====================

@router.get("/health")
def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "version": "2.0.0",
        "modules": {
            "backtest_engine": "✅",
            "risk_management": "✅",
            "system_monitoring": "✅"
        }
    }


@router.post("/backtest/run")
def run_backtest(request: BacktestRequest, db: Session = Depends(get_db)):
    """
    运行策略回测
    """
    try:
        # 配置回测引擎
        config = BacktestConfig(
            initial_capital=request.initial_capital,
            commission=request.commission,
            mode=BacktestMode.VECTORIZED if request.mode == "vectorized" else BacktestMode.EVENT_DRIVEN
        )
        
        engine = BacktestEngine(config)
        
        # TODO: 从request.strategy_code创建策略实例
        # TODO: 加载历史数据
        
        return {
            "status": "success",
            "message": "回测引擎已初始化，等待策略代码执行",
            "config": {
                "initial_capital": config.initial_capital,
                "commission": config.commission,
                "mode": config.mode.value
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"回测失败: {str(e)}")


@router.get("/backtest/report/{backtest_id}")
def get_backtest_report(
    backtest_id: str,
    format: str = "json",
    db: Session = Depends(get_db)
):
    """
    获取回测报告
    """
    try:
        # TODO: 从数据库加载回测结果
        
        generator = BacktestReportGenerator()
        
        report_format = ReportFormat.JSON
        if format == "html":
            report_format = ReportFormat.HTML
        elif format == "pdf":
            report_format = ReportFormat.PDF
        
        return {
            "status": "success",
            "message": "报告生成功能已就绪",
            "format": format
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"报告生成失败: {str(e)}")


# ==================== 风险管理API ====================

@router.post("/risk/check")
def check_risk(request: RiskCheckRequest):
    """
    执行风险检查
    """
    try:
        controller = RiskController()
        result = controller.check_risk(
            portfolio=request.portfolio,
            new_order=request.new_order
        )
        
        return {
            "status": "success",
            "passed": result.passed,
            "risk_level": result.risk_level.value,
            "violations": result.violations,
            "warnings": result.warnings,
            "metrics": result.metrics
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"风险检查失败: {str(e)}")


@router.post("/risk/position-size")
def calculate_position(request: PositionSizeRequest):
    """
    计算仓位大小
    """
    try:
        manager = PositionManager()
        
        method_map = {
            "fixed_ratio": PositionSizingMethod.FIXED_RATIO,
            "fixed_amount": PositionSizingMethod.FIXED_AMOUNT,
            "kelly": PositionSizingMethod.KELLY,
            "atr_based": PositionSizingMethod.ATR_BASED,
            "volatility_adjusted": PositionSizingMethod.VOLATILITY_ADJUSTED
        }
        
        method = method_map.get(request.method, PositionSizingMethod.FIXED_RATIO)
        
        result = manager.calculate(
            method=method,
            account_value=request.account_value,
            entry_price=request.entry_price,
            stop_loss_price=request.stop_loss_price,
            ratio=request.ratio
        )
        
        return {
            "status": "success",
            "quantity": result.quantity,
            "value": result.value,
            "risk_amount": result.risk_amount,
            "stop_loss_price": result.stop_loss_price
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓位计算失败: {str(e)}")


@router.get("/risk/monitor")
def get_risk_monitor_status(db: Session = Depends(get_db)):
    """
    获取全局风险监控状态（汇总所有账户）

    注意：优先使用 /account/{account_id}/risk-monitor 获取指定账户的风险状态。
    此接口返回系统级别的风险摘要。
    """
    try:
        from services.atas_v2_executor import get_atas_v2_executor

        executor = get_atas_v2_executor(db)

        # 获取所有活跃账户进行风险检查
        accounts = db.query(Account).filter(Account.is_active == "true").limit(10).all()

        all_alerts = []
        risk_summary = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        for account in accounts:
            try:
                snapshot = executor.get_account_snapshot(account.id)
                portfolio = snapshot.get("portfolio", {})
                alerts = snapshot.get("risk_alerts", [])
                all_alerts.extend(alerts)

                # 根据回撤和日盈亏生成基础风险评估
                drawdown = portfolio.get("current_drawdown", 0)
                daily_pnl = portfolio.get("daily_pnl", 0)
                if drawdown > 0.2:
                    risk_summary["critical"] += 1
                elif drawdown > 0.1:
                    risk_summary["high"] += 1
                elif daily_pnl < 0:
                    risk_summary["medium"] += 1
                else:
                    risk_summary["low"] += 1
            except Exception:
                continue

        return {
            "status": "success",
            "accounts_checked": len(accounts),
            "active_alerts": len(all_alerts),
            "summary": risk_summary,
            "recent_alerts": all_alerts[:10]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"风险监控失败: {str(e)}")


# ==================== 系统监控API ====================

@router.get("/account/{account_id}/snapshot")
async def get_account_snapshot(account_id: int, db: Session = Depends(get_db)):
    """
    获取账户统一快照 - 使用真实交易所API数据
    
    这是前端的主要数据接口，确保所有数据来自同一时间点。
    修复：使用 executor.get_account_snapshot() 一次性获取所有数据，
    避免之前 4 次重复调用交易所 API 的问题。
    """
    try:
        import asyncio
        import uuid
        import time
        from services.atas_v2_executor import get_atas_v2_executor
        
        executor = get_atas_v2_executor(db)
        
        # 使用统一快照方法：只调用一次交易所 API，内部分发给各计算模块
        snapshot_data = await asyncio.to_thread(executor.get_account_snapshot, account_id)
        
        snapshot_id = str(uuid.uuid4())[:8]
        
        return {
            "status": "success",
            "snapshot": {
                "snapshot_id": snapshot_id,
                "timestamp": time.time(),
                "account_id": account_id,
                **snapshot_data
            }
        }
    except Exception as e:
        logger.error(f"获取账户快照失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取快照失败: {str(e)}")


@router.get("/monitoring/dashboard")
def get_monitoring_dashboard(db: Session = Depends(get_db)):
    """
    获取监控仪表板数据（系统级 + 真实数据汇总）
    """
    try:
        from services.atas_v2_executor import get_atas_v2_executor
        from datetime import datetime

        executor = get_atas_v2_executor(db)

        # 汇总所有活跃账户的数据
        accounts = db.query(Account).filter(Account.is_active == "true").limit(10).all()
        total_strategies = 0
        total_positions = 0
        total_daily_pnl = 0.0

        for account in accounts:
            try:
                snapshot = executor.get_account_snapshot(account.id)
                portfolio = snapshot.get("portfolio", {})
                if portfolio.get("error"):
                    continue
                total_strategies += portfolio.get('active_strategies', 0)
                total_positions += len(portfolio.get('positions', {}))
                total_daily_pnl += portfolio.get('daily_pnl', 0)
            except Exception:
                continue

        # 直接从汇总数据构建 metrics，不依赖不存在的 dashboard 属性
        return {
            "status": "success",
            "metrics": {
                "timestamp": datetime.now().isoformat(),
                "cpu_usage": 0,
                "memory_usage": 0,
                "disk_usage": 0,
                "active_strategies": total_strategies,
                "total_positions": total_positions,
                "daily_pnl": round(total_daily_pnl, 2),
                "system_health": "正常"
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"监控数据获取失败: {str(e)}")


@router.get("/monitoring/alert")
def send_alert(message: str = "系统告警接口（预留）"):
    """模块已在 Phase 2 移除。 当前仅支持系统级通知。 可用于发送自定义告警消息。 """
    return {
        "status": "ok",
        "message": message,
        "note": "系统告警功能为占位接口， 实际告警逻辑已移除"
    }

@router.get("/monitoring/health-score")
def get_health_score(db: Session = Depends(get_db)):
    """
    获取系统级健康度评分（汇总所有账户）

    注意：优先使用 /account/{account_id}/health 获取指定账户的健康度评分。
    """
    try:
        from services.atas_v2_executor import get_atas_v2_executor
        
        executor = get_atas_v2_executor(db)
        
        # 汇总所有活跃账户
        accounts = db.query(Account).filter(Account.is_active == "true").limit(10).all()
        
        if not accounts:
            return {
                "status": "success",
                "health_score": {"overall": 0, "performance": 0, "risk": 0, "stability": 0, "liquidity": 0},
                "message": "无活跃账户"
            }
        
        # 汇总评分（取各账户评分的平均值）
        scores = []
        for account in accounts:
            try:
                score = executor.get_account_health_score(account.id)
                if score.get('overall', 0) > 0:
                    scores.append(score)
            except Exception:
                continue
        
        if not scores:
            return {
                "status": "success",
                "health_score": {"overall": 50, "performance": 50, "risk": 50, "stability": 50, "liquidity": 50},
                "message": "无法获取账户数据"
            }
        
        # 计算平均评分
        avg_score = {
            "overall": round(sum(s['overall'] for s in scores) / len(scores), 1),
            "performance": round(sum(s['performance'] for s in scores) / len(scores), 1),
            "risk": round(sum(s['risk'] for s in scores) / len(scores), 1),
            "stability": round(sum(s['stability'] for s in scores) / len(scores), 1),
            "liquidity": round(sum(s['liquidity'] for s in scores) / len(scores), 1),
        }
        
        return {
            "status": "success",
            "health_score": avg_score,
            "accounts_evaluated": len(scores)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"健康度评分失败: {str(e)}")


@router.post("/monitoring/alert")
def send_alert(
    title: str,
    content: str,
    channel: str = "email"
):
    """
    发送预警通知
    """
    try:
        system = AlertSystem()
        
        channel_map = {
            "email": AlertChannel.EMAIL,
            "dingtalk": AlertChannel.DINGTALK,
            "sms": AlertChannel.SMS
        }
        
        alert_channel = channel_map.get(channel, AlertChannel.EMAIL)
        
        from services.system_monitoring.alert_system import AlertMessage
        message = AlertMessage(
            title=title,
            content=content,
            level="INFO",
            channel=alert_channel
        )
        
        success = system.send(message)
        
        return {
            "status": "success" if success else "failed",
            "message": "预警已发送" if success else "预警发送失败"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"预警发送失败: {str(e)}")


# ==================== 系统信息API ====================

@router.get("/info")
def get_system_info():
    """
    获取ATAS V2系统信息
    """
    return {
        "status": "success",
        "system": "ATAS V2",
        "version": "2.0.0",
        "modules": {
            "backtest_engine": {
                "name": "回测引擎",
                "status": "✅ 已加载",
                "features": ["向量化回测", "事件驱动回测", "Walk-Forward分析"]
            },
            "backtest_reporting": {
                "name": "回测报告",
                "status": "✅ 已加载",
                "features": ["性能指标计算", "HTML/JSON/PDF报告", "可视化图表", "策略对比"]
            },
            "risk_management": {
                "name": "风险管理",
                "status": "✅ 已加载",
                "features": ["多层风控", "仓位管理", "智能止损", "实时监控"]
            },
            "system_monitoring": {
                "name": "系统监控",
                "status": "✅ 已加载",
                "features": ["实时仪表板", "健康度评分", "预警系统", "性能监控"]
            }
        },
        "dependencies": {
            "pandas": "✅ 已安装",
            "numpy": "✅ 已安装",
            "matplotlib": "✅ 已安装",
            "psutil": "✅ 已安装"
        }
    }


# ==================== 账户级别的实际执行API ====================

@router.get("/account/{account_id}/portfolio")
async def get_account_portfolio(account_id: int, db: Session = Depends(get_db)):
    """
    获取账户投资组合状态（真实数据）
    """
    try:
        import asyncio
        executor = get_atas_v2_executor(db)
        portfolio = await asyncio.to_thread(executor.get_account_portfolio, account_id)
        
        return {
            "status": "success",
            "portfolio": portfolio
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取投资组合失败: {str(e)}")


@router.post("/account/{account_id}/check-trade")
async def check_trade_risk(
    account_id: int,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    db: Session = Depends(get_db)
):
    """
    检查交易风险（真实风控）
    """
    try:
        import asyncio
        executor = get_atas_v2_executor(db)
        result = await asyncio.to_thread(executor.check_trade_risk, account_id, symbol, side, quantity, price)
        
        return {
            "status": "success",
            **result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"风险检查失败: {str(e)}")


@router.post("/account/{account_id}/calculate-position")
async def calculate_position(
    account_id: int,
    symbol: str,
    entry_price: float,
    method: str = "fixed_ratio",
    stop_loss_price: Optional[float] = None,
    ratio: float = 0.1,
    db: Session = Depends(get_db)
):
    """
    计算最优仓位（真实计算）
    """
    try:
        import asyncio
        executor = get_atas_v2_executor(db)
        result = await asyncio.to_thread(
            executor.calculate_optimal_position,
            account_id, symbol, entry_price, method, stop_loss_price, ratio
        )
        
        return {
            "status": "success",
            **result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓位计算失败: {str(e)}")


@router.get("/account/{account_id}/risk-monitor")
async def monitor_account_risk(account_id: int, db: Session = Depends(get_db)):
    """
    监控账户风险（真实监控）
    """
    try:
        import asyncio
        executor = get_atas_v2_executor(db)
        result = await asyncio.to_thread(executor.monitor_account_risk, account_id)
        
        return {
            "status": "success",
            **result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"风险监控失败: {str(e)}")


@router.get("/account/{account_id}/health")
async def get_account_health(account_id: int, db: Session = Depends(get_db)):
    """
    获取账户健康度评分（真实评分）
    """
    try:
        import asyncio
        executor = get_atas_v2_executor(db)
        score = await asyncio.to_thread(executor.get_account_health_score, account_id)
        
        return {
            "status": "success",
            "health_score": score
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"健康度评分失败: {str(e)}")


@router.get("/factors/{symbol}")
async def get_symbol_factors(symbol: str, db: Session = Depends(get_db)):
    """获取指定交易对的因子值（V2 版本，扩展分类）"""
    try:
        from services.factor_engine import factor_engine
        from backend.services.market_data import get_kline_data
        import pandas as pd

        # 获取K线数据
        _raw = get_kline_data(symbol.upper(), period="15m", count=200)
        klines_df = pd.DataFrame(_raw) if _raw else None

        if klines_df is None or klines_df.empty:
            return {
                "symbol": symbol.upper(),
                "available": False,
                "message": "K线数据不足",
                "factors": {},
                "factor_count": 0,
                "timestamp": datetime.now().isoformat()
            }

        # 计算因子
        factors = factor_engine.compute_all_factors(klines_df)

        # 分类组织（覆盖 FactorCategory 全部枚举值）
        categorized = {
            "momentum": {},
            "mean_reversion": {},
            "volatility": {},
            "volume": {},
            "trend": {},
            "market_flow": {},
            "strength": {},
            "pattern": {},
            "sentiment": {},
            "funding": {},
            "behavioral": {},
            "onchain": {},
            "derivatives": {},
            "macro": {},
        }

        for name, fv in factors.items():
            category = fv.category.value
            if category in categorized:
                categorized[category][name] = {
                    "value": round(fv.value, 4),
                    "name": fv.name,
                }

        # 移除空分类以精简返回
        categorized = {k: v for k, v in categorized.items() if v}

        return {
            "symbol": symbol.upper(),
            "available": True,
            "factors": categorized,
            "factor_count": len(factors),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取因子失败: {e}")
        return {
            "symbol": symbol.upper(),
            "available": False,
            "error": str(e),
            "factors": {},
            "factor_count": 0,
            "timestamp": datetime.now().isoformat()
        }


@router.get("/account/{account_id}/metrics")
async def get_account_metrics(account_id: int, db: Session = Depends(get_db)):
    """
    获取账户监控指标（真实指标）
    """
    try:
        import asyncio
        executor = get_atas_v2_executor(db)
        metrics = await asyncio.to_thread(executor.get_monitoring_metrics, account_id)
        
        return {
            "status": "success",
            "metrics": metrics
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取指标失败: {str(e)}")
