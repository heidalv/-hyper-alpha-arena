"""
Strategy Template Routes — 策略模板库 CRUD + 导入/导出 API

提供策略模板的增删改查、外部策略导入（LLM 格式适配）、现有策略导出和晋升为模板功能。
"""

import json
import logging
import uuid
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database.connection import get_db
from backend.database.models import StrategyTemplate, AIStrategy, StrategyMemory, SignalDefinition, SignalPool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategy-templates", tags=["Strategy Templates"])


# ── Pydantic Models ──

class TemplateListItem(BaseModel):
    id: int
    template_id: str
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    market_regime: Optional[str] = None
    risk_level: Optional[str] = None
    timeframe: Optional[str] = None
    source: Optional[str] = None
    author: Optional[str] = None
    backtest_win_rate: Optional[float] = None
    backtest_sharpe: Optional[float] = None
    backtest_max_drawdown: Optional[float] = None
    backtest_total_trades: Optional[int] = None
    live_usage_count: Optional[int] = 0
    live_avg_return: Optional[float] = None
    is_active: Optional[bool] = True
    rating: Optional[float] = 0.0
    tags: Optional[list] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_safe(cls, obj):
        return cls(
            id=obj.id,
            template_id=obj.template_id,
            name=obj.name,
            description=obj.description,
            category=obj.category,
            market_regime=obj.market_regime,
            risk_level=obj.risk_level,
            timeframe=obj.timeframe,
            source=obj.source,
            author=obj.author,
            backtest_win_rate=obj.backtest_win_rate,
            backtest_sharpe=obj.backtest_sharpe,
            backtest_max_drawdown=obj.backtest_max_drawdown,
            backtest_total_trades=obj.backtest_total_trades,
            live_usage_count=obj.live_usage_count or 0,
            live_avg_return=obj.live_avg_return,
            is_active=obj.is_active if obj.is_active is not None else True,
            rating=obj.rating or 0.0,
            tags=obj.tags,
            created_at=obj.created_at.isoformat() if hasattr(obj.created_at, 'isoformat') and obj.created_at else str(obj.created_at) if obj.created_at else None,
        )


class TemplateDetailResponse(TemplateListItem):
    strategy_config: Optional[dict] = None
    source_url: Optional[str] = None
    version: Optional[str] = None


class TemplateCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    category: Optional[str] = "trend"
    market_regime: Optional[str] = "all"
    risk_level: Optional[str] = "moderate"
    timeframe: Optional[str] = "15m"
    strategy_config: dict
    tags: Optional[list] = None


class TemplateUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    market_regime: Optional[str] = None
    risk_level: Optional[str] = None
    timeframe: Optional[str] = None
    strategy_config: Optional[dict] = None
    is_active: Optional[bool] = None
    rating: Optional[float] = None
    tags: Optional[list] = None


class ImportRequest(BaseModel):
    """导入策略 — 支持任意格式文本/JSON，由 LLM 适配"""
    content: str                         # 原始策略内容（可以是 JSON / 纯文字 / Pine Script 等）
    name: Optional[str] = None           # 可选名称
    source_url: Optional[str] = None     # 来源 URL
    account_id: Optional[int] = None     # 用于获取 LLM 配置


class ImportResponse(BaseModel):
    success: bool
    template_id: Optional[str] = None
    name: Optional[str] = None
    message: str
    adapted_config: Optional[dict] = None  # LLM 适配后的标准配置


class PromoteRequest(BaseModel):
    """将现有 AIStrategy 晋升为模板"""
    strategy_id: str
    name: Optional[str] = None           # 覆盖名称


# ── CRUD Endpoints ──
# 注意：静态路径必须在动态路径 /{template_id} 之前定义，否则 FastAPI 会误匹配

