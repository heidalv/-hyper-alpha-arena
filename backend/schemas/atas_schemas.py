"""
ATAS V2 API Schemas

Pydantic数据模型定义
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime


# ============================================
# 策略相关Schema
# ============================================

class StrategyCreate(BaseModel):
    """创建策略请求"""
    name: str = Field(..., description="策略名称")
    description: Optional[str] = Field(None, description="策略描述")
    user_id: int = Field(..., description="用户ID")
    
    strategy_type: str = Field(..., description="策略类型")
    generation_method: str = Field(..., description="生成方法")
    
    entry_logic: Optional[Dict[str, Any]] = None
    exit_logic: Optional[Dict[str, Any]] = None
    position_sizing: Optional[Dict[str, Any]] = None
    risk_params: Optional[Dict[str, Any]] = None
    
    code_python: Optional[str] = None
    code_pinescript: Optional[str] = None
    
    required_factors: Optional[List[str]] = None
    factor_weights: Optional[Dict[str, float]] = None
    
    # AI元数据
    ai_model: Optional[str] = None
    prompt_template_id: Optional[int] = None
    user_input: Optional[str] = None
    generation_timestamp: Optional[datetime] = None
    confidence_score: Optional[float] = None
    
    tags: Optional[List[str]] = None
    category: Optional[str] = None
    is_public: bool = False
    is_template: bool = False


class StrategyUpdate(BaseModel):
    """更新策略请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    entry_logic: Optional[Dict[str, Any]] = None
    exit_logic: Optional[Dict[str, Any]] = None
    position_sizing: Optional[Dict[str, Any]] = None
    risk_params: Optional[Dict[str, Any]] = None
    code_python: Optional[str] = None
    tags: Optional[List[str]] = None


class StrategyResponse(BaseModel):
    """策略响应"""
    id: int
    strategy_id: str
    name: str
    description: Optional[str]
    user_id: int
    strategy_type: str
    generation_method: str
    status: str
    
    entry_logic: Optional[Dict[str, Any]]
    exit_logic: Optional[Dict[str, Any]]
    position_sizing: Optional[Dict[str, Any]]
    risk_params: Optional[Dict[str, Any]]
    
    code_python: Optional[str]
    required_factors: Optional[List[str]]
    factor_weights: Optional[Dict[str, float]]
    
    confidence_score: Optional[float]
    
    total_trades: int
    win_rate: float
    sharpe_ratio: float
    max_drawdown: float
    total_return: float
    
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class StrategyList(BaseModel):
    """策略列表响应"""
    total: int
    skip: int
    limit: int
    items: List[StrategyResponse]


# ============================================
# 因子相关Schema
# ============================================

class FactorDetailResponse(BaseModel):
    """因子详情响应"""
    id: int
    factor_id: str
    name: str
    display_name: str
    description: Optional[str]
    category: str
    subcategory: Optional[str]
    
    calculation_method: str
    parameters: Optional[Dict[str, Any]]
    required_data_fields: Optional[List[str]]
    lookback_period: int
    
    ic: Optional[float]
    ir: Optional[float]
    turnover: Optional[float]
    coverage: Optional[float]
    
    status: str
    usage_count: int
    
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class FactorListResponse(BaseModel):
    """因子列表响应"""
    total: int
    skip: int
    limit: int
    items: List[FactorDetailResponse]


class FactorCalculateRequest(BaseModel):
    """因子计算请求"""
    factor_ids: List[str] = Field(..., description="要计算的因子ID列表")
    symbols: List[str] = Field(..., description="交易标的列表")
    timeframe: str = Field("1d", description="时间周期")
    lookback_period: int = Field(100, description="回溯期数")
    
    params: Optional[Dict[str, Dict[str, Any]]] = Field(None, description="因子参数")
    use_cache: bool = Field(True, description="是否使用缓存")
    parallel: bool = Field(False, description="是否并行计算")


class FactorCalculateResponse(BaseModel):
    """因子计算响应"""
    success: bool
    results: Dict[str, Any]
    cache_stats: Optional[Dict[str, Any]] = None


# ============================================
# AI生成相关Schema
# ============================================

class AIGenerateRequest(BaseModel):
    """AI生成策略请求"""
    user_input: str = Field(..., description="用户的自然语言描述")
    user_id: Optional[int] = Field(None, description="用户ID")
    
    strategy_type: Optional[str] = Field(None, description="策略类型")
    market_context: Optional[Dict[str, Any]] = Field(None, description="市场环境")
    constraints: Optional[Dict[str, Any]] = Field(None, description="约束条件")
    
    # AI配置
    model: Optional[str] = Field("gpt-4", description="AI模型")
    temperature: float = Field(0.7, ge=0, le=2, description="温度参数")
    max_tokens: int = Field(2000, ge=100, le=4000, description="最大token数")
    
    # 模板
    template_id: Optional[str] = Field(None, description="提示词模板ID")
    
    # 选项
    auto_create_strategy: bool = Field(False, description="是否自动创建策略记录")
    strict_validation: bool = Field(True, description="是否启用严格验证")


class AIGenerateResponse(BaseModel):
    """AI生成策略响应"""
    success: bool
    generation_id: str
    strategy_id: Optional[str] = None
    
    strategy_name: str
    strategy_description: str
    strategy_code: str
    strategy_logic: Dict[str, Any]
    
    required_factors: List[str]
    factor_weights: Dict[str, float]
    
    confidence_score: float
    generation_time_seconds: float
    
    warnings: List[str]
    error_message: Optional[str] = None


class PromptTemplateResponse(BaseModel):
    """提示词模板响应"""
    template_id: str
    name: str
    description: str
    category: str
    
    system_prompt: Optional[str] = None
    user_prompt_template: Optional[str] = None
    
    recommended_model: str
    temperature: float
    max_tokens: int
    
    variables: Dict[str, Dict[str, Any]]
    example_inputs: Optional[Dict[str, str]] = None
    example_outputs: Optional[Dict[str, str]] = None


class CodeValidateRequest(BaseModel):
    """代码验证请求"""
    code: str = Field(..., description="要验证的代码")
    strict_mode: bool = Field(True, description="严格模式")


class CodeValidateResponse(BaseModel):
    """代码验证响应"""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    info: Dict[str, Any]
