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
    from database.models import Account
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
            from database.models import GlobalSamplingConfig
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
# Smart Prompt Generation APIs (市场感知型智能提示词)
# ============================================================================

from backend.services.smart_prompt_generator import SmartPromptGenerator
from backend.services.market_regime_service import (
    get_adaptive_trading_parameters,
    get_multi_timeframe_regime_consensus,
)


class SmartPromptRequest(BaseModel):
    """智能提示词生成请求"""
    account_id: int = Field(..., alias="accountId", description="AI交易账户ID")
    symbols: List[str] = Field(..., description="交易品种列表")
    strategy_style: str = Field(
        "adaptive",
        alias="strategyStyle", 
        description="策略风格: trend_following, mean_reversion, breakout, scalping, adaptive"
    )
    include_signals: bool = Field(
        True, 
        alias="includeSignals",
        description="是否包含激活的信号信息"
    )
    include_patterns: bool = Field(
        True,
        alias="includePatterns", 
        description="是否包含检测到的模式"
    )

    class Config:
        populate_by_name = True


class SmartPromptResponse(BaseModel):
    """智能提示词响应"""
    success: bool
    prompt_template: Optional[str] = Field(None, alias="promptTemplate")
    strategy_rules: Optional[str] = Field(None, alias="strategyRules")
    market_context: Optional[Dict] = Field(None, alias="marketContext")
    adaptive_parameters: Optional[Dict] = Field(None, alias="adaptiveParameters")
    detected_patterns: Optional[List[Dict]] = Field(None, alias="detectedPatterns")
    error: Optional[str] = None

    class Config:
        populate_by_name = True


class SignalLinkedPromptRequest(BaseModel):
    """信号关联提示词请求"""
    signal_pool_id: int = Field(..., alias="signalPoolId")
    account_id: int = Field(..., alias="accountId")
    include_backtest: bool = Field(True, alias="includeBacktest")

    class Config:
        populate_by_name = True


class AdaptiveParametersResponse(BaseModel):
    """自适应参数响应"""
    symbol: str
    regime_type: str
    regime_direction: str
    regime_confidence: float
    parameters: Dict
    multi_timeframe: Optional[Dict] = None
    recommendations: List[str]


