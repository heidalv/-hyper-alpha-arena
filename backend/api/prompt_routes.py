from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal
from repositories import prompt_repo
from backend.database.models import PromptTemplate, Account
from backend.schemas.prompt import (
    PromptListResponse,
    PromptTemplateUpdateRequest,
    PromptTemplateResponse,
    PromptBindingUpsertRequest,
    PromptBindingResponse,
    PromptTemplateCopyRequest,
    PromptTemplateCreateRequest,
    PromptTemplateNameUpdateRequest,
)


router = APIRouter(prefix="/api/prompts", tags=["Prompt Templates"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Support both /api/prompts and /api/prompts/
@router.get("", response_model=PromptListResponse, response_model_exclude_none=True)
@router.get("/", response_model=PromptListResponse, response_model_exclude_none=True)
def list_prompt_templates(db: Session = Depends(get_db)) -> PromptListResponse:
    templates = prompt_repo.get_all_templates(db)
    bindings = prompt_repo.list_bindings(db)

    template_responses = [
        PromptTemplateResponse.from_orm(template)
        for template in templates
    ]

    binding_responses = []
    for binding, account, template in bindings:
        binding_responses.append(
            PromptBindingResponse(
                id=binding.id,
                account_id=account.id,
                account_name=account.name,
                account_model=account.model,
                prompt_template_id=binding.prompt_template_id,
                prompt_key=template.key,
                prompt_name=template.name,
                updated_by=binding.updated_by,
                updated_at=binding.updated_at,
            )
        )

    return PromptListResponse(templates=template_responses, bindings=binding_responses)


@router.put("/{key}", response_model=PromptTemplateResponse, response_model_exclude_none=True)
def update_prompt_template(
    key: str,
    payload: PromptTemplateUpdateRequest,
    db: Session = Depends(get_db),
) -> PromptTemplateResponse:
    try:
        template = prompt_repo.update_template(
            db,
            key=key,
            template_text=payload.template_text,
            description=payload.description,
            updated_by=payload.updated_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return PromptTemplateResponse.from_orm(template)


# Restore endpoint removed - dangerous operation that overwrites user customizations


@router.post("", response_model=PromptTemplateResponse, response_model_exclude_none=True)
@router.post("/", response_model=PromptTemplateResponse, response_model_exclude_none=True)
def create_prompt_template(
    payload: PromptTemplateCreateRequest,
    db: Session = Depends(get_db),
) -> PromptTemplateResponse:
    """Create a new user-defined prompt template"""
    try:
        template = prompt_repo.create_user_template(
            db,
            name=payload.name,
            description=payload.description,
            template_text=payload.template_text,
            created_by=payload.created_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PromptTemplateResponse.from_orm(template)


@router.post(
    "/{template_id}/copy",
    response_model=PromptTemplateResponse,
    response_model_exclude_none=True,
)
def copy_prompt_template(
    template_id: int,
    payload: PromptTemplateCopyRequest,
    db: Session = Depends(get_db),
) -> PromptTemplateResponse:
    """Copy an existing template to create a new one"""
    try:
        template = prompt_repo.copy_template(
            db,
            template_id=template_id,
            new_name=payload.new_name,
            created_by=payload.created_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return PromptTemplateResponse.from_orm(template)


@router.delete("/{template_id}")
def delete_prompt_template(template_id: int, db: Session = Depends(get_db)) -> dict:
    """Soft delete a prompt template"""
    try:
        prompt_repo.soft_delete_template(db, template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "Template deleted"}


@router.patch(
    "/{template_id}/name",
    response_model=PromptTemplateResponse,
    response_model_exclude_none=True,
)
def update_prompt_template_name(
    template_id: int,
    payload: PromptTemplateNameUpdateRequest,
    db: Session = Depends(get_db),
) -> PromptTemplateResponse:
    """Update template name and description"""
    try:
        template = prompt_repo.update_template_name(
            db,
            template_id=template_id,
            name=payload.name,
            description=payload.description,
            updated_by=payload.updated_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return PromptTemplateResponse.from_orm(template)


@router.post(
    "/bindings",
    response_model=PromptBindingResponse,
    response_model_exclude_none=True,
)
def upsert_prompt_binding(
    payload: PromptBindingUpsertRequest,
    db: Session = Depends(get_db),
) -> PromptBindingResponse:
    if not payload.account_id:
        raise HTTPException(status_code=400, detail="accountId is required")
    if not payload.prompt_template_id:
        raise HTTPException(status_code=400, detail="promptTemplateId is required")

    account = db.get(Account, payload.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    template = db.get(PromptTemplate, payload.prompt_template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Prompt template not found")

    try:
        binding = prompt_repo.upsert_binding(
            db,
            account_id=payload.account_id,
            prompt_template_id=payload.prompt_template_id,
            updated_by=payload.updated_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return PromptBindingResponse(
        id=binding.id,
        account_id=account.id,
        account_name=account.name,
        account_model=account.model,
        prompt_template_id=binding.prompt_template_id,
        prompt_key=template.key,
        prompt_name=template.name,
        updated_by=binding.updated_by,
        updated_at=binding.updated_at,
    )


@router.delete("/bindings/{binding_id}")
def delete_prompt_binding(binding_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        prompt_repo.delete_binding(db, binding_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"message": "Binding deleted"}


@router.post("/preview")
def preview_prompt(
    payload: dict,
    db: Session = Depends(get_db),
) -> dict:
    """
    Preview filled prompt for selected accounts and symbols.

    Payload:
    {
        "templateText": "...",  # Optional: Use this template text directly (for preview before save)
        "promptTemplateKey": "pro",  # Optional: Fallback to database template if templateText not provided
        "accountIds": [1, 2],
        "symbols": ["BTC", "ETH"]
    }

    Returns:
    {
        "previews": [
            {
                "accountId": 1,
                "accountName": "Trader-A",
                "symbol": "BTC",
                "filledPrompt": "..."
            },
            ...
        ]
    }
    """
    from services.ai_decision_service import (
        _get_portfolio_data,
        _build_prompt_context,
        SafeDict,
        SUPPORTED_SYMBOLS,
    )
    from services.market_data import get_last_price
    from services.news_feed import fetch_latest_news
    from services.sampling_pool import sampling_pool
    from backend.database.models import Account
    import logging
    from services.hyperliquid_symbol_service import (
        get_selected_symbols as get_hyperliquid_selected_symbols,
        get_available_symbol_map as get_hyperliquid_symbol_map,
    )

    logger = logging.getLogger(__name__)

    # Priority: use templateText if provided (for preview before save), otherwise query from database
    template_text = payload.get("templateText")
    prompt_key = payload.get("promptTemplateKey", "default")
    account_ids = payload.get("accountIds", [])

    raw_symbols = [str(sym).upper() for sym in payload.get("symbols", []) if sym]
    requested_symbols: List[str] = []
    seen_requested = set()
    for symbol in raw_symbols:
        if symbol and symbol not in seen_requested:
            seen_requested.add(symbol)
            requested_symbols.append(symbol)

    base_symbol_order = list(SUPPORTED_SYMBOLS.keys())
    hyper_watchlist = get_hyperliquid_selected_symbols()
    hyper_symbol_map = get_hyperliquid_symbol_map()

    if not account_ids:
        raise HTTPException(status_code=400, detail="At least one account must be selected")

    # Get template text: use provided templateText or query from database
    if not template_text:
        # Fallback: query from database using promptTemplateKey
        template = prompt_repo.get_template_by_key(db, prompt_key)
        if not template:
            raise HTTPException(status_code=404, detail=f"Prompt template '{prompt_key}' not found")
        template_text = template.template_text
        logger.info(f"Preview: Using database template '{prompt_key}'")
    else:
        logger.info(f"Preview: Using provided templateText (length: {len(template_text)})")

    # Get news
    try:
        news_summary = fetch_latest_news()
        news_section = news_summary if news_summary else "No recent CoinJournal news available."
    except Exception as err:
        logger.warning(f"Failed to fetch news: {err}")
        news_section = "No recent CoinJournal news available."

    # Import multi-symbol sampling data builder
    from services.ai_decision_service import _build_multi_symbol_sampling_data

    previews = []

    for account_id in account_ids:
        account = db.get(Account, account_id)
        if not account:
            logger.warning(f"Account {account_id} not found, skipping")
            continue

        # Check if account uses Hyperliquid - ONLY use global environment
        from services.hyperliquid_environment import get_global_trading_mode
        hyperliquid_environment = get_global_trading_mode(db)

        # NOTE: Account-level environment setting is deprecated
        # All accounts MUST follow the global trading mode

        hyperliquid_state = None

        if hyperliquid_environment in ["testnet", "mainnet"]:
            # Get Hyperliquid real-time data
            try:
                from services.hyperliquid_environment import get_hyperliquid_client

                client = get_hyperliquid_client(db, account_id, override_environment=hyperliquid_environment)
                account_state = client.get_account_state(db)
                # include_timing=True for prompt preview to show position holding duration
                positions = client.get_positions(db, include_timing=True)

                # Build portfolio with Hyperliquid data
                portfolio = {
                    'cash': account_state['available_balance'],
                    'frozen_cash': account_state.get('used_margin', 0),
                    'positions': {},
                    'total_assets': account_state['total_equity']
                }

                for pos in positions:
                    symbol = pos['coin']
                    portfolio['positions'][symbol] = {
                        'quantity': pos['szi'],
                        'avg_cost': pos['entry_px'],
                        'current_value': pos['position_value'],
                        'unrealized_pnl': pos['unrealized_pnl'],
                        'leverage': pos['leverage']
                    }

                # Build Hyperliquid state for prompt context
                hyperliquid_state = {
                    'total_equity': account_state['total_equity'],
                    'available_balance': account_state['available_balance'],
                    'used_margin': account_state.get('used_margin', 0),
                    'margin_usage_percent': account_state['margin_usage_percent'],
                    'maintenance_margin': account_state.get('maintenance_margin', 0),
                    'positions': positions
                }

                logger.info(
                    f"Preview: Using Hyperliquid {hyperliquid_environment} data for {account.name}: "
                    f"equity=${account_state['total_equity']:.2f}"
                )

            except Exception as hl_err:
                logger.error(f"Failed to get Hyperliquid data for {account.name}: {hl_err}")
                raise HTTPException(
                    status_code=502,
                    detail=f"Failed to fetch Hyperliquid {hyperliquid_environment} data: {hl_err}",
                )
        else:
            # Paper trading mode
            portfolio = _get_portfolio_data(db, account)

        # Determine active symbols + metadata for this account
        if hyperliquid_environment in ["testnet", "mainnet"]:
            active_symbols = requested_symbols or hyper_watchlist or base_symbol_order
            symbol_metadata_map = {}
            for sym in active_symbols:
                entry = dict(hyper_symbol_map.get(sym, {}))
                entry.setdefault("name", sym)
                symbol_metadata_map[sym] = entry
        else:
            active_symbols = requested_symbols or base_symbol_order
            symbol_metadata_map = {sym: SUPPORTED_SYMBOLS.get(sym, sym) for sym in active_symbols}

        if not active_symbols:
            active_symbols = base_symbol_order

        prices: Dict[str, float] = {}
        for sym in active_symbols:
            try:
                prices[sym] = get_last_price(sym, "CRYPTO", environment=hyperliquid_environment or "mainnet")
            except Exception as err:
                logger.warning(f"Failed to get price for {sym}: {err}")
                prices[sym] = 0.0

        # Get actual sampling interval from config
        sampling_interval = None
        try:
            from backend.database.models import GlobalSamplingConfig
            config = db.query(GlobalSamplingConfig).first()
            if config:
                sampling_interval = config.sampling_interval
        except Exception as e:
            logger.warning(f"Failed to get sampling interval: {e}")

        sampling_data = _build_multi_symbol_sampling_data(active_symbols, sampling_pool, sampling_interval)
        # IMPORTANT: _build_prompt_context is the ONLY function that builds prompt context.
        # It now handles K-line and indicator variables internally when template_text is provided.
        # DO NOT add separate K-line processing here - it will cause inconsistencies.

        # Build a sample trigger_context for preview purposes
        # This shows users what the variable will look like when triggered
        sample_trigger_context = {
            "trigger_type": "signal",
            "signal_pool_id": 1,
            "signal_pool_name": "OI Surge Monitor",
            "pool_logic": "OR",
            "triggered_signals": [
                {
                    "signal_name": "OI Delta Alert",
                    "description": "Open Interest increased significantly, indicating new positions entering the market",
                    "metric": "oi_delta",
                    "operator": ">",
                    "threshold": 2.0,
                    "current_value": 2.5,
                    "time_window": "15m",
                }
            ],
            "trigger_symbol": "BTC",
        }

        context = _build_prompt_context(
            account,
            portfolio,
            prices,
            news_section,
            None,
            None,
            hyperliquid_state,
            db=db,
            symbol_metadata=symbol_metadata_map,
            symbol_order=active_symbols,
            sampling_interval=sampling_interval,
            environment=hyperliquid_environment or "mainnet",
            template_text=template_text,
            trigger_context=sample_trigger_context,
        )
        context["sampling_data"] = sampling_data

        try:
            filled_prompt = template_text.format_map(SafeDict(context))
        except Exception as err:
            logger.error(f"Failed to fill prompt for {account.name}: {err}")
            filled_prompt = f"Error filling prompt: {err}"

        previews.append({
            "accountId": account.id,
            "accountName": account.name,
            "symbols": requested_symbols if requested_symbols else [],
            "filledPrompt": filled_prompt,
        })

    return {"previews": previews}


# ============================================================================
# AI Prompt Generation Chat APIs
# ============================================================================

from pydantic import BaseModel, Field
from backend.services.ai_prompt_generation_service import (
    generate_prompt_with_ai,
    get_conversation_history,
    get_conversation_messages
)
from backend.database.models import User, UserSubscription


class AiChatRequest(BaseModel):
    """Request to send a message to AI prompt generation chat"""
    account_id: int = Field(..., alias="accountId")
    user_message: str = Field(..., alias="userMessage")
    conversation_id: Optional[int] = Field(None, alias="conversationId")

    class Config:
        populate_by_name = True


class AiChatResponse(BaseModel):
    """Response from AI prompt generation chat"""
    success: bool
    conversation_id: Optional[int] = Field(None, alias="conversationId")
    message_id: Optional[int] = Field(None, alias="messageId")
    content: Optional[str] = None
    prompt_result: Optional[str] = Field(None, alias="promptResult")
    error: Optional[str] = None

    class Config:
        populate_by_name = True


@router.post("/ai-chat", response_model=AiChatResponse)
def ai_chat(
    request: AiChatRequest,
    db: Session = Depends(get_db)
) -> AiChatResponse:
    """
    Send a message to AI prompt generation assistant

    Premium feature - requires active subscription
    """
    # Get user (default user for now)
    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get AI Trader account
    account = db.query(Account).filter(Account.id == request.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="AI Trader not found")

    if account.account_type != "AI":
        raise HTTPException(status_code=400, detail="Selected account is not an AI Trader")

    # Generate response
    result = generate_prompt_with_ai(
        db=db,
        account=account,
        user_message=request.user_message,
        conversation_id=request.conversation_id,
        user_id=user.id
    )

    return AiChatResponse(
        success=result.get("success", False),
        conversation_id=result.get("conversation_id"),
        message_id=result.get("message_id"),
        content=result.get("content"),
        prompt_result=result.get("prompt_result"),
        error=result.get("error")
    )


@router.get("/ai-conversations")
def list_ai_conversations(
    limit: int = 20,
    db: Session = Depends(get_db)
) -> Dict:
    """
    Get list of AI prompt generation conversations

    Premium feature - requires active subscription
    """
    # Get user (default user for now)
    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    conversations = get_conversation_history(
        db=db,
        user_id=user.id,
        limit=limit
    )

    return {"conversations": conversations}


@router.get("/ai-conversations/{conversation_id}/messages")
def get_conversation_messages_api(
    conversation_id: int,
    db: Session = Depends(get_db)
) -> Dict:
    """
    Get all messages in a specific conversation

    Premium feature - requires active subscription
    """
    # Get user (default user for now)
    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    messages = get_conversation_messages(
        db=db,
        conversation_id=conversation_id,
        user_id=user.id
    )

    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"messages": messages}


@router.get("/variables-reference")
def get_variables_reference(lang: str = "en") -> dict:
    """
    Get the prompt variables reference document (Markdown format).
    Used by frontend to display the strategy parameter guide.

    Args:
        lang: Language code ("en" for English, "zh" for Chinese)
    """
    import os

    # Select document based on language
    if lang == "zh":
        filename = "PROMPT_VARIABLES_REFERENCE_ZH.md"
    else:
        filename = "PROMPT_VARIABLES_REFERENCE.md"

    doc_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "config",
        filename
    )

    try:
        with open(doc_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"content": content}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Reference document not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read document: {str(e)}")


# ============================================================================
# 自适应参数 APIs（依赖 market_regime_service，活跃服务）
# ============================================================================

from backend.services.market_regime_service import (
    get_adaptive_trading_parameters,
    get_multi_timeframe_regime_consensus,
)


class AdaptiveParametersResponse(BaseModel):
    """自适应参数响应"""
    symbol: str
    regime_type: str
    regime_direction: str
    regime_confidence: float
    parameters: Dict
    multi_timeframe: Optional[Dict] = None
    recommendations: List[str]


@router.get("/adaptive-parameters/{symbol}", response_model=AdaptiveParametersResponse)
def get_symbol_adaptive_parameters(
    symbol: str,
    period: str = "1h",
    include_multi_timeframe: bool = True,
    db: Session = Depends(get_db)
) -> AdaptiveParametersResponse:
    """
    获取指定品种的自适应交易参数
    
    根据当前市场状态返回:
    - 仓位系数建议 (0.5-1.5)
    - 止损ATR倍数 (1-3)
    - 止盈比例 (1.5-3.0)
    - 入场确认条件数 (1-3)
    - 推荐策略类型
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        symbol = symbol.upper()
        
        # 获取自适应参数
        params = get_adaptive_trading_parameters(db, symbol, period)
        
        # 构建参数字典
        parameters = {
            "position_size_modifier": params.position_size_modifier,
            "stop_loss_atr_multiple": params.stop_loss_atr_multiple,
            "take_profit_ratio": params.take_profit_ratio,
            "entry_confirmation_count": params.entry_confirmation_count,
            "max_position_percent": params.max_position_percent,
            "trailing_stop_enabled": params.trailing_stop_enabled,
        }
        
        # 构建建议列表
        recommendations = []
        
        if params.regime_type == "breakout":
            recommendations.append("市场处于突破状态，建议顺势交易")
            recommendations.append(f"可适当放大仓位至{params.position_size_modifier:.0%}")
        elif params.regime_type == "absorption":
            recommendations.append("市场处于吸收状态，建议区间交易")
            recommendations.append("在支撑位附近做多，阻力位附近做空")
        elif params.regime_type == "noise":
            recommendations.append("市场噪音较大，建议减少交易频率")
            recommendations.append(f"仓位系数降至{params.position_size_modifier:.0%}")
        elif params.regime_type == "exhaustion":
            recommendations.append("市场可能即将反转，注意风险")
            recommendations.append("收紧止损，考虑减仓")
        
        recommendations.append(f"推荐策略: {params.recommended_strategy}")
        recommendations.append(f"止损建议: {params.stop_loss_atr_multiple}倍ATR")
        
        # 多时间周期分析
        multi_timeframe = None
        if include_multi_timeframe:
            try:
                consensus = get_multi_timeframe_regime_consensus(db, symbol)
                multi_timeframe = consensus
            except Exception as e:
                logger.warning(f"Failed to get multi-timeframe consensus: {e}")
        
        return AdaptiveParametersResponse(
            symbol=symbol,
            regime_type=params.regime_type,
            regime_direction=params.regime_direction,
            regime_confidence=params.regime_confidence,
            parameters=parameters,
            multi_timeframe=multi_timeframe,
            recommendations=recommendations,
        )
        
    except Exception as e:
        logger.error(f"Failed to get adaptive parameters for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/strategy-styles")
def get_available_strategy_styles() -> Dict:
    """
    获取可用的策略风格列表
    
    返回系统支持的所有策略风格及其描述
    """
    return {
        "styles": [
            {
                "id": "trend_following",
                "name": "趋势跟踪",
                "description": "顺势而为，在趋势确立后入场，适合单边行情",
                "best_regimes": ["breakout", "continuation"],
                "risk_profile": "moderate",
            },
            {
                "id": "mean_reversion",
                "name": "均值回归",
                "description": "在超买超卖区域反向操作，适合震荡行情",
                "best_regimes": ["absorption", "exhaustion"],
                "risk_profile": "moderate",
            },
            {
                "id": "breakout",
                "name": "突破策略",
                "description": "在关键价位突破时入场，追求大幅波动",
                "best_regimes": ["breakout"],
                "risk_profile": "aggressive",
            },
            {
                "id": "scalping",
                "name": "剥头皮",
                "description": "高频小额交易，追求积少成多",
                "best_regimes": ["absorption", "noise"],
                "risk_profile": "conservative",
            },
            {
                "id": "adaptive",
                "name": "自适应",
                "description": "根据市场状态自动切换策略，全天候适用",
                "best_regimes": ["all"],
                "risk_profile": "moderate",
            },
        ]
    }

