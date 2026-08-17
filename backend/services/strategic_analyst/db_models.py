"""
Strategic Analyst - 数据库 ORM 模型

新增 5 张表（挂在 AnalyticsBase，属于分析审计层）：
- strategic_macro_snapshots: 宏观快照
- strategic_reports: 战略报告持久化
- new_coin_opportunities: 新币打新机会追踪
- strategic_memories: 战略级长期记忆
- cross_market_correlations: 跨市场相关性历史
"""

from sqlalchemy import Column, Integer, String, Float, Text, TIMESTAMP, Index
from sqlalchemy.sql import func

try:
    from backend.database.connection import AnalyticsBase
except ImportError:
    from backend.database.connection import AnalyticsBase


class StrategicMacroSnapshot(AnalyticsBase):
    """宏观快照 - 每次 macro 采集的结果持久化"""
    __tablename__ = "strategic_macro_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    # 主要指标
    dxy_value = Column(Float, nullable=True)
    dxy_change_pct = Column(Float, nullable=True)
    spx_close = Column(Float, nullable=True)
    spx_change_pct = Column(Float, nullable=True)
    csi300_close = Column(Float, nullable=True)
    csi300_change_pct = Column(Float, nullable=True)
    fed_funds_rate = Column(Float, nullable=True)
    crypto_market_cap = Column(Float, nullable=True)
    btc_dominance = Column(Float, nullable=True)
    fear_greed_index = Column(Float, nullable=True)

    # 相关性
    btc_sp500_corr_7d = Column(Float, nullable=True)
    btc_sp500_corr_30d = Column(Float, nullable=True)
    btc_dxy_corr_30d = Column(Float, nullable=True)
    btc_csi300_corr_30d = Column(Float, nullable=True)

    # 综合评估
    regime = Column(String(32), nullable=False, default="unknown")
    risk_on_score = Column(Float, nullable=False, default=0.0)

    # 数据源状态 JSON
    data_sources_status = Column(Text, nullable=True)

    __table_args__ = (
        Index('ix_macro_snapshot_timestamp', 'timestamp'),
    )


class StrategicReportRecord(AnalyticsBase):
    """战略报告持久化"""
    __tablename__ = "strategic_reports"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    report_type = Column(String(32), nullable=False, default="regular")  # regular/weekly/monthly

    # 综合结论
    market_cycle_phase = Column(String(32), nullable=False, default="unknown")
    macro_bias = Column(String(16), nullable=False, default="neutral")
    macro_confidence = Column(Float, nullable=False, default=0.0)
    risk_budget_adjustment = Column(Float, nullable=False, default=1.0)
    recommended_direction = Column(String(16), nullable=False, default="neutral")

    # 详细分析
    sp500_impact_summary = Column(Text, nullable=True)
    china_market_impact_summary = Column(Text, nullable=True)
    geopolitical_risks = Column(Text, nullable=True)        # JSON array
    regulatory_outlook = Column(Text, nullable=True)
    key_insights = Column(Text, nullable=True)               # JSON array

    # LLM 分析
    llm_analysis = Column(Text, nullable=True)

    # 关联数据 macro_snapshot_id = Column(Integer, nullable=True)       # 关联 strategic_macro_snapshots.id
    new_coin_count = Column(Integer, nullable=False, default=0)
    memory_count = Column(Integer, nullable=False, default=0)

    # 数据质量
    data_quality_score = Column(Float, nullable=False, default=0.0)

    __table_args__ = (
        Index('ix_strategic_report_timestamp', 'timestamp'),
    )


class NewCoinOpportunityRecord(AnalyticsBase):
    """新币打新机会追踪"""
    __tablename__ = "new_coin_opportunities"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    symbol = Column(String(32), nullable=False, index=True)
    exchange = Column(String(32), nullable=False)
    listing_date = Column(TIMESTAMP, nullable=True)
    status = Column(String(16), nullable=False, default="pending")  # pending/listing/active/expired

    # 评估
    hype_score = Column(Float, nullable=False, default=0.0)
    project_category = Column(String(32), nullable=False, default="unknown")
    team_background = Column(String(32), nullable=False, default="unknown")
    funding_info = Column(Text, nullable=True)               # JSON
    estimated_volatility = Column(Float, nullable=True)

    # 策略
    recommended_strategy = Column(String(32), nullable=False, default="wait_and_see")
    recommended_position_pct = Column(Float, nullable=False, default=0.0)
    stop_loss_pct = Column(Float, nullable=False, default=0.05)
    take_profit_pct = Column(Float, nullable=False, default=0.15)

    # 分析
    ai_analysis = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)

    # 事后验证
    actual_first_day_pnl_pct = Column(Float, nullable=True)
    actual_max_drawdown_pct = Column(Float, nullable=True)
    postmortem = Column(Text, nullable=True)                 # 事后复盘 JSON

    is_active = Column(String(10), nullable=False, default="true")

    __table_args__ = (
        Index('ix_new_coin_symbol_status', 'symbol', 'status'),
    )