@router.post("/generate-smart-prompt", response_model=SmartPromptResponse)
def generate_smart_prompt(
    request: SmartPromptRequest,
    db: Session = Depends(get_db)
) -> SmartPromptResponse:
    """
    生成市场感知型智能提示词
    
    基于当前市场状态、技术指标和历史模式，自动生成优化的AI交易提示词。
    
    功能:
    1. 分析各品种当前市场状态
    2. 根据状态选择合适的策略规则
    3. 动态插入相关技术指标变量
    4. 生成针对性的风控规则
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # 验证账户
        account = db.query(Account).filter(Account.id == request.account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="AI Trader not found")
        
        if account.account_type != "AI":
            raise HTTPException(status_code=400, detail="Selected account is not an AI Trader")
        
        # 验证品种
        if not request.symbols:
            raise HTTPException(status_code=400, detail="At least one symbol is required")
        
        # 初始化智能提示词生成器
        generator = SmartPromptGenerator()
        
        # 生成市场感知型提示词
        result = generator.generate_market_aware_prompt(
            db=db,
            account_id=request.account_id,
            symbols=request.symbols,
            strategy_style=request.strategy_style
        )
        
        # 收集市场上下文和检测到的模式
        market_context = {}
        detected_patterns = []
        adaptive_params = {}
        
        for symbol in request.symbols:
            # 获取自适应参数
            try:
                params = get_adaptive_trading_parameters(db, symbol)
                adaptive_params[symbol] = {
                    "position_size_modifier": params.position_size_modifier,
                    "stop_loss_atr_multiple": params.stop_loss_atr_multiple,
                    "take_profit_ratio": params.take_profit_ratio,
                    "entry_confirmation_count": params.entry_confirmation_count,
                    "recommended_strategy": params.recommended_strategy,
                    "regime_type": params.regime_type,
                    "regime_direction": params.regime_direction,
                    "regime_confidence": params.regime_confidence,
                }
            except Exception as e:
                logger.warning(f"Failed to get adaptive params for {symbol}: {e}")
            
            # 获取多时间周期共识
            try:
                consensus = get_multi_timeframe_regime_consensus(db, symbol)
                market_context[symbol] = {
                    "consensus": consensus.get("consensus", {}),
                    "timeframes": consensus.get("timeframes", {}),
                    "recommendation": consensus.get("recommendation", ""),
                }
            except Exception as e:
                logger.warning(f"Failed to get regime consensus for {symbol}: {e}")
        
        # 如果需要模式检测
        if request.include_patterns:
            try:
                from services.pattern_recognition_service import PatternRecognitionService
                pattern_service = PatternRecognitionService()
                
                for symbol in request.symbols:
                    patterns = pattern_service.detect_current_patterns(db, symbol, "5m")
                    for p in patterns:
                        detected_patterns.append({
                            "symbol": symbol,
                            "pattern_name": p.get("pattern_name"),
                            "direction": p.get("direction"),
                            "confidence": p.get("confidence"),
                            "historical_win_rate": p.get("historical_win_rate"),
                            "triggered_conditions": p.get("triggered_conditions", []),
                        })
            except Exception as e:
                logger.warning(f"Pattern detection failed: {e}")
        
        return SmartPromptResponse(
            success=True,
            prompt_template=result.get("template_text"),
            strategy_rules=result.get("strategy_rules"),
            market_context=market_context,
            adaptive_parameters=adaptive_params,
            detected_patterns=detected_patterns if detected_patterns else None,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Smart prompt generation failed: {e}")
        return SmartPromptResponse(
            success=False,
            error=str(e)
        )


@router.post("/generate-signal-linked-prompt", response_model=SmartPromptResponse)
def generate_signal_linked_prompt(
    request: SignalLinkedPromptRequest,
    db: Session = Depends(get_db)
) -> SmartPromptResponse:
    """
    创建与信号关联的提示词
    
    当信号触发时:
    1. 提示词包含触发信号的详细背景
    2. 包含该信号历史胜率信息
    3. 提供基于回测的建议仓位和止损
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # 验证账户
        account = db.query(Account).filter(Account.id == request.account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="AI Trader not found")
        
        # 初始化生成器
        generator = SmartPromptGenerator()
        
        # 生成信号关联提示词
        result = generator.create_signal_linked_prompt(
            db=db,
            signal_pool_id=request.signal_pool_id,
            account_id=request.account_id
        )
        
        return SmartPromptResponse(
            success=True,
            prompt_template=result.get("template_text"),
            strategy_rules=result.get("strategy_rules"),
            market_context=result.get("signal_context"),
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Signal-linked prompt generation failed: {e}")
        return SmartPromptResponse(
            success=False,
            error=str(e)
        )


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


@router.post("/generate-adaptive-rules/{symbol}")
def generate_adaptive_strategy_rules(
    symbol: str,
    strategy_style: str = "adaptive",
    db: Session = Depends(get_db)
) -> Dict:
    """
    为指定品种生成自适应策略规则段落
    
    生成可直接插入提示词的策略规则文本，包含:
    - 当前市场状态描述
    - 建议的仓位大小
    - 止损止盈距离
    - 入场确认条件
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        symbol = symbol.upper()
        
        generator = SmartPromptGenerator()
        rules_text = generator.generate_adaptive_strategy_rules(db, symbol)
        
        # 获取额外的市场上下文
        params = get_adaptive_trading_parameters(db, symbol)
        
        return {
            "success": True,
            "symbol": symbol,
            "strategy_style": strategy_style,
            "rules_text": rules_text,
            "regime_summary": {
                "type": params.regime_type,
                "direction": params.regime_direction,
                "confidence": params.regime_confidence,
            },
            "key_parameters": {
                "position_modifier": params.position_size_modifier,
                "stop_loss_atr": params.stop_loss_atr_multiple,
                "take_profit_ratio": params.take_profit_ratio,
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to generate adaptive rules for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