@router.get("", response_model=List[TemplateListItem])
def list_templates(
    category: Optional[str] = Query(None),
    market_regime: Optional[str] = Query(None),
    risk_level: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    """获取策略模板列表，支持多维筛选"""
    query = db.query(StrategyTemplate)
    if category:
        query = query.filter(StrategyTemplate.category == category)
    if market_regime:
        query = query.filter(StrategyTemplate.market_regime.in_([market_regime, "all"]))
    if risk_level:
        query = query.filter(StrategyTemplate.risk_level == risk_level)
    if source:
        query = query.filter(StrategyTemplate.source == source)
    if is_active is not None:
        query = query.filter(StrategyTemplate.is_active == is_active)

    templates = query.order_by(StrategyTemplate.rating.desc(), StrategyTemplate.created_at.desc()).all()
    return [TemplateListItem.from_orm_safe(t) for t in templates]


@router.post("", response_model=TemplateDetailResponse, status_code=201)
def create_template(request: TemplateCreateRequest, db: Session = Depends(get_db)):
    """手动创建策略模板"""
    tpl = StrategyTemplate(
        template_id=f"tpl_{uuid.uuid4().hex[:10]}",
        name=request.name,
        description=request.description,
        category=request.category,
        market_regime=request.market_regime,
        risk_level=request.risk_level,
        timeframe=request.timeframe,
        strategy_config=request.strategy_config,
        source="manual",
        author="user",
        version="1.0",
        is_active=True,
        rating=0.0,
        tags=request.tags,
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)

    base = TemplateListItem.from_orm_safe(tpl)
    return TemplateDetailResponse(**base.dict(), strategy_config=tpl.strategy_config, version=tpl.version)


# ── Import (LLM Adaptation) ──

@router.post("/import", response_model=ImportResponse)
def import_strategy(request: ImportRequest, db: Session = Depends(get_db)):
    """
    导入外部策略 — 支持任意格式（JSON / 纯文字 / Pine Script 等）
    
    调用 LLM 将原始内容转换为系统标准 strategy_config 格式后存入模板库。
    """
    from backend.services.llm_config_service import get_llm_config_for_account, call_llm_api

    # 仅用本账户自有 LLM，禁止公用默认
    llm_config = None
    if request.account_id:
        try:
            llm_config = get_llm_config_for_account(request.account_id, tier="deep")
        except Exception:
            pass

    if not llm_config:
        raise HTTPException(
            status_code=400,
            detail="未配置本账户 AI 大模型。请先在设置中为自己的账户添加 LLM API Key（不可共用）。"
        )

    adapt_prompt = (
        "你是策略格式适配专家。用户提供了一份交易策略，可能是以下任意格式：\n"
        "- 纯文字描述\n- Pine Script (TradingView)\n- JSON配置\n- 表格/列表\n- 其他格式\n\n"
        "请将其转换为以下标准JSON格式（直接返回JSON，不要其他文字）：\n"
        "{\n"
        '  "name": "策略名称",\n'
        '  "description": "策略描述",\n'
        '  "category": "trend|range|breakout|momentum|swing|scalping",\n'
        '  "market_regime": "bull|bear|sideways|all",\n'
        '  "risk_level": "conservative|moderate|aggressive",\n'
        '  "timeframe": "15m|1h|4h|1d",\n'
        '  "strategy_config": {\n'
        '    "strategy_logic": "详细策略逻辑描述",\n'
        '    "entry_conditions": ["入场条件1", "入场条件2", "入场条件3"],\n'
        '    "exit_conditions": ["出场条件1", "出场条件2", "出场条件3"],\n'
        '    "risk_params": {\n'
        '      "max_position_size": 0.2, "stop_loss_pct": 0.05, "take_profit_pct": 0.10,\n'
        '      "max_daily_loss": 0.10, "max_leverage": 5, "default_leverage": 2,\n'
        '      "leverage_mode": "isolated", "snowball_enabled": false\n'
        "    },\n"
        '    "signal_definitions": [\n'
        '      {"name": "信号名", "desc": "描述", "type": "entry|confirmation|filter", "logic": "逻辑", "params": {}}\n'
        "    ],\n"
        '    "applicable_symbols": ["BTC"],\n'
        '    "optimal_market_regime": "all",\n'
        '    "notes": "使用注意事项"\n'
        "  }\n"
        "}\n\n"
        "要求：\n"
        "1. 提取所有入场/出场条件，保留原始策略核心逻辑\n"
        "2. 如未提供风险参数则给出合理默认值\n"
        "3. 识别适用的市场环境和交易对\n"
        "4. signal_definitions 至少提取3个信号\n\n"
        f"═══ 用户提供的策略内容 ═══\n{request.content}\n"
    )

    try:
        import asyncio
        import_messages = [
            {"role": "system", "content": "你是策略格式适配专家。将任意格式的交易策略转换为标准JSON。只返回JSON。"},
            {"role": "user", "content": adapt_prompt},
        ]
        from backend.services.llm_config_service import call_llm_api_sync
        ai_response = call_llm_api_sync(
            config=llm_config,
            messages=import_messages,
            temperature=0.7,
            max_tokens=3000,
        )

        ai_result = None
        if ai_response and "choices" in ai_response and len(ai_response["choices"]) > 0:
            ai_result = ai_response["choices"][0].get("message", {}).get("content", "")
    except Exception as e:
        logger.error(f"[Import] LLM 调用失败: {e}")
        raise HTTPException(status_code=500, detail=f"AI适配失败: {e}")

    if not ai_result:
        raise HTTPException(status_code=500, detail="AI返回空结果")

    import re
    json_match = re.search(r'\{.*\}', ai_result, re.DOTALL)
    if not json_match:
        raise HTTPException(status_code=500, detail="AI返回格式异常，无法解析JSON")

    try:
        parsed = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"JSON解析失败: {e}")

    tpl_name = request.name or parsed.get("name", "导入策略")
    tpl_id = f"tpl_imp_{uuid.uuid4().hex[:8]}"

    tpl = StrategyTemplate(
        template_id=tpl_id,
        name=tpl_name,
        description=parsed.get("description", ""),
        category=parsed.get("category", "trend"),
        market_regime=parsed.get("market_regime", "all"),
        risk_level=parsed.get("risk_level", "moderate"),
        timeframe=parsed.get("timeframe", "15m"),
        strategy_config=parsed.get("strategy_config", parsed),
        source="imported",
        source_url=request.source_url,
        author="imported",
        version="1.0",
        is_active=True,
        rating=0.0,
        tags=["导入"],
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)

    logger.info(f"[Import] 成功导入策略模板: {tpl_name} (ID: {tpl_id})")

    return ImportResponse(
        success=True,
        template_id=tpl_id,
        name=tpl_name,
        message=f"策略已通过AI适配并存入模板库",
        adapted_config=tpl.strategy_config,
    )


