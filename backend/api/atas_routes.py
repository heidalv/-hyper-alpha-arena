"""
ATAS 高级自动化交易系统 - 轻量级 API
复用现有服务，提供稳定的 API 接口

设计原则:
1. 不引入复杂依赖，复用现有成熟服务
2. 所有端点独立处理异常，不影响其他端点
3. 返回结构化数据，便于前端展示
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from pydantic import BaseModel
import logging

from backend.database.connection import SessionLocal, AnalyticsSessionLocal
from backend.database.models import AIDecisionLog, Account, SystemConfig

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ATAS"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==================== 数据模型 ====================

class ATASConfig(BaseModel):
    """ATAS 配置"""
    auto_refresh_enabled: bool = True
    refresh_interval: int = 60  # 秒
    monitored_symbols: List[str] = ["BTC", "ETH", "SOL"]
    risk_level: str = "moderate"  # conservative, moderate, aggressive
    max_position_percent: float = 20.0  # 最大仓位占比
    stop_loss_percent: float = 5.0  # 止损百分比


class ATASConfigUpdate(BaseModel):
    """ATAS 配置更新"""
    auto_refresh_enabled: Optional[bool] = None
    refresh_interval: Optional[int] = None
    monitored_symbols: Optional[List[str]] = None
    risk_level: Optional[str] = None
    max_position_percent: Optional[float] = None
    stop_loss_percent: Optional[float] = None


# ==================== 辅助函数 ====================

def get_atas_config(db: Session) -> ATASConfig:
    """获取 ATAS 配置 (从 system_configs 表)"""
    import json
    config_row = db.query(SystemConfig).filter(SystemConfig.key == "atas_config").first()
    if config_row and config_row.value:
        try:
            data = json.loads(config_row.value)
            return ATASConfig(**data)
        except:
            pass
    return ATASConfig()


def save_atas_config(db: Session, config: ATASConfig):
    """保存 ATAS 配置"""
    import json
    config_row = db.query(SystemConfig).filter(SystemConfig.key == "atas_config").first()
    if not config_row:
        config_row = SystemConfig(key="atas_config")
        db.add(config_row)
    config_row.value = json.dumps(config.dict())
    db.commit()


# ==================== API 端点 ====================

@router.get("/health")
async def atas_health():
    """健康检查 - 始终返回成功"""
    return {
        "available": True,
        "version": "2.0",
        "message": "ATAS 系统运行正常"
    }


@router.get("/status")
async def get_atas_status(db: Session = Depends(get_db)):
    """获取系统状态 - 复用 StrategyManager"""
    try:
        from backend.services.trading_strategy import get_strategy_status
        strategy_status = get_strategy_status()
        
        # 统计活跃账户
        active_strategies = [s for s in strategy_status.get("strategies", []) if s.get("enabled")]
        running_strategies = [s for s in active_strategies if s.get("running")]
        
        return {
            "state": "running" if running_strategies else "idle",
            "is_running": len(running_strategies) > 0,
            "uptime_seconds": 0,  # TODO: 实际运行时间
            "statistics": {
                "active_traders": len(active_strategies),
                "running_traders": len(running_strategies),
                "total_strategies": len(strategy_status.get("strategies", [])),
            },
            "strategies": strategy_status.get("strategies", []),
            "last_update": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取状态失败: {e}")
        return {
            "state": "error",
            "is_running": False,
            "error": str(e),
            "statistics": {"active_traders": 0, "running_traders": 0, "total_strategies": 0}
        }


@router.get("/overview")
async def get_market_overview(db: Session = Depends(get_db)):
    """获取市场概览 - 从缓存获取最新价格"""
    try:
        from backend.services.price_cache import get_cached_price
        
        config = get_atas_config(db)
        symbols = config.monitored_symbols
        
        market_data = {}
        for symbol in symbols:
            try:
                # 获取缓存价格
                price = get_cached_price(symbol, "CRYPTO")
                market_data[symbol] = {
                    "symbol": symbol,
                    "price": price or 0,
                    "price_available": price is not None,
                    "change_24h": 0,  # TODO: 计算24小时变化
                    "volume_24h": 0,
                }
            except Exception as e:
                market_data[symbol] = {
                    "symbol": symbol,
                    "price": 0,
                    "price_available": False,
                    "error": str(e)
                }
        
        return {
            "data": market_data,
            "symbols": symbols,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取市场概览失败: {e}")
        return {"data": {}, "symbols": [], "error": str(e)}


@router.get("/signals")
async def get_active_signals(db: Session = Depends(get_db)):
    """获取活跃信号 - 复用信号检测服务"""
    try:
        from sqlalchemy import text
        
        # 查询最近的信号触发记录
        result = db.execute(text("""
            SELECT 
                stl.id,
                stl.signal_id,
                sd.signal_name,
                stl.symbol,
                stl.metric_value,
                stl.threshold,
                stl.operator,
                stl.triggered_at
            FROM signal_trigger_logs stl
            JOIN signal_definitions sd ON stl.signal_id = sd.id
            WHERE stl.triggered_at > NOW() - INTERVAL '1 hour'
            ORDER BY stl.triggered_at DESC
            LIMIT 20
        """))
        
        signals = []
        for row in result:
            signals.append({
                "id": row[0],
                "signal_id": row[1],
                "signal_name": row[2],
                "symbol": row[3],
                "metric_value": float(row[4]) if row[4] else 0,
                "threshold": float(row[5]) if row[5] else 0,
                "operator": row[6],
                "triggered_at": row[7].isoformat() if row[7] else None,
            })
        
        return {
            "signals": signals,
            "count": len(signals),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取活跃信号失败: {e}")
        return {"signals": [], "count": 0, "error": str(e)}


@router.get("/decisions")
async def get_recent_decisions(
    limit: int = 20,
    account_id: Optional[int] = None,
):
    """获取最近的 AI 决策（AIDecisionLog 在 Analytics DB）；account_id 可选过滤单账户"""
    try:
        analytics_db = AnalyticsSessionLocal()
        try:
            q = analytics_db.query(AIDecisionLog)
            if account_id:
                q = q.filter(AIDecisionLog.account_id == account_id)
            decisions = (
                q.order_by(AIDecisionLog.created_at.desc())
                .limit(limit)
                .all()
            )
        finally:
            analytics_db.close()

        return {
            "decisions": [
                {
                    "id": d.id,
                    "account_id": d.account_id,
                    "symbol": d.symbol,
                    "operation": d.operation,
                    "prev_portion": float(d.prev_portion) if d.prev_portion else 0,
                    "target_portion": float(d.target_portion) if d.target_portion else 0,
                    "reasoning": (d.reason[:200] + "...") if d.reason and len(d.reason) > 200 else d.reason,
                    "executed": d.executed == "true",
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                }
                for d in decisions
            ],
            "count": len(decisions),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取决策失败: {e}")
        return {"decisions": [], "count": 0, "error": str(e)}


@router.get("/config")
async def get_config(db: Session = Depends(get_db)):
    """获取 ATAS 配置"""
    try:
        config = get_atas_config(db)
        return {
            "config": config.dict(),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取配置失败: {e}")
        return {"config": ATASConfig().dict(), "error": str(e)}


@router.put("/config")
async def update_config(
    update: ATASConfigUpdate,
    db: Session = Depends(get_db)
):
    """更新 ATAS 配置"""
    try:
        config = get_atas_config(db)
        
        # 更新非空字段
        update_data = update.dict(exclude_unset=True)
        for key, value in update_data.items():
            if value is not None:
                setattr(config, key, value)
        
        save_atas_config(db, config)
        
        return {
            "success": True,
            "config": config.dict(),
            "message": "配置已更新"
        }
    except Exception as e:
        logger.error(f"更新配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions")
async def get_positions(db: Session = Depends(get_db)):
    """获取当前持仓 - 从数据库获取"""
    try:
        from backend.database.models import Position, Account
        
        positions = []
        # 获取所有活跃账户的持仓
        accounts = db.query(Account).filter(Account.is_active == "true").all()
        for account in accounts:
            account_positions = db.query(Position).filter(
                Position.account_id == account.id,
                Position.quantity != 0
            ).all()
            for pos in account_positions:
                positions.append({
                    "account_id": account.id,
                    "account_name": account.name,
                    "symbol": pos.symbol,
                    "size": float(pos.quantity),
                    "entry_price": float(pos.avg_cost),
                    "market": pos.market,
                })
        
        return {
            "positions": positions,
            "count": len(positions),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取持仓失败: {e}")
        return {"positions": [], "count": 0, "error": str(e)}


@router.get("/statistics")
async def get_statistics():
    """获取统计数据（AIDecisionLog 在 Analytics DB，signal_trigger_logs 在 Core DB）"""
    try:
        from sqlalchemy import text, func

        today = datetime.now().date()

        # AIDecisionLog 查询 — Analytics DB
        analytics_db = AnalyticsSessionLocal()
        try:
            decisions_today = analytics_db.query(func.count(AIDecisionLog.id)).filter(
                func.date(AIDecisionLog.created_at) == today
            ).scalar() or 0

            executions_today = analytics_db.query(func.count(AIDecisionLog.id)).filter(
                func.date(AIDecisionLog.created_at) == today,
                AIDecisionLog.executed == "true"
            ).scalar() or 0
        finally:
            analytics_db.close()

        # signal_trigger_logs 查询 — Core DB
        core_db = SessionLocal()
        try:
            signals_result = core_db.execute(text("""
                SELECT COUNT(*) FROM signal_trigger_logs
                WHERE DATE(triggered_at) = CURRENT_DATE
            """))
            signals_today = signals_result.scalar() or 0
        finally:
            core_db.close()

        return {
            "today": {
                "decisions": decisions_today,
                "executions": executions_today,
                "signals": signals_today,
            },
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取统计失败: {e}")
        return {"today": {"decisions": 0, "executions": 0, "signals": 0}, "error": str(e)}


@router.get("/strategy-analysis")
async def get_strategy_analysis(db: Session = Depends(get_db)):
    """获取策略编排层分析结果 - 中长期规划 + 短期战术"""
    try:
        from backend.services.unified_data_pool import get_unified_data_pool
        
        config = get_atas_config(db)
        symbols = config.monitored_symbols or ["BTC", "ETH", "SOL"]
        
        data_pool = get_unified_data_pool()
        
        # 捕获数据快照
        snapshot = data_pool.capture_snapshot(
            symbols=symbols[:5],
            include_klines=True,
            include_strategy=True,
        )
        
        if not snapshot:
            return {
                "available": False,
                "message": "数据快照不可用",
                "timestamp": datetime.now().isoformat()
            }
        
        strategy = snapshot.strategy
        
        return {
            "available": True,
            "snapshot_id": snapshot.snapshot_id,
            "long_term": {
                "market_cycle": strategy.market_cycle,
                "cycle_confidence": strategy.cycle_confidence,
                "position_bias": strategy.position_bias,
                "recommended_leverage": strategy.recommended_leverage,
                "max_position_size": strategy.max_position_size,
                "max_daily_loss_pct": strategy.max_daily_loss_pct,
                "key_support": strategy.key_support,
                "key_resistance": strategy.key_resistance,
                "regime_warning": strategy.regime_warning,
            },
            "short_term": {
                "tactical_action": strategy.tactical_action,
                "tactical_confidence": strategy.tactical_confidence,
                "entry_timing": strategy.entry_timing,
                "market_condition": strategy.market_condition,
                "suggested_stop_loss": strategy.suggested_stop_loss,
                "suggested_take_profit": strategy.suggested_take_profit,
            },
            "factors": strategy.factors,
            "active_signals": strategy.active_signals,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取策略分析失败: {e}")
        return {
            "available": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@router.get("/factors/{symbol}")
async def get_symbol_factors(symbol: str, db: Session = Depends(get_db)):
    """获取指定交易对的因子值"""
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
                "timestamp": datetime.now().isoformat()
            }
        
        # 计算因子
        factors = factor_engine.compute_all_factors(klines_df)
        
        # 分类组织
        categorized = {
            "momentum": {},
            "mean_reversion": {},
            "volatility": {},
            "volume": {},
            "trend": {},
            "market_flow": {},
        }
        
        for name, fv in factors.items():
            category = fv.category.value
            if category in categorized:
                categorized[category][name] = {
                    "value": round(fv.value, 4),
                    "name": fv.name,
                }
        
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
            "timestamp": datetime.now().isoformat()
        }


# V3 整合：因子推荐端点（FastAPI 中具体段 'recommend' 优先于 {symbol} 参数匹配）


@router.get("/factors/recommend/{symbol}")
async def recommend_factors(symbol: str, db: Session = Depends(get_db)):
    """获取指定交易对的推荐因子组合（基于当前市场状态自适应权重）"""
    try:
        from services.factor_engine import (
            factor_engine,
            get_factor_weighting,
        )
        from backend.services.market_data import get_kline_data
        import pandas as pd

        _raw = get_kline_data(symbol.upper(), period="15m", count=200)
        klines_df = pd.DataFrame(_raw) if _raw else None
        
        if klines_df is None or klines_df.empty:
            return {
                "symbol": symbol.upper(),
                "available": False,
                "message": "K线数据不足，无法推荐因子",
                "recommended_factors": [],
                "factor_weights": {},
                "timestamp": datetime.now().isoformat()
            }
        
        # 计算所有因子值
        factor_values = factor_engine.compute_all_factors(klines_df)
        if not factor_values:
            return {
                "symbol": symbol.upper(),
                "available": False,
                "message": "因子计算失败",
                "recommended_factors": [],
                "factor_weights": {},
                "timestamp": datetime.now().isoformat()
            }
        
        # 获取自适应权重
        weighting = get_factor_weighting()
        adaptive_result = weighting.calculate_adaptive_weights(factor_values)
        weights = adaptive_result.weights
        regime = adaptive_result.regime.value
        regime_confidence = adaptive_result.confidence
        
        # 按权重排序取 top-10
        sorted_factors = sorted(weights.items(), key=lambda x: x[1], reverse=True)
        top_10 = sorted_factors[:10]
        
        # 因子详情
        factor_details = []
        for name, weight in top_10:
            fv = factor_values.get(name)
            factor_details.append({
                "name": name,
                "value": round(fv.value, 6) if fv else 0.0,
                "weight": round(weight, 4),
                "category": fv.category.value if fv else "unknown",
            })
        
        return {
            "symbol": symbol.upper(),
            "available": True,
            "regime": regime,
            "regime_confidence": round(regime_confidence, 4),
            "recommended_factors": [name for name, _ in top_10],
            "factor_weights": {name: round(w, 4) for name, w in top_10},
            "factor_details": factor_details,
            "total_factors": len(factor_values),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"因子推荐失败: {e}")
        return {
            "symbol": symbol.upper(),
            "available": False,
            "error": str(e),
            "recommended_factors": [],
            "factor_weights": {},
            "timestamp": datetime.now().isoformat()
        }


@router.get("/signal-pools")
async def get_signal_pools(db: Session = Depends(get_db)):
    """获取信号池列表和状态"""
    try:
        from sqlalchemy import text
        import json
        
        # 查询信号池
        result = db.execute(text("""
            SELECT id, pool_name, signal_ids, symbols, enabled, logic, description
            FROM signal_pools
            ORDER BY id
        """))
        
        pools = []
        for row in result:
            signal_ids = row[2]
            if isinstance(signal_ids, str):
                try:
                    signal_ids = json.loads(signal_ids)
                except:
                    signal_ids = []
            
            symbols = row[3]
            if isinstance(symbols, str):
                try:
                    symbols = json.loads(symbols)
                except:
                    symbols = []
            
            pools.append({
                "id": row[0],
                "pool_name": row[1],
                "signal_ids": signal_ids or [],
                "symbols": symbols or [],
                "enabled": row[4],
                "logic": row[5] or "OR",
                "description": row[6],
            })
        
        # 查询信号定义
        signals_result = db.execute(text("""
            SELECT id, signal_name, description, trigger_condition, enabled
            FROM signal_definitions
            ORDER BY id
        """))
        
        signals = []
        for row in signals_result:
            trigger_cond = row[3]
            if isinstance(trigger_cond, str):
                try:
                    trigger_cond = json.loads(trigger_cond)
                except:
                    trigger_cond = {}
            
            signals.append({
                "id": row[0],
                "signal_name": row[1],
                "description": row[2],
                "trigger_condition": trigger_cond,
                "enabled": row[4],
            })
        
        return {
            "pools": pools,
            "signals": signals,
            "pool_count": len(pools),
            "signal_count": len(signals),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取信号池失败: {e}")
        return {
            "pools": [],
            "signals": [],
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@router.get("/risk-metrics")
async def get_risk_metrics(db: Session = Depends(get_db)):
    """获取风险指标"""
    try:
        from backend.database.models import Account
        from backend.services.hyperliquid_cache import get_cached_account_state
        
        risk_data = []
        accounts = db.query(Account).filter(
            Account.is_active == "true",
            Account.hyperliquid_enabled == "true"
        ).all()
        
        for account in accounts:
            # 尝试从缓存获取账户状态
            for env in ["testnet", "mainnet"]:
                state_entry = get_cached_account_state(account.id, env, max_age_seconds=60)
                if state_entry:
                    state = state_entry["data"]
                    total_equity = float(state.get("total_equity", 0) or 0)
                    used_margin = float(state.get("used_margin", 0) or 0)
                    margin_usage = float(state.get("margin_usage_percent", 0) or 0)
                    
                    risk_data.append({
                        "account_id": account.id,
                        "account_name": account.name,
                        "environment": env,
                        "total_equity": total_equity,
                        "used_margin": used_margin,
                        "margin_usage_percent": margin_usage,
                        "risk_level": "high" if margin_usage > 70 else "medium" if margin_usage > 50 else "low",
                        "positions_count": len(state.get("positions", [])),
                    })
        
        return {
            "risk_data": risk_data,
            "account_count": len(risk_data),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取风险指标失败: {e}")
        return {
            "risk_data": [],
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


# ============================================================================
# 策略生成与生命周期管理 API
# ============================================================================

class StrategyConfigModel(BaseModel):
    """策略配置模型"""
    name: str = "未命名策略"
    description: str = ""
    symbols: List[str] = ["BTC", "ETH"]
    # 中长线合并后 swing 不再是独立 tier；默认 horizon 改为 trend_follow
    # （入参仍接受 swing，调用方传 swing 时由下游 nature_to_layer 映射到 swing 层）
    horizon: str = "trend_follow"  # intraday, swing, position, trend_follow, long_term
    risk_profile: str = "moderate"  # conservative, moderate, aggressive
    max_position_pct: float = 25.0
    max_total_exposure: float = 80.0
    max_daily_loss_pct: float = 5.0
    stop_loss_pct: float = 3.0
    take_profit_pct: float = 6.0
    enabled_signal_pools: List[int] = []
    min_signal_strength: float = 0.6
    factor_weights: Dict[str, float] = {}
    auto_execute: bool = False
    require_confirmation: bool = True
    max_leverage: float = 20.0


class StrategyConfigUpdate(BaseModel):
    """策略配置更新"""
    name: Optional[str] = None
    description: Optional[str] = None
    symbols: Optional[List[str]] = None
    horizon: Optional[str] = None
    risk_profile: Optional[str] = None
    max_position_pct: Optional[float] = None
    max_total_exposure: Optional[float] = None
    max_daily_loss_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    enabled_signal_pools: Optional[List[int]] = None
    min_signal_strength: Optional[float] = None
    factor_weights: Optional[Dict[str, float]] = None
    auto_execute: Optional[bool] = None
    require_confirmation: Optional[bool] = None
    max_leverage: Optional[float] = None


@router.post("/strategies")
async def create_strategy(
    config: StrategyConfigModel,
    account_id: Optional[int] = None,
    environment: str = "testnet",
    db: Session = Depends(get_db)
):
    """创建新策略"""
    try:
        from backend.services.strategy_generator import (
            get_strategy_generator,
            StrategyConfig,
            StrategyHorizon,
            RiskProfile
        )
        
        generator = get_strategy_generator()
        
        # 转换配置
        strategy_config = StrategyConfig(
            name=config.name,
            description=config.description,
            symbols=config.symbols,
            horizon=StrategyHorizon(config.horizon),
            risk_profile=RiskProfile(config.risk_profile),
            max_position_pct=config.max_position_pct,
            max_total_exposure=config.max_total_exposure,
            max_daily_loss_pct=config.max_daily_loss_pct,
            stop_loss_pct=config.stop_loss_pct,
            take_profit_pct=config.take_profit_pct,
            enabled_signal_pools=config.enabled_signal_pools,
            min_signal_strength=config.min_signal_strength,
            factor_weights=config.factor_weights,
            auto_execute=config.auto_execute,
            require_confirmation=config.require_confirmation,
            max_leverage=config.max_leverage,
        )
        
        strategy = generator.create_strategy(
            config=strategy_config,
            account_id=account_id,
            environment=environment
        )
        
        return {
            "success": True,
            "strategy": generator.to_dict(strategy),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"创建策略失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@router.get("/strategies")
async def list_strategies(
    phase: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """获取策略列表"""
    try:
        from backend.services.strategy_generator import get_strategy_generator, StrategyPhase
        
        generator = get_strategy_generator()
        
        phase_filter = None
        if phase:
            try:
                phase_filter = StrategyPhase(phase)
            except:
                pass
        
        strategies = generator.list_strategies(phase=phase_filter)
        
        return {
            "strategies": [generator.to_dict(s) for s in strategies],
            "count": len(strategies),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取策略列表失败: {e}")
        return {
            "strategies": [],
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@router.get("/strategies/active")
async def get_active_strategy(db: Session = Depends(get_db)):
    """获取当前活跃策略"""
    try:
        from backend.services.strategy_generator import get_strategy_generator
        
        generator = get_strategy_generator()
        strategy = generator.get_active_strategy()
        
        if not strategy:
            return {
                "has_active": False,
                "strategy": None,
                "timestamp": datetime.now().isoformat()
            }
        
        return {
            "has_active": True,
            "strategy": generator.to_dict(strategy),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取活跃策略失败: {e}")
        return {
            "has_active": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@router.get("/strategies/{strategy_id}")
async def get_strategy(strategy_id: str, db: Session = Depends(get_db)):
    """获取策略详情"""
    try:
        from backend.services.strategy_generator import get_strategy_generator
        
        generator = get_strategy_generator()
        strategy = generator.get_strategy(strategy_id)
        
        if not strategy:
            return {
                "found": False,
                "error": "策略不存在",
                "timestamp": datetime.now().isoformat()
            }
        
        return {
            "found": True,
            "strategy": generator.to_dict(strategy),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取策略失败: {e}")
        return {
            "found": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@router.post("/strategies/{strategy_id}/generate")
async def generate_strategy_plan(
    strategy_id: str,
    force_refresh: bool = False,
    db: Session = Depends(get_db)
):
    """生成策略计划"""
    try:
        from backend.services.strategy_generator import get_strategy_generator
        
        generator = get_strategy_generator()
        plan = generator.generate_plan(strategy_id, force_refresh=force_refresh)
        
        if not plan:
            return {
                "success": False,
                "error": "生成策略计划失败",
                "timestamp": datetime.now().isoformat()
            }
        
        strategy = generator.get_strategy(strategy_id)
        
        return {
            "success": True,
            "strategy": generator.to_dict(strategy) if strategy else None,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"生成策略计划失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@router.post("/strategies/{strategy_id}/activate")
async def activate_strategy(strategy_id: str, db: Session = Depends(get_db)):
    """激活策略"""
    try:
        from backend.services.strategy_generator import get_strategy_generator
        
        generator = get_strategy_generator()
        success = generator.activate_strategy(strategy_id)
        
        strategy = generator.get_strategy(strategy_id)
        
        return {
            "success": success,
            "strategy": generator.to_dict(strategy) if strategy else None,
            "message": "策略已激活" if success else "激活失败",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"激活策略失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@router.post("/strategies/{strategy_id}/pause")
async def pause_strategy(strategy_id: str, db: Session = Depends(get_db)):
    """暂停策略"""
    try:
        from backend.services.strategy_generator import get_strategy_generator
        
        generator = get_strategy_generator()
        success = generator.pause_strategy(strategy_id)
        
        strategy = generator.get_strategy(strategy_id)
        
        return {
            "success": success,
            "strategy": generator.to_dict(strategy) if strategy else None,
            "message": "策略已暂停" if success else "暂停失败",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"暂停策略失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@router.post("/strategies/{strategy_id}/cancel")
async def cancel_strategy(strategy_id: str, db: Session = Depends(get_db)):
    """取消策略"""
    try:
        from backend.services.strategy_generator import get_strategy_generator
        
        generator = get_strategy_generator()
        success = generator.cancel_strategy(strategy_id)
        
        strategy = generator.get_strategy(strategy_id)
        
        return {
            "success": success,
            "strategy": generator.to_dict(strategy) if strategy else None,
            "message": "策略已取消" if success else "取消失败",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"取消策略失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@router.put("/strategies/{strategy_id}/config")
async def update_strategy_config(
    strategy_id: str,
    config: StrategyConfigUpdate,
    db: Session = Depends(get_db)
):
    """更新策略配置"""
    try:
        from backend.services.strategy_generator import get_strategy_generator
        
        generator = get_strategy_generator()
        
        # 过滤空值
        updates = {k: v for k, v in config.dict().items() if v is not None}
        
        success = generator.update_config(strategy_id, updates)
        
        strategy = generator.get_strategy(strategy_id)
        
        return {
            "success": success,
            "strategy": generator.to_dict(strategy) if strategy else None,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"更新策略配置失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@router.get("/strategies/{strategy_id}/execution")
async def get_strategy_execution(strategy_id: str, db: Session = Depends(get_db)):
    """获取策略执行状态"""
    try:
        from backend.services.strategy_generator import get_strategy_generator
        
        generator = get_strategy_generator()
        status = generator.get_execution_status(strategy_id)
        
        if not status:
            return {
                "found": False,
                "error": "策略不存在",
                "timestamp": datetime.now().isoformat()
            }
        
        return {
            "found": True,
            "execution": status,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"获取执行状态失败: {e}")
        return {
            "found": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }
