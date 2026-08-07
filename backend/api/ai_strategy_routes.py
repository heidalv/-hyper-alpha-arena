"""AI Strategy Management API Routes

Provides REST endpoints for:
- Creating, reading, updating, deleting AI strategies
- Activating/pausing strategies
- Manually executing strategies
- Querying strategy performance and memory
- Unified strategy creation with real AI generation (LLM-powered)
"""
import logging
import asyncio
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, validator
from sqlalchemy.orm import Session

from backend.database.connection import get_db
from backend.database.models import AIStrategy, StrategyMemory, Account, CryptoKline, SignalDefinition, SignalPool, CustomTradingStyle, PromptTemplate
from backend.services.strategy_coordinator import StrategyCoordinator
from backend.services.market_data_analyzer import MarketDataAnalyzer
from backend.services.llm_config_service import get_llm_config_for_account, get_llm_config, call_llm_api, LLMConfig
import json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai-strategies", tags=["AI Strategies"])


# ===== Request/Response Models =====

class AIStrategyCreateRequest(BaseModel):
    """创建AI策略请求"""
    name: str
    description: Optional[str] = None
    account_id: int
    master_prompt_template_id: int
    prompt_variables: Optional[dict] = {}
    signal_pool_ids: Optional[List[int]] = []
    trigger_mode: str = "hybrid"  # signal_driven, scheduled, hybrid
    trigger_interval: Optional[int] = None
    enabled_factors: Optional[List[str]] = []
    factor_weights: Optional[dict] = {}
    
    # 风险配置
    max_position_size: float = 0.2
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.10
    max_daily_loss: float = 0.10
    
    # 执行配置
    auto_execute: bool = False
    require_confirmation: bool = True
    min_confidence: float = 0.6
    
    # 学习配置
    learning_enabled: bool = True
    optimization_target: str = "sharpe"
    training_frequency: str = "weekly"

    # v3 整改: 多周期分层必填，避免全部塞进 "mid" 造成 strategy-100% mid-skew
    timeframe_tier: str = "mid"  # short / mid / long

    # v3 整改: 进化血缘追踪 —
    #   如果该策略是从父策略/模板衍生而来（GA 冠军同步 / hypothesis 晋升 / 手动 clone），填入父 strategy_id
    parent_strategy_id: Optional[str] = None

    # LLM 绑定（可选）：策略级覆盖，为空时跟随账户绑定，再回退全局默认
    llm_config_id: Optional[int] = None
    llm_config_id_deep: Optional[int] = None

    @validator("timeframe_tier")
    def _validate_tier(cls, v):  # noqa: D401
        _t = (v or "").strip().lower()
        if _t not in ("short", "mid", "long"):
            raise ValueError("timeframe_tier 必填且须为 short / mid / long")
        return _t


class AIStrategyUpdateRequest(BaseModel):
    """更新AI策略请求（所有字段可选）"""
    name: Optional[str] = None
    description: Optional[str] = None
    master_prompt_template_id: Optional[int] = None
    prompt_variables: Optional[dict] = None
    signal_pool_ids: Optional[List[int]] = None
    trigger_mode: Optional[str] = None
    trigger_interval: Optional[int] = None
    enabled_factors: Optional[List[str]] = None
    factor_weights: Optional[dict] = None
    max_position_size: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    max_daily_loss: Optional[float] = None
    auto_execute: Optional[bool] = None
    require_confirmation: Optional[bool] = None
    min_confidence: Optional[float] = None
    learning_enabled: Optional[bool] = None
    optimization_target: Optional[str] = None
    training_frequency: Optional[str] = None
    llm_config_id: Optional[int] = None
    llm_config_id_deep: Optional[int] = None


class AIStrategyResponse(BaseModel):
    """AI策略响应（V2增强 - 包含风控和学习字段）"""
    id: int
    strategy_id: str
    name: str
    description: Optional[str] = None
    status: str
    account_id: int
    master_prompt_template_id: Optional[int] = 1
    prompt_version: Optional[int] = 1
    prompt_variables: Optional[dict] = None
    signal_pool_ids: Optional[List[int]] = []
    trigger_mode: Optional[str] = "hybrid"
    trigger_interval: Optional[int] = None
    enabled_factors: Optional[List[str]] = []
    factor_weights: Optional[dict] = None
    auto_execute: Optional[bool] = False
    require_confirmation: Optional[bool] = True
    
    # 交易对配置
    target_symbols: Optional[List[str]] = ["BTC"]
    primary_symbol: Optional[str] = "BTC"
    timeframe: Optional[str] = "15m"
    
    # 风险配置（V2: 前端策略卡片展示所需）
    max_position_size: Optional[float] = 0.2
    stop_loss_pct: Optional[float] = 0.05
    take_profit_pct: Optional[float] = 0.10
    max_daily_loss: Optional[float] = 0.10
    min_confidence: Optional[float] = 0.6
    
    # 杠杆配置
    max_leverage: Optional[float] = 20.0
    default_leverage: Optional[float] = 10.0
    leverage_mode: Optional[str] = "cross"
    
    # 学习配置
    learning_enabled: Optional[bool] = True
    optimization_target: Optional[str] = "sharpe"
    training_frequency: Optional[str] = "weekly"
    llm_config_id: Optional[int] = None
    llm_config_id_deep: Optional[int] = None
    
    # 自主运行配置
    auto_mode: Optional[str] = "semi_auto"
    analysis_intervals: Optional[dict] = None
    
    # 多周期分层
    timeframe_tier: Optional[str] = None  # short / mid / long
    # 策略基因组（统一参数结构，用于进化和自适应）
    genome: Optional[dict] = None
    
    created_at: Optional[str] = None
    activated_at: Optional[str] = None
    last_executed_at: Optional[str] = None
    
    class Config:
        from_attributes = True
    
    @validator("created_at", "activated_at", "last_executed_at", pre=True, always=True)
    def _ts_to_str(cls, v):
        if v is None:
            return None
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)


class StrategyExecuteRequest(BaseModel):
    """手动执行策略请求"""
    trigger_reason: Optional[str] = "manual_trigger"
    force: bool = False


class StrategyExecuteResponse(BaseModel):
    """策略执行响应"""
    success: bool
    decisions: List[dict]
    error_code: Optional[str] = None
    error_message: Optional[str] = None


# ===== API Endpoints =====

@router.post("", response_model=AIStrategyResponse, status_code=201)
def create_strategy(
    request: AIStrategyCreateRequest,
    db: Session = Depends(get_db),
):
    """创建新的AI策略"""
    try:
        # 验证账户存在
        account = db.query(Account).filter(Account.id == request.account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail=f"Account {request.account_id} not found")
        
        # 生成strategy_id
        import uuid
        strategy_id = f"ai_strategy_{uuid.uuid4().hex[:12]}"

        # v3 整改: 血缘追踪 — 计算 lineage_generation（父代 + 1；父不存在则 0）
        _lineage_gen = 0
        if request.parent_strategy_id:
            _parent = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == request.parent_strategy_id
            ).first()
            if _parent is not None:
                _lineage_gen = int(getattr(_parent, "lineage_generation", 0) or 0) + 1
        
        # V3 整合: 若用户未指定因子权重，自动从因子引擎推荐
        _recommended_factors = []
        _recommended_weights = request.factor_weights or {}
        if not _recommended_weights:
            try:
                from backend.services.strategy_generator import get_strategy_generator
                _sym = (request.target_symbols or ["BTC"])[0]
                _recommended_factors, _recommended_weights = get_strategy_generator()._recommend_factors(_sym)
                if _recommended_factors:
                    logger.info(f"[AIStrategy] 因子推荐: {_sym} → {len(_recommended_factors)} factors")
            except Exception as _rf_err:
                logger.debug(f"[AIStrategy] 因子推荐跳过(非致命): {_rf_err}")
        
        # 创建策略
        strategy = AIStrategy(
            strategy_id=strategy_id,
            name=request.name,
            description=request.description,
            account_id=request.account_id,
            master_prompt_template_id=request.master_prompt_template_id,
            prompt_variables=request.prompt_variables,
            signal_pool_ids=request.signal_pool_ids,
            trigger_mode=request.trigger_mode,
            trigger_interval=request.trigger_interval,
            enabled_factors=request.enabled_factors or (_recommended_factors or None),
            factor_weights=_recommended_weights or request.factor_weights,
            max_position_size=request.max_position_size,
            stop_loss_pct=request.stop_loss_pct,
            take_profit_pct=request.take_profit_pct,
            max_daily_loss=request.max_daily_loss,
            auto_execute=request.auto_execute,
            require_confirmation=request.require_confirmation,
            min_confidence=request.min_confidence,
            learning_enabled=request.learning_enabled,
            optimization_target=request.optimization_target,
            training_frequency=request.training_frequency,
            timeframe_tier=request.timeframe_tier,  # v3 整改: 持久化必填字段，避免 mid-skew
            parent_strategy_id=request.parent_strategy_id,  # v3 整改: 血缘追踪
            lineage_generation=_lineage_gen,
            llm_config_id=request.llm_config_id,
            llm_config_id_deep=request.llm_config_id_deep,
            status="draft",
        )
        
        db.add(strategy)
        db.commit()
        db.refresh(strategy)
        
        logger.info(f"Created AI strategy: {strategy_id} tier={request.timeframe_tier}")
        return strategy
        
    except Exception as e:
        logger.error(f"Failed to create AI strategy: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=List[AIStrategyResponse])
def list_strategies(
    account_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """获取AI策略列表"""
    try:
        query = db.query(AIStrategy)
        
        if account_id:
            query = query.filter(AIStrategy.account_id == account_id)
        if status:
            query = query.filter(AIStrategy.status == status)
        
        strategies = query.order_by(AIStrategy.created_at.desc()).all()
        return strategies
    except Exception as e:
        # 如果查询失败（可能是新列不存在），尝试自动修复
        db.rollback()
        error_msg = str(e).lower()
        if "no such column" in error_msg or "does not exist" in error_msg or "unknown column" in error_msg or "target_symbols" in error_msg or "primary_symbol" in error_msg or "timeframe" in error_msg or "max_leverage" in error_msg or "snowball" in error_msg:
            logger.warning(f"[list_strategies] 检测到缺失列，尝试自动修复: {e}")
            try:
                _auto_fix_missing_columns(db)
                # 重试查询
                query = db.query(AIStrategy)
                if account_id:
                    query = query.filter(AIStrategy.account_id == account_id)
                if status:
                    query = query.filter(AIStrategy.status == status)
                return query.order_by(AIStrategy.created_at.desc()).all()
            except Exception as retry_err:
                logger.error(f"[list_strategies] 自动修复后仍然失败: {retry_err}")
                raise HTTPException(status_code=500, detail=f"数据库列缺失，请重启后端: {str(e)}")
        raise


@router.get("/stats/tier-distribution")
def tier_distribution(
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """
    查询 AI 策略的 tier 分布 — v3 整改新增。

    用于前端 TierDistribution 组件展示活跃策略是否均匀落在 short/mid/long，
    以及与 TIER_DIVERSITY_QUOTA 目标配额的偏差，便于监控 "100% mid-skew" 问题。

    Returns:
        {
          "total": int,
          "distribution": {"short": int, "mid": int, "long": int, "unknown": int},
          "ratio":        {"short": float, "mid": float, "long": float},
          "quota":        {"short": float, "mid": float, "long": float},
          "deviation":    {"short": float, "mid": float, "long": float}   # ratio - quota
        }
    """
    try:
        q = db.query(AIStrategy).filter(AIStrategy.status == "active")
        if account_id:
            q = q.filter(AIStrategy.account_id == account_id)
        rows = q.all()

        dist = {"short": 0, "mid": 0, "long": 0, "unknown": 0}
        for r in rows:
            t = (getattr(r, "timeframe_tier", None) or "").strip().lower()
            dist[t if t in ("short", "mid", "long") else "unknown"] += 1

        known_total = dist["short"] + dist["mid"] + dist["long"]
        ratio = {k: (dist[k] / known_total if known_total > 0 else 0.0) for k in ("short", "mid", "long")}
        deviation = {k: (ratio[k] - TIER_DIVERSITY_QUOTA.get(k, 0.0)) for k in ("short", "mid", "long")}

        return {
            "total": len(rows),
            "distribution": dist,
            "ratio": ratio,
            "quota": TIER_DIVERSITY_QUOTA,
            "deviation": deviation,
        }
    except Exception as e:
        logger.error(f"[tier_distribution] failed: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


def _auto_fix_missing_columns(db: Session):
    """在运行时自动添加缺失列（SQLite 和 PostgreSQL 兼容）"""
    from sqlalchemy import text
    columns_to_add = [
        ("target_symbols", "TEXT"),
        ("primary_symbol", "VARCHAR(20) DEFAULT 'BTC'"),
        ("timeframe", "VARCHAR(10) DEFAULT '15m'"),
        ("max_leverage", "FLOAT DEFAULT 20.0"),
        ("default_leverage", "FLOAT DEFAULT 10.0"),
        ("leverage_mode", "VARCHAR(20) DEFAULT 'cross'"),
        ("snowball_enabled", "BOOLEAN DEFAULT FALSE"),
        ("snowball_max_adds", "INTEGER DEFAULT 3"),
        ("snowball_profit_threshold", "FLOAT DEFAULT 0.05"),
        ("auto_mode", "VARCHAR(20) DEFAULT 'semi_auto'"),
        ("analysis_intervals", "TEXT"),
        ("last_short_analysis_at", "TIMESTAMP"),
        ("last_mid_analysis_at", "TIMESTAMP"),
        ("last_long_analysis_at", "TIMESTAMP"),
        ("analysis_results_cache", "TEXT"),
        ("timeframe_tier", "VARCHAR(10)"),
        ("genome", "TEXT"),
        ("last_trade_at", "TIMESTAMP"),
        ("parent_strategy_id", "VARCHAR(50)"),
        ("lineage_generation", "INTEGER DEFAULT 0"),
    ]
    for col_name, col_def in columns_to_add:
        try:
            db.execute(text(f"ALTER TABLE ai_strategies ADD COLUMN {col_name} {col_def}"))
            logger.info(f"[auto_fix] 已添加列 ai_strategies.{col_name}")
        except Exception:
            pass
    db.commit()
    try:
        db.execute(text("UPDATE ai_strategies SET target_symbols = '[\"BTC\"]' WHERE target_symbols IS NULL"))
        db.execute(text("UPDATE ai_strategies SET primary_symbol = 'BTC' WHERE primary_symbol IS NULL"))
        db.execute(text("UPDATE ai_strategies SET timeframe = '15m' WHERE timeframe IS NULL"))
        db.commit()
    except Exception:
        db.rollback()


# ═══════════════════════════════════════════════════════════
#  快速试单 + 学习激活控制面板 API（须在 /{strategy_id} 之前注册）
# ═══════════════════════════════════════════════════════════

class FastTrialPatch(BaseModel):
    patches: dict


class FastTrialPreset(BaseModel):
    preset: str  # fast | learning | scalp | midlong | balanced | conservative


@router.get("/fast-trial")
def get_fast_trial_config():
    """获取快速试单仪表盘、开关状态与参数 schema。"""
    from backend.services.paper_fast_trial_controller import paper_fast_trial_controller
    return paper_fast_trial_controller.to_dict()


@router.patch("/fast-trial")
def patch_fast_trial_config(body: FastTrialPatch):
    """热更新快速试单参数（运行时生效，持久化到 data/paper_fast_trial.json）。"""
    from backend.services.paper_fast_trial_controller import paper_fast_trial_controller
    try:
        return paper_fast_trial_controller.update(body.patches or {})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/fast-trial/preset")
def apply_fast_trial_preset(body: FastTrialPreset):
    """一键应用预设：fast / learning / scalp / midlong / balanced / conservative。"""
    from backend.services.paper_fast_trial_controller import paper_fast_trial_controller
    try:
        return paper_fast_trial_controller.apply_preset(body.preset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{strategy_id}", response_model=AIStrategyResponse)
def get_strategy(
    strategy_id: str,
    db: Session = Depends(get_db),
):
    """获取单个AI策略详情"""
    try:
        strategy = db.query(AIStrategy).filter(
            AIStrategy.strategy_id == strategy_id
        ).first()
    except Exception as e:
        db.rollback()
        error_msg = str(e).lower()
        if "no such column" in error_msg or "does not exist" in error_msg or "unknown column" in error_msg or "target_symbols" in error_msg or "max_leverage" in error_msg or "snowball" in error_msg:
            _auto_fix_missing_columns(db)
            strategy = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == strategy_id
            ).first()
        else:
            raise
    
    if not strategy:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
    
    return strategy


@router.put("/{strategy_id}", response_model=AIStrategyResponse)
def update_strategy(
    strategy_id: str,
    request: AIStrategyUpdateRequest,
    db: Session = Depends(get_db),
):
    """更新AI策略配置"""
    strategy = db.query(AIStrategy).filter(
        AIStrategy.strategy_id == strategy_id
    ).first()
    
    if not strategy:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
    
    # 只更新提供的字段
    update_data = request.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(strategy, key, value)
    
    db.commit()
    db.refresh(strategy)
    
    logger.info(f"Updated AI strategy: {strategy_id}")
    return strategy


@router.delete("/{strategy_id}", status_code=204)
def delete_strategy(
    strategy_id: str,
    db: Session = Depends(get_db),
):
    """删除AI策略"""
    strategy = db.query(AIStrategy).filter(
        AIStrategy.strategy_id == strategy_id
    ).first()
    
    if not strategy:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
    
    db.delete(strategy)
    db.commit()
    
    logger.info(f"Deleted AI strategy: {strategy_id}")
    return None


@router.post("/{strategy_id}/activate", response_model=AIStrategyResponse)
def activate_strategy(
    strategy_id: str,
    db: Session = Depends(get_db),
):
    """激活AI策略 → 同时注册到自主分析循环"""
    strategy = db.query(AIStrategy).filter(
        AIStrategy.strategy_id == strategy_id
    ).first()
    
    if not strategy:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
    
    strategy.status = "active"
    from datetime import datetime
    strategy.activated_at = datetime.now(timezone.utc)
    
    db.commit()
    db.refresh(strategy)

    try:
        from backend.services.autonomous_strategy_service import autonomous_service
        autonomous_service.register_strategy(strategy_id)
    except Exception as e:
        logger.warning(f"自主循环注册失败（不影响激活）: {e}")
    
    logger.info(f"Activated AI strategy: {strategy_id}")
    return strategy


@router.post("/{strategy_id}/pause", response_model=AIStrategyResponse)
def pause_strategy(
    strategy_id: str,
    db: Session = Depends(get_db),
):
    """暂停AI策略 → 同时从自主分析循环注销"""
    strategy = db.query(AIStrategy).filter(
        AIStrategy.strategy_id == strategy_id
    ).first()
    
    if not strategy:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
    
    strategy.status = "paused"
    
    db.commit()
    db.refresh(strategy)

    try:
        from backend.services.autonomous_strategy_service import autonomous_service
        autonomous_service.unregister_strategy(strategy_id)
    except Exception as e:
        logger.warning(f"自主循环注销失败: {e}")
    
    logger.info(f"Paused AI strategy: {strategy_id}")
    return strategy


@router.post("/{strategy_id}/archive", response_model=AIStrategyResponse)
def archive_strategy(
    strategy_id: str,
    reason: str = Query(default="manual", description="归档原因"),
    db: Session = Depends(get_db),
):
    """归档AI策略 → 从自主分析循环注销"""
    strategy = db.query(AIStrategy).filter(
        AIStrategy.strategy_id == strategy_id
    ).first()

    if not strategy:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")

    old_status = strategy.status
    strategy.status = "archived"
    from datetime import datetime
    strategy.archived_at = datetime.now(timezone.utc)
    strategy.archive_reason = reason

    db.commit()
    db.refresh(strategy)

    try:
        from backend.services.autonomous_strategy_service import autonomous_service
        autonomous_service.unregister_strategy(strategy_id)
    except Exception as e:
        logger.warning(f"自主循环注销失败: {e}")

    logger.info(f"Archived AI strategy: {strategy_id} (was {old_status}, reason={reason})")
    return strategy


@router.post("/{strategy_id}/resume", response_model=AIStrategyResponse)
def resume_strategy(
    strategy_id: str,
    db: Session = Depends(get_db),
):
    """恢复已归档/已终止的策略 → 重新激活"""
    strategy = db.query(AIStrategy).filter(
        AIStrategy.strategy_id == strategy_id
    ).first()

    if not strategy:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")

    if strategy.status not in ("archived", "terminated", "paused"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resume strategy in '{strategy.status}' status"
        )

    strategy.status = "active"
    from datetime import datetime
    strategy.activated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(strategy)

    try:
        from backend.services.autonomous_strategy_service import autonomous_service
        autonomous_service.register_strategy(strategy_id)
    except Exception as e:
        logger.warning(f"自主循环注册失败（不影响恢复）: {e}")

    logger.info(f"Resumed AI strategy: {strategy_id}")
    return strategy


@router.post("/{strategy_id}/execute", response_model=StrategyExecuteResponse)
def execute_strategy(
    strategy_id: str,
    request: StrategyExecuteRequest = StrategyExecuteRequest(),
    db: Session = Depends(get_db),
):
    """手动执行AI策略（V2 - 协调器增强版）"""
    try:
        coordinator = StrategyCoordinator(db)
        
        strategy = db.query(AIStrategy).filter(AIStrategy.strategy_id == strategy_id).first()
        if not strategy:
            return StrategyExecuteResponse(
                success=False,
                decisions=[],
                error_code="STRATEGY_NOT_FOUND",
                error_message=f"Strategy {strategy_id} not found",
            )
        
        symbol = strategy.primary_symbol or "BTC"
        env = coordinator.analyze_market_environment(symbol)

        # 获取当前价格作为 entry_price
        from backend.database.models import CryptoPrice
        price_obj = db.query(CryptoPrice).filter(CryptoPrice.symbol == symbol).first()
        entry_price = price_obj.price if price_obj else 0.0

        strategy_config = {
            "stop_loss_pct": strategy.stop_loss_pct or 0.05,
            "take_profit_pct": strategy.take_profit_pct or 0.10,
            "max_position_size": strategy.max_position_size or 0.2,
            "max_leverage": strategy.max_leverage or 20.0,
            "default_leverage": strategy.default_leverage or 10.0,
            "leverage_mode": strategy.leverage_mode or "isolated",
            # 2026-07-06 整改：calculate_dynamic_risk_params 不再允许从主导周期
            # 静默反推 tier，策略本身已持久化 timeframe_tier，直接显式传入。
            "timeframe_tier": strategy.timeframe_tier or "mid",
        }
        risk_params = coordinator.calculate_dynamic_risk_params(
            symbol, "long", entry_price, strategy_config, env
        )
        from backend.database.models import StrategyMemory
        memory = db.query(StrategyMemory).filter(StrategyMemory.strategy_id == strategy_id).first()
        context = coordinator.build_enhanced_context(strategy, memory, env, risk_params)
        
        return StrategyExecuteResponse(
            success=True,
            decisions=[{
                "action": "context_built",
                "symbol": symbol,
                "market_regime": env.market_cycle if env else "unknown",
                "risk_params": vars(risk_params) if risk_params else {},
                "note": "Use /api/ai-trading for full auto execution",
            }],
        )
            
    except Exception as e:
        logger.error(f"Strategy execution error: {e}")
        return StrategyExecuteResponse(
            success=False,
            decisions=[],
            error_code="INTERNAL_ERROR",
            error_message=str(e),
        )


@router.get("/{strategy_id}/memory")
def get_strategy_memory(
    strategy_id: str,
    db: Session = Depends(get_db),
):
    """获取策略记忆（V2增强版 - 包含市场状态分类表现、成功/失败模式、关键教训）"""
    memory = db.query(StrategyMemory).filter(
        StrategyMemory.strategy_id == strategy_id
    ).first()
    
    if not memory:
        return {
            "strategy_id": strategy_id,
            "total_trades": 0,
            "win_rate": 0.0,
            "message": "No memory data yet",
        }
    
    # 安全解析 JSON 字段
    def safe_json(val):
        if val is None:
            return None
        if isinstance(val, (dict, list)):
            return val
        try:
            return json.loads(val) if isinstance(val, str) else None
        except (json.JSONDecodeError, TypeError):
            return None
    
    return {
        "strategy_id": memory.strategy_id,
        "total_trades": memory.total_trades,
        "win_rate": memory.win_rate,
        "avg_profit": memory.avg_profit,
        "avg_loss": memory.avg_loss,
        "sharpe_ratio": memory.sharpe_ratio,
        "max_drawdown": memory.max_drawdown,
        # V2 新增字段
        "performance_by_regime": safe_json(memory.performance_by_regime),
        "successful_patterns": safe_json(memory.successful_patterns),
        "failed_patterns": safe_json(memory.failed_patterns),
        "key_lessons": safe_json(memory.key_lessons),
        "updated_at": str(memory.updated_at),
    }


def _format_market_env_response(env, coordinator, symbol: str) -> dict:
    """格式化市场环境分析结果为 API 响应"""
    resp = {
        "symbol": symbol,
        "macro": {
            "market_cycle": env.market_cycle,
            "cycle_confidence": round(env.cycle_confidence, 4),
            "risk_budget_pct": round(env.risk_budget_pct, 4),
        },
        "micro": {
            "volatility_regime": env.volatility_regime,
            "volatility_value": round(env.volatility_value, 6),
            "trend_direction": env.trend_direction,
            "trend_strength": round(env.trend_strength, 4),
            "liquidity_score": round(env.liquidity_score, 4),
        },
        "adapted_params": {
            "sl_multiplier": round(env.adapted_sl_multiplier, 4),
            "tp_multiplier": round(env.adapted_tp_multiplier, 4),
            "position_scale": round(env.adapted_position_scale, 4),
            "entry_threshold": round(env.adapted_entry_threshold, 4),
        },
        "guidance": coordinator._generate_market_guidance(env),
        "data_source": env.data_source,
        "price_source": getattr(env, 'price_source', 'unknown'),
        "kline_count": env.kline_count,
        "kline_age_hours": getattr(env, 'kline_age_hours', 0),
        "current_price": round(env.current_price, 2),
        "atr_value": round(env.atr_value, 4),
        "analysis_time": env.analysis_time,
    }
    # 如果价格数据过期，加入警告
    stale_warn = getattr(env, 'price_stale_warning', '')
    if stale_warn:
        resp["price_stale_warning"] = stale_warn
    return resp


# ===== 自主运行 API =====

# ===== 自学习 API =====

@router.post("/{strategy_id}/learn")
def trigger_learning_review(strategy_id: str, days: int = Query(default=7, ge=1, le=90)):
    """手动触发策略学习复盘"""
    from backend.services.strategy_learning_service import strategy_learning
    return strategy_learning.run_periodic_review(strategy_id, days)


@router.get("/{strategy_id}/learning-dashboard")
def get_learning_dashboard(strategy_id: str):
    """获取策略学习仪表盘"""
    from backend.services.strategy_learning_service import strategy_learning
    return strategy_learning.get_learning_dashboard(strategy_id)


# ===== 策略优化 API =====

class OptimizeRequest(BaseModel):
    max_iterations: int = 5
    min_sharpe: float = 1.0
    min_win_rate: float = 0.50
    max_drawdown: float = 0.20


@router.post("/{strategy_id}/optimize")
async def optimize_strategy(strategy_id: str, request: OptimizeRequest = OptimizeRequest()):
    """启动策略自主回测优化"""
    from backend.services.strategy_optimizer_service import strategy_optimizer, OptimizationTargets
    targets = OptimizationTargets(
        min_sharpe=request.min_sharpe,
        min_win_rate=request.min_win_rate,
        max_drawdown=request.max_drawdown,
    )
    result = await strategy_optimizer.optimize_strategy(
        strategy_id=strategy_id,
        max_iterations=request.max_iterations,
        targets=targets,
    )
    return result


@router.get("/{strategy_id}/optimization-status")
def get_optimization_status(strategy_id: str):
    """获取正在运行的优化状态"""
    from backend.services.strategy_optimizer_service import strategy_optimizer
    status = strategy_optimizer.get_optimization_status(strategy_id)
    if not status:
        return {"running": False}
    return {**status, "running": True}


@router.get("/{strategy_id}/optimization-history")
def get_optimization_history(strategy_id: str, limit: int = Query(default=20, ge=1, le=100)):
    """获取策略优化历史"""
    from backend.services.strategy_optimizer_service import strategy_optimizer
    return strategy_optimizer.get_optimization_history(strategy_id, limit)


@router.get("/global/multi-timeframe")
def get_global_multi_timeframe(
    symbol: str = Query(default="BTC"),
    db: Session = Depends(get_db),
):
    """全局多周期趋势分析 —— 同时返回短线/中线/长线三个周期的独立分析结果

    优先从已运行策略的缓存中获取，否则实时计算
    """
    from backend.services.autonomous_strategy_service import autonomous_service
    from dataclasses import asdict

    # 1) 尝试从活跃策略缓存中获取（最快）
    cached = None
    try:
        full_status = autonomous_service.get_status()
        for sid, info in full_status.get("strategies", {}).items():
            syms = info.get("symbols", [])
            if symbol.upper() in [s.upper() for s in syms]:
                analyses = info.get("cached_analyses", {})
                if analyses:
                    cached = analyses
                    break
    except Exception:
        pass

    if cached and any(k in cached for k in ("short", "mid", "long")):
        return {
            "source": "autonomous_cache",
            "symbol": symbol,
            "timeframes": cached,
        }

    # 2) 实时计算（无缓存时）
    from backend.services.strategy_coordinator import StrategyCoordinator
    from backend.services.exchange_config import get_active_exchange
    from datetime import datetime as dt, timezone as tz

    coordinator = StrategyCoordinator(db)
    exchange = get_active_exchange()
    # 修时区 bug：用 UTC-aware 计算 Unix 秒，避免 kline_age 判断和回溯窗口错位
    now_ts = int(dt.now(tz.utc).timestamp())
    result = {}

    timeframe_configs = [
        ("short", "15m", 7),
        ("mid",   "1h",  30),
        ("long",  "1d",  365),
    ]

    for tf_key, period, lookback_days in timeframe_configs:
        klines = coordinator._get_fresh_klines(symbol, period, lookback_days, now_ts, exchange)
        if not klines or len(klines) < 10:
            result[tf_key] = {
                "timeframe_type": tf_key,
                "trend_direction": "neutral",
                "trend_strength": 0.0,
                "volatility_regime": "normal",
                "signal_strength": 0.0,
                "kline_count": len(klines) if klines else 0,
                "current_price": 0.0,
            }
            continue

        closes = [k["close"] for k in klines]
        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]
        volumes = [k["volume"] for k in klines]

        realtime_price = coordinator._get_realtime_price_robust(symbol, exchange)
        kline_close = closes[-1]
        latest_ts = klines[-1].get("timestamp", 0) if klines else 0
        kline_age_h = (now_ts - latest_ts) / 3600 if latest_ts > 0 else 9999
        
        if realtime_price > 0:
            current_price = realtime_price
            price_source = "realtime"
        elif kline_age_h < 2:
            current_price = kline_close
            price_source = "kline_fresh"
        else:
            current_price = kline_close
            price_source = "kline_stale"

        atr_history = []
        for i in range(1, len(klines)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            atr_history.append(tr)
        atr_val = sum(atr_history[-14:]) / min(len(atr_history), 14) if atr_history else 0
        atr_pct = atr_val / current_price if current_price > 0 else 0

        ema_s = coordinator._calc_ema(closes, 9)
        ema_l = coordinator._calc_ema(closes, 21)

        trend = "neutral"
        strength = 0.2
        if current_price > ema_s > ema_l:
            trend = "bullish"
            strength = min((current_price - ema_l) / ema_l * 30, 1.0) if ema_l > 0 else 0.5
        elif current_price < ema_s < ema_l:
            trend = "bearish"
            strength = min((ema_l - current_price) / ema_l * 30, 1.0) if ema_l > 0 else 0.5

        vol = "normal"
        if atr_pct > 0.04: vol = "extreme"
        elif atr_pct > 0.025: vol = "high"
        elif atr_pct < 0.008: vol = "low"

        vol_trend = "stable"
        if len(volumes) >= 10:
            rv = sum(volumes[-5:]) / 5
            ov = sum(volumes[-10:-5]) / 5
            if ov > 0:
                vr = rv / ov
                vol_trend = "increasing" if vr > 1.2 else ("decreasing" if vr < 0.8 else "stable")

        signal = 0.0
        if trend == "bullish": signal += strength * 0.5
        elif trend == "bearish": signal -= strength * 0.5
        if vol_trend == "increasing" and trend != "neutral":
            signal += 0.15 if signal > 0 else -0.15
        if vol == "extreme": signal *= 0.5
        signal = max(-1.0, min(1.0, signal))

        market_cycle = "unknown"
        if tf_key == "long" and len(closes) >= 30:
            p30 = closes[-min(30, len(closes))]
            chg = (current_price - p30) / p30 if p30 > 0 else 0
            if chg > 0.10 and trend == "bullish": market_cycle = "bull"
            elif chg < -0.10 and trend == "bearish": market_cycle = "bear"
            elif abs(chg) < 0.03: market_cycle = "sideways"
            elif chg > 0.03: market_cycle = "bull"
            elif chg < -0.03: market_cycle = "bear"
            else: market_cycle = "sideways"

        result[tf_key] = {
            "timeframe_type": tf_key,
            "trend_direction": trend,
            "trend_strength": round(strength, 3),
            "volatility_regime": vol,
            "signal_strength": round(signal, 3),
            "volume_trend": vol_trend,
            "current_price": round(current_price, 2),
            "atr_pct": round(atr_pct, 6),
            "kline_count": len(klines),
            "market_cycle": market_cycle,
            "analysis_time": dt.now(tz.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "price_source": price_source,
            "kline_age_hours": round(kline_age_h, 1),
        }

    return {
        "source": "realtime",
        "symbol": symbol,
        "timeframes": result,
    }


@router.get("/autonomous/status")
def get_autonomous_status():
    """获取自主策略服务整体状态"""
    from backend.services.autonomous_strategy_service import autonomous_service
    return autonomous_service.get_status()


@router.get("/autonomous/{strategy_id}/status")
def get_autonomous_strategy_status(strategy_id: str):
    """获取单个策略的自主运行状态"""
    from backend.services.autonomous_strategy_service import autonomous_service
    status = autonomous_service.get_strategy_status(strategy_id)
    if not status:
        raise HTTPException(status_code=404, detail="策略未在自主循环中运行")
    return status


# ===== 持仓追踪 API =====

@router.get("/position-tracker/all")
def get_all_tracked_positions():
    """获取所有追踪中的持仓"""
    from backend.services.position_tracker_service import position_tracker
    return position_tracker.get_tracked_positions()


@router.get("/position-tracker/{strategy_id}/{symbol}")
def get_tracked_position(strategy_id: str, symbol: str):
    """获取单个持仓的追踪状态"""
    from backend.services.position_tracker_service import position_tracker
    status = position_tracker.get_position_status(strategy_id, symbol)
    if not status:
        raise HTTPException(status_code=404, detail="持仓未在追踪中")
    return status


@router.post("/position-tracker/{strategy_id}/{symbol}/close")
def force_close_position(strategy_id: str, symbol: str):
    """手动强制平仓"""
    from backend.services.position_tracker_service import position_tracker
    pos = position_tracker.get_position_status(strategy_id, symbol)
    if not pos:
        raise HTTPException(status_code=404, detail="持仓未在追踪中")
    position_tracker.stop_tracking(strategy_id, symbol)
    return {"success": True, "message": f"已停止追踪 {symbol}，请手动在交易所平仓"}


@router.get("/autonomous/{strategy_id}/logs")
def get_autonomous_analysis_logs(
    strategy_id: str,
    limit: int = Query(default=50, ge=1, le=200),
):
    """获取策略的自主分析日志"""
    from backend.services.autonomous_strategy_service import autonomous_service
    return autonomous_service.get_analysis_logs(strategy_id, limit)


@router.get("/global/market-environment")
def get_global_market_environment(
    symbol: str = Query(default="BTC", description="交易对"),
    db: Session = Depends(get_db),
):
    """全局市场环境分析（不依赖任何策略）
    
    直接从数据中心读取K线数据进行多周期分析。
    前端策略列表为空时也能调用。
    """
    from backend.services.strategy_coordinator import StrategyCoordinator
    
    try:
        coordinator = StrategyCoordinator(db)
        env = coordinator.analyze_market_environment(symbol=symbol)
        return _format_market_env_response(env, coordinator, symbol)
    except Exception as e:
        logger.error(f"Global market environment error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{strategy_id}/market-environment")
def get_strategy_market_environment(
    strategy_id: str,
    symbol: str = Query(default="BTC", description="主要交易标的"),
    db: Session = Depends(get_db),
):
    """获取策略关联的市场环境分析"""
    from backend.services.strategy_coordinator import StrategyCoordinator
    
    try:
        strategy = db.query(AIStrategy).filter(
            AIStrategy.strategy_id == strategy_id
        ).first()
        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")
        
        coordinator = StrategyCoordinator(db)
        env = coordinator.analyze_market_environment(
            symbol=symbol,
            account_id=strategy.account_id,
        )
        return _format_market_env_response(env, coordinator, symbol)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Market environment analysis error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{strategy_id}/dynamic-risk")
def get_strategy_dynamic_risk(
    strategy_id: str,
    symbol: str = Query(default="BTC", description="交易标的"),
    entry_price: float = Query(default=0, description="入场价格"),
    side: str = Query(default="buy", description="方向 buy/sell"),
    db: Session = Depends(get_db),
):
    """获取策略的动态风险参数（V2新增）
    
    返回：基于ATR和市场环境计算的动态止损止盈、分批止盈级别、仓位建议
    """
    from backend.services.strategy_coordinator import StrategyCoordinator
    
    try:
        strategy = db.query(AIStrategy).filter(
            AIStrategy.strategy_id == strategy_id
        ).first()
        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")
        
        coordinator = StrategyCoordinator(db)
        
        # 市场环境
        env = coordinator.analyze_market_environment(
            symbol=symbol,
            account_id=strategy.account_id,
        )
        
        # 动态风险参数
        risk = coordinator.calculate_dynamic_risk_params(
            symbol=symbol,
            side=side,
            entry_price=entry_price if entry_price > 0 else 1.0,
            strategy_config={
                "stop_loss_pct": strategy.stop_loss_pct or 0.05,
                "take_profit_pct": strategy.take_profit_pct or 0.10,
                "max_position_size": strategy.max_position_size or 0.2,
                # 2026-07-06 整改：显式传入 tier，禁止函数内部反推
                "timeframe_tier": strategy.timeframe_tier or "mid",
            },
            market_env=env,
        )
        
        return {
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "stop_loss": {
                "type": risk.stop_loss_type,
                "pct": round(risk.stop_loss_pct, 4),
                "price": round(risk.stop_loss_price, 6) if risk.stop_loss_price else None,
                "atr_multiple": round(risk.stop_loss_atr_multiple, 2),
            },
            "take_profit_levels": risk.tp_levels,
            "trailing_stop": {
                "enabled": risk.trailing_stop_enabled,
                "activation_pct": round(risk.trailing_activation_pct, 4),
                "distance_pct": round(risk.trailing_distance_pct, 4),
            },
            "time_stop": {
                "enabled": risk.time_stop_enabled,
                "hours": risk.time_stop_hours,
            },
            "position_size_pct": round(risk.position_size_pct, 4),
            "market_env_summary": {
                "cycle": env.market_cycle,
                "volatility": env.volatility_regime,
                "trend": env.trend_direction,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Dynamic risk calculation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ===== V2: 创建向导辅助 API =====

@router.get("/wizard/market-preview")
def wizard_market_preview(
    account_id: int = Query(..., description="账户ID"),
    symbol: str = Query(default="BTC", description="交易标的"),
    stop_loss_pct: float = Query(default=0.05, description="基础止损百分比"),
    take_profit_pct: float = Query(default=0.10, description="基础止盈百分比"),
    max_position_size: float = Query(default=0.2, description="基础仓位比例"),
    timeframe_tier: str = Query(default="mid", description="交易周期定性 short/mid/long，向导预览用"),
    db: Session = Depends(get_db),
):
    """创建向导用：预览市场环境 + 动态风控参数（不需要已存在的策略）
    
    在创建策略的过程中，让用户实时看到：
    1. 当前市场环境（周期、波动率、趋势）
    2. 基础风控值 vs 动态调整后的值
    3. AI 给出的交易建议

    2026-07-06 整改：新增 timeframe_tier 查询参数，显式声明用户在向导里
    正在预览哪个周期档位（默认 mid），不再让 calculate_dynamic_risk_params
    内部用"主导周期"静默猜测——此处还没有真实策略对象，只能由 API 调用方
    （前端向导）显式声明用户当前选择的 tier。
    """
    from backend.services.strategy_coordinator import StrategyCoordinator
    
    try:
        coordinator = StrategyCoordinator(db)
        
        # 市场环境分析
        env = coordinator.analyze_market_environment(
            symbol=symbol,
            account_id=account_id,
        )
        
        # 基于用户输入的基础值计算动态风控
        risk = coordinator.calculate_dynamic_risk_params(
            symbol=symbol,
            side="buy",
            entry_price=1.0,  # 占位，百分比计算不依赖绝对价格
            strategy_config={
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "max_position_size": max_position_size,
                "timeframe_tier": timeframe_tier,
            },
            market_env=env,
        )
        
        return {
            "market_env": {
                "market_cycle": env.market_cycle,
                "cycle_confidence": round(env.cycle_confidence, 4),
                "risk_budget_pct": round(env.risk_budget_pct, 4),
                "volatility_regime": env.volatility_regime,
                "volatility_value": round(env.volatility_value, 6),
                "trend_direction": env.trend_direction,
                "trend_strength": round(env.trend_strength, 4),
                "liquidity_score": round(env.liquidity_score, 4),
                # 数据溯源
                "data_source": env.data_source,
                "kline_count": env.kline_count,
                "current_price": round(env.current_price, 2),
                "atr_value": round(env.atr_value, 4),
                "analysis_time": env.analysis_time,
            },
            "base_params": {
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "max_position_size": max_position_size,
            },
            "dynamic_params": {
                "stop_loss_pct": round(risk.stop_loss_pct, 4),
                "stop_loss_type": risk.stop_loss_type,
                "tp_levels": risk.tp_levels,
                "trailing_stop": {
                    "enabled": risk.trailing_stop_enabled,
                    "activation_pct": round(risk.trailing_activation_pct, 4),
                    "distance_pct": round(risk.trailing_distance_pct, 4),
                },
                "time_stop_hours": risk.time_stop_hours,
                "position_size_pct": round(risk.position_size_pct, 4),
            },
            "adapted_multipliers": {
                "sl_multiplier": round(env.adapted_sl_multiplier, 4),
                "tp_multiplier": round(env.adapted_tp_multiplier, 4),
                "position_scale": round(env.adapted_position_scale, 4),
                "entry_threshold": round(env.adapted_entry_threshold, 4),
            },
            "guidance": coordinator._generate_market_guidance(env),
        }
    except Exception as e:
        logger.error(f"Wizard market preview error: {e}", exc_info=True)
        # 返回默认值而不是报错（向导阶段不阻断流程）
        return {
            "market_env": {
                "market_cycle": "unknown",
                "cycle_confidence": 0.5,
                "risk_budget_pct": 0.5,
                "volatility_regime": "normal",
                "volatility_value": 0,
                "trend_direction": "neutral",
                "trend_strength": 0,
                "liquidity_score": 1.0,
            },
            "base_params": {
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "max_position_size": max_position_size,
            },
            "dynamic_params": {
                "stop_loss_pct": stop_loss_pct,
                "stop_loss_type": "fixed_pct",
                "tp_levels": [],
                "trailing_stop": {"enabled": False, "activation_pct": 0, "distance_pct": 0},
                "time_stop_hours": 72,
                "position_size_pct": max_position_size,
            },
            "adapted_multipliers": {
                "sl_multiplier": 1.0,
                "tp_multiplier": 1.0,
                "position_scale": 1.0,
                "entry_threshold": 0.6,
            },
            "guidance": "市场环境分析不可用，使用默认参数",
            "error": str(e),
        }


# ===== Strategy Creation Endpoints =====

class GenerateFrameworkRequest(BaseModel):
    """生成策略框架请求"""
    user_requirement: str
    trading_style: str
    account_id: int
    target_symbols: List[str] = ["BTC"]       # 用户选择的交易对
    primary_symbol: Optional[str] = None      # 主要交易标的（默认取 target_symbols[0]）
    timeframe: Optional[str] = None           # 交易时间周期（默认从需求中检测）
    llm_config_id: Optional[int] = None       # 策略级 LLM 覆盖（快）
    llm_config_id_deep: Optional[int] = None  # 策略级 LLM 覆盖（深/分析）


class StrategyFrameworkResponse(BaseModel):
    """策略框架响应"""
    strategy_name: str
    strategy_description: str
    strategy_logic: str
    entry_conditions: List[str]
    exit_conditions: List[str]
    recommended_symbols: List[str] = []
    recommended_timeframe: str = "15m"
    # 生成来源和质量标识
    generation_source: str = "template"  # "ai" = LLM真实生成, "template" = 规则模板回退
    generation_detail: str = ""  # 详细说明（AI模型名称 或 回退原因）
    market_data_used: bool = False  # 是否使用了真实市场数据
    confidence_note: str = ""  # 对生成质量的提示


class GenerateSignalsRequest(BaseModel):
    """生成信号定义请求"""
    strategy_logic: str
    entry_conditions: List[str]
    exit_conditions: List[str]


class SignalDefinitionResponse(BaseModel):
    """信号定义响应"""
    name: str
    description: str
    signal_type: str
    calculation_logic: str
    parameters: dict


class GenerateSignalsResponse(BaseModel):
    """生成信号响应"""
    signals: List[SignalDefinitionResponse]
    signal_pool_logic: str = "AND"


class CreateCompleteRequest(BaseModel):
    """统一创建完整系统请求"""
    # 基本信息
    account_id: int
    user_requirement: str
    trading_style: str
    
    # 交易对配置
    target_symbols: List[str] = ["BTC"]       # 交易对列表
    primary_symbol: Optional[str] = None      # 主要交易标的
    timeframe: Optional[str] = "15m"          # 时间周期
    
    # AI生成的策略框架
    strategy_name: str
    strategy_description: str
    strategy_logic: str
    entry_conditions: List[str]
    exit_conditions: List[str]
    
    # AI生成的信号定义
    generated_signals: List[dict]
    
    # 信号池配置
    signal_pool: dict
    
    # 风险配置
    risk_config: dict
    
    # 执行配置
    execution_config: dict


class CreateCompleteResponse(BaseModel):
    """统一创建响应"""
    success: bool
    strategy_id: str
    strategy_db_id: int
    signal_pool_id: int
    created_signals_count: int
    message: str


@router.post("/generate-framework", response_model=StrategyFrameworkResponse)
async def generate_strategy_framework(
    request: GenerateFrameworkRequest,
    db: Session = Depends(get_db),
):
    """
    第1步：AI生成策略框架（真实LLM + 历史数据分析）
    
    完整流程：
    1. 从用户需求中提取交易标的和时间周期
    2. 查询历史K线数据进行市场状态分析
    3. 将用户需求 + 历史数据洞察 发送给 LLM
    4. LLM 返回结构化的策略框架（JSON格式）
    5. 如果 LLM 不可用，回退到基于规则的模板生成
    """
    try:
        logger.info(f"[AI策略生成] 收到需求: {request.user_requirement[:100]}...")
        
        import re
        
        # ===== 步骤1：确定交易标的 =====
        # 优先使用用户在前端选择的交易对，如果没有则从需求文本中正则提取
        detected_symbols = request.target_symbols if request.target_symbols else []
        
        if not detected_symbols:
            # 回退：从需求文本中提取
            symbol_patterns = {
                r'\bBTC\b|\bbitcoin\b': 'BTC',
                r'\bETH\b|\bethereum\b': 'ETH',
                r'\bSOL\b|\bsolana\b': 'SOL',
                r'\bBNB\b': 'BNB',
                r'\bAVAX\b': 'AVAX',
                r'\bDOGE\b': 'DOGE',
                r'\bXRP\b': 'XRP',
                r'\bADA\b': 'ADA',
                r'\bDOT\b': 'DOT',
                r'\bLINK\b': 'LINK',
            }
            requirement_lower = request.user_requirement.lower()
            for pattern, symbol in symbol_patterns.items():
                if re.search(pattern, requirement_lower, re.IGNORECASE):
                    detected_symbols.append(symbol)
        
        if not detected_symbols:
            detected_symbols = ['BTC']
        
        primary_symbol = request.primary_symbol or detected_symbols[0]
        
        # ===== 步骤2：确定时间周期 =====
        # 优先使用用户选择的时间周期
        detected_period = request.timeframe or '15m'
        
        if not request.timeframe:
            # 回退：从需求文本中提取
            requirement_lower = request.user_requirement.lower()
            period_patterns = {
                r'\b1分钟\b|\b1m\b|\b1min\b': '1m',
                r'\b5分钟\b|\b5m\b|\b5min\b': '5m',
                r'\b15分钟\b|\b15m\b|\b15min\b': '15m',
                r'\b30分钟\b|\b30m\b|\b30min\b': '30m',
                r'\b1小时\b|\b1h\b|\b1hour\b': '1h',
                r'\b4小时\b|\b4h\b|\b4hour\b': '4h',
                r'\b1天\b|\b日线\b|\b1d\b|\b1day\b': '1d',
            }
            for pattern, period in period_patterns.items():
                if re.search(pattern, requirement_lower, re.IGNORECASE):
                    detected_period = period
                    break
        
        logger.info(f"[AI策略生成] 交易对: symbols={detected_symbols}, primary={primary_symbol}, period={detected_period}")
        
        # ===== 步骤3：获取市场数据 =====
        # 策略1: 先从交易所实时拉取最新K线（最可靠）
        # 策略2: 如果失败，回退到数据库历史数据
        historical_analysis = None
        market_context = ""
        data_source_label = ""
        
        # --- 策略1: 直接从交易所API实时获取 ---
        try:
            from services.exchange_config import get_active_exchange, get_exchange_for_account
            
            live_klines = None
            # 通过中央配置决定交易所
            if hasattr(request, 'account_id') and request.account_id:
                active_exchange = get_exchange_for_account(request.account_id)
            else:
                active_exchange = get_active_exchange()
            exchange_label = "Binance" if active_exchange == "binance" else "Hyperliquid"
            
            if active_exchange == "binance":
                # Phase 1: Binance removed - use Hyperliquid path
                pass
            if not live_klines:
                # ?????? active exchange ??????????? HL ??
                try:
                    if active_exchange in ("aster", "asterdex"):
                        from backend.services.kline_data_service import kline_service as _aks
                        rows = _aks.query_klines(
                            primary_symbol, detected_period,
                            exchange="asterdex", limit=200, order="asc",
                        ) or []
                        if len(rows) >= 20:
                            live_klines = [{
                                "timestamp": int(k.get("timestamp", 0)),
                                "open": float(k.get("open", 0)),
                                "high": float(k.get("high", 0)),
                                "low": float(k.get("low", 0)),
                                "close": float(k.get("close", 0)),
                                "volume": float(k.get("volume", 0)),
                            } for k in rows]
                            exchange_label = "Asterdex"
                    elif active_exchange == "binance":
                        from backend.services.kline_data_service import kline_service as _bks
                        rows = _bks.query_klines(
                            primary_symbol, detected_period,
                            exchange="binance", limit=200, order="asc",
                        ) or []
                        if len(rows) >= 20:
                            live_klines = [{
                                "timestamp": int(k.get("timestamp", 0)),
                                "open": float(k.get("open", 0)),
                                "high": float(k.get("high", 0)),
                                "low": float(k.get("low", 0)),
                                "close": float(k.get("close", 0)),
                                "volume": float(k.get("volume", 0)),
                            } for k in rows]
                            exchange_label = "Binance"
                    elif active_exchange == "hyperliquid":
                        # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止直连
                        # HL K线，改为读数据中心 DB（query_klines 已受 DC_ONLY 保护）。
                        from backend.services.market_data import _dc_only_enabled
                        if _dc_only_enabled():
                            from backend.services.kline_data_service import kline_service as _hks
                            rows = _hks.query_klines(
                                primary_symbol, detected_period,
                                exchange="hyperliquid", limit=200, order="asc",
                            ) or []
                            if len(rows) >= 20:
                                live_klines = [{
                                    "timestamp": int(k.get("timestamp", 0)),
                                    "open": float(k.get("open", 0)),
                                    "high": float(k.get("high", 0)),
                                    "low": float(k.get("low", 0)),
                                    "close": float(k.get("close", 0)),
                                    "volume": float(k.get("volume", 0)),
                                } for k in rows]
                                exchange_label = "Hyperliquid"
                        else:
                            from services.hyperliquid_market_data import get_kline_data_from_hyperliquid
                            exchange_label = "Hyperliquid"
                            hl_klines = get_kline_data_from_hyperliquid(
                                symbol=primary_symbol, period=detected_period, count=200, persist=False
                            )
                            if hl_klines:
                                live_klines = [{
                                    "timestamp": int(k.get("timestamp", 0)),
                                    "open": float(k.get("open", 0)),
                                    "high": float(k.get("high", 0)),
                                    "low": float(k.get("low", 0)),
                                    "close": float(k.get("close", 0)),
                                    "volume": float(k.get("volume", 0)),
                                } for k in hl_klines]
                                logger.info(f"[AI????] Hyperliquid ?? {len(live_klines)} ?K?")
                except Exception as _live_err:
                    logger.warning(f"[AI????] ???????????: {_live_err}")

            if live_klines and len(live_klines) >= 20:
                analyzer = MarketDataAnalyzer()
                pa = analyzer._analyze_price(live_klines)
                
                current_price = pa.current_price
                market_context = f"""
【{primary_symbol} {detected_period} 实时市场数据（{exchange_label} 交易所API直接获取）】
- 数据来源: {exchange_label} 交易所实时API
- K线数量: {len(live_klines)}
- 当前价格: {current_price:.2f}
- 趋势方向: {pa.trend_direction}
- 趋势强度: {pa.trend_strength:.2f} (0-1, 越高越强)
- ATR波动率: {pa.volatility_atr:.2f}
- 波动率百分位: {pa.volatility_percentile:.0f}%
- 支撑位: {', '.join([f'{s:.2f}' for s in pa.support_levels[:3]])}
- 阻力位: {', '.join([f'{r:.2f}' for r in pa.resistance_levels[:3]])}
- 最高价: {pa.price_range_high:.2f}
- 最低价: {pa.price_range_low:.2f}
"""
                data_source_label = "exchange_realtime"
                historical_analysis = type('obj', (object,), {'data_points': len(live_klines), 'price_analysis': pa})()
                
                logger.info(f"[AI策略生成] ✅ {exchange_label} 实时数据: {len(live_klines)}条, price={current_price:.2f}, trend={pa.trend_direction}")
            else:
                logger.warning(f"[AI策略生成] 交易所返回数据不足: {len(live_klines) if live_klines else 0}条")
        except Exception as e:
            logger.warning(f"[AI策略生成] 交易所实时数据获取失败: {e}")
        
        # --- 策略2: 回退到数据库历史数据 ---
        if not historical_analysis:
            try:
                from backend.database.connection import SessionLocal
                analysis_db = SessionLocal()
                try:
                    analyzer = MarketDataAnalyzer()
                    db_analysis = analyzer.analyze_period(
                        db=analysis_db,
                        symbol=primary_symbol,
                        period=detected_period,
                        lookback_days=30
                    )
                finally:
                    analysis_db.close()
                
                if db_analysis and db_analysis.data_points > 0:
                    historical_analysis = db_analysis
                    pa = db_analysis.price_analysis
                    va = db_analysis.volume_analysis
                    data_source_label = "database_historical"
                    market_context = f"""
【近30天 {primary_symbol} {detected_period} 历史数据分析结果（数据库缓存）】
- 注意: 数据可能有延迟，建议结合最新行情判断
- 数据点数: {db_analysis.data_points}
- 趋势方向: {pa.trend_direction}
- 趋势强度: {pa.trend_strength:.2f}
- ATR波动率: {pa.volatility_atr:.2f}
- 最新记录价格: {pa.current_price:.2f}
- 支撑位: {', '.join([f'{s:.2f}' for s in pa.support_levels[:3]])}
- 阻力位: {', '.join([f'{r:.2f}' for r in pa.resistance_levels[:3]])}
- 成交量趋势: {va.volume_trend}
- 市场状态: {db_analysis.regime_type}/{db_analysis.regime_direction}
"""
                    logger.info(f"[AI策略生成] 使用数据库历史数据: {db_analysis.data_points}条, price={pa.current_price:.2f}")
                else:
                    dp = db_analysis.data_points if db_analysis else 0
                    logger.warning(f"[AI策略生成] 数据库数据也不足: {dp}条")
            except Exception as e:
                logger.error(f"[AI策略生成] 数据库分析也失败: {e}", exc_info=True)
        
        if not market_context:
            market_context = f"（无法获取 {primary_symbol} 的市场数据，请确保网络连通且K线采集器运行中。AI将仅基于通用市场知识生成策略）"
        
        # ===== 步骤4：调用LLM生成策略框架 =====
        # 解析顺序：请求指定 LLM（须属本账户租户）→ 账户自有绑定；禁止公用默认
        llm_config = None
        _tenant = None
        try:
            from backend.database.models import Account as _Acc
            _acc = db.query(_Acc).filter(_Acc.id == request.account_id).first()
            if _acc and getattr(_acc, "user_id", None):
                _tenant = int(_acc.user_id)
        except Exception:
            _tenant = None
        if getattr(request, "llm_config_id_deep", None):
            llm_config = get_llm_config(
                config_id=request.llm_config_id_deep, tier="deep", tenant_id=_tenant
            )
        elif getattr(request, "llm_config_id", None):
            llm_config = get_llm_config(
                config_id=request.llm_config_id, tier="deep", tenant_id=_tenant
            )
        if not llm_config:
            llm_config = get_llm_config_for_account(request.account_id, tier="deep")

        llm_result = None
        generation_source = "template"
        generation_detail = ""
        llm_failure_reason = ""
        
        if llm_config:
            logger.info(f"[AI策略生成] 使用LLM: {llm_config.provider}/{llm_config.model}")
            
            system_prompt = """你是一位专业的加密货币量化策略设计师。用户会提供策略需求描述和历史市场数据分析结果，你需要设计一个完整的交易策略框架。

你必须严格按照以下JSON格式返回（不要包含任何其他文字，只返回JSON）：

```json
{
  "strategy_name": "策略名称（简短精准，包含币种和风格）",
  "strategy_description": "策略描述（2-4句话，说明策略的核心理念、适用场景和预期表现）",
  "strategy_logic": "详细策略逻辑（包括：核心原理、使用的技术指标、多空判断标准、仓位管理逻辑。如果有历史数据，要结合实际市场状态给出具体的参数建议）",
  "entry_conditions": [
    "入场条件1（具体明确，包含指标名称、参数、阈值）",
    "入场条件2",
    "入场条件3（至少3个条件）"
  ],
  "exit_conditions": [
    "出场条件1（止损/止盈/移动止损/信号反转等）",
    "出场条件2",
    "出场条件3（至少3个条件）"
  ]
}
```

注意事项：
1. 入场和出场条件要具体、可量化，包含明确的指标参数和阈值
2. 如果提供了历史数据分析，必须结合实际市场状态（趋势、波动性、支撑阻力位）调整策略参数
3. 策略逻辑要完整详细，像写给量化开发者的需求文档
4. 每个条件都是独立的、明确可执行的
5. 用中文回复"""

            symbols_str = ", ".join(detected_symbols)
            user_prompt = f"""【用户策略需求】
{request.user_requirement}

【交易风格】{request.trading_style}
【交易对列表】{symbols_str}
【主要标的】{primary_symbol}
【时间周期】{detected_period}

{market_context}

请根据以上信息设计一个完整的交易策略框架。如果有多个交易对，策略逻辑应说明如何处理多币种交易（例如分别独立执行、或根据相关性联动）。严格按JSON格式返回。"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            
            try:
                response = await call_llm_api(
                    config=llm_config,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=3000
                )
                
                if response and 'choices' in response and len(response['choices']) > 0:
                    content = response['choices'][0].get('message', {}).get('content', '')
                    logger.info(f"[AI策略生成] LLM原始响应长度: {len(content)}")
                    
                    json_str = content
                    json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', content, re.DOTALL)
                    if json_match:
                        json_str = json_match.group(1)
                    else:
                        json_match2 = re.search(r'\{[\s\S]*\}', content)
                        if json_match2:
                            json_str = json_match2.group(0)
                    
                    try:
                        llm_result = json.loads(json_str.strip())
                        generation_source = "ai"
                        generation_detail = f"{llm_config.provider}/{llm_config.model}"
                        logger.info(f"[AI策略生成] ✅ LLM JSON解析成功: name={llm_result.get('strategy_name', 'N/A')}")
                    except json.JSONDecodeError as je:
                        llm_failure_reason = f"AI返回格式解析失败: {str(je)[:80]}"
                        logger.warning(f"[AI策略生成] LLM返回的JSON解析失败: {je}, 内容前200字: {json_str[:200]}")
                else:
                    llm_failure_reason = f"AI响应为空或格式异常"
                    logger.warning(f"[AI策略生成] LLM响应格式异常: {str(response)[:200]}")
                    
            except Exception as e:
                llm_failure_reason = f"AI调用异常: {str(e)[:100]}"
                logger.error(f"[AI策略生成] LLM调用失败: {e}", exc_info=True)
        else:
            llm_failure_reason = "未配置LLM（请在设置中添加AI模型配置）"
            logger.warning("[AI策略生成] 未找到可用的LLM配置，将使用规则模板生成")
        
        # ===== 步骤5：构建返回结果（LLM结果 > 规则模板回退）=====
        market_data_used = (historical_analysis is not None and historical_analysis.data_points > 0)
        
        if llm_result:
            strategy_name = llm_result.get('strategy_name', f"{primary_symbol} {request.trading_style} AI策略")
            strategy_desc = llm_result.get('strategy_description', '')
            strategy_logic = llm_result.get('strategy_logic', '')
            entry_conditions = llm_result.get('entry_conditions', [])
            exit_conditions = llm_result.get('exit_conditions', [])
            
            if market_context and market_context.strip():
                strategy_logic = strategy_logic + "\n\n" + market_context.strip()
            
            if not entry_conditions:
                entry_conditions = ["请手动补充入场条件"]
            if not exit_conditions:
                exit_conditions = ["请手动补充出场条件"]
            
            confidence_note = f"由 {generation_detail} 生成"
            if market_data_used:
                if data_source_label == "exchange_realtime":
                    confidence_note += "，已结合交易所实时K线数据"
                else:
                    confidence_note += "，已结合数据库历史K线数据（可能有延迟）"
            else:
                confidence_note += "，未使用市场数据（仅基于AI知识，建议检查网络和K线采集器）"
                
            logger.info(f"[AI策略生成] ✅ 使用LLM结果: {strategy_name}, 入场{len(entry_conditions)}条, 出场{len(exit_conditions)}条")
        else:
            logger.info(f"[AI策略生成] ⚠️ 回退到规则模板生成, 原因: {llm_failure_reason}")
            strategy_name, strategy_desc, strategy_logic, entry_conditions, exit_conditions = (
                _fallback_generate_framework(
                    request.user_requirement, 
                    request.trading_style, 
                    primary_symbol, 
                    detected_period,
                    historical_analysis
                )
            )
            generation_source = "template"
            generation_detail = llm_failure_reason
            confidence_note = f"⚠️ 规则模板生成（原因: {llm_failure_reason}）。建议检查AI配置后点击「重新AI生成」获取更优策略。"
        
        return StrategyFrameworkResponse(
            strategy_name=strategy_name,
            strategy_description=strategy_desc,
            strategy_logic=strategy_logic.strip(),
            entry_conditions=entry_conditions,
            exit_conditions=exit_conditions,
            recommended_symbols=detected_symbols,
            recommended_timeframe=detected_period,
            generation_source=generation_source,
            generation_detail=generation_detail,
            market_data_used=market_data_used,
            confidence_note=confidence_note,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AI策略生成] 生成策略框架失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"生成策略框架失败: {str(e)}")


def _fallback_generate_framework(user_requirement: str, trading_style: str, symbol: str, period: str, historical_analysis=None):
    """
    规则模板回退：当LLM不可用时，使用预设模板生成策略框架。
    返回 (strategy_name, strategy_desc, strategy_logic, entry_conditions, exit_conditions)
    """
    strategy_name = f"{symbol} {trading_style.upper()} AI策略"
    strategy_desc = f"基于 {trading_style} 风格的自动化交易策略（模板生成，建议配置LLM后重新生成）"
    
    if historical_analysis:
        trend = historical_analysis.price_analysis.trend_direction
        volatility = historical_analysis.price_analysis.volatility_atr
        strategy_desc += f"\n\n市场环境: 趋势={trend}, 波动性={volatility:.2f}"
    
    strategy_logic = f"交易标的: {symbol} | 时间周期: {period} | 交易风格: {trading_style}\n用户需求: {user_requirement[:200]}"
    
    STYLE_TEMPLATES = {
        'trend': (
            ["EMA20 上穿 EMA50（多头趋势）", "MACD 金叉确认", "成交量放大 > 20期均值 * 1.5"],
            ["EMA20 下穿 EMA50（趋势反转）", "MACD 死叉", "达到止盈/止损目标"]
        ),
        'momentum': (
            ["RSI(14) 在 55-80（动量向上）", "价格突破 20 周期最高价", "成交量 > 20期均量 * 1.8", "OBV 趋势向上"],
            ["RSI 进入超买区(>80)逐步减仓", "动量衰减（成交量连续下降）", "追踪止损 1.5%"]
        ),
        'mean_reversion': (
            ["价格触及布林带下轨（超卖）", "RSI < 30（超卖区间）", "成交量萎竭确认"],
            ["价格回到布林带中轨", "RSI 回到中性区间 (40-60)", "达到目标收益"]
        ),
        'range': (
            ["价格触及布林带下轨（超卖）", "RSI < 30（超卖区间）", "成交量萎竭确认"],
            ["价格回到布林带中轨", "RSI 回到中性区间 (40-60)", "达到目标收益"]
        ),
        'breakout': (
            ["突破关键阻力位 + 成交量放大", "ATR 扩大（波动性增加）", "价格收盘在突破位上方"],
            ["跌破突破位（假突破）", "成交量萎缩信号", "达到止盈目标"]
        ),
        'scalping': (
            ["EMA5 上穿 EMA13（超短期动量）", "VWAP 支撑/压力确认", "1分钟成交量 > 均量 1.5倍"],
            ["止盈 0.5%", "止损 0.3%", "持仓超过 5 根K线强制离场"]
        ),
        'swing': (
            ["日线 EMA50 > EMA200（多头市确认）", "4H 回调至 EMA20 获得支撑", "出现看涨K线形态"],
            ["止盈 10-15%", "追踪止损 3%", "日线趋势反转时强制平仓"]
        ),
        'dca': (
            ["每日固定时间基础定投", "价格下跌 > 5% 触发智能加仓", "恐慌贪婪指数 < 30 时加倍买入"],
            ["总持仓盈利 > 30% 卖出部分", "RSI(日线) > 85 清仓", "总投入达账户 80% 上限停止定投"]
        ),
        'martingale': (
            ["EMA20 > EMA50（顺趋势开第一单）", "RSI(14) 在 40-60", "亏损 1.5% 触发加倍加仓（最多 3 次）"],
            ["平均成本 + 2%", "4单全开后再跌 3% 清仓", "趋势反转时全部平仓"]
        ),
        'turtle': (
            ["价格突破 20周期最高价", "前一次突破未盈利", "成交量确认 > 均量 * 1.3"],
            ["价格跌破 10周期最低价", "止损: 2倍 ATR(20)", "总风险 > 10% 停止加仓"]
        ),
        'funding_rate': (
            ["资金费率 > +0.1%", "预测资金费率同样偏高", "结算前 30 分钟开仓"],
            ["结算完成后立即平仓", "价格波动超过 1%", "费率回归正常 |rate| < 0.03%"]
        ),
        'arbitrage': (
            ["跨所价差 > 0.5%", "价差持续超过 3 秒", "低价所买入 + 高价所卖出"],
            ["价差收窄至 < 0.1%", "超时 5 分钟未收窄则平仓", "任一所价格反向超 0.3%"]
        ),
        'grid': (
            ["布林带带宽 < 3%（确认横盘）", "ADX(14) < 25", "价格每下跌一格买入，每上涨一格卖出"],
            ["浮亏达到总资金 10% 全部平仓", "突破区间上下限 2% 重置网格", "ADX > 30 暂停网格"]
        ),
    }
    
    entry_conditions, exit_conditions = STYLE_TEMPLATES.get(
        trading_style,
        (["多个技术指标共振", "成交量确认", "风险控制允许"], ["信号反转", "达到止盈/止损", "市场环境变化"])
    )
    
    return strategy_name, strategy_desc, strategy_logic, list(entry_conditions), list(exit_conditions)


@router.post("/generate-signals", response_model=GenerateSignalsResponse)
async def generate_signals(
    request: GenerateSignalsRequest,
    db: Session = Depends(get_db),
):
    """
    第2步：AI生成信号定义（真实LLM + 历史数据优化）
    
    完整流程：
    1. 从策略逻辑中提取交易标的和周期信息
    2. 查询历史数据分析波动性和趋势
    3. 将策略逻辑 + 入出场条件 + 历史分析 发送给 LLM
    4. LLM 返回结构化的信号定义（JSON格式）
    5. 如果 LLM 不可用，回退到基于规则的模板生成
    """
    try:
        logger.info(f"[AI信号生成] 收到策略逻辑: {request.strategy_logic[:100]}...")
        
        import re
        
        # ===== 步骤1：从策略逻辑中提取信息 =====
        symbol_match = re.search(r'交易标的[：:]\s*(\w+)', request.strategy_logic)
        symbol = symbol_match.group(1) if symbol_match else 'BTC'
        
        period_match = re.search(r'时间周期[：:]\s*(\w+)', request.strategy_logic)
        period = period_match.group(1) if period_match else '15m'
        
        style_match = re.search(r'交易风格[：:]\s*(\w+)', request.strategy_logic)
        trading_style = style_match.group(1) if style_match else 'trend'
        
        logger.info(f"[AI信号生成] 提取: symbol={symbol}, period={period}, style={trading_style}")
        
        # ===== 步骤2：分析历史数据 =====
        historical_context = ""
        try:
            analyzer = MarketDataAnalyzer()
            historical_analysis = await asyncio.to_thread(
                analyzer.analyze_period,
                db=db,
                symbol=symbol,
                period=period,
                lookback_days=30
            )
            if historical_analysis:
                pa = historical_analysis.price_analysis
                historical_context = f"""
【近30天历史数据分析】
- 趋势方向: {pa.trend_direction}, 强度: {pa.trend_strength:.2f}
- ATR波动率: {pa.volatility_atr:.2f}
- 成交量趋势: {historical_analysis.volume_analysis.volume_trend}

【参数优化建议】
- 高波动(ATR>{pa.volatility_atr:.0f})时建议用更短的指标周期
- 趋势强度{pa.trend_strength:.2f}: {'强趋势，降低信号触发阈值' if pa.trend_strength > 0.7 else '弱趋势，提高阈值避免假信号' if pa.trend_strength < 0.3 else '正常趋势，使用标准参数'}
"""
        except Exception as e:
            logger.warning(f"[AI信号生成] 历史分析失败: {e}")
        
        # ===== 步骤3：调用LLM生成信号 =====
        llm_config = get_llm_config()
        llm_result = None
        
        if llm_config:
            logger.info(f"[AI信号生成] 使用LLM: {llm_config.provider}/{llm_config.model}")
            
            system_prompt = """你是一位专业的量化交易信号设计师。根据用户提供的策略逻辑和入出场条件，设计具体可执行的技术指标信号。

你必须严格按照以下JSON格式返回（不要包含任何其他文字，只返回JSON）：

```json
{
  "signals": [
    {
      "name": "信号名称（简短，如 EMA交叉信号、RSI超卖信号）",
      "description": "信号描述（1-2句话说明信号用途和触发场景）",
      "signal_type": "entry|exit|filter",
      "calculation_logic": "详细的计算逻辑（包含具体指标名称、参数值、比较运算、触发条件）",
      "parameters": {
        "param_name": "param_value（具体数值，如 period: 14, threshold: 30）"
      }
    }
  ],
  "signal_pool_logic": "AND|OR|WEIGHTED"
}
```

规则：
1. signal_type 必须是 entry（入场信号）、exit（出场信号）、filter（过滤信号）之一
2. 每个信号必须有明确的技术指标名称和参数值
3. parameters 中的值必须是具体数值（数字或字符串），不能是描述性文字
4. 生成 3-6 个信号，覆盖入场、出场和过滤场景
5. calculation_logic 要像伪代码一样具体明确
6. 如果有历史数据分析，要据此优化参数值
7. signal_pool_logic: 趋势策略用 AND，均值回归用 WEIGHTED，其他根据策略特性选择
8. 用中文回复"""

            entry_str = "\n".join([f"  - {c}" for c in request.entry_conditions])
            exit_str = "\n".join([f"  - {c}" for c in request.exit_conditions])
            
            user_prompt = f"""【策略逻辑】
{request.strategy_logic}

【入场条件】
{entry_str}

【出场条件】
{exit_str}

{historical_context}

请根据以上策略信息，生成具体可执行的技术指标信号定义。严格按JSON格式返回。"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            
            try:
                response = await call_llm_api(
                    config=llm_config,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=3000
                )
                
                if response and 'choices' in response and len(response['choices']) > 0:
                    content = response['choices'][0].get('message', {}).get('content', '')
                    logger.info(f"[AI信号生成] LLM原始响应长度: {len(content)}")
                    
                    # 提取JSON
                    json_str = content
                    json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', content, re.DOTALL)
                    if json_match:
                        json_str = json_match.group(1)
                    else:
                        json_match2 = re.search(r'\{[\s\S]*\}', content)
                        if json_match2:
                            json_str = json_match2.group(0)
                    
                    try:
                        llm_result = json.loads(json_str.strip())
                        logger.info(f"[AI信号生成] LLM JSON解析成功: {len(llm_result.get('signals', []))}个信号")
                    except json.JSONDecodeError as je:
                        logger.warning(f"[AI信号生成] LLM返回的JSON解析失败: {je}")
                else:
                    logger.warning(f"[AI信号生成] LLM响应格式异常")
                    
            except Exception as e:
                logger.error(f"[AI信号生成] LLM调用失败: {e}", exc_info=True)
        else:
            logger.warning("[AI信号生成] 未找到可用的LLM配置，将使用规则模板生成")
        
        # ===== 步骤4：构建返回结果 =====
        if llm_result and 'signals' in llm_result:
            # 使用LLM生成的真实结果
            signals = []
            for sig_data in llm_result['signals']:
                # 确保 parameters 中的值都是合法类型
                params = sig_data.get('parameters', {})
                clean_params = {}
                for k, v in params.items():
                    if isinstance(v, (int, float, str, bool)):
                        clean_params[k] = v
                    else:
                        clean_params[k] = str(v)
                
                signals.append(SignalDefinitionResponse(
                    name=sig_data.get('name', '未命名信号'),
                    description=sig_data.get('description', ''),
                    signal_type=sig_data.get('signal_type', 'entry'),
                    calculation_logic=sig_data.get('calculation_logic', ''),
                    parameters=clean_params
                ))
            
            signal_pool_logic = llm_result.get('signal_pool_logic', 'AND')
            
            if not signals:
                logger.warning("[AI信号生成] LLM返回空信号列表，回退到模板")
            else:
                logger.info(f"[AI信号生成] ✅ 使用LLM结果: {len(signals)}个信号, 组合逻辑: {signal_pool_logic}")
                return GenerateSignalsResponse(signals=signals, signal_pool_logic=signal_pool_logic)
        
        # 回退：使用基于规则的模板生成
        logger.info("[AI信号生成] ⚠️ 回退到规则模板生成")
        signals, pool_logic = _fallback_generate_signals(trading_style)
        
        return GenerateSignalsResponse(signals=signals, signal_pool_logic=pool_logic)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AI信号生成] 生成信号失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"生成信号失败: {str(e)}")


def _fallback_generate_signals(trading_style: str):
    """
    规则模板回退：当LLM不可用时，使用预设模板生成信号。
    返回 (signals_list, pool_logic)
    """
    SIGNAL_TEMPLATES = {
        'trend': ([
            SignalDefinitionResponse(name="EMA交叉信号", description="短期EMA上穿长期EMA确认趋势（模板生成）", signal_type="entry",
                                     calculation_logic="EMA(20) > EMA(50) 且前一根K线 EMA(20) < EMA(50)", parameters={"fast_period": 20, "slow_period": 50}),
            SignalDefinitionResponse(name="MACD金叉信号", description="MACD线上穿信号线确认动量", signal_type="entry",
                                     calculation_logic="MACD线上穿信号线，柱状图由负转正", parameters={"fast": 12, "slow": 26, "signal": 9}),
            SignalDefinitionResponse(name="成交量确认", description="成交量放大确认信号有效", signal_type="filter",
                                     calculation_logic="当前成交量 > 20期均量 * 1.5", parameters={"period": 20, "multiplier": 1.5}),
        ], "AND"),
        'mean_reversion': ([
            SignalDefinitionResponse(name="布林带信号", description="价格触及布林带边界", signal_type="entry",
                                     calculation_logic="价格 < 布林带下轨 做多, 价格 > 布林带上轨 做空", parameters={"period": 20, "std_dev": 2}),
            SignalDefinitionResponse(name="RSI超买超卖", description="RSI极值区间反转", signal_type="entry",
                                     calculation_logic="RSI < 30 做多, RSI > 70 做空", parameters={"period": 14, "oversold": 30, "overbought": 70}),
            SignalDefinitionResponse(name="成交量萎竭", description="成交量萎缩确认反转", signal_type="filter",
                                     calculation_logic="成交量 < 20期均值 * 0.7", parameters={"period": 20, "multiplier": 0.7}),
        ], "WEIGHTED"),
        'breakout': ([
            SignalDefinitionResponse(name="突破信号", description="价格突破关键位", signal_type="entry",
                                     calculation_logic="价格突破20期高点且成交量放大", parameters={"lookback_period": 20}),
            SignalDefinitionResponse(name="ATR波动信号", description="ATR扩大确认", signal_type="filter",
                                     calculation_logic="当前ATR > 20期ATR均值 * 1.5", parameters={"period": 14, "multiplier": 1.5}),
            SignalDefinitionResponse(name="收盘确认", description="收盘价确认突破", signal_type="filter",
                                     calculation_logic="收盘价 > 突破位", parameters={}),
        ], "AND"),
    }
    
    signals, pool_logic = SIGNAL_TEMPLATES.get(
        trading_style,
        ([
            SignalDefinitionResponse(name="EMA交叉信号", description="EMA均线交叉（模板生成）", signal_type="entry",
                                     calculation_logic="EMA(20)与EMA(50)交叉", parameters={"fast_period": 20, "slow_period": 50}),
            SignalDefinitionResponse(name="RSI信号", description="RSI超买超卖（模板生成）", signal_type="filter",
                                     calculation_logic="RSI指标判断", parameters={"period": 14}),
        ], "AND")
    )
    
    return signals, pool_logic


@router.post("/create-complete", response_model=CreateCompleteResponse)
def create_complete_strategy_system(
    request: CreateCompleteRequest,
    db: Session = Depends(get_db),
):
    """
    第3步：统一创建完整系统
    
    一次性创建：
    1. 所有信号定义（SignalDefinition）
    2. 信号池（SignalPool）
    3. AI策略（AIStrategy）
    4. 自动关联所有组件
    
    保证数据一致性和逻辑联动
    """
    try:
        import uuid
        from datetime import datetime
        
        # 验证账户存在
        account = db.query(Account).filter(Account.id == request.account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail=f"Account {request.account_id} not found")
        
        logger.info(f"Creating complete strategy system for account {request.account_id}")
        
        # 第1步：创建所有信号定义
        created_signal_ids = []
        for sig_data in request.generated_signals:
            signal_def = SignalDefinition(
                signal_name=sig_data['name'],
                description=sig_data.get('description', ''),
                trigger_condition=json.dumps({
                    'signal_type': sig_data.get('signal_type', 'entry'),
                    'calculation_logic': sig_data.get('calculation_logic', ''),
                    'parameters': sig_data.get('parameters', {})
                }),
                enabled=True,
                created_at=datetime.now()
            )
            db.add(signal_def)
            db.flush()  # 获取ID
            created_signal_ids.append(signal_def.id)
            logger.info(f"Created signal: {signal_def.signal_name} (ID: {signal_def.id})")
        
        # 第2步：创建信号池
        signal_pool = SignalPool(
            pool_name=request.signal_pool.get('name', f"{request.strategy_name}_信号池"),
            signal_ids=json.dumps(created_signal_ids),  # 存储为JSON字符串
            symbols=json.dumps(request.target_symbols or ["BTC"]),  # 使用用户选择的交易对
            logic=request.signal_pool.get('logic', 'AND'),
            weights=json.dumps(request.signal_pool.get('weights', {})) if request.signal_pool.get('weights') else None,
            weight_threshold=0.6,
            enabled=True,
            created_at=datetime.now()
        )
        db.add(signal_pool)
        db.flush()
        logger.info(f"Created signal pool: {signal_pool.pool_name} (ID: {signal_pool.id})")
        
        # 第3步：创建AI策略
        strategy_id = f"ai_strategy_{uuid.uuid4().hex[:12]}"
        
        ai_strategy = AIStrategy(
            strategy_id=strategy_id,
            name=request.strategy_name,
            description=request.strategy_description,
            account_id=request.account_id,
            
            # 关联信号池
            signal_pool_ids=[signal_pool.id],
            
            # 策略逻辑（存储在prompt_variables中）
            master_prompt_template_id=getattr(db.query(PromptTemplate).first(), "id", None),
            prompt_variables={
                "strategy_logic": request.strategy_logic,
                "entry_conditions": request.entry_conditions,
                "exit_conditions": request.exit_conditions,
                "trading_style": request.trading_style,
                "user_requirement": request.user_requirement,
            },
            
            # 交易对配置
            target_symbols=request.target_symbols or ["BTC"],
            primary_symbol=request.primary_symbol or (request.target_symbols[0] if request.target_symbols else "BTC"),
            timeframe=request.timeframe or "15m",
            
            # 触发配置
            trigger_mode="signal_driven",
            trigger_interval=None,
            
            # 风险配置
            max_position_size=request.risk_config.get('max_position_size', 0.2),
            stop_loss_pct=request.risk_config.get('stop_loss_pct', 0.05),
            take_profit_pct=request.risk_config.get('take_profit_pct', 0.10),
            max_daily_loss=request.risk_config.get('max_daily_loss', 0.10),
            
            # 执行配置
            auto_execute=request.execution_config.get('auto_execute', False),
            require_confirmation=request.execution_config.get('require_confirmation', True),
            min_confidence=request.execution_config.get('min_confidence', 0.6),
            
            # 学习配置
            learning_enabled=True,
            optimization_target="sharpe",
            training_frequency="weekly",
            
            status="draft",
            created_at=datetime.now()
        )
        
        db.add(ai_strategy)
        db.commit()
        db.refresh(ai_strategy)
        
        logger.info(f"Created AI strategy: {strategy_id} (DB ID: {ai_strategy.id})")
        logger.info(f"Complete system created: {len(created_signal_ids)} signals, 1 pool, 1 strategy")
        
        return CreateCompleteResponse(
            success=True,
            strategy_id=strategy_id,
            strategy_db_id=ai_strategy.id,
            signal_pool_id=signal_pool.id,
            created_signals_count=len(created_signal_ids),
            message=f"成功创建完整策略系统：{len(created_signal_ids)}个信号 + 1个信号池 + 1个AI策略"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create complete strategy system: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"创建失败: {str(e)}")


# ===== 自定义交易风格 CRUD =====

class CustomStyleCreate(BaseModel):
    name: str
    description: str = ''
    template: str = ''

class CustomStyleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    template: Optional[str] = None

class CustomStyleResponse(BaseModel):
    id: int
    key: str
    name: str
    description: str
    template: str


@router.get("/trading-styles/custom", response_model=list[CustomStyleResponse])
def list_custom_styles(db: Session = Depends(get_db)):
    """获取所有自定义交易风格"""
    styles = db.query(CustomTradingStyle).order_by(CustomTradingStyle.id).all()
    return [
        CustomStyleResponse(
            id=s.id, key=s.key, name=s.name,
            description=s.description or '', template=s.template or ''
        ) for s in styles
    ]


@router.post("/trading-styles/custom", response_model=CustomStyleResponse, status_code=201)
def create_custom_style(req: CustomStyleCreate, db: Session = Depends(get_db)):
    """创建自定义交易风格"""
    import re
    # 生成 key: custom_ + 英文小写/下划线
    base = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff]', '_', req.name).strip('_').lower()
    key = f"custom_{base}" if base else f"custom_{int(__import__('time').time())}"
    # 保证唯一
    existing = db.query(CustomTradingStyle).filter(CustomTradingStyle.key == key).first()
    if existing:
        key = f"{key}_{int(__import__('time').time()) % 10000}"
    style = CustomTradingStyle(key=key, name=req.name, description=req.description, template=req.template)
    db.add(style)
    db.commit()
    db.refresh(style)
    return CustomStyleResponse(
        id=style.id, key=style.key, name=style.name,
        description=style.description or '', template=style.template or ''
    )


@router.put("/trading-styles/custom/{style_id}", response_model=CustomStyleResponse)
def update_custom_style(style_id: int, req: CustomStyleUpdate, db: Session = Depends(get_db)):
    """更新自定义交易风格"""
    style = db.query(CustomTradingStyle).filter(CustomTradingStyle.id == style_id).first()
    if not style:
        raise HTTPException(status_code=404, detail="自定义风格不存在")
    if req.name is not None:
        style.name = req.name
    if req.description is not None:
        style.description = req.description
    if req.template is not None:
        style.template = req.template
    db.commit()
    db.refresh(style)
    return CustomStyleResponse(
        id=style.id, key=style.key, name=style.name,
        description=style.description or '', template=style.template or ''
    )


@router.delete("/trading-styles/custom/{style_id}", status_code=204)
def delete_custom_style(style_id: int, db: Session = Depends(get_db)):
    """删除自定义交易风格"""
    style = db.query(CustomTradingStyle).filter(CustomTradingStyle.id == style_id).first()
    if not style:
        raise HTTPException(status_code=404, detail="自定义风格不存在")
    db.delete(style)
    db.commit()


# ===== 一键启动AI自主交易 =====

class AutoLaunchRequest(BaseModel):
    """一键启动请求 - 用户只需提供最少信息，AI决定其余一切"""
    account_id: int
    target_symbols: List[str] = ["BTC"]
    risk_preference: str = "moderate"  # conservative / moderate / aggressive
    capital_pct: float = 0.3  # 分配给该策略的资金比例
    trading_mode: str = "live"  # "live" | "paper"
    timeframe_slot: Optional[str] = None  # "short" / "mid" / "long" — 多周期编排时指定

class AutoLaunchResponse(BaseModel):
    success: bool
    strategy_id: str
    strategy_name: str
    ai_decided: dict
    message: str
    signal_pool_id: Optional[int] = None
    signal_count: int = 0
    generation_source: str = "template"   # "ai" | "use_template" | "adapt_template" | "auto_template" | "data_template"
    analysis_summary: Optional[dict] = None
    audit_decision: Optional[str] = None     # use_template / adapt_template / generate_new
    audit_score: Optional[int] = None        # LLM 审核评分 0-100
    audit_reason: Optional[str] = None       # LLM 审核理由
    chosen_template_name: Optional[str] = None  # 选用的模板名称
    candidate_count: int = 0                 # 候选模板数量


RISK_PROFILES = {
    "conservative": {
        "label": "保守",
        "max_position_size": 0.10,
        "stop_loss_pct": 0.02,
        "take_profit_pct": 0.04,
        "max_daily_loss": 0.05,
        "min_confidence": 0.75,
        "auto_execute": False,
        # 杠杆配置 - 保守：适中杠杆，严格风控
        "max_leverage": 10,
        "default_leverage": 5,
        "leverage_mode": "isolated",  # 逐仓隔离风险
        # 滚仓：保守不开启滚仓
        "snowball_enabled": False,
        "snowball_max_adds": 0,
        "snowball_profit_threshold": 0,
    },
    "moderate": {
        "label": "均衡",
        "max_position_size": 0.20,
        "stop_loss_pct": 0.04,
        "take_profit_pct": 0.08,
        "max_daily_loss": 0.10,
        "min_confidence": 0.60,
        "auto_execute": True,
        # 杠杆配置 - 均衡：中高杠杆，逐仓隔离
        "max_leverage": 15,
        "default_leverage": 8,
        "leverage_mode": "isolated",
        # 滚仓：盈利5%后最多加2次
        "snowball_enabled": True,
        "snowball_max_adds": 2,
        "snowball_profit_threshold": 0.05,
    },
    "aggressive": {
        "label": "激进",
        "max_position_size": 0.35,
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.15,
        "max_daily_loss": 0.15,
        "min_confidence": 0.50,
        "auto_execute": True,
        # 杠杆配置 - 激进：高杠杆，逐仓隔离
        "max_leverage": 20,
        "default_leverage": 15,
        "leverage_mode": "isolated",
        # 滚仓：盈利3%后最多加3次，极端行情抓利润
        "snowball_enabled": True,
        "snowball_max_adds": 3,
        "snowball_profit_threshold": 0.03,
    },
}

STYLE_BY_CYCLE = {
    "bull": {"style": "ai_driven", "timeframe": "15m", "label": "AI分析"},
    "bear": {"style": "ai_driven", "timeframe": "15m", "label": "AI分析"},
    "sideways": {"style": "ai_driven", "timeframe": "15m", "label": "AI分析"},
    "transition": {"style": "ai_driven", "timeframe": "15m", "label": "AI分析"},
    "unknown": {"style": "ai_driven", "timeframe": "15m", "label": "AI分析"},
}

# 多周期槽位配置
# v3 整改: 三个 tier 使用差异化 slot（替代原 _NEUTRAL_SLOT 三档完全一致）
#   - short: 高频入场，更紧 SL/TP，较低仓位权重
#   - mid:   中等节奏，标准 SL/TP
#   - long:  低频捕捉大段，更宽 SL/TP，较高仓位权重
#   目的：消除"只改字段名仍全塞 mid"的等效性陷阱
_NEUTRAL_SLOT = {  # 兼容旧引用，默认行为与 mid 一致
    "timeframes": ["15m", "1h", "4h"],
    "capital_weight": 1.0,
    "analysis_intervals": {"short": 1800, "mid": 3600, "long": 14400},
    "position_scale": 1.0,
    "leverage_scale": 1.0,
    "sl_scale": 1.0,
    "tp_scale": 1.0,
}
TIMEFRAME_SLOT_CONFIG = {
    "short": {
        "timeframes": ["5m", "15m", "1h"],
        "capital_weight": 0.8,
        "analysis_intervals": {"short": 900, "mid": 1800, "long": 7200},
        "position_scale": 0.85,
        "leverage_scale": 1.15,
        "sl_scale": 0.7,
        "tp_scale": 0.7,
    },
    "mid": dict(_NEUTRAL_SLOT),
    "long": {
        "timeframes": ["1h", "4h", "1d"],
        "capital_weight": 1.2,
        "analysis_intervals": {"short": 3600, "mid": 7200, "long": 21600},
        "position_scale": 1.15,
        "leverage_scale": 0.85,
        "sl_scale": 1.4,
        "tp_scale": 1.6,
    },
}

# v3 整改: tier 多样性配额（总和 = 1.0）—
#   在 strategy_coordinator 选择新策略时按配额约束，避免单 tier 过载造成 100% mid-skew
TIER_DIVERSITY_QUOTA = {
    "short": 0.35,
    "mid": 0.35,
    "long": 0.30,
}


@router.post("/auto-launch", response_model=AutoLaunchResponse)
def auto_launch_strategy(
    request: AutoLaunchRequest,
    db: Session = Depends(get_db),
):
    """
    一键启动AI自主交易

    用户只需提供：账户、交易对、风险偏好
    系统自动完成：
    1. 分析当前市场环境 → 决定交易风格和周期
    2. 调用AI生成策略框架
    3. 创建策略 + 信号 + 信号池
    4. 立即激活 + 注册自主分析循环
    """
    import uuid
    from datetime import datetime

    account = db.query(Account).filter(Account.id == request.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="账户不存在")

    # 初始化模拟账户余额（不修改账户的 trading_mode）
    try:
        if request.trading_mode == "paper":
            try:
                from backend.services.paper_trading_engine import paper_engine
                paper_engine.initialize_account(db, account.id)
            except Exception as pe:
                logger.warning(f"[AutoLaunch] Paper 账户初始化: {pe}")
    except Exception as mode_err:
        logger.warning(f"[AutoLaunch] 设置 trading_mode 失败（列可能不存在）: {mode_err}")
        db.rollback()
        # 尝试 ALTER TABLE 添加列
        try:
            from sqlalchemy import text
            db.execute(text("ALTER TABLE accounts ADD COLUMN trading_mode VARCHAR(10) DEFAULT 'live'"))
            db.commit()
        except Exception:
            pass

    risk = RISK_PROFILES.get(request.risk_preference, RISK_PROFILES["moderate"]).copy()
    primary_symbol = request.target_symbols[0] if request.target_symbols else "BTC"

    # ╔══════════════════════════════════════════════════════════════╗
    # ║  Step 1: 深度市场分析 — 拉取真实 K 线 + 计算技术指标        ║
    # ╚══════════════════════════════════════════════════════════════╝
    market_cycle = "unknown"
    trend_direction = "neutral"
    volatility = "normal"
    market_env_raw = None
    analysis_summary = {
        "data_source": "none",
        "indicators": {},
        "kline_counts": {},
        "current_price": None,
    }

    # 1a) 调用 StrategyCoordinator 做多周期分析
    try:
        from backend.services.strategy_coordinator import StrategyCoordinator
        coordinator = StrategyCoordinator(db)
        env = coordinator.analyze_market_environment(primary_symbol)
        if env:
            market_env_raw = env
            market_cycle = getattr(env, "market_cycle", "unknown")
            trend_direction = getattr(env, "trend_direction", "neutral")
            volatility = getattr(env, "volatility_regime", "normal")
            analysis_summary["data_source"] = "strategy_coordinator"
            analysis_summary["current_price"] = getattr(env, "current_price", None)
            analysis_summary["indicators"]["atr"] = getattr(env, "volatility_value", None)
            analysis_summary["indicators"]["trend_strength"] = getattr(env, "trend_strength", None)
            analysis_summary["indicators"]["liquidity"] = getattr(env, "liquidity_score", None)
            analysis_summary["indicators"]["risk_budget"] = getattr(env, "risk_budget_pct", None)
            logger.info(f"[AutoLaunch] StrategyCoordinator 分析完成: cycle={market_cycle}, trend={trend_direction}, vol={volatility}")
    except Exception as e:
        logger.warning(f"[AutoLaunch] StrategyCoordinator 失败: {e}")

    # 1b) 拉取多周期 K 线并计算指标 — 无论 coordinator 是否成功都做
    kline_analysis = {}
    try:
        from backend.services.strategy_coordinator import StrategyCoordinator as _SC
        import time as _time
        _sc_inst = _SC(db)

        for period, lookback_days, label in [("15m", 7, "短线"), ("1h", 30, "中线"), ("1d", 180, "长线")]:
            start_ts = int(_time.time()) - lookback_days * 86400
            end_ts = int(_time.time())
            klines = _sc_inst._query_klines(primary_symbol, period, start_ts, end_ts, "hyperliquid")
            if not klines or len(klines) < 20:
                klines = _sc_inst._query_klines(primary_symbol, period, start_ts, end_ts, "binance")
            if klines and len(klines) >= 20:
                closes = [float(k.get("close", k.get("c", 0))) for k in klines]
                highs = [float(k.get("high", k.get("h", 0))) for k in klines]
                lows = [float(k.get("low", k.get("l", 0))) for k in klines]
                volumes = [float(k.get("volume", k.get("v", 0))) for k in klines]

                ema9 = _sc_inst._calc_ema(closes, 9)
                ema21 = _sc_inst._calc_ema(closes, 21)
                ema50 = _sc_inst._calc_ema(closes, 50)

                # RSI 14
                gains, losses = [], []
                for i in range(1, len(closes)):
                    delta = closes[i] - closes[i - 1]
                    gains.append(max(delta, 0))
                    losses.append(max(-delta, 0))
                avg_gain = sum(gains[-14:]) / 14 if len(gains) >= 14 else (sum(gains) / max(len(gains), 1))
                avg_loss = sum(losses[-14:]) / 14 if len(losses) >= 14 else (sum(losses) / max(len(losses), 1))
                rsi14 = 100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss > 0 else 100

                # ATR 14
                atr_vals = []
                for i in range(1, len(closes)):
                    tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
                    atr_vals.append(tr)
                atr14 = sum(atr_vals[-14:]) / min(len(atr_vals), 14) if atr_vals else 0

                # MACD
                ema12 = _sc_inst._calc_ema(closes, 12)
                ema26 = _sc_inst._calc_ema(closes, 26)
                macd_line = ema12 - ema26

                cur = closes[-1]
                chg_pct = ((cur - closes[0]) / closes[0] * 100) if closes[0] > 0 else 0
                vol_recent = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0
                vol_older = sum(volumes[-10:-5]) / 5 if len(volumes) >= 10 else vol_recent
                vol_trend = "increasing" if vol_recent > vol_older * 1.1 else ("decreasing" if vol_recent < vol_older * 0.9 else "stable")

                trend = "bullish" if ema9 > ema21 > ema50 else ("bearish" if ema9 < ema21 < ema50 else "neutral")

                kline_analysis[period] = {
                    "label": label,
                    "count": len(klines),
                    "price": round(cur, 2),
                    "change_pct": round(chg_pct, 2),
                    "ema9": round(ema9, 2),
                    "ema21": round(ema21, 2),
                    "ema50": round(ema50, 2),
                    "rsi14": round(rsi14, 1),
                    "atr14": round(atr14, 2),
                    "atr_pct": round(atr14 / cur * 100, 3) if cur > 0 else 0,
                    "macd": round(macd_line, 2),
                    "vol_trend": vol_trend,
                    "trend": trend,
                }
                analysis_summary["kline_counts"][period] = len(klines)

        analysis_summary["indicators"]["kline_analysis"] = kline_analysis
        if kline_analysis:
            analysis_summary["data_source"] = "real_kline"
            logger.info(f"[AutoLaunch] K线分析完成: {list(kline_analysis.keys())}")

            # 用真实数据修正 coordinator 可能的默认值
            if market_cycle == "unknown" and kline_analysis:
                d15 = kline_analysis.get("15m", {})
                d1h = kline_analysis.get("1h", {})
                d1d = kline_analysis.get("1d", {})
                bullish_count = sum(1 for d in [d15, d1h, d1d] if d.get("trend") == "bullish")
                bearish_count = sum(1 for d in [d15, d1h, d1d] if d.get("trend") == "bearish")
                if bullish_count >= 2:
                    market_cycle = "bull"
                elif bearish_count >= 2:
                    market_cycle = "bear"
                else:
                    market_cycle = "sideways"

            if trend_direction == "neutral" and "1h" in kline_analysis:
                trend_direction = kline_analysis["1h"]["trend"]

            atr_pct_1h = kline_analysis.get("1h", {}).get("atr_pct", 0)
            if atr_pct_1h > 3.0:
                volatility = "extreme"
            elif atr_pct_1h > 2.0:
                volatility = "high"
            elif atr_pct_1h > 1.0:
                volatility = "normal"
            else:
                volatility = "low"
    except Exception as e:
        logger.warning(f"[AutoLaunch] K线分析失败: {e}", exc_info=True)

    # 1c) 获取实时价格
    if not analysis_summary["current_price"]:
        try:
            from backend.services.strategy_coordinator import StrategyCoordinator as _SCC
            from backend.services.exchange_config import get_active_exchange
            _active_exchange = get_active_exchange()
            analysis_summary["current_price"] = _SCC._get_realtime_price_robust(primary_symbol, _active_exchange)
        except Exception as _pe:
            logger.warning(f"[AutoLaunch] 实时价格获取失败: {_pe}")

    # ══════════════════════════════════════════════════════════════
    #  简化：AI 全权决策，无需策略风格选择或 LLM 审核
    # ══════════════════════════════════════════════════════════════
    slot = request.timeframe_slot
    # 【修复日志】确保 slot 传递可追踪，如果为 None 则从市场环境推断
    if not slot:
        cycle = market_cycle or "unknown"
        # v3 整改: 反向分布补齐
        #   - 过去 volatile/breakout/unknown 全塞进 "mid"，加剧 100% mid-skew 问题
        #   - 现在：高波动/突破更应走短线捕捉拐点 → short；未知优先保守走长线 → long
        #   - 稳态趋势仍走 long，震荡走 short，mid 只保留过渡态（none/sleeping）
        _CYCLE_SLOT_MAP = {
            "bull": "long", "bear": "long",
            "sideways": "short", "ranging": "short",
            "volatile": "short", "breakout": "short",
            "unknown": "long",
            "transition": "mid", "none": "mid", "sleeping": "mid",
        }
        slot = _CYCLE_SLOT_MAP.get(cycle, "long")
        logger.info(f"[AutoLaunch] timeframe_slot 未指定，从市场周期 {cycle} 反向分布推断为 {slot}")
    slot_config = TIMEFRAME_SLOT_CONFIG.get(slot) if slot else None

    chosen_style = "ai_driven"
    chosen_timeframe = slot_config["timeframes"][0] if slot_config else "15m"
    slot_label = {"short": "短线", "mid": "中线", "long": "长线"}.get(slot, "") if slot else ""
    style_info = {"style": "ai_driven", "timeframe": chosen_timeframe, "label": f"{slot_label}AI分析"}

    intelligence_summary = {}
    if market_env_raw:
        intelligence_summary = {
            "sentiment_index": getattr(market_env_raw, "sentiment_index", 50),
            "sentiment_zone": getattr(market_env_raw, "sentiment_zone", "neutral"),
            "news_impact": getattr(market_env_raw, "news_impact", 0),
            "news_top_event": getattr(market_env_raw, "news_top_event", ""),
            "whale_direction": getattr(market_env_raw, "whale_direction", 0),
            "derivatives_signal": getattr(market_env_raw, "derivatives_signal", "neutral"),
            "funding_rate": getattr(market_env_raw, "funding_rate", 0),
        }

    ai_decided = {
        "market_cycle": market_cycle,
        "trend_direction": trend_direction,
        "volatility": volatility,
        "trading_style": "ai_driven",
        "style_label": style_info["label"],
        "timeframe": chosen_timeframe,
        "risk_profile": risk["label"],
        "intelligence": intelligence_summary,
    }

    adjusted_leverage = risk["default_leverage"]
    adjusted_max = risk["max_leverage"]
    if volatility == "extreme":
        adjusted_leverage = max(5, int(risk["default_leverage"] * 0.6))
        adjusted_max = max(5, int(risk["max_leverage"] * 0.6))
        logger.info(f"[AutoLaunch] 极端波动，杠杆降档: {risk['default_leverage']}x → {adjusted_leverage}x")
    elif volatility == "high":
        adjusted_leverage = max(5, int(risk["default_leverage"] * 0.8))
        adjusted_max = max(5, int(risk["max_leverage"] * 0.8))

    if slot_config:
        adjusted_leverage = max(5, int(adjusted_leverage * slot_config["leverage_scale"]))
        adjusted_max = max(5, int(adjusted_max * slot_config["leverage_scale"]))
        risk["stop_loss_pct"] = round(risk["stop_loss_pct"] * slot_config["sl_scale"], 4)
        risk["take_profit_pct"] = round(risk["take_profit_pct"] * slot_config["tp_scale"], 4)
        risk["max_position_size"] = round(risk["max_position_size"] * slot_config["position_scale"], 4)

    ai_decided["default_leverage"] = adjusted_leverage
    ai_decided["max_leverage"] = adjusted_max
    ai_decided["snowball_enabled"] = risk["snowball_enabled"]
    if slot:
        ai_decided["timeframe_slot"] = slot

    logger.info(f"[AutoLaunch] AI驱动策略创建: {primary_symbol} [{slot_label}] lev={adjusted_leverage}x vol={volatility}")

    # 策略名称 — 简洁明了，不再使用"趋势跟踪/均值回归"等伪标签
    slot_tag = {"short": "短线", "mid": "中线", "long": "长线"}.get(slot, "") if slot else ""
    strategy_name = f"AI_{slot_tag}_{primary_symbol}_{datetime.now().strftime('%m%d%H%M')}"
    strategy_logic = (
        f"AI全权驱动 | {slot_tag or 'AI'} | {primary_symbol}\n"
        f"市场: {market_cycle} | 趋势: {trend_direction} | 波动率: {volatility}\n"
        f"决策由 MasterController(LLM) 根据5路分析师实时报告综合判断\n"
        f"止盈止损由 AI 动态管理，无需预设固定策略模板"
    )
    entry_conditions = ["AI 分析师综合评估后自主决策"]
    exit_conditions = ["AI 动态管理止盈止损，保本止损自动推进"]
    generation_source = "ai_driven"
    audit_result = None

    analysis_summary["generation_note"] = "AI全权驱动模式，无需策略模板审核"

    # --- 创建通用信号定义 + 信号池 ---
    created_signal_ids = []
    signal_pool_id = None

    UNIVERSAL_SIGNALS = [
        {"name": "EMA多周期", "desc": "EMA9/21/50排列判断趋势方向", "type": "entry",
         "logic": "EMA9/21/50 多头或空头排列", "params": {"ema_fast": 9, "ema_mid": 21, "ema_slow": 50}},
        {"name": "RSI超买超卖", "desc": "RSI极端区域+趋势方向综合判断", "type": "entry",
         "logic": "RSI>70超买 / RSI<30超卖，结合趋势方向", "params": {"period": 14}},
        {"name": "成交量异动", "desc": "成交量放大确认价格动量", "type": "confirmation",
         "logic": "成交量 > MA(volume, 20) * 1.5", "params": {"volume_ma": 20, "threshold": 1.5}},
    ]

    try:
        for sig in UNIVERSAL_SIGNALS:
            signal_def = SignalDefinition(
                signal_name=f"[AI] {sig['name']}",
                description=sig["desc"],
                trigger_condition=json.dumps({
                    "signal_type": sig["type"],
                    "calculation_logic": sig["logic"],
                    "parameters": sig["params"],
                    "style": "ai_driven",
                    "symbols": request.target_symbols,
                }, ensure_ascii=False),
                enabled=True,
                created_at=datetime.now(timezone.utc),
            )
            db.add(signal_def)
            db.flush()
            created_signal_ids.append(signal_def.id)

        signal_pool = SignalPool(
            pool_name=f"{strategy_name}_信号池",
            signal_ids=json.dumps(created_signal_ids),
            symbols=json.dumps(request.target_symbols),
            logic="AND",
            weight_threshold=0.6,
            enabled=True,
            created_at=datetime.now(timezone.utc),
        )
        db.add(signal_pool)
        db.flush()
        signal_pool_id = signal_pool.id
        logger.info(f"[AutoLaunch] 通用信号池创建: {signal_pool.pool_name} ({len(created_signal_ids)}个信号)")
    except Exception as sig_err:
        logger.warning(f"[AutoLaunch] 信号池创建失败（非致命）: {sig_err}")
        signal_pool_id = None
        created_signal_ids = []

    # --- Step 3: 创建策略 ---
    # 最终防线：创建前检查该 account+symbol+tier 是否已有 active/paused 策略
    _existing_dup = db.query(AIStrategy).filter(
        AIStrategy.account_id == request.account_id,
        AIStrategy.primary_symbol == primary_symbol,
        AIStrategy.timeframe_tier == (slot or "mid"),
        AIStrategy.status.in_(["active", "paused"]),
    ).first()
    if _existing_dup:
        logger.warning(
            f"[AutoLaunch] 拦截重复创建: {primary_symbol}/{slot} 已有策略 "
            f"{_existing_dup.strategy_id[:8]} (status={_existing_dup.status})"
        )
        return AutoLaunchResponse(
            success=True,
            strategy_id=_existing_dup.strategy_id,
            strategy_name=_existing_dup.name or "",
            ai_decided={},
            message=f"策略已存在，无需重复创建",
            signal_pool_id=None, signal_count=0,
            generation_source="existing",
            analysis_summary={},
            audit_decision=None, audit_score=None, audit_reason=None,
            chosen_template_name=None, candidate_count=0,
        )

    strategy_id = f"auto_{uuid.uuid4().hex[:10]}"

    ai_strategy = AIStrategy(
        strategy_id=strategy_id,
        name=strategy_name,
        description=f"AI自主交易 | {style_info['label']} | {risk['label']}风险 | 市场:{market_cycle}",
        account_id=request.account_id,
        signal_pool_ids=[signal_pool_id] if signal_pool_id else [],
        master_prompt_template_id=getattr(db.query(PromptTemplate).first(), "id", None),
        prompt_variables={
            "strategy_logic": strategy_logic,
            "entry_conditions": entry_conditions,
            "exit_conditions": exit_conditions,
            "trading_style": chosen_style,
            "auto_launch": True,
            "market_context": ai_decided,
        },
        target_symbols=request.target_symbols,
        primary_symbol=primary_symbol,
        timeframe=chosen_timeframe,
        trigger_mode="hybrid",
        max_position_size=risk["max_position_size"],
        stop_loss_pct=risk["stop_loss_pct"],
        take_profit_pct=risk["take_profit_pct"],
        max_daily_loss=risk["max_daily_loss"],
        auto_execute=True,
        require_confirmation=False,
        min_confidence=risk["min_confidence"],
        # 杠杆配置
        max_leverage=adjusted_max,
        default_leverage=adjusted_leverage,
        leverage_mode=risk["leverage_mode"],
        # 滚仓配置
        snowball_enabled=risk["snowball_enabled"],
        snowball_max_adds=risk["snowball_max_adds"],
        snowball_profit_threshold=risk["snowball_profit_threshold"],
        learning_enabled=True,
        optimization_target="sharpe",
        training_frequency="weekly",
        auto_mode="full_auto",
        analysis_intervals=(
            slot_config["analysis_intervals"]
            if slot_config else {"short": 900, "mid": 3600, "long": 14400}
        ),
        status="active",
        created_at=datetime.now(timezone.utc),
        activated_at=datetime.now(timezone.utc),
        timeframe_tier=slot or "mid",
    )

    # 初始化策略基因组 — 按 tier 设置不同的 trade_nature 和持仓周期
    # 注：中长线合并后 mid 不再是独立 tier，已并入 long（swing agent 路径删除，
    # mid_view 归入 long thesis）。mid 入参映射到 long 口径，保留 key 以兼容旧调用。
    _TIER_GENOME = {
        "short": {"trade_nature": "intraday", "expected_hold_hours": 4},
        "mid":   {"trade_nature": "position", "expected_hold_hours": 168},
        "long":  {"trade_nature": "position", "expected_hold_hours": 168},
    }
    try:
        from backend.services.strategy_genome import create_default_genome
        ai_strategy.genome = create_default_genome("trend")
        ai_strategy.genome["stop_loss_pct"] = risk["stop_loss_pct"]
        ai_strategy.genome["take_profit_pct"] = risk["take_profit_pct"]
        ai_strategy.genome["max_position_size"] = risk["max_position_size"]
        ai_strategy.genome["default_leverage"] = float(adjusted_leverage)
        ai_strategy.genome["max_leverage"] = float(adjusted_max)
        ai_strategy.genome["min_confidence"] = risk["min_confidence"]
        # 按 tier 写入 trade_nature 和持仓周期，让自适应模块正确区分短/中/长
        # mid 与 long 现在共享同一基因组口径（中长线已合并）
        _tier_cfg = _TIER_GENOME.get(slot or "long", _TIER_GENOME["long"])
        ai_strategy.genome["trade_nature"] = _tier_cfg["trade_nature"]
        ai_strategy.genome["expected_hold_hours"] = _tier_cfg["expected_hold_hours"]
    except Exception as ge:
        logger.warning(f"[AutoLaunch] 基因组初始化失败: {ge}")

    db.add(ai_strategy)
    db.commit()
    db.refresh(ai_strategy)

    # --- 注册到 FullAuto 会话（确保 90s 统一循环能运行该策略）---
    try:
        from backend.database.models import FullAutoSession
        existing_session = db.query(FullAutoSession).filter(
            FullAutoSession.account_id == account.id,
            FullAutoSession.status == "running",
        ).first()
        if existing_session:
            _ids = list(existing_session.active_strategy_ids or [])
            if strategy_id not in _ids:
                _ids.append(strategy_id)
                existing_session.active_strategy_ids = _ids
                _syms = list(existing_session.symbols or [])
                if primary_symbol and primary_symbol not in _syms:
                    _syms.append(primary_symbol)
                    existing_session.symbols = _syms
                db.commit()
                logger.info(f"[AutoLaunch] 策略 {strategy_id} 已注册到现有 FullAutoSession")
        else:
            logger.info(
                f"[AutoLaunch] 无运行中的 FullAutoSession，策略 {strategy_id} 已创建但需手动启动全自动会话")
    except Exception as e:
        logger.warning(f"[AutoLaunch] FullAutoSession 注册失败(非致命): {e}")

    logger.info(f"[AutoLaunch] AI驱动策略创建完成: {strategy_id} | {strategy_name}")

    return AutoLaunchResponse(
        success=True,
        strategy_id=strategy_id,
        strategy_name=strategy_name,
        ai_decided=ai_decided,
        message=f"AI自主交易已启动！{slot_label} | 周期: {chosen_timeframe} | 风险: {risk['label']}",
        signal_pool_id=signal_pool_id,
        signal_count=len(created_signal_ids),
        generation_source="ai_driven",
        analysis_summary=analysis_summary,
        audit_decision=None,
        audit_score=None,
        audit_reason=None,
        chosen_template_name=None,
        candidate_count=0,
    )