# ── Export ──

@router.get("/export/{strategy_id}")
def export_strategy(strategy_id: str, db: Session = Depends(get_db)):
    """将现有 AIStrategy 导出为标准 JSON（含信号定义、信号池、绩效）"""
    strategy = db.query(AIStrategy).filter(AIStrategy.strategy_id == strategy_id).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    # 收集关联的信号定义
    signal_defs = []
    pool_ids = strategy.signal_pool_ids or []
    for pid in pool_ids:
        pool = db.query(SignalPool).filter(SignalPool.id == pid).first()
        if pool:
            sig_ids = json.loads(pool.signal_ids) if isinstance(pool.signal_ids, str) else (pool.signal_ids or [])
            for sid in sig_ids:
                sig = db.query(SignalDefinition).filter(SignalDefinition.id == sid).first()
                if sig:
                    signal_defs.append({
                        "name": sig.signal_name,
                        "desc": sig.description,
                        "trigger_condition": json.loads(sig.trigger_condition) if isinstance(sig.trigger_condition, str) else sig.trigger_condition,
                    })

    memory = db.query(StrategyMemory).filter(StrategyMemory.strategy_id == strategy_id).first()

    export_data = {
        "name": strategy.name,
        "description": strategy.description,
        "strategy_id": strategy.strategy_id,
        "category": getattr(strategy, "prompt_variables", {}).get("trading_style", "trend") if strategy.prompt_variables else "trend",
        "timeframe": strategy.timeframe or "15m",
        "strategy_config": {
            "strategy_logic": (strategy.prompt_variables or {}).get("strategy_logic", ""),
            "entry_conditions": (strategy.prompt_variables or {}).get("entry_conditions", []),
            "exit_conditions": (strategy.prompt_variables or {}).get("exit_conditions", []),
            "risk_params": {
                "max_position_size": strategy.max_position_size,
                "stop_loss_pct": strategy.stop_loss_pct,
                "take_profit_pct": strategy.take_profit_pct,
                "max_daily_loss": strategy.max_daily_loss,
                "max_leverage": strategy.max_leverage,
                "default_leverage": strategy.default_leverage,
                "leverage_mode": strategy.leverage_mode,
                "snowball_enabled": strategy.snowball_enabled,
            },
            "signal_definitions": signal_defs,
            "applicable_symbols": strategy.target_symbols or ["BTC"],
        },
        "performance": {
            "total_trades": memory.total_trades if memory else 0,
            "win_rate": memory.win_rate if memory else 0,
            "sharpe_ratio": memory.sharpe_ratio if memory else 0,
            "max_drawdown": memory.max_drawdown if memory else 0,
        } if memory else None,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }

    return export_data


