"""MLTO Analytics ORM models."""
from sqlalchemy import JSON, Column, Float, Index, Integer, String, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
from sqlalchemy.sql import func

# [中长线合并修复] jsonb 落库正确类型：模型列声明�?JSON（自动序列化 dict），
# PG �?with_variant 编译�?JSONB（可查询/可索引），SQLite 兜底 TEXT�?
# 直接赋�?dict 即可，杜�?cast(json_str, JSONB) 在真�?PG 上生�?
# CAST(%s::JSONB AS JSONB) + Jsonb 包装参数导致�?INSERT 失败�?
_JSON_OR_TEXT = JSON().with_variant(_PG_JSONB, "postgresql").with_variant(Text, "sqlite")

try:
    from backend.database.connection import AnalyticsBase
except ImportError:
    from backend.database.connection import AnalyticsBase


class MltoThesis(AnalyticsBase):
    __tablename__ = "mlto_thesis"

    id = Column(Integer, primary_key=True, index=True)
    thesis_id = Column(String(64), unique=True, nullable=False, index=True)
    session_id = Column(String(64), nullable=False, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    tier = Column(String(16), nullable=False)
    direction = Column(String(16), nullable=False, default="neutral")
    thesis_summary = Column(Text, nullable=True)
    # [add] reasoning 模型完整思维链快照（区别于精简�?thesis_summary，供复盘/学习）�?
    # 由阶�?捞回�?_reasoning_content 透传写入，上�?6000 字�?
    reasoning_snapshot = Column(Text, nullable=True)
    llm_conviction = Column(Integer, nullable=False, default=0)
    hub_composite = Column(Float, nullable=False, default=0.0)
    hub_adjusted = Column(Float, nullable=False, default=0.0)
    consistency = Column(Float, nullable=False, default=0.0)
    open_readiness = Column(Integer, nullable=False, default=0)
    stable_since = Column(TIMESTAMP, nullable=True)
    review_count = Column(Integer, nullable=False, default=0)
    tranche_stage = Column(Integer, nullable=False, default=0)
    regime_hash = Column(String(64), nullable=True)
    invalidation_json = Column(Text, nullable=True)
    missing_evidence_json = Column(Text, nullable=True)
    owm_weights_json = Column(Text, nullable=True)
    # [阶段2] 中周期子视图 JSON（MidViewDTO 序列化）。PG 下迁移建�?JSONB�?
    # SQLite/老库兜底�?TEXT；None=向后兼容（无 mid_view 分析）�?
    mid_view_json = Column(_JSON_OR_TEXT, nullable=True)
    # [v6 S2-7] regime 参数建议通道落库（校验后 applied dict 序列化）�?
    # PG 下迁移建�?JSONB，SQLite/老库兜底 TEXT；None=LLM 未提供或尚未校验�?
    regime_suggestion_json = Column(_JSON_OR_TEXT, nullable=True)
    # [v6 阶段2 审计�?] LLM exit_plan 止损参数直通落库（0017 迁移加列）�?
    # qual_layer 解析 LLM 输出 �?ThesisDTO.sl_pct/tp_pct �?这里持久化；
    # 0.0/None = LLM 本轮未提供（执行层走 structure_stops 兜底）�?
    sl_pct = Column(Float, nullable=True)
    tp_pct = Column(Float, nullable=True)
    # [v6 4.2] 本次决策注入的回测智�?id 列表（JSON 数组文本）；平仓结算�?
    # 读回用于 evaluate_wisdom_result。None=从未注入�?
    wisdom_ids_json = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    __table_args__ = (
        UniqueConstraint("session_id", "symbol", "tier", name="uq_mlto_thesis_session_sym_tier"),
        Index("ix_mlto_thesis_session", "session_id"),
    )


class MltoMemoryEvent(AnalyticsBase):
    __tablename__ = "mlto_memory_events"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(64), unique=True, nullable=False, index=True)
    thesis_id = Column(String(64), nullable=False, index=True)
    layer = Column(String(16), nullable=False)
    source = Column(String(32), nullable=False)
    signal = Column(String(64), nullable=False)
    summary = Column(Text, nullable=True)
    raw_payload_json = Column(Text, nullable=True)
    recency_score = Column(Float, nullable=False, default=0.0)
    relevancy_score = Column(Float, nullable=False, default=0.0)
    importance_score = Column(Float, nullable=False, default=0.0)
    gamma = Column(Float, nullable=False, default=0.0)
    cited_by_llm = Column(Integer, nullable=False, default=0)
    outcome_pnl = Column(Float, nullable=True)
    ts = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


class MltoThesisEvent(AnalyticsBase):
    __tablename__ = "mlto_thesis_events"

    id = Column(Integer, primary_key=True, index=True)
    thesis_id = Column(String(64), nullable=False, index=True)
    event_type = Column(String(32), nullable=False)
    payload_json = Column(Text, nullable=True)
    ts = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


class MltoSignalWeight(AnalyticsBase):
    __tablename__ = "mlto_signal_weights"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(64), nullable=False, index=True)
    tier = Column(String(16), nullable=False)
    source = Column(String(32), nullable=False)
    weight = Column(Float, nullable=False, default=1.0)
    win_count = Column(Integer, nullable=False, default=0)
    loss_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    __table_args__ = (
        UniqueConstraint("session_id", "tier", "source", name="uq_mlto_owm"),
    )


class MltoDebateLog(AnalyticsBase):
    __tablename__ = "mlto_debate_log"

    id = Column(Integer, primary_key=True, index=True)
    debate_id = Column(String(64), unique=True, nullable=False, index=True)
    thesis_id = Column(String(64), nullable=False, index=True)
    round_num = Column(Integer, nullable=False, default=1)
    side = Column(String(16), nullable=False)
    content_json = Column(Text, nullable=True)
    cited_event_ids_json = Column(Text, nullable=True)
    ts = Column(TIMESTAMP, server_default=func.current_timestamp())
