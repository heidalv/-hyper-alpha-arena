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
    """**中长线论题的活表**（`alpha_analytics.mlto_thesis`）。

    ⚠️ [轮113 2026-09-19 归因纪律] 做"仓位 → 论题 → 置信度"的归因时，**必须**查本表，
    不能查 `alpha_arena.brain_theses`：

      · `brain_theses` 是**委员会影子**的落库表，实测最后一行停在 **2026-08-31**
        （1069 行、source 全为 `committee_shadow`），是**冻结快照**；
      · 但它与活表**共用同一批 thesis_id**，所以"positions ⋈ brain_theses"能查出结果、
        却给出 8 月的陈旧 `llm_conviction` —— 轮112 的归因就是这样拿了假数据；
      · 本表在**另一个库**（analytics），跨库 SQL join 不可用 ⇒ 归因要两次查询：
        先从 `paper_positions.exit_state_json.open_metadata.thesis_id` 取 id，
        再用 `AnalyticsSessionLocal` 查本表。
      · 另注：thesis 按 (session, symbol, tier) **复用并原地更新**，所以
        "9 个 thesis 对应 71 笔开仓"是正常的 —— 同一 thesis_id 会服务多笔入场。
    """

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
    # [2026-09-05 LLM 主脑] 开仓/平仓指令与过期时间必须落库，重启后否决闸才有效。
    recommend_open = Column(Integer, nullable=True)
    should_close = Column(Integer, nullable=False, default=0)
    accepted = Column(Integer, nullable=False, default=0)
    expires_at = Column(TIMESTAMP, nullable=True)
    analysis_run_id = Column(String(64), nullable=True)
    # [2026-09-07 OPRO 地基] 主脑 prompt 版本短哈希（0023 迁移加列）；
    # 平仓结算按版本统计胜率，为 prompt 自适应优化提供评分。
    prompt_version = Column(String(32), nullable=True)
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


class MltoEpisode(AnalyticsBase):
    """[2026-09-07] 情景记忆库（海马体式 Episodic Memory）。

    每个论题刷新 = 一个「情景」：写入时快照市场指纹（regime/波动档/趋势方向），
    平仓后回填结局（pnl/持仓时长/平仓原因）。主脑写新论题前检索「相似指纹的
    历史情景及其结局」，让模型看到「上次行情长这样时，做对了还是错了」。
    纯增量新表（create_all 自动建），不动 mlto_thesis 结构。
    """
    __tablename__ = "mlto_episodes"

    id = Column(Integer, primary_key=True, index=True)
    episode_id = Column(String(64), unique=True, nullable=False, index=True)
    thesis_id = Column(String(64), nullable=True, index=True)
    session_id = Column(String(64), nullable=False, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    tier = Column(String(16), nullable=False, index=True)
    direction = Column(String(16), nullable=False, default="neutral")
    # 市场指纹（写入时快照）：regime/vol_bucket/trend_1h/trend_4h/dist_inv_pct
    fingerprint_json = Column(Text, nullable=True)
    accepted = Column(Integer, nullable=False, default=0)
    recommend_open = Column(Integer, nullable=False, default=0)
    # 结局（平仓后回填；None=未开仓或未平仓）
    opened = Column(Integer, nullable=False, default=0)
    outcome_pnl = Column(Float, nullable=True)
    outcome_pct = Column(Float, nullable=True)
    outcome_hold_hours = Column(Float, nullable=True)
    outcome_close_reason = Column(String(100), nullable=True)
    outcome_ts = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    __table_args__ = (
        Index("ix_mlto_episodes_sym_tier_created", "symbol", "tier", "created_at"),
    )


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
