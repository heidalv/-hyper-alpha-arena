"""
Strategic Analyst - 数据类定义

定义战略分析师模块的核心数据结构：
- MacroSnapshot: 宏观快照
- MacroAssessment: 宏观评估结果
- NewCoinOpportunity: 新币打新机会
- StrategicMemory: 战略记忆条目
- StrategicReport: 战略报告（核心输出）
- CrossMarketCorrelation: 跨市场相关性
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class MacroSnapshot:
    """宏观快照 - 所有宏观指标的一次采集结果"""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    # 主要指标
    dxy_value: Optional[float] = None               # 美元指数
    dxy_change_pct: Optional[float] = None           # DXY 日涨跌幅
    spx_close: Optional[float] = None                # S&P500 收盘价
    spx_change_pct: Optional[float] = None           # S&P500 日涨跌幅
    csi300_close: Optional[float] = None             # 沪深300收盘价
    csi300_change_pct: Optional[float] = None        # 沪深300日涨跌幅
    fed_funds_rate: Optional[float] = None            # 联邦基金利率
    crypto_market_cap: Optional[float] = None         # 加密总市值（亿美元）
    btc_dominance: Optional[float] = None             # BTC 市值占比
    fear_greed_index: Optional[float] = None          # 恐贪指数 (0-100)
    # 派生指标
    btc_sp500_corr_7d: Optional[float] = None         # BTC-SPX 7日相关系数
    btc_sp500_corr_30d: Optional[float] = None        # BTC-SPX 30日相关系数
    btc_dxy_corr_30d: Optional[float] = None          # BTC-DXY 30日相关系数
    btc_csi300_corr_30d: Optional[float] = None       # BTC-CSI300 30日相关系数
    # 综合评估
    regime: str = "unknown"                            # risk_on/risk_off/neutral/transition
    risk_on_score: float = 0.0                         # -1 ~ +1
    data_sources_status: Dict = field(default_factory=dict)  # 各数据源采集状态


@dataclass
class MacroAssessment:
    """宏观评估结果"""
    regime: str = "unknown"                    # risk_on/risk_off/neutral/transition
    risk_on_score: float = 0.0                 # -1 ~ +1
    confidence: float = 0.0                    # 0 ~ 1
    impact_direction: str = "neutral"          # bullish/bearish/neutral
    impact_magnitude: float = 0.0              # 0 ~ 1
    key_risks: List[str] = field(default_factory=list)
    cross_market_correlations: Dict[str, float] = field(default_factory=dict)
    regime_transition_signal: bool = False
    # 分项评估
    dxy_impact: str = "neutral"                # DXY对加密市场的影响方向
    spx_impact: str = "neutral"                # SPX对加密市场的影响方向
    china_market_impact: str = "neutral"       # 中国股市对加密市场的影响方向
    liquidity_condition: str = "normal"        # 紧缩/宽松/正常


@dataclass
class NewCoinOpportunity:
    """新币打新机会"""
    symbol: str = ""
    exchange: str = ""
    listing_date: Optional[datetime] = None
    status: str = "pending"                    # pending/listing/active/expired
    # 评估
    hype_score: float = 0.0                    # 0-100 热度评分
    project_category: str = "unknown"          # DeFi/L2/Meme/Infra/GameFi/Other
    team_background: str = "unknown"           # 知名VC背书/社区驱动/匿名团队
    funding_info: Dict = field(default_factory=dict)  # 融资信息
    estimated_volatility: Optional[float] = None      # 预估波动率
    # [2026-08-15] 波动率是否为类别默认假设（无 K 线历史时的估计值）
    volatility_is_estimate: bool = True
    # 策略
    recommended_strategy: str = "wait_and_see"  # scalp_first/wait_and_see/avoid
    recommended_position_pct: float = 0.0       # 建议仓位占比
    stop_loss_pct: float = 0.05                 # 止损百分比
    take_profit_pct: float = 0.15               # 止盈百分比
    # 分析
    ai_analysis: Optional[str] = None
    confidence: float = 0.0
    # 事后验证（上线后填充）
    actual_first_day_pnl_pct: Optional[float] = None
    actual_max_drawdown_pct: Optional[float] = None


@dataclass
class StrategicMemory:
    """战略记忆条目"""
    id: Optional[int] = None
    memory_type: str = ""           # macro_lesson/cycle_pattern/new_coin_postmortem/regime_transition
    market_context: str = ""        # 当时市场环境描述
    observation: str = ""           # 观察到的现象
    lesson: str = ""                # 经验教训
    applicability_conditions: Dict = field(default_factory=dict)  # 适用条件
    confidence: float = 0.0         # 置信度 0-1
    times_validated: int = 0        # 被验证次数
    times_invalidated: int = 0      # 被证伪次数
    source: str = "auto"            # auto/human/llm
    related_symbols: List[str] = field(default_factory=list)
    created_at: Optional[datetime] = None


@dataclass
class CrossMarketCorrelation:
    """跨市场相关性"""
    pair_name: str = ""             # btc_spx/btc_dxy/btc_csi300
    correlation_7d: float = 0.0
    correlation_30d: float = 0.0
    correlation_90d: float = 0.0
    rolling_beta: float = 0.0
    regime: str = "unknown"         # decoupled/weak_corr/strong_corr
    significance: float = 0.0


@dataclass
class StrategicReport:
    """战略报告 - 模块的核心输出"""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    # 宏观评估
    macro_assessment: Optional[MacroAssessment] = None
    new_coin_opportunities: List[NewCoinOpportunity] = field(default_factory=list)
    relevant_memories: List[StrategicMemory] = field(default_factory=list)
    cross_market_correlations: List[CrossMarketCorrelation] = field(default_factory=list)
    # 综合结论
    market_cycle_phase: str = "unknown"    # 基于宏观的周期阶段
    macro_bias: str = "neutral"            # bullish/bearish/neutral
    macro_confidence: float = 0.0          # 宏观置信度 0-1
    risk_budget_adjustment: float = 1.0    # 风险预算调整系数 (0.5 ~ 1.5)
    recommended_direction: str = "neutral"  # long/short/neutral
    # 详细分析
    key_insights: List[str] = field(default_factory=list)
    sp500_impact_summary: str = ""
    china_market_impact_summary: str = ""
    geopolitical_risks: List[str] = field(default_factory=list)
    regulatory_outlook: str = ""
    # LLM 输出
    llm_analysis: Optional[str] = None
    # 数据质量
    data_quality_score: float = 0.0        # 0-1, 数据完整性评分