class StrategicMemoryRecord(AnalyticsBase):
    """战略级长期记忆"""
    __tablename__ = "strategic_memories"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    memory_type = Column(String(32), nullable=False, index=True)  # macro_lesson/cycle_pattern/new_coin_postmortem/regime_transition
    market_context = Column(Text, nullable=False)
    observation = Column(Text, nullable=False)
    lesson = Column(Text, nullable=False)
    applicability_conditions = Column(Text, nullable=True)   # JSON
    confidence = Column(Float, nullable=False, default=0.5)

    # 验证统计
    times_validated = Column(Integer, nullable=False, default=0)
    times_invalidated = Column(Integer, nullable=False, default=0)
    last_validated_at = Column(TIMESTAMP, nullable=True)

    # 来源与关联
    source = Column(String(16), nullable=False, default="auto")  # auto/human/llm
    related_symbols = Column(Text, nullable=True)            # JSON array
    metadata_json = Column(Text, nullable=True)

    is_active = Column(String(10), nullable=False, default="true")

    __table_args__ = (
        Index('ix_strategic_memory_type_confidence', 'memory_type', 'confidence'),
    )


class CrossMarketCorrelationRecord(AnalyticsBase):
    """跨市场相关性历史"""
    __tablename__ = "cross_market_correlations"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    pair_name = Column(String(32), nullable=False, index=True)  # btc_spx/btc_dxy/btc_csi300
    correlation_7d = Column(Float, nullable=True)
    correlation_30d = Column(Float, nullable=True)
    correlation_90d = Column(Float, nullable=True)
    rolling_beta = Column(Float, nullable=True)
    regime = Column(String(16), nullable=False, default="unknown")  # decoupled/weak_corr/strong_corr
    significance = Column(Float, nullable=True)

    __table_args__ = (
        Index('ix_cross_market_pair_timestamp', 'pair_name', 'timestamp'),
    )


class MacroRegimeStateRecord(AnalyticsBase):
    """持久化宏观周期心智 — 跨 tick/跨天缓慢演化的阶段判断"""

    __tablename__ = "macro_regime_states"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(16), nullable=False, default="GLOBAL", index=True)

    cycle_phase = Column(String(32), nullable=False, default="accumulation")
    prev_phase = Column(String(32), nullable=True)
    phase_confidence = Column(Float, nullable=False, default=0.0)
    direction_constraint = Column(String(32), nullable=False, default="both")

    macro_regime = Column(String(32), nullable=False, default="neutral")
    risk_on_score = Column(Float, nullable=False, default=0.0)

    evidence_json = Column(Text, nullable=True)
    transition_signal = Column(String(8), nullable=False, default="false")

    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    valid_until = Column(TIMESTAMP, nullable=True)
    source = Column(String(64), nullable=False, default="planner+macro+smoothing")

    __table_args__ = (
        Index('ix_macro_regime_symbol_updated', 'symbol', 'updated_at'),
    )


class TrendPredictionRecord(AnalyticsBase):
    """TrendAgent 1-2 周走势预测 — scenario A/B/C 结构化落库与复查对照。"""

    __tablename__ = "trend_prediction_records"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    paper_position_id = Column(Integer, nullable=True, index=True)
    opened_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    lifecycle = Column(String(32), nullable=True)
    scenario_a = Column(Text, nullable=True)
    scenario_b = Column(Text, nullable=True)
    scenario_c = Column(Text, nullable=True)
    phase_at_entry = Column(String(32), nullable=True)
    macro_regime = Column(String(32), nullable=True)
    entry_price = Column(Float, nullable=True)

    review_snapshots_json = Column(Text, nullable=False, default="[]")
    outcome = Column(String(16), nullable=False, default="pending")
    outcome_note = Column(Text, nullable=True)
    closed_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    __table_args__ = (
        Index('ix_trend_pred_position', 'paper_position_id'),
        Index('ix_trend_pred_symbol_opened', 'symbol', 'opened_at'),
    )