# ── Promote (strategy → template) ──

@router.post("/promote", response_model=TemplateDetailResponse)
def promote_strategy_to_template(request: PromoteRequest, db: Session = Depends(get_db)):
    """将现有 AIStrategy 晋升为策略模板"""
    strategy = db.query(AIStrategy).filter(AIStrategy.strategy_id == request.strategy_id).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    memory = db.query(StrategyMemory).filter(StrategyMemory.strategy_id == request.strategy_id).first()

    existing = db.query(StrategyTemplate).filter(
        StrategyTemplate.name == (request.name or strategy.name),
        StrategyTemplate.source == "promoted",
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="该策略已晋升为模板")

    trading_style = (strategy.prompt_variables or {}).get("trading_style", "trend")

    from backend.services.strategy_library import build_promoted_strategy_config
    _promoted_cfg = build_promoted_strategy_config(strategy, memory)

    tpl_id = f"tpl_pro_{uuid.uuid4().hex[:8]}"
    tpl = StrategyTemplate(
        template_id=tpl_id,
        name=request.name or f"[实战验证] {strategy.name}",
        description=f"从实战策略 {strategy.strategy_id} 晋升。{strategy.description or ''}",
        category=trading_style,
        market_regime="all",
        risk_level="moderate",
        timeframe=strategy.timeframe or "15m",
        tier=getattr(strategy, "timeframe_tier", None) or "mid",
        strategy_config=_promoted_cfg,
        source="promoted",
        author="system",
        version="1.0",
        backtest_win_rate=memory.win_rate if memory else None,
        backtest_sharpe=memory.sharpe_ratio if memory else None,
        backtest_max_drawdown=memory.max_drawdown if memory else None,
        backtest_total_trades=memory.total_trades if memory else None,
        is_active=True,
        rating=4.0 if memory and memory.win_rate > 0.6 else 3.0,
        tags=["实战验证", "promoted_live", trading_style],
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)

    logger.info(f"[Promote] 策略 {request.strategy_id} 已晋升为模板 {tpl_id}")

    base = TemplateListItem.from_orm_safe(tpl)
    return TemplateDetailResponse(**base.dict(), strategy_config=tpl.strategy_config, version=tpl.version)


# ── 动态路径参数路由（必须放在所有静态路径路由之后） ──

@router.get("/{template_id}", response_model=TemplateDetailResponse)
def get_template(template_id: str, db: Session = Depends(get_db)):
    """获取单个模板详情（含完整 strategy_config）"""
    tpl = db.query(StrategyTemplate).filter(StrategyTemplate.template_id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")

    base = TemplateListItem.from_orm_safe(tpl)
    return TemplateDetailResponse(
        **base.dict(),
        strategy_config=tpl.strategy_config,
        source_url=tpl.source_url,
        version=tpl.version,
    )


@router.put("/{template_id}", response_model=TemplateDetailResponse)
def update_template(template_id: str, request: TemplateUpdateRequest, db: Session = Depends(get_db)):
    """更新策略模板"""
    tpl = db.query(StrategyTemplate).filter(StrategyTemplate.template_id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")

    for field, value in request.dict(exclude_unset=True).items():
        setattr(tpl, field, value)

    db.commit()
    db.refresh(tpl)

    base = TemplateListItem.from_orm_safe(tpl)
    return TemplateDetailResponse(**base.dict(), strategy_config=tpl.strategy_config, version=tpl.version)


@router.delete("/{template_id}")
def delete_template(template_id: str, db: Session = Depends(get_db)):
    """删除策略模板"""
    tpl = db.query(StrategyTemplate).filter(StrategyTemplate.template_id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    db.delete(tpl)
    db.commit()
    return {"success": True, "message": f"模板 {template_id} 已删除"}
