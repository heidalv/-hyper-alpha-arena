import os
import sys
from typing import Dict, List

from pydantic import BaseModel

# 确保 qaa_architecture_package 可全局导入
_QAA_PKG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "qaa_architecture_package"))
if _QAA_PKG_DIR not in sys.path:
    sys.path.insert(0, _QAA_PKG_DIR)

# 先 .env 再 rollout（与 main.py 一致，pytest 直 import settings 时也尊重 .env）
try:
    from dotenv import load_dotenv
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    load_dotenv(os.path.join(_root, ".env"), override=False)
except Exception:
    pass

# 量化框架升级激进 rollout：env 未显式设置时默认全开（2026-07-09）
try:
    from backend.config.framework_rollout import apply_aggressive_rollout
    apply_aggressive_rollout()
except Exception:
    pass


class MarketConfig(BaseModel):
    market: str
    min_commission: float
    commission_rate: float
    exchange_rate: float
    min_order_quantity: int = 1
    lot_size: int = 1


class HyperliquidBuilderConfig(BaseModel):
    """Hyperliquid Builder Fee Configuration"""
    builder_address: str
    builder_fee: int  # Fee in tenths of basis point (30 = 0.03%)


#  default configs for CRYPTO markets
DEFAULT_TRADING_CONFIGS: Dict[str, MarketConfig] = {
    "CRYPTO": MarketConfig(
        market="CRYPTO",
        min_commission=0.1,  # $0.1 minimum commission for crypto
        commission_rate=0.001,  # 0.1% commission rate (typical for crypto)
        exchange_rate=1.0,  # USD base
        min_order_quantity=1,  # Can trade fractional amounts
        lot_size=1,
    ),
}

# ══════════════════════════════════════════════════
#  全自动交易治理配置
# ══════════════════════════════════════════════════

# 编排器硬门控：当 true 时，编排器 frozen/wait 决策会阻止总控开仓
# 2026-06-17: Paper 默认 false（软建议模式，用于探索/积累样本，AI 可自主决定）。
# 2026-07-06 整改：Live 环境不能沿用这个"探索期"默认值——三周期编排器明显看空时
# 仍允许中线 AI 开多单，属于审查报告 4.5/#17 指出的风险敞口，Live 强制改为 true
# （硬拦截）。这是 Live/Paper 的合理环境差异化，不是同一环境内的新旧兼容开关。
ORCHESTRATOR_HARD_GATE = os.getenv("ORCHESTRATOR_HARD_GATE", "false").lower() == "true"
LIVE_ORCHESTRATOR_HARD_GATE = os.getenv("LIVE_ORCHESTRATOR_HARD_GATE", "true").strip().lower() in (
    "true", "1", "yes", "on",
)


def get_orchestrator_hard_gate(trading_mode: str = "paper") -> bool:
    """按交易模式返回编排器硬门控是否生效：Paper=沿用现有软建议默认值 / Live=强制 true。"""
    if (trading_mode or "paper").strip().lower() == "paper":
        return ORCHESTRATOR_HARD_GATE
    return LIVE_ORCHESTRATOR_HARD_GATE

# 编排器 wait 覆盖阈值：编排器=wait（建议观望）时，DualAgent/总控置信度
# ≥ 此值可覆盖编排器建议（编排器=建议、DualAgent=拍板）。frozen 不可覆盖。
# 统一供 _clean_decisions 与 _orchestrator_blocks_open 两处使用，消除两套 85 口径。
ORCHESTRATOR_WAIT_OVERRIDE_CONF = int(os.getenv("ORCHESTRATOR_WAIT_OVERRIDE_CONF", "85"))

# K线分析师 LLM 模式：
#   all    = 每轮对所有交易币种并行深度分析（币种多易超时）
#   rotate = 每轮只分析一批，其余用缓存（推荐币种 >8 时）
#   off    = 关闭 K 线 LLM
KLINE_ANALYST_MODE = os.getenv("KLINE_ANALYST_MODE", "rotate")

# 趋势仓开仓是否必须有 K 线 LLM 深度结论（非规则回退）
TREND_REQUIRES_KLINE_DEEP: bool = os.getenv(
    "TREND_REQUIRES_KLINE_DEEP", "true"
).strip().lower() in ("true", "1", "yes")

# rotate 模式：每轮新做 LLM 深度分析的币种数（持仓币种优先）
KLINE_ROTATE_BATCH_SIZE = int(os.getenv("KLINE_ROTATE_BATCH_SIZE", "4"))

# K线 LLM 每轮最大调用次数（0=不限制，建议 ≥ 交易币种数）
KLINE_LLM_MAX_PER_CYCLE = int(os.getenv("KLINE_LLM_MAX_PER_CYCLE", "10"))

# K线并行分析线程数
KLINE_ANALYST_MAX_PARALLEL = int(os.getenv("KLINE_ANALYST_MAX_PARALLEL", "5"))

# 总控 LLM 每轮最大调用次数（仅 Master，不含 K线）
LLM_MAX_CALLS_PER_CYCLE = int(os.getenv("LLM_MAX_CALLS_PER_CYCLE", "2"))

# ══════════════════════════════════════════════════
#  K-line LLM 缓存配置 (Tier 1 优化)
# ══════════════════════════════════════════════════
# K线 LLM 结果缓存 TTL（秒），默认 300s = 覆盖一个完整 tick 周期
KLINE_LLM_CACHE_TTL = int(os.getenv("KLINE_LLM_CACHE_TTL", "300"))
# 缓存最大条目数
KLINE_LLM_CACHE_MAX_SIZE = int(os.getenv("KLINE_LLM_CACHE_MAX_SIZE", "50"))
# Symbol 级变更检测阈值：价格变化低于此比例则跳过 LLM（0.15%=更及时）
KLINE_CHANGE_THRESHOLD_PCT = float(os.getenv("KLINE_CHANGE_THRESHOLD_PCT", "0.0015"))
# Symbol 最少间隔几次 tick 后强制刷新（即使价格未变）
KLINE_FORCE_REFRESH_EVERY_N_TICKS = int(os.getenv("KLINE_FORCE_REFRESH_EVERY_N_TICKS", "2"))

# ══════════════════════════════════════════════════
#  QAA v3 分级 tick 配置 (Tier 2A 优化)
# ══════════════════════════════════════════════════
# 每隔 N 个 tick 执行一次完整 AI 分析（含 MasterController LLM）
# 快 tick 仅运行 QAA v3 编排器 + 规则化分析，跳过 LLM（AI 主导模式下见下方 override）
QAA_DEEP_ANALYSIS_EVERY_N_TICKS = int(os.getenv("QAA_DEEP_ANALYSIS_EVERY_N_TICKS", "3"))

# ─────────────────────────────────────────────────────────────────
# QAA 统一调度开关（qaa_scheduler 域注册表总开关 + 域级开关，默认关）
#
# 总开关 QAA_SCHEDULER_ENABLED 控制 run_due_domains 是否执行；各域级开关
# 控制对应域是否参与调度（rebate=套利域 / full_auto=全自动域，各 15 分钟间隔）。
# 默认全部关闭保持历史行为；v6-S2-10c 验证开启：
#   QAA_SCHEDULER_ENABLED=true
#   QAA_REBATE_SCHEDULE_ENABLED=true
#   QAA_FULLAUTO_SCHEDULE_ENABLED=true
# ─────────────────────────────────────────────────────────────────
QAA_SCHEDULER_ENABLED: bool = os.getenv("QAA_SCHEDULER_ENABLED", "false").strip().lower() in (
    "1", "true", "yes", "on",
)
QAA_REBATE_SCHEDULE_ENABLED: bool = os.getenv("QAA_REBATE_SCHEDULE_ENABLED", "false").strip().lower() in (
    "1", "true", "yes", "on",
)
QAA_FULLAUTO_SCHEDULE_ENABLED: bool = os.getenv("QAA_FULLAUTO_SCHEDULE_ENABLED", "false").strip().lower() in (
    "1", "true", "yes", "on",
)

# ══════════════════════════════════════════════════
#  纸面仿真保真分级
# ══════════════════════════════════════════════════

# 仿真模式：demo（当前逻辑）/ research（启用 funding、标记价、MM 阶梯）/ off（纯信号）
PAPER_SIMULATION_TIER = os.getenv("PAPER_SIMULATION_TIER", "demo")

# 模拟盘快速试单：更高 tick 频率、放宽开单门控、加速学习闭环（默认随 ai_first 开启）
_paper_fast_raw = os.getenv("PAPER_FAST_TRIAL", "").strip().lower()
PAPER_FAST_TRIAL: bool = (
    _paper_fast_raw in ("1", "true", "yes", "on")
    if _paper_fast_raw
    else os.getenv("FULLAUTO_FLOW_MODE", "ai_first").strip().lower() == "ai_first"
)
# FullAuto 主循环内学习集成间隔（tick 数）；快速试单下每 tick 触发
FULLAUTO_LEARNING_INTEGRATION_EVERY_N: int = int(
    os.getenv(
        "FULLAUTO_LEARNING_INTEGRATION_EVERY_N",
        "1" if PAPER_FAST_TRIAL else "5",
    )
)

# ══════════════════════════════════════════════════
#  三周期分层 Tick（协调器 vs AI 分析）
# ══════════════════════════════════════════════════
# 协调器 tick：轻量心跳（学习/巡检），不含 LLM；短线走 ScalpRouter 独立循环
TIER_TICK_SCHEDULER_ENABLED: bool = os.getenv("TIER_TICK_SCHEDULER_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
TIER_COORDINATOR_TICK_SEC: int = int(os.getenv("TIER_COORDINATOR_TICK_SEC", "45"))
# 中线 / 长线 AI+MLTO 分析间隔（秒）；勿与协调器 tick 混为一谈
TIER_MID_AI_TICK_SEC: int = int(os.getenv("TIER_MID_AI_TICK_SEC", "45"))
TIER_LONG_AI_TICK_SEC: int = int(os.getenv("TIER_LONG_AI_TICK_SEC", "90"))

# ─────────────────────────────────────────────────────────────────
# 三周期 tier 执行总开关（2026-07-20 新增）
#
# 用于在不改代码、只改 .env 的情况下，按需关闭某个 tier 的 AI 执行。
# - TIER_MID_ENABLED=false  → 中线 SwingAgent 不再调度（仅影响 AI 决策，
#   不影响已开仓位的 TP/SL/退出保护，那些由 paper_trading_engine 兜底）
# - TIER_LONG_ENABLED=false → 长线 TrendAgent + MLTO 长线段不再调度
# - 短线没有开关：ScalpRouter 走独立循环，只要 session running 就会跑
#
# get_due_ai_tiers() 和 coordinator_loop 的回退路径都读这两个开关。
# 默认 true 保持历史行为不变。
# ─────────────────────────────────────────────────────────────────
TIER_MID_ENABLED: bool = os.getenv("TIER_MID_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
TIER_LONG_ENABLED: bool = os.getenv("TIER_LONG_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)

# 资金费率结算周期（秒）
FUNDING_SETTLE_INTERVAL_SEC = int(os.getenv("FUNDING_SETTLE_INTERVAL_SEC", str(8 * 3600)))

# [P0-8] 资金费率结算开关：默认开启，不再与 PAPER_SIMULATION_TIER 绑定。
# FUNDING_SETTLE_APPLY_PNL=false 时仅写 paper_funding_ledger 不动净值（dry-run 观察期），
# 观察两周对拍（纸面 vs 实盘资金费）后改 true 计入 realized_pnl。
FUNDING_SETTLE_ENABLED: bool = os.getenv("FUNDING_SETTLE_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
FUNDING_SETTLE_APPLY_PNL: bool = os.getenv("FUNDING_SETTLE_APPLY_PNL", "false").lower() in (
    "1", "true", "yes", "on",
)

# 维持保证金率（简化固定值），仅 research 模式生效
MAINT_MARGIN_RATIO = float(os.getenv("MAINT_MARGIN_RATIO", "0.005"))

# ══════════════════════════════════════════════════
#  决策链门控与 fallback
# ══════════════════════════════════════════════════

# 编排器门控模式：hard（frozen/wait 时禁止总控开仓）/ soft（仅注入 prompt，不硬拦截）
# 全自动流程：ai_first=每 tick 只跑 AI 决策；legacy=旧版「完整健康检查」大包
FULLAUTO_FLOW_MODE = os.getenv("FULLAUTO_FLOW_MODE", "ai_first").strip().lower()
# 维护周期（策略淘汰/模板/V3/风控），ai_first 下每 N 个 90s tick 跑一次（6≈9分钟）
FULLAUTO_MAINTENANCE_EVERY_N_TICKS = int(os.getenv("FULLAUTO_MAINTENANCE_EVERY_N_TICKS", "6"))
# 交易大脑：true=一轮 Master 统筹三周期（省 3×LLM）；false=三 tier 各跑全套分析师
FULLAUTO_AI_UNIFIED_ANALYSIS = os.getenv("FULLAUTO_AI_UNIFIED_ANALYSIS", "true").lower() in (
    "1", "true", "yes", "on",
)
# AI 主导（全局）：默认关闭；勿与「短线因子+中长线 AI」混用。
_ai_dom_raw = os.getenv("FULLAUTO_AI_DOMINANT", "").strip().lower()
FULLAUTO_AI_DOMINANT: bool = (
    _ai_dom_raw in ("1", "true", "yes", "on")
    if _ai_dom_raw
    else False
)
# 中长线 AI 强制：每 tick 调度 Swing/Trend LLM，不受 tick 限流/编排 create 门控。
_midlong_ai_raw = os.getenv("MIDLONG_AI_MANDATORY", "").strip().lower()
MIDLONG_AI_MANDATORY: bool = (
    _midlong_ai_raw in ("1", "true", "yes", "on")
    if _midlong_ai_raw
    else True  # 2026-07-04 设计默认：Swing/Trend 每 tick 必跑 LLM
)
if MIDLONG_AI_MANDATORY and not os.getenv("QAA_DEEP_ANALYSIS_EVERY_N_TICKS"):
    QAA_DEEP_ANALYSIS_EVERY_N_TICKS = 1

# ══════════════════════════════════════════════════
#  混合信号生成模式（技术指标预筛选 + LLM 最终决策）
# ══════════════════════════════════════════════════
# 仅用于短线 tier 预筛选（见 unified 分析 screen_batch tier=short）；中长线不走因子硬 gate。
HYBRID_SIGNAL_MODE_ENABLED = os.getenv("HYBRID_SIGNAL_MODE", "true").lower() in (
    "1", "true", "yes", "on",
)
PRESCREENER_ENABLED = os.getenv("PRESCREENER_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
# 各 tier 日最低信号目标
MIN_DAILY_SIGNALS = {"short": 5, "mid": 2, "long": 1}
# 频率保障触发小时数（连续无信号 X 小时后开始降低阈值）
SIGNAL_FREQUENCY_GUARD_HOURS = int(os.getenv("SIGNAL_FREQUENCY_GUARD_HOURS", "2"))
# 阈值叠加上限（防止横盘市场叠加到 85%+）
MAX_THRESHOLD_STACK_PCT = {"short": 55, "mid": 60, "long": 65}
# [已废弃 2026-06-13] DYNAMIC_THRESHOLD_ENABLED — 历史假开关。
#   原配套的 dynamic_threshold_manager.py 无任何消费端（已删除）。
#   动态阈值职责已由 decision_core/threshold_resolver.py（统一有效门槛解析）
#   + maturity_controller.py（数据成熟度松紧）接管。保留常量仅为向后兼容，
#   不再有任何代码读取它。
DYNAMIC_THRESHOLD_ENABLED = os.getenv("DYNAMIC_THRESHOLD_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)

# 分析师系统异常 fallback：none（仅记事件，不执行）/ legacy（回退旧路径）
FULLAUTO_ANALYST_FALLBACK = os.getenv("FULLAUTO_ANALYST_FALLBACK", "none")
# 规则化五路分析师（仓位/行情/情报/风险/策略）并行；K 线 LLM 仍单独串行，避免嵌套线程池叠满
ANALYST_RULES_PARALLEL: bool = os.getenv("ANALYST_RULES_PARALLEL", "true").lower() in (
    "1", "true", "yes", "on",
)
ANALYST_RULES_MAX_PARALLEL: int = int(os.getenv("ANALYST_RULES_MAX_PARALLEL", "5"))
# OrchBG 已有新鲜缓存时，tick 内跳过同步 evaluate_portfolio（省 10~15s，与 FULLAUTO_RUN_TRADING_ORCHESTRATOR 可并存）
FULLAUTO_ORCH_SKIP_SYNC_WHEN_CACHE_FRESH: bool = os.getenv(
    "FULLAUTO_ORCH_SKIP_SYNC_WHEN_CACHE_FRESH", "true"
).lower() in ("1", "true", "yes", "on")
ORCHBG_CACHE_FRESH_SEC: int = int(os.getenv("ORCHBG_CACHE_FRESH_SEC", "600"))

# 严格数据门控：无真实 K 线/指标时禁止开仓（靠 SL/TP 管已有仓）
# 2026-06-17: 默认改为 true。编排器在数据缺失时会凭 1h/24h 涨跌幅强行伪造方向(orchestrator:640-659)，
# 与 AI 的 K线分析打架。开启后数据缺失时编排器输出 neutral/0，不臆断方向。
# 如需放宽（允许用涨跌幅近似推断方向），设 STRICT_DATA_GATE=false。
STRICT_DATA_GATE = os.getenv("STRICT_DATA_GATE", "true").lower() in ("1", "true", "yes", "on")
# standard=有真实价格+1h/4h K线即可开仓；strict=还要求指标/快照审计全绿
TRADING_DATA_MODE = os.getenv("TRADING_DATA_MODE", "standard").strip().lower()
# LLM/规则降级时禁止 buy/sell（禁止假数据开仓）
BLOCK_FALLBACK_OPENS = os.getenv("BLOCK_FALLBACK_OPENS", "true").lower() in ("1", "true", "yes", "on")

# ══════════════════════════════════════════════════
#  LLM 超时与稳定性
# ══════════════════════════════════════════════════

# LLM API 调用超时（秒）— 快速模型
LLM_CALL_TIMEOUT_SECONDS = int(os.getenv("LLM_CALL_TIMEOUT_SECONDS", "90"))

# LLM 出网代理（独立于行情代理）。
# 默认空 = 直连 DeepSeek（api.deepseek.com）。注意：行情侧 HTTP_PROXY/HTTPS_PROXY
# 若被设置为本地代理（如 Shadowsocks 127.0.0.1:1080），其 SSE 长连接不稳定，
# 会导致 LLM 流式调用 SSL:UNEXPECTED_EOF_WHILE_READING 中断（约 30s 后空响应）。
# 因此 LLM 调用**不再继承行情代理**；确需代理访问时才在此显式配置。
LLM_HTTP_PROXY = os.getenv("LLM_HTTP_PROXY", "").strip()
LLM_HTTPS_PROXY = os.getenv("LLM_HTTPS_PROXY", "").strip()

# 深度推理模型（reasoner / thinking / r1）单次调用超时（秒，仅非流式）
LLM_CALL_TIMEOUT_DEEP_SECONDS = int(os.getenv("LLM_CALL_TIMEOUT_DEEP_SECONDS", "240"))

# 流式推理防挂死上限（秒）。
# 180s 为模拟盘默认（深度推理模型如 DeepSeek v4-flash 需要更长推理时间）；
# 原 120s 反复触发 safety cap 导致 LLM JSON 解析失败。深度模型可通过 env 调高。
LLM_STREAM_SAFETY_CAP_SECONDS = float(os.getenv("LLM_STREAM_SAFETY_CAP_SECONDS", "180"))

# 全局同步 LLM 并发上限（BackgroundScheduler 线程池保护）
# DeepSeek 官方并发限制: v4-pro=500, v4-flash=2500（现统一用 flash）。
# 系统日调用几千次，远低于限制，不需要本地并发槽限制。
# 原 LLM_GLOBAL_MAX_CONCURRENT=3 导致辅助分析占满槽位，SwingAgent/TrendAgent
# 排队超时 → conf=0% → 中长线不开仓。设为 0 表示不限制。
LLM_GLOBAL_MAX_CONCURRENT = int(os.getenv("LLM_GLOBAL_MAX_CONCURRENT", "0"))
LLM_SEMAPHORE_WAIT_SECONDS = float(os.getenv("LLM_SEMAPHORE_WAIT_SECONDS", "30"))
# LLM 并发：默认关闭人为分桶限流（各账户自备 Key，并行只受机器资源约束）
# 设 LLM_BUDGET_ENABLED=true 且数值>0 才启用限流；0=不限制
LLM_BUDGET_ENABLED: bool = os.getenv("LLM_BUDGET_ENABLED", "false").strip().lower() in (
    "1", "true", "yes", "on",
)
LLM_BUDGET_MIDLONG: int = int(os.getenv("LLM_BUDGET_MIDLONG", "0"))
LLM_BUDGET_MASTER: int = int(os.getenv("LLM_BUDGET_MASTER", "0"))
LLM_BUDGET_SCALP: int = int(os.getenv("LLM_BUDGET_SCALP", "0"))
LLM_BUDGET_OTHER: int = int(os.getenv("LLM_BUDGET_OTHER", "0"))
LLM_BUDGET_WAIT_MIDLONG: float = float(os.getenv("LLM_BUDGET_WAIT_MIDLONG", "45"))
LLM_BUDGET_WAIT_MASTER: float = float(os.getenv("LLM_BUDGET_WAIT_MASTER", "15"))
LLM_BUDGET_WAIT_SCALP: float = float(os.getenv("LLM_BUDGET_WAIT_SCALP", "20"))
LLM_BUDGET_WAIT_OTHER: float = float(os.getenv("LLM_BUDGET_WAIT_OTHER", "10"))
# 多账户租户槽：默认关闭（与平台分桶一并关闭）；需要时再开
LLM_BUDGET_PER_TENANT: bool = os.getenv("LLM_BUDGET_PER_TENANT", "false").strip().lower() in (
    "1", "true", "yes", "on",
)
LLM_BUDGET_TENANT_MIDLONG: int = int(os.getenv("LLM_BUDGET_TENANT_MIDLONG", "0"))
LLM_BUDGET_TENANT_MASTER: int = int(os.getenv("LLM_BUDGET_TENANT_MASTER", "0"))
LLM_BUDGET_TENANT_SCALP: int = int(os.getenv("LLM_BUDGET_TENANT_SCALP", "0"))
LLM_BUDGET_TENANT_OTHER: int = int(os.getenv("LLM_BUDGET_TENANT_OTHER", "0"))
# 选币并行：0=按当前 session 数全开
AUTO_COIN_PARALLEL_SESSIONS: int = int(os.getenv("AUTO_COIN_PARALLEL_SESSIONS", "0"))
# APScheduler 线程池（多账户 tick）
SCHEDULER_MAX_WORKERS: int = int(os.getenv("SCHEDULER_MAX_WORKERS", "64"))
# 审计 JSONL 轮转 / 日志清理
AUDIT_JSONL_MAX_BYTES: int = int(os.getenv("AUDIT_JSONL_MAX_BYTES", str(20 * 1024 * 1024)))
AUDIT_JSONL_BACKUP_COUNT: int = int(os.getenv("AUDIT_JSONL_BACKUP_COUNT", "5"))
LOG_RETENTION_DAYS: int = int(os.getenv("LOG_RETENTION_DAYS", "30"))
REPORT_RETENTION_DAYS: int = int(os.getenv("REPORT_RETENTION_DAYS", "60"))
AI_DECISION_LOG_RETENTION_DAYS: int = int(os.getenv("AI_DECISION_LOG_RETENTION_DAYS", "90"))
# 数据中心采集通道预留（P0 优先）
KLINE_P0_CONCURRENCY: int = int(os.getenv("KLINE_P0_CONCURRENCY", "16"))
KLINE_P1_CONCURRENCY: int = int(os.getenv("KLINE_P1_CONCURRENCY", "4"))
KLINE_P1_HL_CONCURRENCY: int = int(os.getenv("KLINE_P1_HL_CONCURRENCY", "3"))
# 禁止公用/平台默认 LLM：每个账户必须自备 Key，交易链路不得回退别人或 env 种子
FORBID_SHARED_PLATFORM_LLM: bool = os.getenv(
    "FORBID_SHARED_PLATFORM_LLM", "true"
).strip().lower() in ("1", "true", "yes", "on")
# VIP 共用 AI 选币：管理员租户 LLM（仅 coin_select 用途；交易仍禁止公用）
COIN_SELECT_ADMIN_TENANT_ID: int = int(os.getenv("COIN_SELECT_ADMIN_TENANT_ID", "0") or "0")
COIN_SELECT_PLATFORM_ENABLED: bool = os.getenv(
    "COIN_SELECT_PLATFORM_ENABLED", "true"
).strip().lower() in ("1", "true", "yes", "on")
# 会话 AutoCoin：platform_board=只跟投管理员 VIP 短线看板（统一默认）；legacy=旧独立扫描
AUTO_COIN_SOURCE: str = os.getenv("AUTO_COIN_SOURCE", "platform_board").strip().lower()
COIN_SELECT_SCAN_INTERVAL_SEC: int = int(os.getenv("COIN_SELECT_SCAN_INTERVAL_SEC", "1800"))
COIN_SELECT_AI_MAX_CANDIDATES: int = int(os.getenv("COIN_SELECT_AI_MAX_CANDIDATES", "15"))
COIN_SELECT_BOARD_TTL_HOURS: int = int(os.getenv("COIN_SELECT_BOARD_TTL_HOURS", "12"))
# ── CoinRank 共用排序内核（docs/AI选币全面升级设计_2026-08.md）──
COIN_RANK_ENGINE_ENABLED: bool = os.getenv(
    "COIN_RANK_ENGINE_ENABLED", "true"
).strip().lower() in ("1", "true", "yes", "on")
COIN_RANK_GATES_ENABLED: bool = os.getenv(
    "COIN_RANK_GATES_ENABLED", "true"
).strip().lower() in ("1", "true", "yes", "on")
COIN_RANK_FEEDBACK_ENABLED: bool = os.getenv(
    "COIN_RANK_FEEDBACK_ENABLED", "true"
).strip().lower() in ("1", "true", "yes", "on")
COIN_RANK_TRAP_SOFT_REJECT: float = float(os.getenv("COIN_RANK_TRAP_SOFT_REJECT", "0.55"))
COIN_RANK_TRAP_HARD_REJECT: float = float(os.getenv("COIN_RANK_TRAP_HARD_REJECT", "0.85"))
COIN_RANK_MTF_MIN_STRONG: float = float(os.getenv("COIN_RANK_MTF_MIN_STRONG", "0.5"))
COIN_RANK_FEEDBACK_INTERVAL_SEC: int = int(os.getenv("COIN_RANK_FEEDBACK_INTERVAL_SEC", "900"))
# midlong 独立循环相对 Master 主循环的首跑错峰（秒）
MIDLONG_MASTER_STAGGER_SEC: int = int(os.getenv("MIDLONG_MASTER_STAGGER_SEC", "22"))

# QAA run_full_analysis 外层超时（秒，仅快速非流式模型）；0=自动估算
QAA_ANALYST_TIMEOUT_S = float(os.getenv("QAA_ANALYST_TIMEOUT_S", "0"))

# 流式深度分析的外层防挂死（秒）。
# 必须大于内层单次流式上限（LLM_STREAM_SAFETY_CAP_SECONDS×币种数），并为多币种整轮分析留预算。
# 原 480s 在多币种+深度推理时频繁触发整轮超时降级为 hold。
QAA_ANALYST_STREAM_SAFETY_CAP_S = float(os.getenv("QAA_ANALYST_STREAM_SAFETY_CAP_S", "900"))

# 全自动统一分析：true=强制跳过 LLM（仅调试）；false=先深度分析，超时/失败再降级 hold
FULLAUTO_FAST_DECISION_MODE: bool = os.getenv("FULLAUTO_FAST_DECISION_MODE", "false").lower() in (
    "1", "true", "yes", "on",
)

# 自动计算外层超时的上限（秒），防止单 tick 无限阻塞
# 原 900s 在深度推理模型下会截断正常分析，提高到 1800s 给 LLM 足够时间
QAA_ANALYST_TIMEOUT_MAX_S = float(os.getenv("QAA_ANALYST_TIMEOUT_MAX_S", "1800"))


def compute_qaa_analyst_timeout(symbol_count: int = 1, account_id=None) -> float:
    """快速非流式模型的外层超时预算。深度流式请用 should_use_llm_streaming + [DONE]。"""
    if QAA_ANALYST_TIMEOUT_S > 0:
        return QAA_ANALYST_TIMEOUT_S

    try:
        from backend.services.llm_config_service import (
            get_llm_config_for_analysis,
            resolve_llm_call_timeout,
        )
        cfg = get_llm_config_for_analysis(account_id)
        per_call = resolve_llm_call_timeout(cfg)
        # QAA 真实 LLM 分析走深度档；外层预算不得低于深度推理单次上限
        per_call = max(per_call, float(LLM_CALL_TIMEOUT_DEEP_SECONDS))
    except Exception:
        per_call = max(float(LLM_CALL_TIMEOUT_DEEP_SECONDS), float(LLM_CALL_TIMEOUT_SECONDS))

    kline_budget = 0.0
    if KLINE_ANALYST_MODE in ("all", "rotate") and symbol_count > 0:
        workers = max(KLINE_ANALYST_MAX_PARALLEL, 1)
        if KLINE_ANALYST_MODE == "rotate":
            effective = min(symbol_count, KLINE_ROTATE_BATCH_SIZE)
        else:
            effective = symbol_count
        if KLINE_LLM_MAX_PER_CYCLE > 0:
            effective = min(effective, KLINE_LLM_MAX_PER_CYCLE)
        import math
        batches = math.ceil(effective / workers)
        kline_budget = batches * per_call

    master_retries = int(os.getenv("MASTER_LLM_MAX_RETRIES", "0"))
    master_budget = per_call * (1 + master_retries)
    overhead = 30.0
    total = max(kline_budget + master_budget + overhead, per_call * 2 + overhead)
    return min(total, QAA_ANALYST_TIMEOUT_MAX_S)

# ══════════════════════════════════════════════════
#  动态止盈止损 v2
# ══════════════════════════════════════════════════

# 利润保护版本：v1（旧追踪止损） / v2（TP进度分层保护）— 默认 v2
PROFIT_PROTECTION_VERSION = os.getenv("PROFIT_PROTECTION_VERSION", "v2")

# 峰值利润最大回撤比例（v2）— 从峰值利润回撤多少触发保护平仓
PROFIT_PROTECTION_DRAWDOWN = float(os.getenv("PROFIT_PROTECTION_DRAWDOWN", "0.50"))

# 紧急全平回撤比例（v2）
PROFIT_PROTECTION_EMERGENCY = float(os.getenv("PROFIT_PROTECTION_EMERGENCY", "0.65"))

# [已废弃] 固定美元激活门槛不适合小资金。v2 使用 TIER_PROTECTION_PARAMS.lock_min_margin_pct
PROFIT_PROTECTION_ACTIVATION_USD = float(os.getenv("PROFIT_PROTECTION_ACTIVATION_USD", "0.0"))

# ══════════════════════════════════════════════════
#  利润保护 · 最短持仓 & 同向冷却
# ══════════════════════════════════════════════════

# 利润保护最短持仓时间（秒）：低于此时间不触发分批锁利/回撤保护（被 TIER_PROTECTION_PARAMS 覆盖）
PROFIT_PROTECTION_MIN_HOLD_SEC = int(os.getenv("PROFIT_PROTECTION_MIN_HOLD_SEC", "600"))

# 同向再开仓冷却时间（秒）（被 TIER_PROTECTION_PARAMS 覆盖）
REENTRY_COOLDOWN_SECONDS = int(os.getenv("REENTRY_COOLDOWN_SECONDS", "600"))  # 默认 10 分钟

# ══════════════════════════════════════════════════
#  全周期交易 · Tier 差异化保护参数
# ══════════════════════════════════════════════════
#
# [三周期持仓时间收敛 2026-08-13]
# 持仓复审点的唯一权威 = data/runtime_tuning.json 的 tier_max_hold_sec
# （resolve_tier_review_seconds 优先读 runtime_tuning，此处 max_hold_sec 仅作回退）。
# 当前权威值：short=7200s(2h) / mid=172800s(48h) / long=604800s(7d)。
# min_hold_sec 的权威 = 本表（min_hold 不参与 runtime_tuning 热调），
# 且必须与 unified_exit_state_machine.TIER_PROTECTION、TIER_PROMPT_HINTS 保持一致。

TIER_PROTECTION_PARAMS = {
    "short": {
        "min_hold_sec":        int(os.getenv("TIER_SHORT_MIN_HOLD_SEC", "3600")),      # 1 hour（日内单最少持仓1小时，不是10分钟）
        "max_hold_sec":        int(os.getenv("TIER_SHORT_MAX_HOLD_SEC", "43200")),     # 12 hours（日内单最多持仓到当日结束）
        "lock_stages":         1,
        "lock_tp_progress":   [0.80],
        "lock_close_pct":     [0.30],
        "lock_sl_to_progress": [0.60],
        "lock_min_margin_pct": [0.50],
        "breakeven_tp_progress": 0.85,  # [2026-07-30 crypto-native] 0.70→0.85 等85%才推保本，避免微利就走
        "drawdown_emergency":  0.80,                                                   # 80% 回撤才紧急平仓
        "drawdown_protect":    0.65,                                                   # 65% 回撤保护
        "drawdown_activate":   1.50,                                                   # 利润达保证金150%才激活
        "min_hold_emergency_loss_pct": float(os.getenv("TIER_SHORT_MIN_HOLD_EMERGENCY_LOSS_PCT", "8")),  # 保护期内允许 close 的保证金亏损%（正数）
        "tight_trail_start":   0.90,
        "cooldown_sec":        int(os.getenv("TIER_SHORT_COOLDOWN_SEC", "14400")),     # 原 900(15min) → 14400(4h)，与 SHORT_TIER_SAME_DIR_COOLDOWN_S 对齐，防止短线频繁重开
    },
    "mid": {
        # [2026-07-17 修复] 此前 30min 与 TIER_PROMPT_HINTS["mid"] 承诺 AI 的
        # "至少12h内不得主动全平"完全不符——master controller 实际上30分钟就能
        # 强制平掉中线仓位，导致"中线"名不副实。改为 12h，与 prompt 承诺对齐。
        "min_hold_sec":        int(os.getenv("TIER_MID_MIN_HOLD_SEC", "43200")),        # 12 hours（与 prompt 承诺对齐）
        "max_hold_sec":        int(os.getenv("TIER_MID_MAX_HOLD_SEC", "172800")),      # 48 hours (波段中线，2026-06-28 恢复)
        "lock_stages":         2,
        "lock_tp_progress":   [0.70, 0.90],
        "lock_close_pct":     [0.25, 0.30],
        "lock_sl_to_progress": [0.50, 0.70],
        "lock_min_margin_pct": [0.50, 0.70],
        "breakeven_tp_progress": 0.65,
        "drawdown_emergency":  0.80,                                                   # 80% 回撤才紧急平仓
        "drawdown_protect":    0.65,                                                   # 65% 回撤保护
        "drawdown_activate":   2.00,                                                   # 利润达保证金200%才激活
        "min_hold_emergency_loss_pct": float(os.getenv("TIER_MID_MIN_HOLD_EMERGENCY_LOSS_PCT", "6")),
        "tight_trail_start":   0.92,
        "cooldown_sec":        int(os.getenv("TIER_MID_COOLDOWN_SEC", "1800")),        # 30 min
    },
    "long": {
        # [2026-07-17 修复] 此前 2h 的最短保护期离"长线"名不副实——TrendAgent/MLTO
        # 周期用 4h~1d K线判断趋势，2小时内趋势根本不可能走完一段，但 master
        # controller 却能在 2h 就把仓位强平掉。用户反馈"长线至少要3天以上才算长线"，
        # 改为 72h(3天)，与 max_hold_sec=7天 组成 [3天,7天] 的合理长线持仓区间。
        # 注：此保护只挡 master 主动 close/reduce，不影响 SL/TP 硬止损止盈的正常触发。
        "min_hold_sec":        int(os.getenv("TIER_LONG_MIN_HOLD_SEC", "259200")),      # 72 hours = 3 days
        "max_hold_sec":        int(os.getenv("TIER_LONG_MAX_HOLD_SEC", "604800")),     # 7 days (长线，与 position/trend_follow 预期一致)
        "lock_stages":         1,
        "lock_tp_progress":   [0.90],
        "lock_close_pct":     [0.25],
        "lock_sl_to_progress": [0.65],
        "lock_min_margin_pct": [0.60],
        "breakeven_tp_progress": 0.50,
        "drawdown_emergency":  0.80,
        "drawdown_protect":    0.65,
        "drawdown_activate":   3.00,                                                   # 利润达保证金300%才激活
        "min_hold_emergency_loss_pct": float(os.getenv("TIER_LONG_MIN_HOLD_EMERGENCY_LOSS_PCT", "5")),
        "tight_trail_start":   0.95,
        "cooldown_sec":        int(os.getenv("TIER_LONG_COOLDOWN_SEC", "14400")),      # 4 hour
    },
    # [三周期持仓时间收敛 2026-08-13] 研究车道独立 tier（NATURE_TO_TIER:
    # pair_research/research→research）：与 mid 统计/冷却/门控完全隔离。
    # 2h 固定上限（=复审点=绝对天花板，禁 AI 延长），TP/SL 分钟级退出走
    # Tier0 直通不受 min_hold 影响，故 min_hold_sec=0（无保护期）。
    "research": {
        "min_hold_sec":        0,                                                     # 无保护期（研究仓退出全走 Tier0 直通）
        "max_hold_sec":        int(os.getenv("TIER_RESEARCH_MAX_HOLD_SEC", "7200")),  # 2 hours（研究车道固定上限）
        "min_hold_emergency_loss_pct": 5.0,
    },
}

# 统一退出执行器（P1）：false 回退旧 inline 门控逻辑
UNIFIED_EXIT_EXECUTOR_ENABLED: bool = os.getenv(
    "UNIFIED_EXIT_EXECUTOR_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")

# Tier-aware TP/SL defaults (when AI doesn't set them)
# V5 经济学重构（2026-06）：修复盈亏比倒挂
# 此前 short 1:1、mid 1.2:1 → 平均盈利 +320 vs 平均亏损 -9070 的根因之一
# 现在短线默认约 1.5:1（ATR 自适应）；中长线仍偏 ≥1.8:1。
# 硬约束按 nature 拆分，见 V5_SCALP_MIN_RR / V5_TREND_MIN_RR。
#
# Fix 3 (2026-06-23): 短线 TP/SL 改为 ATR 自适应。
# DB 证实固定 2.5% SL 在高波动币(ASTER)上被频繁扫损；
# 改为基于 ATR 动态计算，波动大的币给宽止损，波动小的给紧止损。
# use_atr=True 时，实际 TP/SL = max(ATR_MULT × ATR%, MIN/MAX_PCT)
TIER_TP_SL_DEFAULTS = {
    "short": {
        "tp_pct": float(os.getenv("TIER_SHORT_TP_PCT", "0.018")),   # 1.8%（实测1-2%胜率73%）
        "sl_pct": float(os.getenv("TIER_SHORT_SL_PCT", "0.012")),   # 1.2%（RR=1.5）
        "use_atr": os.getenv("TIER_SHORT_USE_ATR", "true").lower() in ("1", "true", "yes"),
        "atr_sl_mult": float(os.getenv("TIER_SHORT_ATR_SL_MULT", "1.5")),   # SL = 1.5×ATR%（币圈常用≥1.5×）
        "atr_tp_mult": float(os.getenv("TIER_SHORT_ATR_TP_MULT", "2.0")),  # TP = 2.0×ATR%
        "min_sl_pct": float(os.getenv("TIER_SHORT_MIN_SL", "0.010")),  # SL 最小 1.0%（平静市 ATR 地板）
        "max_sl_pct": float(os.getenv("TIER_SHORT_MAX_SL", "0.020")),  # SL 最大 2%（避开死亡区间）
        "min_tp_pct": float(os.getenv("TIER_SHORT_MIN_TP", "0.015")),  # TP 最小 1.5%
        "max_tp_pct": float(os.getenv("TIER_SHORT_MAX_TP", "0.025")),  # TP 最大 2.5%
    },
    "mid": {
        "tp_pct": float(os.getenv("TIER_MID_TP_PCT", "0.07")),      # 7% TP
        "sl_pct": float(os.getenv("TIER_MID_SL_PCT", "0.035")),     # 3.5% SL → 2:1
        "use_atr": os.getenv("TIER_MID_USE_ATR", "false").lower() in ("1", "true", "yes"),
        "atr_sl_mult": 1.8,
        "atr_tp_mult": 3.6,
        "min_sl_pct": 0.02,
        "max_sl_pct": 0.05,
        "min_tp_pct": 0.04,
        "max_tp_pct": 0.09,
    },
    "long": {"tp_pct": 0.0, "sl_pct": 0.0, "use_atr": False},   # ATR 动态止损，无固定 TP
}

# Fix 3: 短线最小持仓时间保护（秒）
# DB 证实持仓<300s 的42笔净亏-2611——秒进秒出多半是滑点/噪音触发。
# 此值以下的仓位不得因 SL 平仓（除非硬止损如爆仓边界），给行情发展时间。
SHORT_MIN_HOLD_BEFORE_SL_SEC: int = int(os.getenv("SHORT_MIN_HOLD_BEFORE_SL_SEC", "180"))

# 金字塔/滚仓参数（按 tier 分档）
# ScalpRouter 独立路径最大加仓次数（短线因子引擎专用，有别于 AI 金字塔）
SCALP_ROUTER_MAX_ADDS = 2

# ScalpRouter 开仓冷却（秒）：同一 symbol 开仓后，在此时间内禁止再开。
# 这是当前系统唯一缺失的"开仓后冷却"（平仓冷却 reentry_cooldown 已有）。
# 2026-06-22: 修复短线无限制频繁开单 —— turbo 档 45s/tick 下原本每 tick 都能开，
# 现在强制最少 N 秒间隔。默认 300s(5分钟)，即同 symbol 每小时最多 12 次。
SCALP_OPEN_COOLDOWN_SEC: int = int(os.getenv("SCALP_OPEN_COOLDOWN_SEC", "300"))
# 同 symbol 同向开仓冷却（比通用更严，防止单边反复加码后立刻重来）
SCALP_OPEN_SAME_SIDE_COOLDOWN_SEC: int = int(os.getenv("SCALP_OPEN_SAME_SIDE_COOLDOWN_SEC", "600"))
# ScalpRouter 全局每 tick 最大开仓数（防止一波行情所有币齐开）
SCALP_MAX_OPENS_PER_TICK: int = int(os.getenv("SCALP_MAX_OPENS_PER_TICK", "1"))
# 短线因子独立调度（与 AI 主循环解耦，避免 LLM 分析阻塞因子扫描）
# true=单独 APScheduler job；false=仍挂在统一循环开头（旧行为）
SCALP_FACTOR_INDEPENDENT_SCHEDULER: bool = os.getenv(
    "SCALP_FACTOR_INDEPENDENT_SCHEDULER", "true"
).lower() in ("true", "1", "yes", "on")
# 因子扫描间隔（秒）；0=跟随 PaperPace tick_seconds
SCALP_FACTOR_SCAN_INTERVAL_SEC: int = int(os.getenv("SCALP_FACTOR_SCAN_INTERVAL_SEC", "45"))
# 中线/长线 Agent 独立调度（与 QAA 主循环解耦，避免 900s 分析阻塞 mid/long）
MIDLONG_AGENT_INDEPENDENT_SCHEDULER: bool = os.getenv(
    "MIDLONG_AGENT_INDEPENDENT_SCHEDULER", "true"
).lower() in ("true", "1", "yes", "on")
# 主循环 mid/long 新开委托给独立循环（与 SCALP_MASTER_HARD_BLOCK 对称，避免双 LLM/双开）
MIDLONG_MASTER_DELEGATE: bool = os.getenv(
    "MIDLONG_MASTER_DELEGATE", "true"
).lower() in ("true", "1", "yes", "on")
# Live 宪法级风控：开单前/会话 tick 强制走 risk_control_service（Paper 不受影响）
LIVE_CONSTITUTIONAL_RISK_ENABLED: bool = os.getenv(
    "LIVE_CONSTITUTIONAL_RISK_ENABLED", "true"
).lower() in ("true", "1", "yes", "on")
# 因子开单门槛（score=|direction|×100，一次项）
# 探索：≥CONFIRM 可开单；直通：≥EXECUTE 更高置信
# ScalpExecutionLane 分层门槛：≥DIRECT 直通；VETO_BAND~DIRECT 走 5s Flash Veto；<VETO_BAND 不开
SCALP_FACTOR_CONFIRM_THRESHOLD: int = int(os.getenv("SCALP_FACTOR_CONFIRM_THRESHOLD", "35"))
SCALP_FACTOR_EXECUTE_THRESHOLD: int = int(os.getenv("SCALP_FACTOR_EXECUTE_THRESHOLD", "45"))
SCALP_DIRECT_THRESHOLD: int = int(os.getenv("SCALP_DIRECT_THRESHOLD", "45"))
SCALP_VETO_BAND_LOW: int = int(os.getenv("SCALP_VETO_BAND_LOW", "35"))
SCALP_EXECUTION_LANE_ENABLED: bool = os.getenv(
    "SCALP_EXECUTION_LANE_ENABLED", "true"
).lower() in ("true", "1", "yes", "on")
SCALP_VETO_MODE: str = os.getenv("SCALP_VETO_MODE", "tiered")  # tiered / off
SCALP_VETO_TIMEOUT_S: int = int(os.getenv("SCALP_VETO_TIMEOUT_S", "5"))
# Paper 基线：保持 fail-open（LLM 超时/异常时放行边缘信号），目的是模拟盘持续
# 积累样本，不因链路抖动被迫大量 hold；旧调用点未传 trading_mode 时按此值兼容。
SCALP_VETO_FAIL_OPEN: bool = os.getenv("SCALP_VETO_FAIL_OPEN", "true").lower() in (
    "true", "1", "yes", "on",
)
# Live 基线：改为 fail-closed。真实资金环境下"看不清就放行"等价于放弃这道闸，
# 与 AKIVA-AI 等生产级系统"任何安全检查异常=停止交易"的 fail-closed 公理保持一致。
LIVE_SCALP_VETO_FAIL_OPEN: bool = os.getenv(
    "LIVE_SCALP_VETO_FAIL_OPEN", "false"
).strip().lower() in ("true", "1", "yes", "on")


def get_scalp_veto_fail_open(trading_mode: str = "paper") -> bool:
    """按交易模式返回 Scalp Flash Veto 超时/异常时是否 fail-open：Live=false / Paper=true。"""
    if (trading_mode or "paper").strip().lower() == "paper":
        return SCALP_VETO_FAIL_OPEN
    return LIVE_SCALP_VETO_FAIL_OPEN
SCALP_MASTER_HARD_BLOCK: bool = os.getenv("SCALP_MASTER_HARD_BLOCK", "true").lower() in (
    "true", "1", "yes", "on",
)
ORCH_BG_INTERVAL_SEC: int = int(os.getenv("ORCH_BG_INTERVAL_SEC", "600"))
SCALP_STRUCTURE_SL_BUFFER_PCT: float = float(os.getenv("SCALP_STRUCTURE_SL_BUFFER_PCT", "0.008"))
SCALP_RANGE_MAX_LONG: float = float(os.getenv("SCALP_RANGE_MAX_LONG", "0.72"))
SCALP_RANGE_MIN_SHORT: float = float(os.getenv("SCALP_RANGE_MIN_SHORT", "0.28"))
# 编排器方向与因子方向冲突时，long/short 最低 effective_score（ScalpExecutionGate）
SCALP_ORCH_CONFLICT_MIN_SCORE: int = int(os.getenv("SCALP_ORCH_CONFLICT_MIN_SCORE", "50"))
# 短线因子 IC 权重回写频率（独立调度 tick 数；80×45s≈1h）
SCALP_FACTOR_IC_EVAL_EVERY_N_TICKS: int = int(os.getenv("SCALP_FACTOR_IC_EVAL_EVERY_N_TICKS", "80"))

# ─────────────────────────────────────────────────────────────────
# 短线转正 · 阶段一：手续费感知期望值闸门 + 置信度校准（2026-07-08）
# 根因：短线开仓只看"因子分是否过线"，从不校验"扣掉往返手续费+滑点后是否还有
# 正的数学期望"。往返成本 ~0.17%+滑点，胜率仅 ~42%，结构性负期望。
# 详见 docs 短线因子策略转正方案。全程 flag 门控，默认开启，可秒回滚。
# ─────────────────────────────────────────────────────────────────
# EV 闸门总开关（下单前计算 EV=p_win×tp−(1−p_win)×sl−往返成本，<EV_MIN 拦截）
SCALP_EV_GATE_ENABLED: bool = os.getenv("SCALP_EV_GATE_ENABLED", "true").lower() in (
    "true", "1", "yes", "on",
)
# EV 放行的最低净期望（notional 口径）。默认 0.03% 轻微过滤；0=几乎不拦。
SCALP_EV_MIN_PCT: float = float(os.getenv("SCALP_EV_MIN_PCT", "0.0003"))
# Live 下 EV 评估异常时 fail-closed；Paper 仍 fail-open 保样本
SCALP_EV_FAIL_CLOSED_LIVE: bool = os.getenv(
    "SCALP_EV_FAIL_CLOSED_LIVE", "true"
).lower() in ("true", "1", "yes", "on")
# TP/SL 实现率：真实交易很少吃满计划 TP（分批/追踪/超时），亏损往往吃满甚至更多。
# EV 用 tp_pct×TP实现率 作为期望盈利幅度、sl_pct×SL实现率 作为期望亏损幅度，更贴近实盘。
SCALP_EV_TP_REALIZATION: float = float(os.getenv("SCALP_EV_TP_REALIZATION", "0.55"))
SCALP_EV_SL_REALIZATION: float = float(os.getenv("SCALP_EV_SL_REALIZATION", "1.0"))
# 置信度校准器：用历史 scalp_composite 反馈把因子分映射成校准胜率 p_win。
SCALP_CALIBRATOR_ENABLED: bool = os.getenv("SCALP_CALIBRATOR_ENABLED", "true").lower() in (
    "true", "1", "yes", "on",
)
# 校准所需最小样本数；不足则回退到锚定基础胜率的线性映射（冷启动）。
SCALP_CALIBRATOR_MIN_SAMPLES: int = int(os.getenv("SCALP_CALIBRATOR_MIN_SAMPLES", "20"))
# 校准回看天数 + 结果缓存 TTL（秒）。
SCALP_CALIBRATOR_LOOKBACK_DAYS: int = int(os.getenv("SCALP_CALIBRATOR_LOOKBACK_DAYS", "30"))
SCALP_CALIBRATOR_CACHE_TTL_SEC: int = int(os.getenv("SCALP_CALIBRATOR_CACHE_TTL_SEC", "600"))

# ─────────────────────────────────────────────────────────────────
# 中长线信号质量 · S1-1 通用置信度校准器（2026-07-08）
# 把 swing/trend 的 LLM 打分用历史真实战绩校准成胜率 p_win（PAVA 保序回归），
# 供 EV 闸门与统一口径使用。中长线成交稀疏 → 回看更长、min_samples 更低。
# ─────────────────────────────────────────────────────────────────
# 总开关：一键回滚整个中长线校准（关闭后 estimate_p_win 走线性映射）。
MIDLONG_CALIBRATOR_ENABLED: bool = os.getenv("MIDLONG_CALIBRATOR_ENABLED", "true").lower() in (
    "true", "1", "yes", "on",
)
# 中线 swing 校准器
# [阶段4 — 部分弃用] SwingAgent 独立分析路径已删除（mid_view 接管中线），
# 但 SWING_CALIBRATOR / SWING_EV 配置仍保留：trade_nature="swing" 的存量仓位与
# 历史 feedback 样本仍会经此校准路径，删除会破坏 confidence_calibrator 的样本类型
# 分桶（"swing_agent_score"）。新代码不应再产出 swing 校准样本。
SWING_CALIBRATOR_ENABLED: bool = os.getenv("SWING_CALIBRATOR_ENABLED", "true").lower() in (
    "true", "1", "yes", "on",
)
# [2026-07-20 修复 — 中线连续多日 0 成交根因之一] 原阈值 30 太低：实测样本刚过 30
# (n=36) 就被判定"已校准"，但每个分数桶(10分一档)的保序回归只要求≥3笔就参与拟合，
# 3笔胜率(0%/33%/67%/100%)方差极大，且这36笔恰好覆盖了这周逐个修复的多个历史bug
# (parity_score误杀冻结/exit_plan硬校验漏读/仓位管理问题)导致的胜率≈30.6%——把一段
# "系统当时确实有bug"的历史战绩当成"中线策略天生只有30%胜率"永久校准，导致EV闸门
# 数学上再也拦不过、中线自2026-07-18后颗粒无收(死循环：不交易→攒不出新样本→永远
# 用旧样本校准)。提到60：不足则自动回退线性映射+影子模式(见 MidLongEvGate 的
# shadow_cold 逻辑)，让中线先恢复交易积累"修复后"的干净样本，自然稀释/替换污染数据。
SWING_CALIBRATOR_MIN_SAMPLES: int = int(os.getenv("SWING_CALIBRATOR_MIN_SAMPLES", "60"))
SWING_CALIBRATOR_LOOKBACK_DAYS: int = int(os.getenv("SWING_CALIBRATOR_LOOKBACK_DAYS", "45"))
SWING_CALIBRATOR_CACHE_TTL_SEC: int = int(os.getenv("SWING_CALIBRATOR_CACHE_TTL_SEC", "900"))
SWING_CALIBRATOR_PIVOT: float = float(os.getenv("SWING_CALIBRATOR_PIVOT", "52"))
# 长线 trend 校准器
TREND_CALIBRATOR_ENABLED: bool = os.getenv("TREND_CALIBRATOR_ENABLED", "true").lower() in (
    "true", "1", "yes", "on",
)
TREND_CALIBRATOR_MIN_SAMPLES: int = int(os.getenv("TREND_CALIBRATOR_MIN_SAMPLES", "25"))
TREND_CALIBRATOR_LOOKBACK_DAYS: int = int(os.getenv("TREND_CALIBRATOR_LOOKBACK_DAYS", "60"))
TREND_CALIBRATOR_CACHE_TTL_SEC: int = int(os.getenv("TREND_CALIBRATOR_CACHE_TTL_SEC", "900"))
TREND_CALIBRATOR_PIVOT: float = float(os.getenv("TREND_CALIBRATOR_PIVOT", "56"))

# ─────────────────────────────────────────────────────────────────
# AI 决策置信度校准器 · S2-8（2026-08-05）
# 从 ai_decision_logs 提取 (LLM confidence, 实际胜负) 样本，PAVA 保序回归拟合
# conf→胜率曲线。样本：buy/sell 已执行且已回填 realized_pnl 的决策；
# confidence 从 decision_snapshot 提取（兼容 0-1 / 0-100 两种历史格式），
# 缺失回退三周期 mid_confidence。供决策质量审计/前端看板/未来 conf 改写闸门。
# ─────────────────────────────────────────────────────────────────
# 总开关：关闭后 estimate_p_win 走锚定基础胜率的线性映射。
AI_DECISION_CALIBRATOR_ENABLED: bool = os.getenv(
    "AI_DECISION_CALIBRATOR_ENABLED", "true"
).lower() in ("true", "1", "yes", "on")
# 校准所需最小样本数；不足则回退线性映射（冷启动）。
AI_DECISION_CALIBRATOR_MIN_SAMPLES: int = int(
    os.getenv("AI_DECISION_CALIBRATOR_MIN_SAMPLES", "30"))
# 回看天数 + 结果缓存 TTL（秒）。
AI_DECISION_CALIBRATOR_LOOKBACK_DAYS: int = int(
    os.getenv("AI_DECISION_CALIBRATOR_LOOKBACK_DAYS", "45"))
AI_DECISION_CALIBRATOR_CACHE_TTL_SEC: int = int(
    os.getenv("AI_DECISION_CALIBRATOR_CACHE_TTL_SEC", "900"))
# 每个置信度桶(0.1宽)最少样本数，不足则跳过该桶（防小样本胜率噪声压曲线）。
AI_DECISION_CALIBRATOR_MIN_BUCKET: int = int(
    os.getenv("AI_DECISION_CALIBRATOR_MIN_BUCKET", "5"))

# ─────────────────────────────────────────────────────────────────
# 中长线信号质量 · S1-2 期望值(EV)闸门（2026-07-08）
# 放行前校验"扣往返成本后期望收益率为正"。EV_min 取正数（少而准）。
# 关闭 MIDLONG_EV_GATE_ENABLED → 影子模式（记录不拦截）。
# ─────────────────────────────────────────────────────────────────
MIDLONG_EV_GATE_ENABLED: bool = os.getenv("MIDLONG_EV_GATE_ENABLED", "true").lower() in (
    "true", "1", "yes", "on",
)
# 缺显式止盈时用 sl×该RR 兜底
MIDLONG_EV_FALLBACK_RR: float = float(os.getenv("MIDLONG_EV_FALLBACK_RR", "2.0"))
# 冷启动防死锁：p_win 未经校准时仅影子记录不硬拦，待校准生效后才拦截
MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION: bool = os.getenv(
    "MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION", "true"
).lower() in ("true", "1", "yes", "on")
# 中线 swing：tp 实现率偏高（波段吃满概率尚可），sl 常吃满
SWING_EV_MIN_PCT: float = float(os.getenv("SWING_EV_MIN_PCT", "0.0005"))
SWING_EV_TP_REALIZATION: float = float(os.getenv("SWING_EV_TP_REALIZATION", "0.70"))
SWING_EV_SL_REALIZATION: float = float(os.getenv("SWING_EV_SL_REALIZATION", "1.0"))
# 长线 trend：趋势骑乘常回吐部分利润（tp 实现率更保守），门槛更高
TREND_EV_MIN_PCT: float = float(os.getenv("TREND_EV_MIN_PCT", "0.0008"))
TREND_EV_TP_REALIZATION: float = float(os.getenv("TREND_EV_TP_REALIZATION", "0.60"))
TREND_EV_SL_REALIZATION: float = float(os.getenv("TREND_EV_SL_REALIZATION", "1.0"))

# ─────────────────────────────────────────────────────────────────
# 中长线信号质量 · S1-3 入场多周期一致性约束（2026-07-08）
# 逆更高周期(日线/4h)强偏向 → 否决/缩仓。复用 OrchBG 缓存的多周期偏向，无额外重算。
# ─────────────────────────────────────────────────────────────────
MIDLONG_MTF_ENFORCE_ENABLED: bool = os.getenv("MIDLONG_MTF_ENFORCE_ENABLED", "true").lower() in (
    "true", "1", "yes", "on",
)
MIDLONG_MTF_STRONG_CONF: float = float(os.getenv("MIDLONG_MTF_STRONG_CONF", "0.7"))
MIDLONG_MTF_CONFLICT_MULT: float = float(os.getenv("MIDLONG_MTF_CONFLICT_MULT", "0.6"))

# ─────────────────────────────────────────────────────────────────
# 中长线 S0 止血修复（2026-07-19，对应 04 综合方案 §3.2）
#
# 根因（审计报告）：独立 Agent 路径（try_execute_independent_agent_open）
# 此前完全绕过 reentry_cooldown + mid_long_structure_stop——
#   R1: 同币种同方向平仓后立即再开，是"开仓→亏损→同向再开→继续亏损"
#      恶性循环的直接代码层根因（实测 57.4% 亏损后 24h 同向再开率）。
#   R2: 用 LLM 窄 sl_pct（部分单 0.8%），跳过结构止损，高波动币被震出。
# 本批改动：接线复用现有 reentry_cooldown + mid_long_structure_stop 模块，
#          而非新建（04 综合方案"复用现有代码"原则）。
#
# Flag 设计：
#   MIDLONG_INDEPENDENT_COOLDOWN_ENFORCE: 独立路径冷却门控（默认 true）
#       false → 影子模式（只记不拦），出问题时秒回退。
#   MIDLONG_STRUCTURE_STOP_ON_INDEPENDENT: 独立路径接入结构 SL（默认 true）
#       false → 用 LLM 原始 sl_pct，回退到改动前行为。
# ─────────────────────────────────────────────────────────────────
MIDLONG_INDEPENDENT_COOLDOWN_ENFORCE: bool = os.getenv(
    "MIDLONG_INDEPENDENT_COOLDOWN_ENFORCE", "true"
).lower() in ("true", "1", "yes", "on")
MIDLONG_STRUCTURE_STOP_ON_INDEPENDENT: bool = os.getenv(
    "MIDLONG_STRUCTURE_STOP_ON_INDEPENDENT", "true"
).lower() in ("true", "1", "yes", "on")

# ── P1 虚拟币中长线交易设计（2026-07-31）──
MIDLONG_CHOP_GATE_ENABLED: bool = os.getenv("MIDLONG_CHOP_GATE_ENABLED", "true").lower() in (
    "true", "1", "yes", "on",
)
MIDLONG_CHOP_ADX_MAX: float = float(os.getenv("MIDLONG_CHOP_ADX_MAX", "18"))
MIDLONG_FUNDING_GATE_ENABLED: bool = os.getenv("MIDLONG_FUNDING_GATE_ENABLED", "true").lower() in (
    "true", "1", "yes", "on",
)
MIDLONG_FUNDING_HOLD_HOURS: float = float(os.getenv("MIDLONG_FUNDING_HOLD_HOURS", "72"))
MIDLONG_MIN_NET_RR: float = float(os.getenv("MIDLONG_MIN_NET_RR", "2.0"))
MIDLONG_FUNDING_ABS_WARN: float = float(os.getenv("MIDLONG_FUNDING_ABS_WARN", "0.0005"))
MIDLONG_ATR_SIZING_ENABLED: bool = os.getenv("MIDLONG_ATR_SIZING_ENABLED", "true").lower() in (
    "true", "1", "yes", "on",
)
MIDLONG_ATR_SL_MULT: float = float(os.getenv("MIDLONG_ATR_SL_MULT", "1.5"))
MIDLONG_RISK_PCT: float = float(os.getenv("MIDLONG_RISK_PCT", "0.01"))
# 杠杆：中长线升级禁止另设。开仓走动态杠杆 + 已有仓统一杠杆；
# leverage_authority 仅作上限钳制，不是按周期固定分配。

# ── P2 组合层 + 币池（2026-07-31）──
MIDLONG_PORTFOLIO_GATE_ENABLED: bool = os.getenv("MIDLONG_PORTFOLIO_GATE_ENABLED", "true").lower() in (
    "true", "1", "yes", "on",
)
# 中长线净方向名义敞口 / 权益 上限。
# 与真实开仓尺度对齐：NIBBLE/BUILD 常见保证金 5%–30% ×10x → 名义 50%–300%。
# 旧默认 0.30 会在第一笔成交后锁死整条中长线通道。
MIDLONG_MAX_NET_EXPOSURE_PCT: float = float(os.getenv("MIDLONG_MAX_NET_EXPOSURE_PCT", "1.5"))
# 探针单可单独更宽（避免首笔试探占满后无法继续）
MIDLONG_NIBBLE_NET_EXPOSURE_PCT: float = float(
    os.getenv("MIDLONG_NIBBLE_NET_EXPOSURE_PCT", "2.0") or "2.0"
)
# 相关簇同向持仓数上限
MIDLONG_CORR_CLUSTER_SYMBOLS: str = os.getenv("MIDLONG_CORR_CLUSTER_SYMBOLS", "BTC,ETH,SOL")
MIDLONG_CORR_CLUSTER_MAX: int = int(os.getenv("MIDLONG_CORR_CLUSTER_MAX", "2") or "2")
MIDLONG_MAX_OPEN_POSITIONS: int = int(os.getenv("MIDLONG_MAX_OPEN_POSITIONS", "4") or "4")
# 无进展超时离场：持仓过久且峰值未达 0.5R → 全平
MIDLONG_NO_PROGRESS_EXIT_ENABLED: bool = os.getenv("MIDLONG_NO_PROGRESS_EXIT_ENABLED", "true").lower() in (
    "true", "1", "yes", "on",
)
MIDLONG_NO_PROGRESS_HOURS_MID: float = float(os.getenv("MIDLONG_NO_PROGRESS_HOURS_MID", "18"))
MIDLONG_NO_PROGRESS_HOURS_LONG: float = float(os.getenv("MIDLONG_NO_PROGRESS_HOURS_LONG", "72"))
MIDLONG_NO_PROGRESS_MIN_PEAK_R: float = float(os.getenv("MIDLONG_NO_PROGRESS_MIN_PEAK_R", "0.5"))
# 人工核心币池：并入长线正向白名单（仍排除 AI 选币）；空=仅用会话 symbols
MIDLONG_CORE_BASKET: str = os.getenv("MIDLONG_CORE_BASKET", "")

# ─────────────────────────────────────────────────────────────────
# 中长线因子科研 · S4 基座（2026-07-08）
# 在 4h/1d 上对发现因子做样本外回测/打分/退役，与短线因子集按 extra.horizon 隔离。
# ─────────────────────────────────────────────────────────────────
MIDLONG_FACTOR_RESEARCH_ENABLED: bool = os.getenv("MIDLONG_FACTOR_RESEARCH_ENABLED", "true").lower() in (
    "true", "1", "yes", "on",
)
# 中长线活跃因子上限（独立于短线 SCALP_ACTIVE_FACTOR_MAX）
MIDLONG_ACTIVE_FACTOR_MAX: int = int(os.getenv("MIDLONG_ACTIVE_FACTOR_MAX", "30"))
# 中长线单因子样本外回测超参（时间框架更长 → 回看更多、前向更短、门槛略低）
# [2026-08-16 深度适配] lookback 按周期分档：4h=2400 根（≈400 天，asterdex 4h 现深 2400）；
# 1d 单独档 FACTOR_SCORER_MIDLONG_LOOKBACK_1D（asterdex 1d 仅 3.1 年≈1126 根，2400 根=6.6 年永远不够）。
FACTOR_SCORER_MIDLONG_LOOKBACK: int = int(os.getenv("FACTOR_SCORER_MIDLONG_LOOKBACK", "2400"))
FACTOR_SCORER_MIDLONG_LOOKBACK_1D: int = int(os.getenv("FACTOR_SCORER_MIDLONG_LOOKBACK_1D", "1000"))
FACTOR_SCORER_MIDLONG_FWD_4H: int = int(os.getenv("FACTOR_SCORER_MIDLONG_FWD_4H", "6"))    # ≈1天
FACTOR_SCORER_MIDLONG_FWD_1D: int = int(os.getenv("FACTOR_SCORER_MIDLONG_FWD_1D", "3"))    # ≈3天
FACTOR_SCORER_MIDLONG_MIN_SHARPE: float = float(os.getenv("FACTOR_SCORER_MIDLONG_MIN_SHARPE", "0.4"))

# S2-1：把量化简报（多周期一致性/结构位/数据完整度）注入 Swing/Trend prompt
MIDLONG_QUANT_BRIEF_IN_PROMPT: bool = os.getenv("MIDLONG_QUANT_BRIEF_IN_PROMPT", "true").lower() in (
    "true", "1", "yes", "on",
)
# S2-2/S2-3：中长线模拟盘试单严格模式——抬高 hold→open 门槛、trend override 需 MTF 对齐、
# paper FactGuard 强制 enforce（少而准）。默认开启（仅影响 paper 试单）。
MIDLONG_PAPER_PROBE_STRICT: bool = os.getenv("MIDLONG_PAPER_PROBE_STRICT", "true").lower() in (
    "true", "1", "yes", "on",
)
# 阶段一：重新启用被拆掉的防线（微观结构/手续费软过滤），flag 门控便于灰度/回滚。
SCALP_MICROSTRUCTURE_GUARD_ENABLED: bool = os.getenv(
    "SCALP_MICROSTRUCTURE_GUARD_ENABLED", "true"
).lower() in ("true", "1", "yes", "on")
# 微观结构过滤只对"强反向证据"一票否决，避免过度拦截（EV 闸门做主）。
SCALP_MICRO_GUARD_STRICT: bool = os.getenv(
    "SCALP_MICRO_GUARD_STRICT", "false"
).lower() in ("true", "1", "yes", "on")
# Paper 样本期：微观结构对立改为扣分放行（不 hold），避免高分信号被 OBI/CVD 一票否决饿死样本。
PAPER_SCALP_MICRO_SOFT: bool = os.getenv(
    "PAPER_SCALP_MICRO_SOFT", "true"
).lower() in ("true", "1", "yes", "on")
PAPER_SCALP_MICRO_SOFT_PENALTY: int = int(os.getenv("PAPER_SCALP_MICRO_SOFT_PENALTY", "8"))
# Paper：连亏币自适应门槛上限（原硬抬到 50，会把 36–49 分信号整批 hold）
PAPER_SCALP_ADAPTIVE_LOSS_CEILING: int = int(
    os.getenv("PAPER_SCALP_ADAPTIVE_LOSS_CEILING", "38")
)
# Paper：advisory.penalty 折扣（1.0=不打折）；默认 0.5 避免 penalty 后掉进 veto 带
PAPER_SCALP_ADVISORY_PENALTY_MULT: float = float(
    os.getenv("PAPER_SCALP_ADVISORY_PENALTY_MULT", "0.5")
)
# 反向 advisory 软否决的缩仓乘数（缩仓而非硬拦，避免架空方向判断）。
SCALP_REVERSE_SOFT_VETO_MULT: float = float(os.getenv("SCALP_REVERSE_SOFT_VETO_MULT", "0.5"))
# [2026-07-18 新增] 插针/操纵防护：规划文档§3.4——若把插针检测做成一个因子权重项，
# 会被其他1000+因子稀释到几乎不起作用；改为在 ScalpExecutionGate 里做专用硬拦截，
# 与既有的 _adjust_sl_for_stop_hunt(SL避让猎杀区) 合并为统一的"操纵防护"模块。
# wick_density = (high-low-|close-open|)/(high-low)，越接近1说明整根K线大部分
# 是被插针刺出来又收回的影线，价格行为不可信，直接 block 该 tick 的开仓。
SCALP_WICK_MANIPULATION_GUARD_ENABLED: bool = os.getenv(
    "SCALP_WICK_MANIPULATION_GUARD_ENABLED", "true"
).lower() in ("true", "1", "yes", "on")
SCALP_WICK_DENSITY_BLOCK_THRESHOLD: float = float(
    os.getenv("SCALP_WICK_DENSITY_BLOCK_THRESHOLD", "0.30")
)
# 多周期 H1–H5 约束强制生效（独立 scalp 循环此前绕过）。
SCALP_MTF_ENFORCE_ENABLED: bool = os.getenv(
    "SCALP_MTF_ENFORCE_ENABLED", "true"
).lower() in ("true", "1", "yes", "on")
# 4h 视为"强信号"的置信度阈值：≥此值且与 scalp 反向 → 禁开（否则仅缩仓）。
SCALP_MTF_STRONG_CONF: float = float(os.getenv("SCALP_MTF_STRONG_CONF", "0.7"))
# 多周期冲突时的缩仓乘数。
SCALP_MTF_CONFLICT_MULT: float = float(os.getenv("SCALP_MTF_CONFLICT_MULT", "0.5"))
# true：MTF 默认只缩仓不 hold；仅在关闭逆势解禁且 4h 强反向时才硬拦。
# 共振 no_trade 也改为缩仓，避免与 V5 重复硬杀样本。
SCALP_MTF_HARD_ONLY_ANCHOR: bool = os.getenv(
    "SCALP_MTF_HARD_ONLY_ANCHOR", "true"
).lower() in ("true", "1", "yes", "on")
# true：ExecutionGate 不再因 regime_extreme 硬拦（交给 V5 终裁）；仍传 size_multiplier
SCALP_GATE_DEFER_REGIME_TO_V5: bool = os.getenv(
    "SCALP_GATE_DEFER_REGIME_TO_V5", "true"
).lower() in ("true", "1", "yes", "on")
# Paper 样本期：universe_degraded 改为缩仓放行（不硬拦新开），避免 PUMP/ZEC/KAITO
# 等主力 alt 因流动性复查短暂降级后整日零样本。Live 仍硬拦。回滚：设 false。
PAPER_SCALP_UNIVERSE_DEGRADED_SOFT: bool = os.getenv(
    "PAPER_SCALP_UNIVERSE_DEGRADED_SOFT", "true"
).lower() in ("true", "1", "yes", "on")
PAPER_SCALP_UNIVERSE_DEGRADED_SIZE_MULT: float = float(
    os.getenv("PAPER_SCALP_UNIVERSE_DEGRADED_SIZE_MULT", "0.35")
)

# ─────────────────────────────────────────────────────────────────
# MTF三重屏幕加权共振引擎（2026-07-18，规划文档§5.5 P2）
# 独立于上面 A/B/C（OrchBG粗粒度离散偏向）的第二套约束：直接用1h/15m/5m K线
# 现算EMA200/ADX(情境层)、MACD/RSI(确认层)、CVD背离(执行层)，加权(50/30/20)得出
# 连续共振分[-1,1]，ATR噪声比>3时降5m权重。见 mtf_resonance_engine.py。
# 回滚：设为 false 即刻恢复到只有 A/B/C 硬约束的现状。
SCALP_MTF_RESONANCE_ENABLED: bool = os.getenv(
    "SCALP_MTF_RESONANCE_ENABLED", "true"
).lower() in ("true", "1", "yes", "on")

# ─────────────────────────────────────────────────────────────────
# Parity Score 验证管线（2026-07-18，规划文档§4.5 P3）
# 每周对比实盘成交(paper_orders) vs LivePipelineBacktestEngine同窗口回放，算6维度
# 加权Score。<0.85告警，<0.70通过RuntimeGovernor提交disabled_natures冻结该nature
# 新开仓。见 backend/services/backtest_engine/parity_score.py。
PARITY_SCORE_ENABLED: bool = os.getenv(
    "PARITY_SCORE_ENABLED", "true"
).lower() in ("true", "1", "yes", "on")

# ─────────────────────────────────────────────────────────────────
# 短线逆势解禁（2026-07-09）
# 背景：多频率"三周期方向冲突"硬约束（1h≠4h 即 BLOCK）在 2026-07-06 从"只记日志"
# 被改成"硬拦截"，随后实测 3 小时内掐死 67% 短线信号、零成交。而短线（scalp，持仓
# 数分钟）的本质就是在中期趋势里抓反抽/噪声波动，禁止它逆 4h 趋势属于逻辑自相矛盾。
# 本开关默认开启：把"禁止短线逆势"的三处硬拦（V5 multi_freq_constraint、ScalpMTF
# 4h强反向禁开、ScalpGate 逆势 -5 扣分）统一降级为"缩仓"，让短线可逆势但仓位更小；
# 中长线（swing/trend/MLTO）的三周期约束原样保留、不受影响。
# 回滚：设为 false 即刻恢复到"硬拦截"现状。
SCALP_ALLOW_COUNTER_TREND: bool = os.getenv(
    "SCALP_ALLOW_COUNTER_TREND", "true"
).lower() in ("true", "1", "yes", "on")
# 短线逆势（与 4h 中期趋势相反）时的额外缩仓乘数：解禁不等于满仓裸奔，
# 逆势单默认再打 5 折，仓位反映风险、评分反映信号质量，两者分开。
SCALP_COUNTER_TREND_SIZE_MULT: float = float(
    os.getenv("SCALP_COUNTER_TREND_SIZE_MULT", "0.5")
)

# ─────────────────────────────────────────────────────────────────
# 震荡均值回归模式（2026-07-09）
# 背景：现有短线整链路是"趋势跟随 + 亏小赚大"（止盈≥2%/止损≥1%/盈亏比≥2.5），
# 在 1% 振幅的震荡市里目标够不到、EV 恒负，导致震荡行情长期零成交。本模式是一套
# 【独立打法】：仅当 regime==ranging 且振幅落在[MIN,MAX]时接管，用"区间位置+RSI极值"
# 高抛低吸、贴区间边缘设小止盈小止损；趋势市完全不启用、原逻辑一行不动。
# 全程开关门控，先上线拿模拟盘观察胜率/盈亏，不好就一键回滚。
SCALP_RANGING_MR_ENABLED: bool = os.getenv(
    "SCALP_RANGING_MR_ENABLED", "true"
).lower() in ("true", "1", "yes", "on")
# 振幅下限：48×5m 区间振幅低于此值不做（手续费盖不住薄利）。
# [2026-07-31 research] 0.8%→1.5%：SL 抬到 1.2%+ 后，过窄振幅装不下合理 TP/SL。
SCALP_MR_MIN_RANGE_PCT: float = float(os.getenv("SCALP_MR_MIN_RANGE_PCT", "0.015"))
# 振幅上限：超过此值可能是趋势启动/异动，不做均值回归（会被趋势碾压）。
SCALP_MR_MAX_RANGE_PCT: float = float(os.getenv("SCALP_MR_MAX_RANGE_PCT", "0.050"))
# 区间上下沿：range_position ≤ LOW 视为区间低位（找多），≥ HIGH 视为高位（找空）。
SCALP_MR_LOW_BAND: float = float(os.getenv("SCALP_MR_LOW_BAND", "0.30"))
SCALP_MR_HIGH_BAND: float = float(os.getenv("SCALP_MR_HIGH_BAND", "0.70"))
# RSI 超卖/超买线：低位 + RSI≤OS 才做多，高位 + RSI≥OB 才做空（双确认，防抄底摸顶）。
SCALP_MR_RSI_OS: float = float(os.getenv("SCALP_MR_RSI_OS", "40"))
SCALP_MR_RSI_OB: float = float(os.getenv("SCALP_MR_RSI_OB", "60"))
# MR 专用止盈/盈亏比下限。
# [2026-07-31 crypto-native] MIN_TP 0.6%→1.0%；MIN_RR 1.0→1.2。
# [2026-07-31 research 虚拟币永续] MIN_TP 1.0%→1.5%，MIN_RR 1.2→1.5：
#   行业主流 RR≥1.5~2；taker 往返≈0.1% 时，1.0% TP 净利偏薄；禁为控亏强压 SL。
# [2026-08-02 修复] 默认值改回 0.6%/1.0（对齐 unified_gate.py fallback 与注释）：
#   07-31 抬到 1.5/0.015 后，ScalpMR 实际产出 RR1.25/TP1.5% 被风险闸全拦（日志实证
#   risk_reward 占总拦截 93%），等于事实上下线 MR 策略。行业 RR≥1.5 的经验不适用于
#   MR（小止盈+反转高胜率打法，靠胜率非盈亏比赚钱），且 EV 闸会独立校验期望值兜底。
SCALP_MR_MIN_TP: float = float(os.getenv("SCALP_MR_MIN_TP", "0.006"))
SCALP_MR_MIN_RR: float = float(os.getenv("SCALP_MR_MIN_RR", "1.0"))
# 止盈取"到区间对沿距离"的比例（0.55≈吃掉过半回归空间就走，不贪满区间）。
SCALP_MR_TP_RANGE_FRAC: float = float(os.getenv("SCALP_MR_TP_RANGE_FRAC", "0.55"))

# ── MR 独立胜率校准/EV 口径（2026-07-11：新策略不再背老趋势打法的历史包袱）──
# 根因：EV 闸门此前对所有短线单统一用同一份"胜率校准模型"和 0.55 的止盈实现率折扣——
# 而这份校准模型的历史样本几乎全部来自"趋势跟随"打法(scalp_composite)。趋势单常被
# master_running_reduce 中途砍仓，止盈很少吃满，0.55 折扣对它是合理的；但 MR 单止盈
# 贴在区间边缘、是固定小目标，一般能吃满，硬套 0.55 折扣会把 EV 判成"永远负"，导致
# 即使评分达标、Gate/V5 都放行，也在最后一步被 EV 闸门 100% 拦截——MR 策略永远开不出
# 第一单，也就永远攒不出自己的真实战绩（先有鸡还是先有蛋的死循环）。
# 解决：MR 单独用一套止盈实现率 + 独立冷启动基础胜率，不再借用趋势打法的历史数据；
# 待 MR 自己攒够 SCALP_CALIBRATOR_MIN_SAMPLES 笔真实成交后，会自动切到 MR 专属的
# 校准曲线（见 scalp_confidence_calibrator.py），用真实战绩说话，不再是"猜"。
SCALP_MR_EV_TP_REALIZATION: float = float(os.getenv("SCALP_MR_EV_TP_REALIZATION", "0.85"))
SCALP_MR_COLD_BASE_RATE: float = float(os.getenv("SCALP_MR_COLD_BASE_RATE", "0.50"))

# ── 校准样本"脏数据"截止线（2026-07-11，用户要求：清理历史垃圾订单样本）──
# 排查发现：短线胜率校准(scalp_confidence_calibrator)当前全部历史样本只有14笔，
# 全部来自 2026-07-08~07-09（当时"幽灵单对冲""清算磁吸反向硬扛"等多个已知bug还没修，
# DB 泄漏/AI超时假生效等根因问题也还没修），真实胜率只有35.7%——这14笔不具代表性，
# 却在拖累今天(已修复过一轮)的每一次开仓判断。
# 这里给校准器一条"起算线"：早于此时间的样本不参与胜率拟合(仍保留在数据库里，不删数据，
# 可随时改回/往前挪)，等新代码跑出的真实成交攒够 SCALP_CALIBRATOR_MIN_SAMPLES 笔，
# 自动切换到新样本拟合出的真实曲线。
SCALP_CALIBRATOR_SAMPLE_SINCE: str = os.getenv(
    "SCALP_CALIBRATOR_SAMPLE_SINCE", "2026-07-10T00:00:00+08:00"
)

# ── 冷启动数据积累豁免（2026-07-11，用户明确要求：现在的目的是积累数据而不是空跑）──
# 只在【Paper 模拟盘 + 校准器还没攒够真实样本(cold_linear)】时生效：给 EV 闸门的
# 门槛额外让出一点空间，让 RR 尚可、只是被 0.55 止盈折扣打到临界负的信号也能先跑，
# 攒出真实成交结果。一旦某策略攒够 SCALP_CALIBRATOR_MIN_SAMPLES(40) 笔真实样本，
# 自动切换成真实校准曲线，本豁免立刻失效——不需要手动关，用真实战绩说话。
# 真金模式(mode=live)恒为 0，不受影响。
SCALP_EV_COLD_START_ALLOWANCE_PCT: float = float(
    os.getenv("SCALP_EV_COLD_START_ALLOWANCE_PCT", "0.0025")
)

# ─────────────────────────────────────────────────────────────────
# 短线转正 · 阶段二：因子回测打分闸门（2026-07-08）
# 自动发现的因子必须先过"单因子样本外回测 + IC/净收益打分 + 正交去冗余"，
# A/B 级才准入短线活跃因子集。详见 factor_backtest_scorer.py。
# ─────────────────────────────────────────────────────────────────
FACTOR_SCORER_SYMBOLS: str = os.getenv("FACTOR_SCORER_SYMBOLS", "BTC,ETH,SOL")
FACTOR_SCORER_INTERVAL: str = os.getenv("FACTOR_SCORER_INTERVAL", "1h")
FACTOR_SCORER_LOOKBACK_BARS: int = int(os.getenv("FACTOR_SCORER_LOOKBACK_BARS", "720"))
# [2026-08-13 P1-5] 打分前瞻期：0=按周期分档（与进化侧 _PERIOD_FWD_BARS 对齐），
# 显式 >0 时覆盖（回滚：设回 5 即恢复旧全局 5 根前瞻）。
FACTOR_SCORER_FWD_PERIOD: int = int(os.getenv("FACTOR_SCORER_FWD_PERIOD", "0") or 0)
# 单因子回测每次持仓的往返成本（手续费+滑点，价格变动比例口径）。
FACTOR_SCORER_COST: float = float(os.getenv("FACTOR_SCORER_COST", "0.0021"))
# 准入门槛：样本外 Sharpe 与净收益需同时达标，且非冗余。
FACTOR_SCORER_MIN_SHARPE: float = float(os.getenv("FACTOR_SCORER_MIN_SHARPE", "0.5"))
FACTOR_SCORER_MIN_NET_RETURN: float = float(os.getenv("FACTOR_SCORER_MIN_NET_RETURN", "0.0"))
FACTOR_SCORER_REDUNDANCY_CORR: float = float(os.getenv("FACTOR_SCORER_REDUNDANCY_CORR", "0.8"))
# [2026-08-13 短线因子根因修复 P1-7] 打分闸门成本/防过拟合升级：
# funding 费率（永续 8h 结算，短线过夜持仓真实成本）、DSR/PBO 多重检验闸门、
# 每笔平均净收益须覆盖往返成本 + NET_BUFFER 缓冲、PBO 上限。
FACTOR_SCORER_FUNDING_RATE: float = float(os.getenv("FACTOR_SCORER_FUNDING_RATE", "0.0001"))
FACTOR_SCORER_DSR_REQUIRED: bool = os.getenv("FACTOR_SCORER_DSR_REQUIRED", "true").lower() in (
    "true", "1", "yes", "on",
)
FACTOR_SCORER_DSR_N_TRIALS: int = int(os.getenv("FACTOR_SCORER_DSR_N_TRIALS", "40"))
FACTOR_SCORER_MAX_PBO: float = float(os.getenv("FACTOR_SCORER_MAX_PBO", "0.5"))
# [2026-08-14 P0-1] DSR/PBO 跨币样本下限：单因子打分的 ICIR 样本数（币种数）
# 低于该值时多重检验无法估计，闸门显式 fail-open 跳过并告警（由 OOS/净收益/冗余兜底）。
# 默认 4 对应 PBO 简化实现的最小样本要求；可用 env 覆盖。
FACTOR_SCORER_DSR_MIN_SYMBOLS: int = int(os.getenv("FACTOR_SCORER_DSR_MIN_SYMBOLS", "4"))
FACTOR_SCORER_NET_BUFFER: float = float(os.getenv("FACTOR_SCORER_NET_BUFFER", "0.0005"))
# [2026-08-14 阶段2 P1-E1/E2/E3/P2-8] 信号融合方向/权重修复的灰度回退开关。
# 默认全部开启（修复生效）；出问题时设 false 一键回退旧行为（无需改代码）。
FACTOR_FUNDING_DIRECTION_FIX: bool = os.getenv("FACTOR_FUNDING_DIRECTION_FIX", "true").lower() in (
    "true", "1", "yes", "on",
)
FACTOR_SIGNAL_FILTER_NONDIRECTIONAL: bool = os.getenv("FACTOR_SIGNAL_FILTER_NONDIRECTIONAL", "true").lower() in (
    "true", "1", "yes", "on",
)
FUSION_REGIME_WEIGHT_MULTIPLIERS: bool = os.getenv("FUSION_REGIME_WEIGHT_MULTIPLIERS", "true").lower() in (
    "true", "1", "yes", "on",
)
FUSION_LOW_QUALITY_HOLD: bool = os.getenv("FUSION_LOW_QUALITY_HOLD", "false").lower() in (
    "true", "1", "yes", "on",
)
# [2026-08-14 P1-C4] PAPER 影子因子在线权重上限（拍板：PAPER 保持可交易但权重受限）。
# factor_evaluation_pipeline 对 state=PAPER 的因子强制 min(weight, cap)；0=不限制。
PAPER_FACTOR_WEIGHT_CAP: float = float(os.getenv("PAPER_FACTOR_WEIGHT_CAP", "0.5") or 0.5)
# [2026-08-14 P1-E5] 云端因子同步总开关：安全加固完成前默认禁用。
# sync_from_repo 在开关关闭时直接跳过；本地化产物一律 candidate（待验证）。
FACTOR_CLOUD_SYNC_ENABLED: bool = os.getenv("FACTOR_CLOUD_SYNC_ENABLED", "false").lower() in (
    "true", "1", "yes", "on",
)
# 短线活跃因子集上限（避免无限膨胀）。
SCALP_ACTIVE_FACTOR_MAX: int = int(os.getenv("SCALP_ACTIVE_FACTOR_MAX", "40"))

# 阶段一 1.5：验证期严格化 —— "少而精"。收紧短线开仓门槛/冷却/并发，
# 并（在下方 PAPER_FAST_TRIAL 块中）关闭对短线的放宽，让模拟盘门槛与真金一致。
# 默认开启（当前正处于止血验证期）；每项仍可用同名 env 覆盖，或整体设 false 回滚。
SCALP_STRICT_VALIDATION: bool = os.getenv("SCALP_STRICT_VALIDATION", "true").lower() in (
    "true", "1", "yes", "on",
)
if SCALP_STRICT_VALIDATION:
    # 提高开仓门槛：EV 闸门+校准已做主，这里再抬高基础分门槛，减少边缘探索单。
    if not os.getenv("SCALP_FACTOR_CONFIRM_THRESHOLD"):
        SCALP_FACTOR_CONFIRM_THRESHOLD = 42
    if not os.getenv("SCALP_FACTOR_EXECUTE_THRESHOLD"):
        SCALP_FACTOR_EXECUTE_THRESHOLD = 50
    if not os.getenv("SCALP_DIRECT_THRESHOLD"):
        SCALP_DIRECT_THRESHOLD = 50
    if not os.getenv("SCALP_VETO_BAND_LOW"):
        SCALP_VETO_BAND_LOW = 40
    # 延长冷却，降低同币/同向频繁开单。
    if not os.getenv("SCALP_OPEN_COOLDOWN_SEC"):
        SCALP_OPEN_COOLDOWN_SEC = 600
    if not os.getenv("SCALP_OPEN_SAME_SIDE_COOLDOWN_SEC"):
        SCALP_OPEN_SAME_SIDE_COOLDOWN_SEC = 1200
    # 每 tick 最多开 1 仓（限制并发爆发）。
    if not os.getenv("SCALP_MAX_OPENS_PER_TICK"):
        SCALP_MAX_OPENS_PER_TICK = 1
# 2026-07-06 新增：因子聚合时单一类别（如全是动量类）最多贡献的权重占比，
# 防止同类因子（RSI/MACD/Momentum/ROC 均属 momentum）重复计权、虚增置信度。
# 参见 docs/SCALP_FACTOR_STRATEGY_ANALYSIS_2026-07-06.md 第2.1节。
FACTOR_CATEGORY_MAX_SHARE: float = float(os.getenv("FACTOR_CATEGORY_MAX_SHARE", "0.40"))
FACTOR_CATEGORY_DEDUP_ENABLED: bool = os.getenv(
    "FACTOR_CATEGORY_DEDUP_ENABLED", "true"
).lower() in ("true", "1", "yes", "on")

# 2026-07-06 新增：多场所资金费采集器（为 delta-neutral 刷分补第二条腿的数据管道）。
# 用公共只读 ccxt 客户端轮询各场所资金费写入 perp_funding，与 hyperliquid 数据配对。
# 默认关闭：需在有外网环境显式开启（无网时会空跑网络请求）。
MULTI_VENUE_FUNDING_COLLECTOR_ENABLED: bool = os.getenv(
    "MULTI_VENUE_FUNDING_COLLECTOR_ENABLED", "false"
).lower() in ("true", "1", "yes", "on")
# 采集间隔（秒）；资金费小时级更新，默认 5 分钟足够。
MULTI_VENUE_FUNDING_COLLECT_INTERVAL_SECONDS: int = int(
    os.getenv("MULTI_VENUE_FUNDING_COLLECT_INTERVAL_SECONDS", "300")
)
# 采集场所（逗号分隔，小写）；留空用采集器默认（binance/bybit/okx/gateio/asterdex）。
MULTI_VENUE_FUNDING_VENUES: str = os.getenv("MULTI_VENUE_FUNDING_VENUES", "")
# 采集 symbol 白名单（逗号分隔基础符号，如 BTC,ETH）；留空用采集器默认核心币种。
MULTI_VENUE_FUNDING_SYMBOLS: str = os.getenv("MULTI_VENUE_FUNDING_SYMBOLS", "")
# 某场所连续 N 轮采集失败（error/cancelled/timeout）即通过飞书告警一次（恢复后自动复位）。
# 设为 0 关闭告警。默认 3 轮（配合 300s 间隔≈15 分钟持续失败才报，避免抖动误报）。
MULTI_VENUE_FUNDING_ALERT_THRESHOLD: int = int(
    os.getenv("MULTI_VENUE_FUNDING_ALERT_THRESHOLD", "3")
)

TIER_PYRAMID_PARAMS = {
    "short": {
        "enabled": False,
        "max_adds": 2,  # 虽然 enabled=False 禁用 AI 金字塔，但 ScalpRouter 用 SCALP_ROUTER_MAX_ADDS
    },
    "mid": {
        "enabled": True,
        "max_adds": 2,
        "min_profit_pcts": [0.015, 0.030],
        "size_ratios": [0.50, 0.25],
        "cooldown_min": 60,
        "min_adx": 20,
        "sl_lock_ratio": 0.50,
    },
    "long": {
        "enabled": True,
        "max_adds": 3,
        "min_profit_pcts": [0.015, 0.030, 0.050],
        "size_ratios": [0.50, 0.30, 0.20],
        "cooldown_min": 120,
        "min_adx": 25,
        "sl_lock_ratio": 0.40,
    },
}

# DynamicStopManager ATR 倍数（按 tier 分化）
# v5: ATR 倍数再上调，给 SL 更多呼吸空间（尤其中线）
TIER_ATR_MULTIPLIER = {
    "short": 3.0,
    "mid":   3.5,
    "long":  4.5,
}

# ══════════════════════════════════════════════════
#  多周期并行交易配置
# ══════════════════════════════════════════════════

# 各 tier 使用的 K 线周期（用于分析师数据采集）
TIER_KLINE_PERIODS = {
    "short": ["5m", "15m"],
    "mid":   ["1h", "4h"],
    "long":  ["4h", "1d"],
}

# 各 tier 分析的主 K 线周期（传给因子/信号引擎）
TIER_PRIMARY_PERIOD = {
    "short": "15m",
    "mid":   "1h",
    "long":  "4h",
}

# 各 tier 的 equity 预算占比（总和 <= 1.0）
# 剩余部分作为安全边际不分配
# 三层资金分配比例（占 equity）
# 修复（2026-06-24）：原配置 short=25%/mid=40%/long=25%，但实际短线吃掉 100%
# 保证金（76笔 scalp 全做多亏 $20k），长线分不到资金。
# 调整为短线收缩、长线加权：大资金留给趋势仓（盈亏比高、胜率高），短线只做小仓试探。
TIER_BUDGET_ALLOCATION = {
    "short": float(os.getenv("TIER_SHORT_BUDGET", "0.15")),   # 原 0.25 → 0.15（短线收缩）
    "mid":   float(os.getenv("TIER_MID_BUDGET", "0.35")),     # 原 0.40 → 0.35
    "long":  float(os.getenv("TIER_LONG_BUDGET", "0.40")),    # 原 0.25 → 0.40（大资金留长线）
}

# 各 tier 的最小分析间隔（秒）
TIER_ANALYSIS_INTERVAL = {
    "short": int(os.getenv("TIER_SHORT_ANALYSIS_INTERVAL", "180")),   # 3 分钟
    "mid":   int(os.getenv("TIER_MID_ANALYSIS_INTERVAL", "600")),     # 10 分钟
    "long":  int(os.getenv("TIER_LONG_ANALYSIS_INTERVAL", "1800")),   # 30 分钟
}

# 单 tier 最大保证金占 equity 比例
# 修复（2026-06-24）：原 short max_margin=0.30，短线可占 30% 保证金。
# 配合补仓失控（RESOLV 同方向开 20 仓），短线吃掉全部资金。
# 收紧 short 到 15%，long 提到 40%，大资金留给趋势仓。
TIER_MAX_MARGIN_PCT = {
    "short": float(os.getenv("TIER_SHORT_MAX_MARGIN", "0.15")),  # 原 0.30 → 0.15
    "mid":   float(os.getenv("TIER_MID_MAX_MARGIN", "0.35")),    # 原 0.45 → 0.35
    "long":  float(os.getenv("TIER_LONG_MAX_MARGIN", "0.40")),   # 原 0.30 → 0.40
}

# 跨 tier 冲突策略：conservative=减仓, neutral=独立运行, aggressive=跟随多数
CROSS_TIER_CONFLICT_POLICY = os.getenv("CROSS_TIER_CONFLICT_POLICY", "conservative")

# ══════════════════════════════════════════════════
#  整改项1: 减仓冷却保护
# ══════════════════════════════════════════════════
ENABLE_REDUCE_COOLDOWN: bool = os.getenv("ENABLE_REDUCE_COOLDOWN", "true").lower() == "true"
REDUCE_MAX_COUNT: int = int(os.getenv("REDUCE_MAX_COUNT", "2"))
# Master 主动减仓保证金亏损硬底线（V5.2 从5%→10%）
# 根因：5%是保证金比例，杠杆越高越易触发（8x下价格跌0.625%即达标，加密市场噪音级别）
# 修复：提升至10%作为安全地板，配合 master_close_guard 规则⑥的SL逼近度门控（≥60%SL距离）
MASTER_REDUCE_MIN_LOSS_PCT: float = float(os.getenv("MASTER_REDUCE_MIN_LOSS_PCT", "0.10"))
# 盈利仓浮盈低于此比例时，禁止 Master close/微仓全平（避免小赚就跑）
MASTER_CLOSE_MIN_PROFIT_PCT: float = float(os.getenv("MASTER_CLOSE_MIN_PROFIT_PCT", "0.03"))

# ══════════════════════════════════════════════════
#  整改项2: 防守模式分层管理
# ══════════════════════════════════════════════════
DEFENSIVE_TIERED_MODE: bool = os.getenv("DEFENSIVE_TIERED_MODE", "true").lower() == "true"

# 防守模式波动率感知阈值 — 不同波动率币种使用不同的亏损分档标准
# 低波动币(BTC/ETH): ±1.5% 是正常噪声，乘数=1.0 (基线)
# 中波动币(SOL/BNB等): ±3% 是正常噪声，乘数=1.5
# 高波动币(VIRTUAL/ASTER等小市值): ±5%+ 是正常噪声，乘数=2.5
DEFENSIVE_VOLATILITY_TIERS = {
    # 基线阈值(绝对值)：轻微=-2%, 中度=-5%, 严重=-10%
    "light_pct": 0.02,
    "moderate_pct": 0.05,
    "severe_pct": 0.10,
    # 波动率分档乘数
    "vol_multipliers": {
        "low": 1.0,      # BTC, ETH 等: 日波动 2-3%
        "mid": 1.5,       # SOL, BNB, XRP, DOGE 等: 日波动 3-5%
        "high": 2.5,      # VIRTUAL, ASTER 等小市值: 日波动 6-15%
    },
    # 币种→波动率分档映射（小写）
    "symbol_vol_map": {
        # 低波动
        "btc": "low", "eth": "low", "wbtc": "low",
        # 中波动
        "sol": "mid", "bnb": "mid", "xrp": "mid", "doge": "mid",
        "ada": "mid", "avax": "mid", "link": "mid", "dot": "mid",
        "matic": "mid", "atom": "mid", "uni": "mid", "near": "mid",
        "ltc": "mid", "etc": "mid", "fil": "mid", "apt": "mid",
        "arb": "mid", "op": "mid", "sui": "mid", "sei": "mid",
        # 高波动（小市值）
        "virtual": "high", "aster": "high", "render": "high",
        "pepe": "high", "wif": "high", "bonk": "high", "floki": "high",
        "trump": "high", "ai16z": "high", "griffain": "high",
    },
}

# ══════════════════════════════════════════════════
#  整改项6: 仓位最小决策间隔
# ══════════════════════════════════════════════════
POSITION_MIN_DECISION_INTERVAL_ENABLED: bool = os.getenv("POSITION_MIN_DECISION_INTERVAL", "true").lower() == "true"

# Hyperliquid Builder Fee Configuration
HYPERLIQUID_BUILDER_CONFIG = HyperliquidBuilderConfig(
    builder_address=os.getenv(
        "HYPERLIQUID_BUILDER_ADDRESS",
        "0x012E82f81e506b8f0EF69FF719a6AC65822b5924"
    ),
    builder_fee=int(os.getenv("HYPERLIQUID_BUILDER_FEE", "30"))  # 0.03% default
)

# ══════════════════════════════════════════════════
#  Redis / 多 worker WS 广播 (Phase 5 Task 5.2)
# ══════════════════════════════════════════════════
# 空 = 单 worker 本地(不走 Redis,dev 默认);设置 = 多 worker 跨进程广播 WS 消息。
# 示例: redis://127.0.0.1:6379/0
# 详见 services/ws_redis_bridge.py — 未设时优雅退化为本地直发,不强制依赖 Redis。
REDIS_URL = os.getenv("REDIS_URL", "")  # 空=单worker本地(不走 Redis);设置=多worker广播

# ══════════════════════════════════════════════════
#  套利引擎配置 (Phase 2)
# ══════════════════════════════════════════════════

# 资金费率套利（V3 统计套利）引擎总开关 —— 默认关闭，需手动启用。
# [2026-06-13 校正] 原默认值误写为 "true"（与注释/产品口径「手动开」矛盾），
#   虽有会话级 arb_enabled(默认 False) 再次兜底，但全局开关默认开会误导排查。
#   现统一为默认 false：V3 套利必须显式开会话 arb_enabled + 设此环境变量才运行。
FUNDING_ARB_ENABLED: bool = os.getenv("FUNDING_ARB_ENABLED", "false").lower() == "true"

# ══════════════════════════════════════════════════
#  AI学习系统整合 Feature Flags
#  所有整合点可通过环境变量开关，随时关闭回退到纯规则模式
# ══════════════════════════════════════════════════

# DRL建议注入主循环（默认关闭；shadow 模式在 ENABLE_DRL_INTEGRATION=True 且 DRL_SHADOW_MODE=True 时记录不执行）
ENABLE_DRL_INTEGRATION: bool = os.getenv("ENABLE_DRL_INTEGRATION", "false").lower() == "true"

# Kelly仓位作为上限约束（v3 整改: 默认开启，仅夹紧上限，行为兼容）
ENABLE_KELLY_POSITION: bool = os.getenv("ENABLE_KELLY_POSITION", "true").lower() == "true"

# 进化结果反馈到实盘genome（默认关闭，需与 params_registry 写入口一起启用）
# [2026-08-13 R3] ENABLE_EVOLUTION_FEEDBACK 假开关已删除（历史消费端 adapt_params 从未被调用，
# 进化反哺统一走 data/v5_runtime_gates.json 通道）。历史代码见 git commit 4b0fa39 之前。

# 组合级风险聚合（默认关闭，需先验证 PortfolioRiskAggregator 与单笔风控联动）
ENABLE_PORTFOLIO_RISK: bool = os.getenv("ENABLE_PORTFOLIO_RISK", "false").lower() == "true"

# 系统协调器自动触发（v3 整改: 默认开启，仅观察+触发紧急进化/重训，带冷却防抖）
ENABLE_COORDINATOR: bool = os.getenv("ENABLE_COORDINATOR", "true").lower() == "true"

# DRL影子模式：仅记录建议不执行（默认开启，验证DRL与实盘一致性后关闭）
DRL_SHADOW_MODE: bool = os.getenv("DRL_SHADOW_MODE", "false").lower() == "true"  # 默认关闭shadow记录（DRL预测无交易执行权）

# Kelly作为仓位上限（而非精确值），默认开启
KELLY_AS_UPPER_BOUND: bool = os.getenv("KELLY_AS_UPPER_BOUND", "true").lower() == "true"

# 组合最大风险占比（PortfolioRiskAggregator使用）
PORTFOLIO_MAX_RISK: float = float(os.getenv("PORTFOLIO_MAX_RISK", "0.30"))

# 组合单币种最大仓位占比（PortfolioRiskAggregator使用）
PORTFOLIO_MAX_SINGLE_POSITION: float = float(os.getenv("PORTFOLIO_MAX_SINGLE_POSITION", "0.25"))

# [已删除 2026-06-11] DRL_SHADOW_CONSISTENCY_THRESHOLD — 假开关，全库无消费端；DRL 已下线

# Prompt 自动进化总开关（2026-06-11 默认关闭）：
# 历史 36/36 次 LLM 改写提示词全部失败，改用 v5_runtime_gates 运行时门槛闭环代替。
# 教训提取/参数自适应/因子权重复盘不受此开关影响，仅禁用"LLM 改写提示词模板"环节。
PROMPT_EVOLUTION_ENABLED: bool = os.getenv("PROMPT_EVOLUTION_ENABLED", "false").lower() == "true"
# Hermes L2：Paper 默认关闭 A/B，优化后直接 active（减门策略，避免新版本永不上线）
# Live 可设 HERMES_L2_AB_ENABLED=true 开启真 A/B
HERMES_L2_AB_ENABLED: bool = os.getenv("HERMES_L2_AB_ENABLED", "false").lower() in (
    "1", "true", "yes", "on",
)
# A/B 开启时 B 版流量占比（按请求随机，非 consumer 固定）
HERMES_AB_TRAFFIC_RATIO: float = max(
    0.05,
    min(0.95, float(os.getenv("HERMES_AB_TRAFFIC_RATIO", "0.5"))),
)
# Paper 模式下 Governor pending patch 自动批准（L3/L4/Hermes 减门）
RUNTIME_GOVERNOR_AUTO_APPROVE_PAPER: bool = os.getenv(
    "RUNTIME_GOVERNOR_AUTO_APPROVE_PAPER", "true"
).lower() in ("1", "true", "yes", "on")
# Paper 下 L3 架构提案 pending 批量 auto-accept（每批上限，避免一次处理过多）
# [2026-08-05 v6 8.3 阶段1] 默认改 false：禁用假进化（reconcile_implemented_paper 自动标记），
# 一切进化产物必须过“采纳→使用→验证→淘汰”闭环；如需临时开启需显式 env。
HERMES_L3_AUTO_ACCEPT_PAPER: bool = os.getenv(
    "HERMES_L3_AUTO_ACCEPT_PAPER", "false"
).lower() in ("1", "true", "yes", "on")
HERMES_L3_AUTO_ACCEPT_BATCH: int = int(os.getenv("HERMES_L3_AUTO_ACCEPT_BATCH", "20"))
# 策略级 Prompt Training：Paper 默认关闭 A/B，B 版直接绑定策略
PROMPT_TRAINING_AB_ENABLED: bool = os.getenv("PROMPT_TRAINING_AB_ENABLED", "false").lower() in (
    "1", "true", "yes", "on",
)
# OpenCode 启动时批量处理 pending 提案（减门：避免长期堆积）
OPENCODE_PENDING_DRAIN_ON_STARTUP: bool = os.getenv(
    "OPENCODE_PENDING_DRAIN_ON_STARTUP", "true"
).lower() in ("1", "true", "yes", "on")
OPENCODE_PENDING_DRAIN_LIMIT: int = int(os.getenv("OPENCODE_PENDING_DRAIN_LIMIT", "30"))
OPENCODE_PENDING_DRAIN_ROUNDS: int = int(os.getenv("OPENCODE_PENDING_DRAIN_ROUNDS", "3"))
# A/B 学习对照框架（默认关；且 record_trade 未接线，开启后仍无实际分流）
AI_AB_FRAMEWORK_ENABLED: bool = os.getenv("AI_AB_FRAMEWORK_ENABLED", "false").lower() in (
    "1", "true", "yes", "on",
)

# ══════════════════════════════════════════════════
#  AI 自动学习闭环 Feature Flags (P2-3)
# ══════════════════════════════════════════════════

# 学习闭环总开关：关闭后 LearningLoopService 的三个 tick 全部跳过（Kelly/DRL/Coord）
LEARNING_LOOP_ENABLED: bool = os.getenv("LEARNING_LOOP_ENABLED", "true").lower() == "true"

# 学习闭环 tick 周期（秒）；快速试单默认大幅缩短以尽快积累样本
LEARNING_LOOP_OUTCOME_INTERVAL_S: int = int(
    os.getenv("LEARNING_LOOP_OUTCOME_INTERVAL_S", "90" if PAPER_FAST_TRIAL else str(5 * 60))
)
LEARNING_LOOP_PAPER_BACKFILL_INTERVAL_S: int = int(
    os.getenv("LEARNING_LOOP_PAPER_BACKFILL_INTERVAL_S", "120" if PAPER_FAST_TRIAL else str(10 * 60))
)
LEARNING_LOOP_KELLY_INTERVAL_S: int = int(
    os.getenv("LEARNING_LOOP_KELLY_INTERVAL_S", "600" if PAPER_FAST_TRIAL else str(30 * 60))
)
LEARNING_LOOP_COORD_INTERVAL_S: int = int(
    os.getenv("LEARNING_LOOP_COORD_INTERVAL_S", "1800" if PAPER_FAST_TRIAL else str(60 * 60))
)
# MLTO 平仓复盘节流（秒）；快速试单 5min，正常 1h
THESIS_POSTMORTEM_COOLDOWN_SEC: int = int(
    os.getenv("THESIS_POSTMORTEM_COOLDOWN_SEC", "300" if PAPER_FAST_TRIAL else "3600")
)

# DRL 自动重训：false 时 LearningLoop 消费 trigger_drl_retrain 只记录日志不训练
DRL_RETRAIN_AUTO: bool = os.getenv("DRL_RETRAIN_AUTO", "false").lower() == "true"  # 默认关闭自动DRL训练（41天数据不足以训练可用模型）

# S5: RL仓位管理器（SARSA Q-Learning），作为 Kelly 的补充接入仓位决策管线
ENABLE_RL_POSITION_SIZER: bool = os.getenv("ENABLE_RL_POSITION_SIZER", "false").lower() == "true"  # 默认关闭，待 Q-table 积累足够经验后启用

# 组合风险硬阻塞：true=超阈值 passed=False；false=仅夹紧 position_pct（默认稳妥）
PORTFOLIO_RISK_HARD_BLOCK: bool = os.getenv("PORTFOLIO_RISK_HARD_BLOCK", "false").lower() == "true"

# 组合最大相关性风险（单币种相关性 × 仓位的加权和上限）
PORTFOLIO_MAX_CORRELATION_RISK: float = float(os.getenv("PORTFOLIO_MAX_CORRELATION_RISK", "0.75"))

# ══════════════════════════════════════════════════
#  策略护栏：低胜率策略自动冷却 & 统计显著性门槛
# ══════════════════════════════════════════════════
# 评估窗口：在该窗口内若一个 strategy 达到 MIN_SAMPLES 且胜率低于 MIN_WINRATE
# 则进入冷却，LearningLoop 会在 Coordinator 的 reasons 里记一条；
# 下单前在 TradingDecisionInterface 的 risk_check 里会夹紧 size=0（软冷冻）。
STRATEGY_GUARD_WINDOW_HOURS: int = int(os.getenv("STRATEGY_GUARD_WINDOW_HOURS", "48"))
STRATEGY_GUARD_MIN_SAMPLES: int = int(os.getenv("STRATEGY_GUARD_MIN_SAMPLES", "10"))
STRATEGY_GUARD_MIN_WINRATE: float = float(os.getenv("STRATEGY_GUARD_MIN_WINRATE", "0.15"))
STRATEGY_GUARD_COOLDOWN_HOURS: int = int(os.getenv("STRATEGY_GUARD_COOLDOWN_HOURS", "6"))

# ══════════════════════════════════════════════════
#  Stage E 风控重构常量（对齐 docs/research/decisions.md + cross_review.md）
#  新常量一律 *_V2 / *_BY_VOL_BAND 命名，保持旧常量可回滚
#  所有新代码路径由 RISK_STAGE_E_* feature flag 驱动，默认 off（P5: 分批开）
# ══════════════════════════════════════════════════

# --- D1 — 按波动带分层的 TP/SL 默认 -------------------------------------
# 事实依据: market_statistics.csv 中 4h ATR_P50
#   low (BTC/ETH/BNB)     ≈ 1.46–2.10%
#   mid (SOL/ASTER)       ≈ 2.11–2.24%
#   high (VIRTUAL)        ≈ 3.03%
#   x-high (XPL)          ≈ 3.85%
# 推导规则: short.sl = round_to_0.5%(ATR_P50(1h) × 3)，short.tp = short.sl × 1.4
TIER_TP_SL_DEFAULTS_BY_VOL_BAND = {
    "low": {
        "short": {"tp_pct": 0.025, "sl_pct": 0.018},
        "mid":   {"tp_pct": 0.045, "sl_pct": 0.030},
        "long":  {"tp_pct": 0.000, "sl_pct": 0.000},
    },
    "mid": {
        "short": {"tp_pct": 0.035, "sl_pct": 0.025},
        "mid":   {"tp_pct": 0.060, "sl_pct": 0.040},
        "long":  {"tp_pct": 0.000, "sl_pct": 0.000},
    },
    "high": {
        "short": {"tp_pct": 0.055, "sl_pct": 0.040},
        "mid":   {"tp_pct": 0.090, "sl_pct": 0.060},
        "long":  {"tp_pct": 0.000, "sl_pct": 0.000},
    },
    "x-high": {
        "short": {"tp_pct": 0.080, "sl_pct": 0.060},
        "mid":   {"tp_pct": 0.120, "sl_pct": 0.085},
        "long":  {"tp_pct": 0.000, "sl_pct": 0.000},
    },
}

# --- D2 — 按波动带分层的 ATR 倍数 ---------------------------------------
# 事实依据: M4 表中 XPL 1h kurt=71 / 15m kurt=171，是全盘最重尾
# 原则: 越重尾，ATR 倍数越小（避免单根 K 线刺穿止损的概率被放大）
TIER_ATR_MULTIPLIER_BY_VOL_BAND = {
    "low":    {"short": 3.0, "mid": 3.5, "long": 4.5},
    "mid":    {"short": 2.5, "mid": 3.0, "long": 4.0},
    "high":   {"short": 2.2, "mid": 2.8, "long": 3.5},
    "x-high": {"short": 1.8, "mid": 2.3, "long": 3.0},
}

# --- D3 — 重分类的防御波动档位（取代 DEFENSIVE_VOLATILITY_TIERS 的 symbol_vol_map 逻辑）
# 注意: 保留旧 DEFENSIVE_VOLATILITY_TIERS 不动（回滚兼容），新代码读下方新表
DEFENSIVE_VOLATILITY_TIERS_V2 = {
    "light_pct": 0.02,
    "moderate_pct": 0.05,
    "severe_pct": 0.05,
    "vol_multipliers": {
        "low":    1.0,
        "mid":    1.5,
        "high":   2.0,
        "x-high": 3.0,
    },
    "symbol_vol_map": {
        # ── 主账户实际交易的 7 个币（基于 docs/research/market_statistics.csv 重分类） ──
        "btc":     "low",
        "eth":     "low",
        "bnb":     "low",
        "sol":     "mid",
        "aster":   "mid",
        "virtual": "high",
        "xpl":     "x-high",
        # ── 兼容旧表里的其他币（保持审计文档里标的备用位点） ──
        "wbtc":    "low",
        "xrp":     "mid",
        "doge":    "mid",
        "ada":     "mid", "avax": "mid", "link": "mid", "dot": "mid",
        "matic":   "mid", "atom": "mid", "uni":  "mid", "near": "mid",
        "ltc":     "mid", "etc":  "mid", "fil":  "mid", "apt":  "mid",
        "arb":     "mid", "op":   "mid", "sui":  "mid", "sei":  "mid",
        "render":  "high", "pepe": "high", "wif": "high", "bonk": "high",
        "floki":   "high", "trump": "high", "ai16z": "high", "griffain": "high",
    },
    # 未命中的币默认走哪一档（P3 规范: 必须明确，不得返回 None）
    "unknown_fallback": "mid",
}

# --- D4 — 按波动带 / nature 分层的杠杆上限 ----------------------------
# Stage G P1: band_cap 上还要再套 effective = band_cap / sqrt(count_same_bucket_open + 1)
# Stage G P6: swing=15x (不是 10x)，对齐 strategy_genome 演化空间的 60% 上沿
LEVERAGE_CAP_BY_VOL_BAND = {
    "low":    20,
    "mid":    15,
    "high":   10,
    "x-high": 6,
}
# 2026-06-18: 杠杆分层按交易常识反转。原值 scalp 20x / trend 5x 是反的——
# 短线靠胜率该低杠杆防插针爆仓，长线把握大、止损宽可适度提杠杆。
# 2026-06-22: 各 nature 上限统一抬到 20，完全交给 calculate_dynamic_leverage
# （市场因子 + 本金因子）决定，nature 不再作为杠杆硬瓶颈。
# scalp 防插针由 V5 单笔风险硬顶 (max_loss ≤ equity×1.5%) 和 sl_cap (lev×sl≤50%) 兜底。
LEVERAGE_CAP_BY_NATURE = {
    "scalp":        20,  # 原 8 → 20：由动态杠杆统一管，防插针交给 SL/V5 硬顶
    "intraday":     20,
    "swing":        20,
    "trend_follow": 20,
    "position":     20,
    None:           20,
}

# --- D5 — 相关性桶与桶级风险上限 ---------------------------------------
# Stage G P9: 桶划分基于 1h + 4h 两套相关矩阵都 ≥0.7 的约束
# 事实依据: docs/research/symbol_correlation_matrix.csv
#   BTC-ETH=0.91, BTC-BNB=0.84, BTC-SOL=0.82, BTC-VIRTUAL=0.78
#   XPL 对任何币相关 ≤ 0.42, ASTER 相关均在 0.54-0.57 中间带
CORRELATION_BUCKETS = [
    {
        "name": "majors",
        "symbols": ["BTC", "ETH", "BNB", "SOL", "VIRTUAL"],
        "max_concurrent_positions": 3,
        "bucket_risk_cap_pct": 0.30,
    },
    {
        "name": "mid-alt",
        "symbols": ["ASTER"],
        "max_concurrent_positions": 1,
        "bucket_risk_cap_pct": 0.15,
    },
    {
        "name": "indep",
        "symbols": ["XPL"],
        "max_concurrent_positions": 1,
        "bucket_risk_cap_pct": 0.15,
    },
]

# --- D7 — 利润保护新增档 + trailing ------------------------------------
# Stage G P7: profit_lock_2 对 x-high 带不生效（XPL 单波段 5-10% 太密）
TIER_PROTECTION_PARAMS_V2_EXTRAS = {
    "profit_lock_2": {
        "trigger_pct": 0.035,
        "exit_ratio":  0.30,
        "excluded_vol_bands": ["x-high"],
    },
    "atr_trailing": {
        "activate_after_pct": 0.020,
        "atr_mult":           2.0,
    },
}

# --- D8 — 无 trade_nature 标签的兜底规则 ------------------------------
# Stage G P8: 按 expected_hold_hours 猜；兜底退回 intraday
DEFAULT_TRADE_NATURE_FOR_MISSING = os.getenv("DEFAULT_TRADE_NATURE_FOR_MISSING", "intraday")
LOG_ALARM_ON_MISSING_NATURE: bool = os.getenv("LOG_ALARM_ON_MISSING_NATURE", "true").lower() == "true"
TRADE_NATURE_BY_HOLD_HOURS = [
    (2,   "scalp"),
    (8,   "intraday"),
    (48,  "swing"),
    (10**6, "position"),
]

# --- D9 — 样本不足币种的仓位打折 ---------------------------------------
# Stage G P2: scale = sqrt(min(1, n_bars/min_daily_bars))，上线初值 0.77/0.82
SAMPLE_INSUFFICIENT_SYMBOLS = {
    "ASTER": {"min_daily_bars": 365, "bootstrap_scale": 0.77},
    "XPL":   {"min_daily_bars": 365, "bootstrap_scale": 0.82},
}

# --- Stage E feature flags（全部默认 off，分 3 批逐级打开；见 cross_review.md P5） ---
RISK_STAGE_E_ENABLED:                 bool = os.getenv("RISK_STAGE_E_ENABLED", "true").lower() == "true"
RISK_USE_VOL_BAND_DEFAULTS:           bool = os.getenv("RISK_USE_VOL_BAND_DEFAULTS", "true").lower() == "true"
RISK_USE_VOL_BAND_ATR_MULT:           bool = os.getenv("RISK_USE_VOL_BAND_ATR_MULT", "true").lower() == "true"
RISK_USE_VOL_BAND_X_HIGH:             bool = os.getenv("RISK_USE_VOL_BAND_X_HIGH", "true").lower() == "true"
# 注：原 RISK_USE_LEVERAGE_CAP_BY_BAND / CORR_BUCKETS / PROFIT_LOCK_2 /
# ATR_TRAILING / SAMPLE_INSUFFICIENT_SCALE / MIGRATE_FLYING_POSITIONS
# 六个 flag 已删除——定义后从未被任何代码读取（功能或未实现或已硬编码）
# ══════════════════════════════════════════════════
#  Stage E P2 扩展常量（治本版）
#  D10–D15: 对齐"三周期错位"事实梳理 + 候选方案
#  事实支撑: 2026-04-22 账户诊断
#    - 三周期实际 SL 带宽几乎重叠 (short 2.5-3% / long 3-4.5%)
#    - 三周期实际持仓中位 5h 左右，完全不分层
#    - long tier 66% 用 ≥15x 杠杆（反常识）
#    - long tier 54% 由 master_running_reduce 退出（没有战略出场）
# ══════════════════════════════════════════════════

# --- D11 — 三周期硬拉开 ≥3 倍的 TP/SL 默认 ---
# 注意: 本表覆盖 D1 的 TIER_TP_SL_DEFAULTS_BY_VOL_BAND（当 flag on 时取 V2）
# 设计原则:
#   short: SL 1.5-2.0%, TP 2-3%            (scalp 级，与 15m K 线尺度对齐)
#   mid:   SL 3-4%,     TP 5-7%            (intraday 级，与 1h 尺度对齐)
#   long:  SL 6-10%,    TP 15-25%（分批）   (trend 级，与 1d 尺度对齐；TP 由 D14 分批机制接管)
TIER_TP_SL_DEFAULTS_V2 = {
    # 2026-04-27 修复: short tier TP:SL 至少 2:1，避免负期望（微利止盈 / 频繁止损）
    # 原 low short tp=2%/sl=1.5% (1.33:1) → 新 tp=5%/sl=2.5% (2:1)
    # long SL 保持 ≥ short SL × 3 (D11)
    "low": {
        "short": {"tp_pct": 0.050, "sl_pct": 0.025},
        "mid":   {"tp_pct": 0.050, "sl_pct": 0.035},
        "long":  {"tp_pct": 0.000, "sl_pct": 0.080},   # TP=0 交给 D14 分批 TP；3×2.5%=7.5%→取 8%
    },
    "mid": {
        "short": {"tp_pct": 0.060, "sl_pct": 0.030},
        "mid":   {"tp_pct": 0.065, "sl_pct": 0.045},
        "long":  {"tp_pct": 0.000, "sl_pct": 0.095},   # 3×3%=9%→取 9.5%
    },
    "high": {
        "short": {"tp_pct": 0.080, "sl_pct": 0.040},
        "mid":   {"tp_pct": 0.090, "sl_pct": 0.060},
        "long":  {"tp_pct": 0.000, "sl_pct": 0.120},   # 3×4%=12%
    },
    "x-high": {
        "short": {"tp_pct": 0.110, "sl_pct": 0.055},
        "mid":   {"tp_pct": 0.120, "sl_pct": 0.080},
        "long":  {"tp_pct": 0.000, "sl_pct": 0.165},   # 3×5.5%=16.5%
    },
}

# --- D12 — 动态杠杆（替代固定 tier 专属上限） ---
# 所有 tier 统一使用相同的动态杠杆，根据实时市场情况在 5-20x 区间自适应调整。
# AI 置信度 + 波动率/资金费率/市场状态/回撤 共同决定最终杠杆。
DYNAMIC_LEVERAGE_MIN: float = float(os.getenv("DYNAMIC_LEVERAGE_MIN", "5.0"))
DYNAMIC_LEVERAGE_MAX: float = float(os.getenv("DYNAMIC_LEVERAGE_MAX", "20.0"))

# 全局手动配置交易对的杠杆 band 上限（波动带 D3 分级不变）
MANUAL_SYMBOL_LEVERAGE_CAP: float = float(
    os.getenv("MANUAL_SYMBOL_LEVERAGE_CAP", os.getenv("DYNAMIC_LEVERAGE_MAX", "20.0"))
)

# Factor weights for dynamic leverage calculation (market factors sum = 1.0)
# 2026-06-22: 本金因子改走独立乘数通道（_calc_equity_mult, mult∈[0.5,1.5]），
# 不再占 risk 权重，故 4 个市场因子权重恢复原值。
DYNAMIC_LEVERAGE_VOLATILITY_WEIGHT:  float = float(os.getenv("DYNLEV_VOL_WEIGHT",  "0.40"))
DYNAMIC_LEVERAGE_FUNDING_WEIGHT:     float = float(os.getenv("DYNLEV_FUNDING_WEIGHT", "0.25"))
DYNAMIC_LEVERAGE_REGIME_WEIGHT:      float = float(os.getenv("DYNLEV_REGIME_WEIGHT", "0.20"))
DYNAMIC_LEVERAGE_DRAWDOWN_WEIGHT:    float = float(os.getenv("DYNLEV_DRAWDOWN_WEIGHT", "0.15"))
# 本金因子（独立通道，不占上述权重）：小本金 mult>1 放大，大本金 mult<1 压低
# 公式: mult = clamp((equity_ref / equity)^0.4, 0.5, 1.5)
DYNAMIC_LEVERAGE_EQUITY_REF:         float = float(os.getenv("DYNLEV_EQUITY_REF",  "5000.0"))  # 基准本金(USDT)

# Legacy tier cap (保留兼容，但默认关闭 —— 由动态杠杆接管)
# 与动态杠杆范围 5-20x 对齐；long 略保守
LEVERAGE_CAP_BY_TIER = {
    "short": 20,
    "mid":   20,
    "long":  12,
}

# --- D10 — long tier 使用 1d ATR 数据源的倍率 ---
# 事实: 现 long 用 1h ATR × 4 冒充 4h ATR，√t 定律只应 ×2；改读 1d ATR
# 1d ATR 的倍数设计（在真实 1d ATR 上直接乘）：
LONG_TIER_ATR_1D_MULTIPLIER = {
    "low":    2.5,   # BTC 1d ATR_P50=1.87% × 2.5 ≈ 4.7% → 配合 D11 sl=6% 取较宽
    "mid":    2.0,
    "high":   1.8,
    "x-high": 1.5,   # XPL 1d ATR_P50 极大，小倍数即可
}

# --- D13 — long tier 出场保护 ---
# long tier 只允许 SL/TP/人工/强平 四类退出；对 master_running_reduce / ai_reverse 免疫
#
# [补齐修复 2026-07-19，对应 04 综合方案 §3.2 P0-1] 此前只有 master_running /
# master_running_reduce 在列表里，缺了实盘里占比最高的 master_running_close
# （Master 主动全平的具体标签），导致 long tier 实际上完全没被保护到——审计报告
# 显示 33% 的 mid/long 平仓由 master_running_close 触发、胜率仅 16%。现在补上，
# 与下方 MID_TIER_PROTECTED_FROM 对齐。
LONG_TIER_PROTECTED_FROM = {
    "master_running_reduce",
    "master_running",           # 主控主动全平（AI 非 SL 类）
    "master_running_close",     # 主控主动全平的实际标签（此前缺失，是 long 保护失效的根因）
    "master_defensive_reduce",  # 防御性减仓
    "ai_reverse",               # AI 反向信号
}
# 不屏蔽的：sl, tp, tp_target, profit_lock_*, emergency_drawdown, manual, 强平

# ─────────────────────────────────────────────────────────────────
# S0-6 止血修复（R3）：mid tier 出场保护（2026-07-19，对应 04 综合方案 §3.2）
#
# 根因（审计报告）：LONG_TIER_PROTECTED_FROM 只保护 long tier，mid tier 无对应保护，
# 导致 master_running_close 在 mid/long 占比 33%（胜率仅 16%）—— Master 微亏越权砍中长线，
# 与"中长线让利润奔跑"哲学冲突。
# 修复：新增 MID_TIER_PROTECTED_FROM + RISK_USE_MID_TIER_IMMUNE flag，
#      复用 is_close_reason_blocked_for_long 的同一套机制扩展到 mid。
# 默认 enforce（RISK_USE_MID_TIER_IMMUNE=true）—— 影子模式可设 false 秒回退。
# ─────────────────────────────────────────────────────────────────
MID_TIER_PROTECTED_FROM = {
    "master_running_reduce",
    "master_running_close",
    "master_running",            # 兼容多种 close_reason 标签
    "master_defensive_reduce",
    "ai_reverse",
}
# 不屏蔽的：sl, tp, tp_target, profit_lock_*, emergency_drawdown, manual, 强平, invalidation
RISK_USE_MID_TIER_IMMUNE: bool = os.getenv(
    "RISK_USE_MID_TIER_IMMUNE", "true"
).lower() in ("true", "1", "yes", "on")

# --- D14 — long tier 分批战略 TP ---
# 取代 long tier 单一 TP 点位，改为"浮盈 %" 分档
LONG_TIER_STAGED_TP = {
    # [P0-1 修复] 原 8%/15%/25% 触发线（TP1=5.6% 价格）从未触发——全库 long tier
    # peak 上限 4.64%；下修到 4%/8%/12%（TP1 触发线 2.8%）进入实际 peak 可达区间。
    "stages": [
        {"trigger_pnl_pct": 0.04, "exit_ratio": 0.30},   # TP1: 浮盈 4% 减 30%
        {"trigger_pnl_pct": 0.08, "exit_ratio": 0.30},   # TP2: 浮盈 8% 再减 30%
        {"trigger_pnl_pct": 0.12, "exit_ratio": 0.30},   # TP3: 浮盈 12% 再减 30%
    ],
    "trailing_after_final_stage": {
        "activate_after_pnl_pct": 0.12,
        "atr_mult":               2.0,
    },
    # 浮盈 % 的计算基准：
    #   "entry_price"  — 相对开仓价的 PnL% （默认，不看杠杆）
    #   "margin"       — 相对保证金的 PnL%（= entry_price × leverage）
    # 用 entry_price 是因为"战略 TP"应该独立于杠杆决策
    "pnl_basis": "entry_price",
}

# --- D15 — 三周期 prompt 片段（trading_analysts 会 import） ---
# [2026-08-10 v3.1.0] mid/long 与 docs/opencode/prompts/tasks 模板对齐：
# 去掉 SL/杠杆硬编码（AI 主导：SL 宽度/杠杆由 LLM 依据波动率与结构自主决定，
# 系统仅以 5 条物理风险底线 + 执行层动态杠杆约束）。
TIER_PROMPT_HINTS = {
    "short": (
        "SHORT (scalp, 5-15min K): 快进快出，对错 30 分钟内必须平仓；"
        "SL ≤ 3.5%；杠杆 5-20x（系统动态控制）；持仓目标 < 2h；仅高置信度/明确形态才开。"
    ),
    "mid": (
        "MID (swing, 15m/1h/4h K): 聚焦 15m/1h/4h 结构验证（回调/突破/量价确认），"
        "1d 作为趋势方向锚；持仓目标 2-8 小时，至少 12h 内不得主动全平"
        "（与系统 min_hold 12h 保护一致，紧急亏损除外）；"
        "SL 宽度/杠杆由你依据波动率与结构自主决定（系统动态控制）。"
    ),
    "long": (
        "LONG (trend, 4h/1d/1w/1M K): 聚焦大周期结构+生命周期阶段+宏观背景；"
        "1h/4h 仅作入场择时（由 mid_view 承载）；持仓目标 3-7 天，至少 72h 内不得主动全平；"
        "开仓后 3 天(72h) 内不得主动减仓（SL/TP 硬止损止盈不受此限制）；"
        "SL 宽度/杠杆由你依据 ATR 与结构自主决定（系统动态控制）；"
        "仅在明确周级突破/多周期共振时才开仓。"
    ),
}

# --- P2 feature flags（默认 on，V2 TP/SL + 杠杆上限 + long ATR + long 免疫 + 分批 TP） ---
RISK_P2_ENABLED:                       bool = os.getenv("RISK_P2_ENABLED", "true").lower() == "true"
RISK_USE_TIER_TP_SL_V2:                bool = os.getenv("RISK_USE_TIER_TP_SL_V2", "true").lower() == "true"
# 开仓时用网格训练出的 (tp_pct,sl_pct) 覆盖静态表（见 backend/data/tp_sl_learned/latest.json）
RISK_USE_LEARNED_TP_SL:                bool = os.getenv("RISK_USE_LEARNED_TP_SL", "true").lower() in (
    "true", "1", "yes", "on",
)
# 每日自动网格训练 TP/SL（05:00 + 启动补训）
RISK_TP_SL_TRAIN_AUTO:                 bool = os.getenv("RISK_TP_SL_TRAIN_AUTO", "true").lower() in (
    "true", "1", "yes", "on",
)
RISK_USE_LEVERAGE_CAP_BY_TIER:         bool = os.getenv("RISK_USE_LEVERAGE_CAP_BY_TIER", "true").lower() == "true"
DYNAMIC_LEVERAGE_ENABLED:              bool = os.getenv("DYNAMIC_LEVERAGE_ENABLED", "true").lower() == "true"
RISK_USE_LONG_TIER_1D_ATR:             bool = os.getenv("RISK_USE_LONG_TIER_1D_ATR", "true").lower() == "true"
RISK_USE_LONG_TIER_IMMUNE:             bool = os.getenv("RISK_USE_LONG_TIER_IMMUNE", "true").lower() == "true"
# [P0-10 双重减仓收口] 默认 false：RISK_V2_UNIFIED_STAGED_TP=true 时统一分段止盈是唯一权威，
# long_tier_staged_tp 若同时开，long 仓会被两套引擎各自减仓（_dim_staged_tp 无 v2 门）。
RISK_USE_LONG_TIER_STAGED_TP:          bool = os.getenv("RISK_USE_LONG_TIER_STAGED_TP", "false").lower() == "true"
RISK_USE_TIER_PROMPT_HINTS:            bool = os.getenv("RISK_USE_TIER_PROMPT_HINTS", "true").lower() == "true"

# --- B方案：趋势健康分 / 退出编排 / 双Agent ---
# 2026-06-19: 默认改 off。DirectionAgent 已被三层专家替代（ScalpRouter/SwingAgent/TrendAgent）。
# DirectionAgent 对 swing/trend 的 LLM 决策被专家覆盖（纯浪费 8192-token），
# 且其 hold 阻止专家被调用。三层路由在 _execute_master_decisions 里独立决策，不依赖 DualAgent。
DUAL_AGENT_MODE: str = os.getenv("DUAL_AGENT_MODE", "off").lower()  # off/shadow/advisory/primary
# 注：原 DUAL_AGENT_ENTRY_ENABLED / EXIT_NATURES / FORCE_REDUCE_EVIDENCE_MIN
# 三个 flag 已删除——零读取，双 Agent 行为实际只由 DUAL_AGENT_MODE 控制

# short/intraday/scalp 硬门槛（绩效归因：短线 tier 累计亏损）
SHORT_TIER_CONFIDENCE_EXTRA: int = int(os.getenv("SHORT_TIER_CONFIDENCE_EXTRA", "8"))
SHORT_TIER_SAME_DIR_COOLDOWN_S: int = int(os.getenv("SHORT_TIER_SAME_DIR_COOLDOWN_S", "14400"))  # Live 默认 4h
# Paper 样本期同向冷却（秒）；未设时回退 SHORT_TIER_SAME_DIR_COOLDOWN_S
SHORT_TIER_SAME_DIR_COOLDOWN_PAPER_S: int = int(
    os.getenv("SHORT_TIER_SAME_DIR_COOLDOWN_PAPER_S", "600")
)
# true：跳过 short_tier 置信度双闸（V5 已判 conf）；只保留熔断+同向冷却
SHORT_TIER_SKIP_CONFIDENCE: bool = os.getenv(
    "SHORT_TIER_SKIP_CONFIDENCE", "true"
).strip().lower() in ("true", "1", "yes", "on")
SHORT_TIER_DISABLED_NATURES: str = os.getenv("SHORT_TIER_DISABLED_NATURES", "")  # 例: scalp
RISK_USE_NATURE_EXIT_ORCHESTRATOR: bool = os.getenv("RISK_USE_NATURE_EXIT_ORCHESTRATOR", "true").lower() == "true"

# Phase B+C: 统一分段止盈/利润回撤/追踪/止盈安全网全部收敛进 _run_v2_protection。
# 开启后: 1) v2 内部跑 ATR 自适应的 staged TP + drawdown + trailing + 80% 利润上限安全网;
#        2) PEO 的 nature_staged_tp.check 被旁路(避免与 v2 双触发)。
# 默认 true(Phase B+C 目标态); 关闭则回退到 v2 原行为 + PEO staged TP。
RISK_V2_UNIFIED_STAGED_TP: bool = os.getenv("RISK_V2_UNIFIED_STAGED_TP", "true").lower() == "true"
# TP 安全网利润上限(未杠杆 PnL%); 超过即全平。从 v1 的 _TP_SAFETY_NET_BY_NATURE 复活为统一硬上限。
RISK_V2_TP_SAFETY_NET_CAP: float = float(os.getenv("RISK_V2_TP_SAFETY_NET_CAP", "0.80") or "0.80")

NATURE_HEALTH_PROFILES = {
    "trend_follow": {"review_threshold": 45.0, "chandelier_atr_mult": 3.0},
    "position": {"review_threshold": 50.0, "chandelier_atr_mult": 3.5},
    "swing": {"review_threshold": 40.0, "chandelier_atr_mult": 2.0},
    "intraday": {"review_threshold": 35.0, "chandelier_atr_mult": 1.5},
    "scalp": {"review_threshold": 30.0, "chandelier_atr_mult": 1.0},
}

NATURE_EXIT_PROFILES = {
    "trend_follow": {
        "stages": [
            {"trigger_pnl_pct": 0.08, "exit_ratio": 0.25},
            {"trigger_pnl_pct": 0.15, "exit_ratio": 0.25},
            {"trigger_pnl_pct": 0.25, "exit_ratio": 0.30},
        ],
        "chandelier": {"activate_pnl_pct": 0.08, "atr_mult": 3.0},
        "trailing_final": {"atr_mult": 2.0, "min_band_pct": 0.003},
        "drawdown_threshold_adj": 0.10,
    },
    "position": {
        "stages": [
            {"trigger_pnl_pct": 0.10, "exit_ratio": 0.20},
            {"trigger_pnl_pct": 0.20, "exit_ratio": 0.25},
            {"trigger_pnl_pct": 0.35, "exit_ratio": 0.30},
        ],
        "chandelier": {"activate_pnl_pct": 0.10, "atr_mult": 3.5},
        "trailing_final": {"atr_mult": 2.5, "min_band_pct": 0.004},
        "drawdown_threshold_adj": 0.05,
    },
    "swing": {
        "stages": [
            {"trigger_pnl_pct": 0.08, "exit_ratio": 0.30},
            {"trigger_pnl_pct": 0.15, "exit_ratio": 0.30},
        ],
        "chandelier": {"activate_pnl_pct": 0.05, "atr_mult": 2.0},
        "trailing_final": {"atr_mult": 1.8, "min_band_pct": 0.003},
        "drawdown_threshold_adj": 0.0,
    },
    "intraday": {
        "stages": [
            {"trigger_pnl_pct": 0.05, "exit_ratio": 0.35},
            {"trigger_pnl_pct": 0.10, "exit_ratio": 0.35},
        ],
        "chandelier": {"activate_pnl_pct": 0.05, "atr_mult": 1.5},
        "trailing_final": {"atr_mult": 1.2, "min_band_pct": 0.002},
        "drawdown_threshold_adj": -0.05,
    },
    "scalp": {
        # V5: 1.5% 即落袋刚好喂手续费（往返成本 0.09% 名义 ≈ 杠杆后 1%+ 保证金），
        # 分批止盈起步 1.5%→2.5%，让单笔盈利能覆盖成本并留出利润
        "stages": [
            {"trigger_pnl_pct": 0.04, "exit_ratio": 0.35},
            {"trigger_pnl_pct": 0.07, "exit_ratio": 0.35},
        ],
        "chandelier": {"activate_pnl_pct": 0.04, "atr_mult": 1.2},
        "trailing_final": {"atr_mult": 1.0, "min_band_pct": 0.0015},
        "drawdown_threshold_adj": -0.10,
    },
}

# ══════════════════════════════════════════════════════════════════════
# P3 — 决策协调治本版（2026-04-22 起）
# 目的：解决"多个决策层各自为政 → master_running_reduce 胜率 5%、
#      ai_reverse 胜率 17%、同 symbol 连续反向链"等协调性问题。
#
# 证据锚：
#   - docs/research/decisions_p3.md 或 stage_f_runbook §十一
#   - DB 查询 (7天):
#       master_running_reduce  20 次 胜率 5%   总亏 -12U
#       master_running         5 次  胜率 0%   总亏 -22U
#       ai_reverse            12 次 胜率 17%  总亏 -17U
#
# 本批不重训 LLM、不改 prompt、不引入 DRL 协调器。
# ══════════════════════════════════════════════════════════════════════

# M1 — master close/reduce 硬事实门控：按 tier 差异化的"最小必要浮亏"
# 关键含义：如果 LLM 说要 close/reduce，但浮亏还没到这个阈值（且没穿 SL），就不准执行
# 数字基于 P2 D11 的 tier SL 范围略松（避免和 SL 系统抢活）
MASTER_CLOSE_MIN_LOSS_PCT_BY_TIER = {
    "short": 0.020,   # 2%  短线 SL 2% 左右，门控设一致
    "mid":   0.060,   # 6%  波段中线（2026-06-28 提高，防微亏早平）
    "long":  0.090,   # 9%  周级趋势仓，轻微波动不许平
}

# 禁止 master_*_close_tiny 微仓全平的 tier（中线/长线交给 SL/TP，禁止 AI 微亏秒平）
MASTER_CLOSE_TINY_DISABLED_TIERS: frozenset = frozenset(
    t.strip().lower()
    for t in os.getenv("MASTER_CLOSE_TINY_DISABLED_TIERS", "mid,long").split(",")
    if t.strip()
)

# 止盈/保本止盈后同向再开最低冷却（秒）；修复 tp 后 15 分钟内重复开仓
REENTRY_MIN_COOLDOWN_AFTER_TP_SEC: int = int(
    os.getenv("REENTRY_MIN_COOLDOWN_AFTER_TP_SEC", "1800")
)

# 锁定持仓时限热改：true 时 OpenCode/runtime_tuning 不得修改 tier_max_hold_sec
HOLD_TIME_TUNING_LOCKED: bool = os.getenv(
    "HOLD_TIME_TUNING_LOCKED", "true"
).lower() in ("true", "1", "yes", "on")

# SL 穿透率阈值：当前价距 entry 的距离 / SL 距 entry 的距离
#   ≥ 1.0 表示价格已触 SL
#   ≥ 1.5 表示深度穿透（SL 系统已失效，放行 AI 紧急平仓）
# 这一阈值沿用 full_auto_trading_service v6 close 门槛里的 1.5
MASTER_CLOSE_SL_BREACH_THRESHOLD = 1.5

# --- P3 feature flags（默认全部 off；按批次在 .env 里逐条打开）---
# 总闸：关掉后下面所有 P3 逻辑都短路到"不影响现状"
RISK_P3_ENABLED:                       bool = os.getenv("RISK_P3_ENABLED", "true").lower() == "true"

# M1 master close/reduce 硬事实门控：off | shadow | enforce
#   off     — 彻底关闭（默认）
#   shadow  — 只记日志、不拦截（批次 P3-B 用）
#   enforce — 真正拦截（批次 P3-C 用）
RISK_P3_MASTER_CLOSE_REQUIRES_HARDFACT: str = os.getenv("RISK_P3_MASTER_CLOSE_REQUIRES_HARDFACT", "enforce").lower()

# Swing/Trend Agent Fact Guard：off | shadow | enforce（默认 shadow 只审计不拦截）
AGENT_FACT_GUARD_MODE: str = os.getenv("AGENT_FACT_GUARD_MODE", "shadow").lower()

# ── 中线/长线 Agent 全局升级 v2 Feature Flags ──
MIDLONG_ORCH_SNAPSHOT_V2: bool = os.getenv("MIDLONG_ORCH_SNAPSHOT_V2", "true").lower() in (
    "1", "true", "yes", "on",
)
MIDLONG_AGENT_SL_TO_EXECUTE: bool = os.getenv("MIDLONG_AGENT_SL_TO_EXECUTE", "true").lower() in (
    "1", "true", "yes", "on",
)
MIDLONG_QUANT_BRIEF_ENABLED: bool = os.getenv("MIDLONG_QUANT_BRIEF_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
ORCH_MID_INDEPENDENT_TRIGGER: bool = os.getenv("ORCH_MID_INDEPENDENT_TRIGGER", "true").lower() in (
    "1", "true", "yes", "on",
)
MIDLONG_PERSISTENCE_TICKS: int = int(os.getenv("MIDLONG_PERSISTENCE_TICKS", "1") or "1")

# ── 中长线激活总开关（阶段一 A1）──
# 诊断发现：中线/长线最近 8 天几乎停止开新仓（长线 +2 笔、中线后 3 天 0 笔），
# 账面盈利是历史存量。根因是多处"人为锁死"叠加（长线周上限 2、门槛被 runtime_tuning
# 抬高、独立循环每 tick 仅扫 1 币）。本开关统一门控阶段一的放宽项，便于一键回滚。
# 默认 true（当前仅模拟盘）；置 false 即恢复改造前的保守约束。
MIDLONG_ACTIVATION_ENABLED: bool = os.getenv("MIDLONG_ACTIVATION_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
# 长线每周开单上限（旧，保留向后兼容）。
TREND_MAX_OPENS_PER_WEEK: int = int(
    os.getenv("TREND_MAX_OPENS_PER_WEEK", "6" if MIDLONG_ACTIVATION_ENABLED else "2") or "6"
)
# 长线并发持仓上限（新，修复 LongWeeklyCap 过度拦截）。
# 统计当前 open 持仓数而非历史开仓订单数。仓位平掉即释放配额。
TREND_MAX_CONCURRENT_LONG: int = int(os.getenv("TREND_MAX_CONCURRENT_LONG", "10"))
# 中长线独立循环每 tick 扫描的 symbol 数（阶段一 A3）。原实现每 tick 只扫 1 个币且
# mid/long 交替，6 个币轮一圈耗时数分钟，长线 tick 90s → 覆盖率极低、常"轮不到"。
# 提高到 3 可显著提升扫描覆盖，同时受 _midlong_loop_running 串行保护不会并发爆炸。
MIDLONG_SCAN_BATCH: int = int(
    os.getenv("MIDLONG_SCAN_BATCH", "3" if MIDLONG_ACTIVATION_ENABLED else "1") or "3"
)
MIDLONG_MONTE_CARLO_ENABLED: bool = os.getenv("MIDLONG_MONTE_CARLO_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
# ── 中长线主动退出（阶段二 B2）──
# 诊断：中长线仓位大量靠 max_hold_timeout（到 48h/7d）被动了结，论点已破却还在扛。
# 开启后：当多周期编排器对应周期 bias 强烈反向（论点失效）时，主动平仓，而不是死等超时。
# 复用已算好的 orchestrator bias，不引入额外重计算；默认跟随中长线激活总开关。
MIDLONG_ACTIVE_EXIT_ENABLED: bool = os.getenv(
    "MIDLONG_ACTIVE_EXIT_ENABLED", "true" if MIDLONG_ACTIVATION_ENABLED else "false"
).lower() in ("1", "true", "yes", "on")
# 反向 bias 置信度 ≥ 此值 → 论点失效，主动平仓
MIDLONG_EXIT_INVALIDATE_CONF: float = float(os.getenv("MIDLONG_EXIT_INVALIDATE_CONF", "0.68"))
# 仓位需已持有的最短时间（秒）才允许主动失效退出，避免刚开就被反向噪声打出（默认 1h）
MIDLONG_EXIT_MIN_HOLD_SEC: int = int(os.getenv("MIDLONG_EXIT_MIN_HOLD_SEC", "3600"))
# ── 中长线持仓管理模式（Phase 5）──
# 开仓后分析大脑从「入场思维」切换到「持仓发展思维」：有仓时不再做入场分析，
# 而是围绕持仓做六维发展分析（方向延续/滚仓/TP-SL调整/DCA/分批止盈/反转离场）。
MIDLONG_POSITION_MGMT_ENABLED: bool = os.getenv("MIDLONG_POSITION_MGMT_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
# 模式 B 整体执行节流（秒）：0=随 tick（默认 ~120s）；>0 则两次分析间隔不小于该值。
# 注意：即使为 0，规则维度（分批止盈/反转离场）仍每 tick 跑；LLM 维度受
# MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC 单独节流。
MIDLONG_POSITION_MGMT_INTERVAL_SEC: int = int(os.getenv("MIDLONG_POSITION_MGMT_INTERVAL_SEC", "0") or "0")
# LLM 维度（方向复查/滚仓判断）节流（秒），默认 15 分钟——控制 LLM 成本，
# 同时复用 exit_state_json.last_trend_review_ts 让 90min run_trend_review 兜底休眠。
MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC: int = int(os.getenv("MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC", "900") or "900")
# 仅浮盈滚仓：浮亏（含保本）时禁止任何加仓（加密永续亏损加仓=自杀原则）
MIDLONG_POSITION_MGMT_PYRAMID_ONLY_PROFIT: bool = os.getenv(
    "MIDLONG_POSITION_MGMT_PYRAMID_ONLY_PROFIT", "true"
).lower() in ("1", "true", "yes", "on")
# [P1-1] 滚仓规则直通：保证金口径浮盈 > 此值且 review 方向 valid(hold/tighten) 时，
# 跳过 LLM wait 直接进 5 层门控（修复 LLM 从未判 add 导致滚仓 0 执行）
MIDLONG_POSITION_MGMT_PYRAMID_DIRECT_PNL: float = float(
    os.getenv("MIDLONG_POSITION_MGMT_PYRAMID_DIRECT_PNL", "0.05")
)
# [P0-2] 浮盈 tighten 保护：保证金口径浮盈 > 此值时，收紧 SL 不得越过 entry±MIDLONG_TIGHTEN_SL_FLOOR
MIDLONG_TIGHTEN_PROFIT_FLOOR: float = float(os.getenv("MIDLONG_TIGHTEN_PROFIT_FLOOR", "0.015"))
MIDLONG_TIGHTEN_SL_FLOOR: float = float(os.getenv("MIDLONG_TIGHTEN_SL_FLOOR", "0.01"))
# [DEPRECATED — 阶段4] 原 SwingAgent 独立分支的 QuantBrief 对齐阈值；该分支已删除，
# 中线对齐现由 long thesis 的 mid_view + decision_hub mid_timing 权重统一处理。
# 保留变量定义避免 env_registry/旧调用点报 AttributeError；新代码不应再读取。
SWING_MIN_ALIGNMENT: int = int(os.getenv("SWING_MIN_ALIGNMENT", "6") or "6")
TREND_MIN_ALIGNMENT: int = int(os.getenv("TREND_MIN_ALIGNMENT", "6") or "6")
MIDLONG_ORCH_STALE_REFRESH_SEC: int = int(os.getenv("MIDLONG_ORCH_STALE_REFRESH_SEC", "900") or "900")

# ── MLTO (MidLong Thesis Orchestrator) ──
MIDLONG_THESIS_LEDGER_ENABLED: bool = os.getenv("MIDLONG_THESIS_LEDGER_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
# true=中长线开单走 MLTO 新链路（ingest → LLM thesis_update → Hub → open_gate）
# false=回退旧路径 SwingAgent/TrendAgent.analyze 直接开单（仅兼容/对照）
MIDLONG_MLTO_CONTROLS_EXEC: bool = os.getenv("MIDLONG_MLTO_CONTROLS_EXEC", "true").lower() in (
    "1", "true", "yes", "on",
)
# MidLong v2 Single Writer：trend | mlto。空则按 trading_mode 智能默认（paper→mlto）。
# 禁止 dual：同一时刻只有一个组件可发中长线新开。
MIDLONG_EXEC_AUTHORITY: str = os.getenv("MIDLONG_EXEC_AUTHORITY", "mlto").strip().lower()
# Master 对中长线：summary=不重跑深度 LLM（减负）；full=旧行为
MASTER_MIDLONG_LLM_MODE: str = os.getenv("MASTER_MIDLONG_LLM_MODE", "summary").strip().lower()
# Paper TrendAgent 分数地板（低于则 hold）；略低于旧 38 以减少样本期全 hold
TREND_PAPER_SCORE_FLOOR: int = int(os.getenv("TREND_PAPER_SCORE_FLOOR", "32"))
# true：LLM should_open=true 且 score≥floor-5 时允许缩仓开
TREND_TRUST_SHOULD_OPEN_SOFT: bool = os.getenv(
    "TREND_TRUST_SHOULD_OPEN_SOFT", "true"
).strip().lower() in ("1", "true", "yes", "on")
# MidLong v2 Phase2：Paper Hub 门槛（Live 仍用 AI_FIRST / 旧表）
MIDLONG_HUB_WAIT_PAPER: float = float(os.getenv("MIDLONG_HUB_WAIT_PAPER", "0.28"))
MIDLONG_HUB_NIBBLE_PAPER: float = float(os.getenv("MIDLONG_HUB_NIBBLE_PAPER", "0.36"))
MIDLONG_HUB_BUILD_PAPER: float = float(os.getenv("MIDLONG_HUB_BUILD_PAPER", "0.55"))
# Trend should_open 且方向与 Hub 一致时 adj 加成（封顶 1.0）
MIDLONG_HUB_TREND_SIGNAL_BONUS: float = float(
    os.getenv("MIDLONG_HUB_TREND_SIGNAL_BONUS", "0.05")
)
# Paper 震荡市允许缩仓试探；Live 默认禁止 ranging 新开
MIDLONG_ALLOW_RANGE_PROBE: bool = os.getenv(
    "MIDLONG_ALLOW_RANGE_PROBE", "true"
).strip().lower() in ("1", "true", "yes", "on")
# WAIT 时是否 Paper 探针开仓（默认关，避免 WAIT 被静默改成成交）
MIDLONG_PAPER_PROBE_ON_WAIT: bool = os.getenv(
    "MIDLONG_PAPER_PROBE_ON_WAIT", "false"
).strip().lower() in ("1", "true", "yes", "on")
# Paper：Hub=NIBBLE 但方向落在 AI 中性带(0.45-0.55)时，用更软门槛给方向试探
# （否则 direction_to_action→hold，NIBBLE 永远转化不成开仓）
MIDLONG_NIBBLE_PROBE_ENABLED: bool = os.getenv(
    "MIDLONG_NIBBLE_PROBE_ENABLED", "true"
).strip().lower() in ("1", "true", "yes", "on")
MIDLONG_NIBBLE_PROBE_DAILY_MAX: int = int(
    os.getenv("MIDLONG_NIBBLE_PROBE_DAILY_MAX", "2") or "2"
)
# 探针仓相对 NIBBLE 保证金再打折（默认再 ×0.5 → 约 7.5% 档）
MIDLONG_NIBBLE_PROBE_MARGIN_MULT: float = float(
    os.getenv("MIDLONG_NIBBLE_PROBE_MARGIN_MULT", "0.5") or "0.5"
)
# MidLong v2 Phase4：概念信念闭环（失败 Intent → 信念 → prompt/OWM）
MIDLONG_BELIEF_LOOP_ENABLED: bool = os.getenv(
    "MIDLONG_BELIEF_LOOP_ENABLED", "true"
).strip().lower() in ("1", "true", "yes", "on")
# [2026-08-10 问题三 / 2026-08-12 接通] AI 中线通道：符号主源 = 平台看板
# coin_select_candidates（horizon=midlong, approve, conf≥MIDLONG_AI_MIN_CONF），
# 经 get_ai_mid_candidates_for_session 粘性输出（槽位 ≤3），与固定长线白名单正交。
# 看板无合格项时才兜底短线 auto_coin_symbols。
MIDLONG_MID_VIA_MLTO: bool = os.getenv("MIDLONG_MID_VIA_MLTO", "false").strip().lower() in (
    "1", "true", "yes", "on",
)
# [2026-08-15 因子化] 中线因子路由：旧 AI 中线（MLTO mid thesis）停用后，
# 中线入场决策由「通过 4h/1d 样本外闸门的活跃因子」合成信号驱动。
# 开启后 midlong_loop 对中线宇宙逐币调用 factor_route_decide，buy/sell 走
# execute_midlong_open(source=factor_route)（authority=mlto 时放行）。
MIDLONG_MID_VIA_FACTOR_ROUTE: bool = os.getenv(
    "MIDLONG_MID_VIA_FACTOR_ROUTE", "false"
).strip().lower() in ("1", "true", "yes", "on")
# 因子路由入场参数：最少活跃因子数 / 合成分数阈值 / 止损止盈 / 权重衰减
FACTOR_ROUTE_MIN_ACTIVE_FACTORS: int = int(os.getenv("FACTOR_ROUTE_MIN_ACTIVE_FACTORS", "2") or "2")
FACTOR_ROUTE_ENTRY_THRESHOLD: float = float(os.getenv("FACTOR_ROUTE_ENTRY_THRESHOLD", "0.35") or "0.35")
FACTOR_ROUTE_SL_PCT: float = float(os.getenv("FACTOR_ROUTE_SL_PCT", "0.05") or "0.05")
FACTOR_ROUTE_TP_PCT: float = float(os.getenv("FACTOR_ROUTE_TP_PCT", "0.10") or "0.10")
# 因子路由单笔保证金比例（占权益）：小资金账户（权益~400）在 10x 杠杆口径下，
# 默认 1.0 档会估出 4000 名义 → 净敞口 1000% 被组合风控永久拦截。
# 0.12 → 480 名义 = 120% 敞口，在 MIDLONG_MAX_NET_EXPOSURE_PCT(1.5) 之内。
FACTOR_ROUTE_TRANCHE_MARGIN_PCT: float = float(
    os.getenv("FACTOR_ROUTE_TRANCHE_MARGIN_PCT", "0.12") or "0.12"
)
# AI 中线候选最低置信度：看板 midlong approve 且 confidence ≥ 此值才进 mid 槽。
MIDLONG_AI_MIN_CONF: float = float(os.getenv("MIDLONG_AI_MIN_CONF", "0.60") or "0.60")
MIDLONG_QUANT_BRIEF_HARD_GATE: bool = os.getenv("MIDLONG_QUANT_BRIEF_HARD_GATE", "false").lower() in (
    "1", "true", "yes", "on",
)
# v6：物理安全网默认开（数据/白名单/recommend_open/funding 清算级）；chop 仅 soft。
MIDLONG_THESIS_OPEN_GATE: bool = os.getenv("MIDLONG_THESIS_OPEN_GATE", "true").lower() in (
    "1", "true", "yes", "on",
)
MIDLONG_THESIS_DEBATE_ENABLED: bool = os.getenv("MIDLONG_THESIS_DEBATE_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
MIDLONG_TRANCHE_ENTRY_ENABLED: bool = os.getenv("MIDLONG_TRANCHE_ENTRY_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
MIDLONG_THESIS_REGIME_RESET: bool = os.getenv("MIDLONG_THESIS_REGIME_RESET", "true").lower() in (
    "1", "true", "yes", "on",
)


def _midlong_int_setting(env_key: str, *, ai_first_default: int, legacy_default: int) -> int:
    raw = os.getenv(env_key, "").strip()
    if raw:
        return int(raw)
    return ai_first_default if MIDLONG_AI_MANDATORY else legacy_default


# ai_first 下默认放宽门控（修复：原 45/50 过高导致 readiness 差 1-2 分被拦）
MIDLONG_OPEN_READINESS_MIN_MID: int = _midlong_int_setting(
    "MIDLONG_OPEN_READINESS_MIN_MID", ai_first_default=38, legacy_default=72,
)
MIDLONG_OPEN_READINESS_MIN_LONG: int = _midlong_int_setting(
    "MIDLONG_OPEN_READINESS_MIN_LONG", ai_first_default=40, legacy_default=78,
)
MIDLONG_THESIS_STABLE_MIN_SEC_MID: int = _midlong_int_setting(
    "MIDLONG_THESIS_STABLE_MIN_SEC_MID", ai_first_default=0, legacy_default=1800,
)
MIDLONG_THESIS_STABLE_MIN_SEC_LONG: int = _midlong_int_setting(
    "MIDLONG_THESIS_STABLE_MIN_SEC_LONG", ai_first_default=300, legacy_default=7200,
)
MIDLONG_THESIS_MIN_REVIEWS: int = _midlong_int_setting(
    "MIDLONG_THESIS_MIN_REVIEWS", ai_first_default=1, legacy_default=3,
)
MIDLONG_THESIS_STALE_MAX_SEC: int = _midlong_int_setting(
    "MIDLONG_THESIS_STALE_MAX_SEC", ai_first_default=600, legacy_default=120,
)
if MIDLONG_AI_MANDATORY and not os.getenv("MIDLONG_PERSISTENCE_TICKS"):
    MIDLONG_PERSISTENCE_TICKS = 1

# 快速试单：进一步放宽门控（仍可用 env 逐项覆盖）
if PAPER_FAST_TRIAL:
    if not os.getenv("MIDLONG_OPEN_READINESS_MIN_MID"):
        MIDLONG_OPEN_READINESS_MIN_MID = 35
    if not os.getenv("MIDLONG_OPEN_READINESS_MIN_LONG"):
        MIDLONG_OPEN_READINESS_MIN_LONG = 40
    if not os.getenv("MIDLONG_THESIS_STABLE_MIN_SEC_LONG"):
        MIDLONG_THESIS_STABLE_MIN_SEC_LONG = 0
    if not os.getenv("MIDLONG_THESIS_STABLE_MIN_SEC_MID"):
        MIDLONG_THESIS_STABLE_MIN_SEC_MID = 0
    # 阶段一 1.5：验证期（SCALP_STRICT_VALIDATION）不再对短线放宽门槛/冷却，
    # 让模拟盘与真金一致；下列放宽仅在关闭严格验证时生效。
    if not SCALP_STRICT_VALIDATION:
        if not os.getenv("SCALP_FACTOR_CONFIRM_THRESHOLD"):
            SCALP_FACTOR_CONFIRM_THRESHOLD = 25
        if not os.getenv("SCALP_FACTOR_EXECUTE_THRESHOLD"):
            SCALP_FACTOR_EXECUTE_THRESHOLD = 35
        if not os.getenv("SCALP_OPEN_COOLDOWN_SEC"):
            SCALP_OPEN_COOLDOWN_SEC = 60
        if not os.getenv("SCALP_VETO_BAND_LOW"):
            SCALP_VETO_BAND_LOW = max(20, int(SCALP_FACTOR_CONFIRM_THRESHOLD or 25))
        if not os.getenv("SCALP_RANGE_MAX_LONG"):
            SCALP_RANGE_MAX_LONG = 0.88
        if not os.getenv("SCALP_ORCH_CONFLICT_MIN_SCORE"):
            SCALP_ORCH_CONFLICT_MIN_SCORE = 38
    if not os.getenv("TIER_SHORT_COOLDOWN_SEC"):
        TIER_PROTECTION_PARAMS["short"]["cooldown_sec"] = 600
    if not os.getenv("SHORT_TIER_CONFIDENCE_EXTRA"):
        SHORT_TIER_CONFIDENCE_EXTRA = 0
    if not os.getenv("MIDLONG_PERSISTENCE_TICKS"):
        MIDLONG_PERSISTENCE_TICKS = 1
    if not os.getenv("MIDLONG_THESIS_STALE_MAX_SEC"):
        MIDLONG_THESIS_STALE_MAX_SEC = 900
    # 分层 tick：只加速协调器+学习+短线因子，中线/长线 AI 保持独立节奏
    if not os.getenv("TIER_COORDINATOR_TICK_SEC"):
        TIER_COORDINATOR_TICK_SEC = 30
    if not os.getenv("TIER_MID_AI_TICK_SEC"):
        TIER_MID_AI_TICK_SEC = 45
    if not os.getenv("TIER_LONG_AI_TICK_SEC"):
        TIER_LONG_AI_TICK_SEC = 90
    if not os.getenv("SCALP_FACTOR_SCAN_INTERVAL_SEC"):
        SCALP_FACTOR_SCAN_INTERVAL_SEC = 30

# unified 快照：paper 默认开启衍生品预取（Phase B 中线/长线 evidence）
UNIFIED_DATA_POOL_KLINE_DERIVATIVES_PREFETCH: bool = os.getenv(
    "UNIFIED_DATA_POOL_KLINE_DERIVATIVES_PREFETCH", "true"
).lower() in ("1", "true", "yes", "on")

# paper 模式下 FactGuard 是否强制拦截（全局仍为 shadow 时可单独打开）
AGENT_FACT_GUARD_PAPER_ENFORCE: bool = os.getenv(
    "AGENT_FACT_GUARD_PAPER_ENFORCE", "false"
).lower() in ("1", "true", "yes", "on")

# M2 同 symbol ai_reverse 冷却秒数：0 = 禁用；默认 1800 = 30 分钟
RISK_P3_AI_REVERSE_COOLDOWN_SEC:       int = int(os.getenv("RISK_P3_AI_REVERSE_COOLDOWN_SEC", "1800") or "0")

# ai_reverse 强反转最低置信度（0~1）
RISK_AI_REVERSE_MIN_CONF: float = float(os.getenv("RISK_AI_REVERSE_MIN_CONF", "0.65"))
# ai_reverse 微亏翻仓：亏损比例与最低置信度
RISK_AI_REVERSE_MICRO_LOSS_PCT: float = float(os.getenv("RISK_AI_REVERSE_MICRO_LOSS_PCT", "0.03"))
RISK_AI_REVERSE_MICRO_LOSS_MIN_CONF: float = float(os.getenv("RISK_AI_REVERSE_MICRO_LOSS_MIN_CONF", "0.55"))

# 方向一致性协议：enforce | audit | off
# 2026-06-17: 默认改为 audit。编排器(MTOrchestrator)是纯技术指标规则系统(零LLM)，
# 与 AI/LLM 判断来源完全不同，enforce 模式会把 AI 的 buy/sell 强制改成 hold，
# 导致"AI 看好却开不出仓"。audit 模式下编排器仍记录方向分歧(标 audit_only)，
# 但不拦截 —— 让 AI 的方向判断优先。如需恢复严格拦截，设 DIRECTION_COHERENCE_MODE=enforce。
DIRECTION_COHERENCE_MODE: str = os.getenv("DIRECTION_COHERENCE_MODE", "audit").strip().lower()

# Pace shadow 时同步禁止新开仓（开平对称）
# 2026-06-18: 默认改 false。原 true 导致 shadow 平仓模式下禁止任何新开仓，
# AI 出 buy 信号却被对称逻辑挡死，模拟盘根本开不了仓训练。模拟盘目的是让 AI 多交易攒样本，
# 不该用"开平对称"卡死。如需恢复严格对称，设 PAPER_PACE_SYMMETRIC_CLOSE=true。
PAPER_PACE_SYMMETRIC_CLOSE: bool = os.getenv("PAPER_PACE_SYMMETRIC_CLOSE", "false").lower() in (
    "1", "true", "yes", "on",
)

# 训练期暂停 auto_coin 注入
TRAINING_CORE_SYMBOLS: List[str] = [
    s.strip().upper()
    for s in os.getenv("TRAINING_CORE_SYMBOLS", "BTC,ETH,SOL,BNB,ASTER").split(",")
    if s.strip()
]
TRAINING_PHASE_BLOCK_AUTO_COIN: bool = os.getenv("TRAINING_PHASE_BLOCK_AUTO_COIN", "true").lower() in (
    "1", "true", "yes", "on",
)

# P2-2: 历史表现门控 — 阻止在已知亏损方向开仓
PERFORMANCE_GATE_ENABLED: bool = os.getenv("PERFORMANCE_GATE_ENABLED", "true").lower() == "true"
PERFORMANCE_GATE_MIN_SAMPLES: int = int(os.getenv("PERFORMANCE_GATE_MIN_SAMPLES", "20"))
PERFORMANCE_GATE_MIN_WR: float = float(os.getenv("PERFORMANCE_GATE_MIN_WR", "0.30"))
PERFORMANCE_GATE_TTL_HOURS: int = int(os.getenv("PERFORMANCE_GATE_TTL_HOURS", "168"))  # 7 天自动过期

# M4 DecisionArbiter 事件日志开关：打开后每次平/减仓决策落到 data/decision_arbiter.jsonl
RISK_P3_DECISION_LOG_ENABLED:          bool = os.getenv("RISK_P3_DECISION_LOG_ENABLED", "false").lower() == "true"


# 旧路径硬回滚开关（任何一项 Stage F 监控熔断都会把这个设成 true → 全部 Stage E / P2 失效）
# 优先级：环境变量 > data/stage_f_rollback.flag 文件存在 > 默认 false
def _read_stage_f_rollback_flag() -> bool:
    try:
        _env = os.getenv("LEGACY_RISK_HARD_ROLLBACK")
        if _env is not None:
            return _env.lower() == "true"
        _flag = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                             "data", "stage_f_rollback.flag")
        return os.path.exists(_flag)
    except Exception:
        return False

LEGACY_RISK_HARD_ROLLBACK:            bool = _read_stage_f_rollback_flag()

# ══════════════════════════════════════════════════
#  AI 自动选币系统配置 (AutoCoinSelector)
# ══════════════════════════════════════════════════

# 总开关：控制 main.py 是否启动 AutoCoinScheduler（2026-06 接线，默认 true
# 保持历史行为不变；设 AUTO_COIN_ENABLED=false 可彻底关闭自动选币调度）
AUTO_COIN_ENABLED: bool = os.getenv("AUTO_COIN_ENABLED", "true").lower() == "true"
# 2026-06-11 调优：数据证实 AI 选币单笔盈利效率约为默认币种 20 倍
# （50 笔 +66192 vs 130 笔 +8283），故扫描 1h→30min、池容量 5→7 放大优势
AUTO_COIN_SCAN_INTERVAL: int = int(os.getenv("AUTO_COIN_SCAN_INTERVAL", "1800"))
AUTO_COIN_EVALUATION_INTERVAL: int = int(os.getenv("AUTO_COIN_EVALUATION_INTERVAL", "3600"))
# AI 中线候选「粘性」重算间隔（秒）。短线选币仍按 AUTO_COIN_SCAN_INTERVAL(~30min) 轮换；
# 中线候选独立慢刷新，默认 3h（可调 2–4h: 7200–14400），避免跟短线同频换人。
AUTO_COIN_MID_RESAMPLE_SEC: int = int(os.getenv("AUTO_COIN_MID_RESAMPLE_SEC", "10800") or "10800")
AUTO_COIN_MID_MAX_SLOTS: int = int(os.getenv("AUTO_COIN_MID_MAX_SLOTS", "3") or "3")
# P2 调优（2026-07-14）：池容量 5→8，降低门槛让更多候选通过，趋势维度修复后评分更准
AUTO_COIN_MAX_COUNT: int = int(os.getenv("AUTO_COIN_MAX_COUNT", "7"))
AUTO_COIN_MIN_SCORE: float = float(os.getenv("AUTO_COIN_MIN_SCORE", "0.50"))
# 阶段C：AI 审核最低置信度 0.60→0.50。配合三层渐进 prompt，LAYER 2 候选
# （试仓）在 0.50-0.59 区间也能通过，把通过率从 <8% 拉到 20-40%。
AUTO_COIN_MIN_AI_CONFIDENCE: float = float(os.getenv("AUTO_COIN_MIN_AI_CONFIDENCE", "0.50"))
AUTO_COIN_CANDIDATE_TOP_N: int = int(os.getenv("AUTO_COIN_CANDIDATE_TOP_N", "30"))
AUTO_COIN_COOLING_HOURS: int = int(os.getenv("AUTO_COIN_COOLING_HOURS", "1"))
AUTO_COIN_BLACKLIST_SCORE: float = float(os.getenv("AUTO_COIN_BLACKLIST_SCORE", "0.35"))

# 因子发现开关（断点①修复：原代码 import 此变量但 settings.py 从未定义 → AttributeError → except 吞成 False）
AI_FACTOR_DISCOVERY_ENABLED: bool = os.getenv("AI_FACTOR_DISCOVERY_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
AUTO_COIN_BLACKLIST_DAYS: int = int(os.getenv("AUTO_COIN_BLACKLIST_DAYS", "7"))
AUTO_COIN_DEFAULT_EXCHANGE: str = os.getenv("AUTO_COIN_DEFAULT_EXCHANGE", "asterdex")
# 全局默认交易所（新账户/新用户/重置后的首选交易所）
# AsterDex 为首选（返利/积分生态），Binance 为备选
# 老账户不批量修改，仅新账户生效；用户可手动切换
DEFAULT_EXCHANGE: str = os.getenv("DEFAULT_EXCHANGE", "asterdex")
# 四所全量 K 线同步名单（P1 仓储；与决策 active_exchange 解耦）
KLINE_SYNC_EXCHANGES: list = [
    s.strip().lower() for s in os.getenv(
        "KLINE_SYNC_EXCHANGES", "asterdex,binance,okx,hyperliquid",
    ).split(",") if s.strip()
]
# P1 仓储周期：默认全周期（1m~1M 月线），禁止只采短线三档
# [2026-08-10 修复] 纳入 1M 月线（asterdex 主所此前无月线，长线缺月线锚）
KLINE_P1_PERIODS: str = os.getenv(
    "KLINE_P1_PERIODS", "1m,3m,5m,15m,30m,1h,4h,1d,1w,1M",
)
# rotate=分三组轮转覆盖；all=每批一次采完全部周期（更慢更重）
KLINE_P1_PERIOD_MODE: str = os.getenv("KLINE_P1_PERIOD_MODE", "rotate")
# 合法交易所白名单（单一真相源，user_routes/account_routes 复用）
SUPPORTED_EXCHANGES_LIST: list = [
    "asterdex", "binance", "hyperliquid", "bybit", "okx", "gateio",
]
# ── 多交易所市场流采集配置 ──
# 默认只开 Asterdex（产品默认所）；需要 HL 时在 .env 里显式追加，禁止再默认 Hyperliquid
ACTIVE_MARKET_FLOW_EXCHANGES: list = [
    s.strip() for s in os.getenv(
        "ACTIVE_MARKET_FLOW_EXCHANGES", "asterdex",
    ).split(",") if s.strip()
]
# CVD/trades 聚合窗口（秒）。旧实现硬编码 60s（"非实时"痛点），现默认降到 15s
CVD_AGGREGATION_WINDOW_SECONDS: int = int(
    os.getenv("CVD_AGGREGATION_WINDOW_SECONDS", "15")
)
# Hyperliquid API endpoint（从旧的硬编码 https://api.hyperliquid.xyz 改为可配置）
HYPERLIQUID_API_URL: str = os.getenv(
    "HYPERLIQUID_API_URL", "https://api.hyperliquid.xyz",
)
# V2: 币种轮换与评分引擎配置（2026-08 全面升级：Live 默认 0.12，防僵尸池）
AUTO_COIN_REPLACEMENT_MARGIN: float = float(os.getenv("AUTO_COIN_REPLACEMENT_MARGIN", "0.12"))
AUTO_COIN_MIN_HOLD_HOURS: int = int(os.getenv("AUTO_COIN_MIN_HOLD_HOURS", "4"))
# 统一离场整改：long tier 不再被 72h 封顶（恢复设计值 7天）
# 72h 封顶只对 short/mid 生效，long 用 TIER_PROTECTION_PARAMS 的 max_hold_sec
AUTO_COIN_MAX_HOLD_HOURS_SHORT: int = int(os.getenv("AUTO_COIN_MAX_HOLD_HOURS_SHORT", "2"))  # [P1-3] 72h→2h，scalp实际<30min
# [2026-07-23 统一守卫] AI 选币一律不进长线 tier（TrendAgent 独立 + MLTO thesis + 前端展示）
# 默认 True：长线只跑会话固定币种（session.symbols - auto_coin_symbols）。
# 设 false 退回旧行为（反向排除法兜底，不推荐——有 stale ORM 快照窗口期）。
AUTO_COIN_FORBID_LONG: bool = os.getenv("AUTO_COIN_FORBID_LONG", "true").lower() in (
    "1", "true", "yes", "on",
)
AUTO_COIN_MAX_HOLD_HOURS_MID: int = int(os.getenv("AUTO_COIN_MAX_HOLD_HOURS_MID", "72"))
AUTO_COIN_MAX_HOLD_HOURS_LONG: int = int(os.getenv("AUTO_COIN_MAX_HOLD_HOURS_LONG", "168"))  # 7天（恢复设计值）
AUTO_COIN_MAX_HOLD_SEC = AUTO_COIN_MAX_HOLD_HOURS_LONG * 3600  # 向后兼容（取最大值）
# [2026-07-20 修复] 原来漏了 "scalp"——scalp_loop.py 实际写入的 trade_nature 是
# "scalp"（不是 "intraday"），导致 AI 选币的短线决策在 tier_fanout.py 里被误判为
# [中长线升级同步修复] 中线(SwingAgent)已并入长线 mid_view,swing/intraday 不再
# 是独立执行路径。auto-coin 选出的币只进短线 scalp(scalp_loop 独立线程处理)。
AUTO_COIN_ALLOWED_NATURES: frozenset = frozenset({"scalp"})
AUTO_COIN_EXPIRY_KEEP_SCORE: float = float(os.getenv("AUTO_COIN_EXPIRY_KEEP_SCORE", "0.45"))
AUTO_COIN_EXPIRY_REMOVE_SCORE: float = float(os.getenv("AUTO_COIN_EXPIRY_REMOVE_SCORE", "0.28"))
AUTO_COIN_GRACE_CYCLES: int = int(os.getenv("AUTO_COIN_GRACE_CYCLES", "2"))
AUTO_COIN_COOLING_SHORT_HOURS: int = int(os.getenv("AUTO_COIN_COOLING_SHORT_HOURS", "2"))
AUTO_COIN_COOLING_LONG_HOURS: int = int(os.getenv("AUTO_COIN_COOLING_LONG_HOURS", "8"))
AUTO_COIN_COOLING_VERY_LONG_HOURS: int = int(os.getenv("AUTO_COIN_COOLING_VERY_LONG_HOURS", "24"))
AUTO_COIN_PERF_WEIGHT: float = float(os.getenv("AUTO_COIN_PERF_WEIGHT", "0.40"))
AUTO_COIN_MARKET_WEIGHT: float = float(os.getenv("AUTO_COIN_MARKET_WEIGHT", "0.30"))
AUTO_COIN_RETENTION_WEIGHT: float = float(os.getenv("AUTO_COIN_RETENTION_WEIGHT", "0.15"))
AUTO_COIN_DIVERSITY_WEIGHT: float = float(os.getenv("AUTO_COIN_DIVERSITY_WEIGHT", "0.15"))
# AI 自动选币 — 严选模式：提高门禁、降低仓位（原 V5 放宽已关闭）
AUTO_COIN_V5_CONF_RELIEF: int = int(os.getenv("AUTO_COIN_V5_CONF_RELIEF", "0"))    # 遗留：>0 时仍可降低门槛
AUTO_COIN_V5_CONF_PENALTY: int = int(os.getenv("AUTO_COIN_V5_CONF_PENALTY", "12"))  # 开仓置信度 +12%
AUTO_COIN_V5_MIN_RR: float = float(os.getenv("AUTO_COIN_V5_MIN_RR", "1.8"))         # 精选币最低盈亏比（与全局对齐，避免短线样本被 2.2 误杀）
AUTO_COIN_POSITION_SIZE_MULT: float = float(os.getenv("AUTO_COIN_POSITION_SIZE_MULT", "0.35"))
AUTO_COIN_PROBE_SIZE_MULT: float = float(os.getenv("AUTO_COIN_PROBE_SIZE_MULT", "0.50"))
AUTO_COIN_PROBE_MIN_CLOSED: int = int(os.getenv("AUTO_COIN_PROBE_MIN_CLOSED", "5"))
# 高置信 AI 信号放宽（FanOut 后 ≥68% 视为强烈看多/看空，避免被进化闭环 3.0 RR 误拦）
V5_HIGH_CONF_THRESHOLD: int = int(os.getenv("V5_HIGH_CONF_THRESHOLD", "68"))
V5_HIGH_CONF_CONF_RELIEF: int = int(os.getenv("V5_HIGH_CONF_CONF_RELIEF", "10"))
V5_HIGH_CONF_MIN_RR: float = float(os.getenv("V5_HIGH_CONF_MIN_RR", "2.0"))
# 反馈/进化写入的运行时 min_rr 上限（防止冠军模板 TP/SL 派生出 3.0 全局硬顶）
V5_MAX_RUNTIME_MIN_RR: float = float(os.getenv("V5_MAX_RUNTIME_MIN_RR", "2.5"))

# ── 周期方向概率门禁（cycle_direction_probability 引擎）──
# 默认关闭：属于新增的、边际优势较弱的信号，opt-in 后才参与门控，避免默认改变现网行为。
# 开启后：开仓方向与概率引擎"明显反向"时，Live 直接 block，Paper 软性缩仓。
# 关键安全阀：只有当该 tier 的历史校准质量 ≥ CYCLE_PROB_GATE_MIN_CALIBRATION 时才启用硬拦截，
# 校准差（当前加密数据方向本就难预测，quality 常低于 0.15）时自动退化为"仅记日志的观察模式"，
# 不会因为一个弱信号就误杀真实机会。随数据积累、校准变好后自动生效。
CYCLE_PROB_GATE_ENABLED: bool = os.getenv(
    "CYCLE_PROB_GATE_ENABLED", "false"
).strip().lower() in ("true", "1", "yes", "on")
# 启用硬拦截所需的最低校准质量（0~1）；低于此值只观察不拦截
CYCLE_PROB_GATE_MIN_CALIBRATION: float = float(
    os.getenv("CYCLE_PROB_GATE_MIN_CALIBRATION", "0.15")
)
# "明显反向"判定：反向概率 - 意图方向概率 ≥ 该 margin 才触发拦截/缩仓
CYCLE_PROB_GATE_MARGIN: float = float(os.getenv("CYCLE_PROB_GATE_MARGIN", "0.08"))
# Paper 命中冲突时的缩仓系数（软处理，不 block）
CYCLE_PROB_GATE_PAPER_SIZE_MULT: float = float(
    os.getenv("CYCLE_PROB_GATE_PAPER_SIZE_MULT", "0.5")
)

# ── 短线因子 × AI周期概率引擎 融合打分（ScalpFusionScorer）──
# 2026-07-06：把训练好但此前只给 mid/long 用的 cycle_direction_probability short tier
# 接入短线因子路由，按"校准质量"加权把 AI 概率信号融合进因子分数。默认直接开启生效
# （不同于 CYCLE_PROB_GATE 的默认关闭），出问题时改成 false 即可秒回滚到改动前行为。
SCALP_FUSION_ENABLED: bool = os.getenv(
    "SCALP_FUSION_ENABLED", "true"
).strip().lower() in ("true", "1", "yes", "on")
# cycle_prob 引擎能对因子分数产生的最大加/减分（校准质量越低，实际影响越接近0）
SCALP_FUSION_MAX_DELTA: int = int(os.getenv("SCALP_FUSION_MAX_DELTA", "15"))
# 启用融合所需的最低校准质量（0~1）；低于此值直接跳过融合（当前默认0不设硬门槛，
# 完全靠 calibration_quality 乘数自然衰减，预留给未来收紧）
SCALP_FUSION_MIN_CALIBRATION: float = float(
    os.getenv("SCALP_FUSION_MIN_CALIBRATION", "0.0")
)

# 仓位记忆 / 心理状态机（连亏冻结阈值，默认放宽：4→6 避免 AI 强信号也无法开仓）
CONSECUTIVE_LOSS_PROTECTION_ENABLED: bool = os.getenv(
    "CONSECUTIVE_LOSS_PROTECTION_ENABLED", "true"
).strip().lower() in ("true", "1", "yes", "on")
# 模拟盘训练：不因亏损/回撤/连亏进入防守、冻结或拦截新开仓（默认开启）
PAPER_DISABLE_LOSS_LOCKS: bool = os.getenv(
    "PAPER_DISABLE_LOSS_LOCKS", "true"
).strip().lower() in ("true", "1", "yes", "on")
# Paper Engine One-Way 净额模式（匹配 Hyperliquid/Asterdex 真实行为）
#  - true (默认): 保证金/爆仓价/可用余额/总权益按每币种净头寸计算，
#    对冲对释放保证金（如 scalp 空 + trend 多 只按净额占用保证金）
#  - false: 回退到旧行级 margin 求和（应急开关）
# 注意: 记账层 (PaperPosition 分行) 不受影响，仅风险/余额视角变净额
PAPER_NETTING_MODE: bool = os.getenv(
    "PAPER_NETTING_MODE", "true"
).strip().lower() in ("true", "1", "yes", "on")
# Paper Engine 单向(One-Way)反手净额抵消（2026-07-03 修复：消除同层多空并存伪对冲）
#  - true (默认): 反向订单先平/减同层(scalp/swing/trend)已有反向仓，剩余量才翻新仓，
#    保证同一币同一层永远只有一个方向（真 One-Way 记账，杜绝"短线全是多空对冲单"）
#  - false: 回退旧行为（反向单直接新开一行，同层多空并存）
# 注意: 与 PAPER_NETTING_MODE 互补——后者只在风险/余额层净额，本开关修记账层。
PAPER_ONE_WAY_REVERSE_NETTING: bool = os.getenv(
    "PAPER_ONE_WAY_REVERSE_NETTING", "true"
).strip().lower() in ("true", "1", "yes", "on")
# 模拟盘训练：自动选币 V5 加严放宽（不影响实盘）
PAPER_RELAX_AUTO_COIN_V5: bool = os.getenv(
    "PAPER_RELAX_AUTO_COIN_V5", "true"
).strip().lower() in ("true", "1", "yes", "on")
PAPER_AUTO_COIN_V5_CONF_PENALTY: int = int(
    os.getenv("PAPER_AUTO_COIN_V5_CONF_PENALTY", "0")
)
PAPER_AUTO_COIN_V5_MIN_RR: float = float(
    os.getenv("PAPER_AUTO_COIN_V5_MIN_RR", "1.5")
)

# ── AutoCoin V3 改造开关（默认全关 = 旧行为；见 docs/AutoCoin选币改造落地设计稿_2026-08-02.md）──
AUTO_COIN_SCORE_V3_ENABLED: bool = os.getenv(
    "AUTO_COIN_SCORE_V3_ENABLED", "false"
).strip().lower() in ("true", "1", "yes", "on")
AUTO_COIN_W_BASE: float = float(os.getenv("AUTO_COIN_W_BASE", "0.55"))
AUTO_COIN_W_FLOW: float = float(os.getenv("AUTO_COIN_W_FLOW", "0.20"))
AUTO_COIN_W_WHALE: float = float(os.getenv("AUTO_COIN_W_WHALE", "0.10"))
AUTO_COIN_W_NEWS: float = float(os.getenv("AUTO_COIN_W_NEWS", "0.10"))
AUTO_COIN_W_SECTOR: float = float(os.getenv("AUTO_COIN_W_SECTOR", "0.05"))
# ── S2-9 选币因子自适应：IC 加权 + 相关性去重 + LLM 组合决策 ──
# IC 加权：以 auto_coin_selections 的 factor_snapshot_json + hit_24h 为样本，
# 计算各因子 Spearman IC 并归一化为 V3 合成权重（负 IC 弃用）；样本不足回退静态权重。
AUTO_COIN_IC_WEIGHTS_ENABLED: bool = os.getenv(
    "AUTO_COIN_IC_WEIGHTS_ENABLED", "true"
).strip().lower() in ("true", "1", "yes", "on")
# 单因子有效样本下限（与 factor_ic_evaluator 的 MIN_SAMPLES 对齐）
AUTO_COIN_IC_MIN_SAMPLES: int = int(os.getenv("AUTO_COIN_IC_MIN_SAMPLES", "30"))
# IC 权重缓存 TTL（秒）
AUTO_COIN_IC_CACHE_TTL_SEC: int = int(os.getenv("AUTO_COIN_IC_CACHE_TTL_SEC", "900"))
# 样本回看窗口（天）
AUTO_COIN_IC_LOOKBACK_DAYS: int = int(os.getenv("AUTO_COIN_IC_LOOKBACK_DAYS", "45"))
# 组合相关性去重阈值（余弦相似度，>= 阈值视为同质；0 关闭）
AUTO_COIN_CORR_DEDUP_THRESHOLD: float = float(os.getenv("AUTO_COIN_CORR_DEDUP_THRESHOLD", "0.85"))
# LLM 组合决策（候选池 → 最终名单）；关闭或失败时回退规则路径
AUTO_COIN_LLM_COMPOSE_ENABLED: bool = os.getenv(
    "AUTO_COIN_LLM_COMPOSE_ENABLED", "false"
).strip().lower() in ("true", "1", "yes", "on")
AUTO_COIN_LLM_COMPOSE_MAX: int = int(os.getenv("AUTO_COIN_LLM_COMPOSE_MAX", "5"))
AUTO_COIN_NEWS_HOURS: int = int(os.getenv("AUTO_COIN_NEWS_HOURS", "24"))
AUTO_COIN_NEWS_MIN_CONF: float = float(os.getenv("AUTO_COIN_NEWS_MIN_CONF", "0.3"))
AUTO_COIN_NEWS_HALF_LIFE_MIN: float = float(os.getenv("AUTO_COIN_NEWS_HALF_LIFE_MIN", "120"))
AUTO_COIN_NEWS_COOLING_HOURS: float = float(os.getenv("AUTO_COIN_NEWS_COOLING_HOURS", "4"))
AUTO_COIN_WHALE_DIR_THRESHOLD: float = float(os.getenv("AUTO_COIN_WHALE_DIR_THRESHOLD", "0.15"))
AUTO_COIN_FLOW_OI_CLIP_PCT: float = float(os.getenv("AUTO_COIN_FLOW_OI_CLIP_PCT", "8.0"))
AUTO_COIN_SECTOR_SIGNAL_ENABLED: bool = os.getenv(
    "AUTO_COIN_SECTOR_SIGNAL_ENABLED", "false"
).strip().lower() in ("true", "1", "yes", "on")
AUTO_COIN_SECTOR_LEADER_RET_1H: float = float(os.getenv("AUTO_COIN_SECTOR_LEADER_RET_1H", "0.04"))
AUTO_COIN_SECTOR_LEADER_VOL_Z: float = float(os.getenv("AUTO_COIN_SECTOR_LEADER_VOL_Z", "2.0"))
AUTO_COIN_SECTOR_PEER_TOP_K: int = int(os.getenv("AUTO_COIN_SECTOR_PEER_TOP_K", "4"))
AUTO_COIN_MAX_PER_SECTOR: int = int(os.getenv("AUTO_COIN_MAX_PER_SECTOR", "2"))
AUTO_COIN_WATCH_TTL_MIN: int = int(os.getenv("AUTO_COIN_WATCH_TTL_MIN", "45"))
# Live 默认关；Paper 由 PAPER_AUTO_COIN_MULTI_LANE / 调度器侧判定打开
AUTO_COIN_MULTI_LANE_ENABLED: bool = os.getenv(
    "AUTO_COIN_MULTI_LANE_ENABLED", "false"
).strip().lower() in ("true", "1", "yes", "on")
PAPER_AUTO_COIN_MULTI_LANE: bool = os.getenv(
    "PAPER_AUTO_COIN_MULTI_LANE", "true"
).strip().lower() in ("true", "1", "yes", "on")
AUTO_COIN_NORMAL_INTERVAL_SEC: int = int(os.getenv("AUTO_COIN_NORMAL_INTERVAL_SEC", "900"))
AUTO_COIN_FAST_INTERVAL_SEC: int = int(os.getenv("AUTO_COIN_FAST_INTERVAL_SEC", "180"))
AUTO_COIN_FAST_MIN_GAP_SEC: int = int(os.getenv("AUTO_COIN_FAST_MIN_GAP_SEC", "120"))
AUTO_COIN_EVENT_DEDUP_SEC: int = int(os.getenv("AUTO_COIN_EVENT_DEDUP_SEC", "300"))
AUTO_COIN_FAST_FORCE_AI: bool = os.getenv(
    "AUTO_COIN_FAST_FORCE_AI", "true"
).strip().lower() in ("true", "1", "yes", "on")
AUTO_COIN_FAST_AI_MAX_PER_HOUR: int = int(os.getenv("AUTO_COIN_FAST_AI_MAX_PER_HOUR", "6"))
AUTO_COIN_FACTOR_MIN_MARKET: float = float(os.getenv("AUTO_COIN_FACTOR_MIN_MARKET", "0.40"))
AUTO_COIN_FACTOR_MIN_ABS_ALPHA: float = float(os.getenv("AUTO_COIN_FACTOR_MIN_ABS_ALPHA", "0.005"))
AUTO_COIN_FACTOR_BLEND: float = float(os.getenv("AUTO_COIN_FACTOR_BLEND", "0.50"))
AUTO_COIN_FACTOR_MATCH_ENABLED: bool = os.getenv(
    "AUTO_COIN_FACTOR_MATCH_ENABLED", "true"
).strip().lower() in ("true", "1", "yes", "on")

# Paper 灰度：默认打开 V3 评分 + 更积极轮换（不影响实盘）
PAPER_AUTO_COIN_SCORE_V3: bool = os.getenv(
    "PAPER_AUTO_COIN_SCORE_V3", "true"
).strip().lower() in ("true", "1", "yes", "on")
PAPER_AUTO_COIN_ROTATE: bool = os.getenv(
    "PAPER_AUTO_COIN_ROTATE", "true"
).strip().lower() in ("true", "1", "yes", "on")
# Paper 替换差距：0.20→0.10；到期续期门槛：0.45→0.55（更易腾出空位）
PAPER_AUTO_COIN_REPLACEMENT_MARGIN: float = float(
    os.getenv("PAPER_AUTO_COIN_REPLACEMENT_MARGIN", "0.08")
)
PAPER_AUTO_COIN_EXPIRY_KEEP_SCORE: float = float(
    os.getenv("PAPER_AUTO_COIN_EXPIRY_KEEP_SCORE", "0.55")
)
# Paper 预检放宽：5m 不新鲜时只要 15m+1h 新鲜且根数够即可注入
PAPER_AUTO_COIN_PREFLIGHT_RELAX: bool = os.getenv(
    "PAPER_AUTO_COIN_PREFLIGHT_RELAX", "true"
).strip().lower() in ("true", "1", "yes", "on")
# Paper：Universe 合格集过小时跳过硬门（避免 rebuild 后只剩 3~4 币 → 选币 0 候选）
PAPER_AUTO_COIN_UNIVERSE_SOFT: bool = os.getenv(
    "PAPER_AUTO_COIN_UNIVERSE_SOFT", "true"
).strip().lower() in ("true", "1", "yes", "on")
PAPER_AUTO_COIN_UNIVERSE_MIN_SIZE: int = int(
    os.getenv("PAPER_AUTO_COIN_UNIVERSE_MIN_SIZE", "30")
)
# 模拟盘不因健康检查自动 terminate 策略（保留 paused 可恢复）
PAPER_SKIP_STRATEGY_TERMINATE: bool = os.getenv(
    "PAPER_SKIP_STRATEGY_TERMINATE", "true"
).strip().lower() in ("true", "1", "yes", "on")
# 模拟盘每币最多同时 active 的策略数（控制健康检查耗时）
PAPER_MAX_ACTIVE_STRATEGIES_PER_SYMBOL: int = int(
    os.getenv("PAPER_MAX_ACTIVE_STRATEGIES_PER_SYMBOL", "5")
)
# TrendAgent 开仓最低评分：实盘默认 50，纸盘略放宽便于观察
TREND_MIN_SCORE_TO_OPEN: int = int(os.getenv("TREND_MIN_SCORE_TO_OPEN", "50"))
PAPER_TREND_MIN_SCORE_TO_OPEN: int = int(os.getenv("PAPER_TREND_MIN_SCORE_TO_OPEN", "40"))

# ── 阶段2(S2-10) wisdom 闭环：净扣费 + 质量闸门 + 验证强度排序 ──
# 质量闸门：|pnl_pct| 或 |pnl| 任一达到门槛才计入有效评估样本（防噪声污染）
WISDOM_QUALITY_PNL_PCT_GATE: float = float(os.getenv("WISDOM_QUALITY_PNL_PCT_GATE", "0.003"))
WISDOM_QUALITY_PNL_USD_GATE: float = float(os.getenv("WISDOM_QUALITY_PNL_USD_GATE", "1.0"))
# 净扣费：tanh(|pnl|/scale) 金额加权信号（小赚小亏低权重，大亏重罚）
WISDOM_AMOUNT_SCALE_USD: float = float(os.getenv("WISDOM_AMOUNT_SCALE_USD", "50.0"))
# 验证强度排序：quality_hit_count 达到该值视为充分验证（强度权重 1.0）
WISDOM_MIN_QUALITY_SAMPLES: int = int(os.getenv("WISDOM_MIN_QUALITY_SAMPLES", "5"))

# ── 阶段2(S2-10) 参数域扩展：Hermes 高置信模式 → GA 搜索域动态扩展 ──
# 单条 improved 模式扩展系数（increase→上界×ratio / decrease→下界÷ratio）
PARAM_DOMAIN_EXPAND_ENABLED: bool = os.getenv(
    "PARAM_DOMAIN_EXPAND_ENABLED", "true"
).strip().lower() in ("true", "1", "yes", "on")
PARAM_DOMAIN_EXPAND_RATIO: float = float(os.getenv("PARAM_DOMAIN_EXPAND_RATIO", "1.2"))
# 相对基础域的总扩展上限（防域失控）
PARAM_DOMAIN_EXPAND_MAX: float = float(os.getenv("PARAM_DOMAIN_EXPAND_MAX", "1.5"))
# 高置信模式质量门槛：样本数 + 归因置信度
PARAM_DOMAIN_MIN_SAMPLES: int = int(os.getenv("PARAM_DOMAIN_MIN_SAMPLES", "3"))
PARAM_DOMAIN_MIN_CONFIDENCE: float = float(os.getenv("PARAM_DOMAIN_MIN_CONFIDENCE", "0.5"))
# 模式读取缓存 TTL（模式库 EMA 聚合，变化缓慢）
PARAM_DOMAIN_CACHE_TTL_SEC: int = int(os.getenv("PARAM_DOMAIN_CACHE_TTL_SEC", "1800"))

# ── 阶段2(S2-10) QAA 调度统一：域注册表 + 统一心跳 + 统一调度 ──
# 总开关默认 False：只建架构不改行为，运维按需开启
QAA_SCHEDULER_ENABLED: bool = os.getenv(
    "QAA_SCHEDULER_ENABLED", "false"
).strip().lower() in ("true", "1", "yes", "on")
# 域级开关与间隔（秒）
QAA_REBATE_SCHEDULE_ENABLED: bool = os.getenv(
    "QAA_REBATE_SCHEDULE_ENABLED", "false"
).strip().lower() in ("true", "1", "yes", "on")
QAA_REBATE_INTERVAL_SEC: int = int(os.getenv("QAA_REBATE_INTERVAL_SEC", "900"))
QAA_FULLAUTO_SCHEDULE_ENABLED: bool = os.getenv(
    "QAA_FULLAUTO_SCHEDULE_ENABLED", "false"
).strip().lower() in ("true", "1", "yes", "on")
QAA_FULLAUTO_INTERVAL_SEC: int = int(os.getenv("QAA_FULLAUTO_INTERVAL_SEC", "900"))


def get_trend_min_score_to_open(trading_mode: str = "paper") -> int:
    """按交易模式返回 TrendAgent 最低开仓分。Paper 优先 TREND_PAPER_SCORE_FLOOR。"""
    if (trading_mode or "paper").strip().lower() == "paper":
        try:
            return int(TREND_PAPER_SCORE_FLOOR)
        except Exception:
            return int(PAPER_TREND_MIN_SCORE_TO_OPEN)
    return TREND_MIN_SCORE_TO_OPEN


MENTAL_LOSS_TO_CAUTIOUS: int = int(os.getenv("MENTAL_LOSS_TO_CAUTIOUS", "3"))
MENTAL_LOSS_TO_FROZEN: int = int(os.getenv("MENTAL_LOSS_TO_FROZEN", "6"))
MENTAL_FROZEN_COOLDOWN_MINUTES: int = int(os.getenv("MENTAL_FROZEN_COOLDOWN_MINUTES", "10"))
# frozen 冷却期内：置信度 ≥ 此值允许「试探仓」(size×0.35)，避免强信号完全浪费
MENTAL_HIGH_CONF_FROZEN_BYPASS: float = float(os.getenv("MENTAL_HIGH_CONF_FROZEN_BYPASS", "0.78"))
MENTAL_FROZEN_PROBE_SIZE_MULT: float = float(os.getenv("MENTAL_FROZEN_PROBE_SIZE_MULT", "0.35"))

# ══════════════════════════════════════════════════
#  V4 QAA 多智能体架构配置
# ══════════════════════════════════════════════════

# QAA 模式开关: legacy=原始代码路径 / qaa=新 QAA dispatcher 路径
# 渐进迁移: 默认 legacy, 逐 Agent 打开 qaa
QAA_MODE: str = os.getenv("QAA_MODE", "legacy").strip().lower()

# QAA v3.0 开关 (仅 QAA_MODE=qaa 时生效)
# false=旧版 EventBus 调度 / true=TickOrchestrator + TradingPlugin v3.0
QAA_V3_ENABLED: bool = os.getenv("QAA_V3_ENABLED", "false").strip().lower() in ("true", "1", "yes")

# QAA 逐 Agent 开关 (仅 QAA_MODE=qaa 时生效)
# 格式: 逗号分隔的 agent_id 列表, 空字符串=全部使用 QAA
QAA_ENABLED_AGENTS: str = os.getenv("QAA_ENABLED_AGENTS", "")

# 注：原 QAA_ENABLED_DOMAINS flag 已删除——零读取。QAA 域的启用实际由
# main.py 中各域插件（如 ArbitragePlugin）的显式加载决定，与该环境变量无关
# （.env 中的 QAA_ENABLED_DOMAINS=... 配置行同样无效，可一并清理）

# QAA tick 预算 (毫秒)
QAA_TICK_BUDGET_MS: int = int(os.getenv("QAA_TICK_BUDGET_MS", "120000"))

# ══════════════════════════════════════════════════
#  V5 决策核心（decision_core）— 2026-06 经济学重构
#  根因: 盈亏比倒挂(+320 vs -9070) + 过度交易(94笔/周) + 手续费侵蚀
# ══════════════════════════════════════════════════

# 总开关：false 时 V5 门控直接放行（回滚用）
V5_DECISION_CORE_ENABLED: bool = os.getenv("V5_DECISION_CORE_ENABLED", "true").strip().lower() in ("true", "1", "yes")

# 每日开仓额度总开关：整改后终态默认开启（此前默认 false = 完全不限次数，
# 导致 README 宣传的"日上限"从未真正生效，退回 V5 上线前的过度交易状态）
V5_DAILY_TRADE_CAP_ENABLED: bool = os.getenv(
    "V5_DAILY_TRADE_CAP_ENABLED", "true"
).strip().lower() in ("true", "1", "yes")

# 每日开仓上限（旧版单一值，未区分 live/paper，仅作历史兼容 fallback；
# unified_gate.py 的实际生效值改用下方 get_v5_max_daily_trades() 按模式区分）
V5_MAX_DAILY_TRADES: int = int(os.getenv("V5_MAX_DAILY_TRADES", "50"))

# 同一 symbol 每日开仓上限（旧版单一值，未区分 live/paper，仅作历史兼容 fallback）
V5_MAX_SYMBOL_TRADES_PER_DAY: int = int(os.getenv("V5_MAX_SYMBOL_TRADES_PER_DAY", "20"))

# ── Live/Paper 差异化日额度（终态设计：Live 严格控制过度交易，
#    Paper 放宽以积累训练样本，但绝不是"不限"，防止死循环式重复下单）──
V5_MAX_DAILY_TRADES_LIVE: int = int(os.getenv("V5_MAX_DAILY_TRADES_LIVE", "12"))
V5_MAX_DAILY_TRADES_PAPER: int = int(os.getenv("V5_MAX_DAILY_TRADES_PAPER", "60"))
V5_MAX_SYMBOL_TRADES_PER_DAY_LIVE: int = int(
    os.getenv("V5_MAX_SYMBOL_TRADES_PER_DAY_LIVE", "4")
)
V5_MAX_SYMBOL_TRADES_PER_DAY_PAPER: int = int(
    os.getenv("V5_MAX_SYMBOL_TRADES_PER_DAY_PAPER", "12")
)

# ── 各 tier 独立日开仓配额（2026-07-23 改造：替代共享 daily_cap_base 比例分配）──
# 每个策略页面独立配置、独立保存、独立生效。这两个值仅作为 env fallback；
# 实际运行时生效值由 runtime_tuning_store.get_tuning_int 读取
# （data/runtime_tuning.json 持久化，前端 PUT /api/strategy-config/daily-cap/{tier} 热改）。
# 默认兜底：短线/中长线配额解耦；模拟盘高配额由 runtime_tuning 热改（可高于本默认）。
# 中长线一体：仅 trend_daily_cap，不设独立 swing 日配额。
SCALP_DAILY_OPEN_CAP: int = int(os.getenv("SCALP_DAILY_OPEN_CAP", "150"))
TREND_DAILY_OPEN_CAP: int = int(os.getenv("TREND_DAILY_OPEN_CAP", "15"))


def get_v5_max_daily_trades(trading_mode: str = "paper") -> int:
    """按交易模式返回日开仓上限（legacy 全局口径；真实硬拦以 tier 配额为准）。"""
    if (trading_mode or "paper").strip().lower() == "paper":
        return V5_MAX_DAILY_TRADES_PAPER
    return V5_MAX_DAILY_TRADES_LIVE


def get_v5_max_symbol_trades_per_day(trading_mode: str = "paper") -> int:
    """按交易模式返回同一 symbol 每日开仓上限：Live 4 / Paper 可放宽攒样本。"""
    if (trading_mode or "paper").strip().lower() == "paper":
        return V5_MAX_SYMBOL_TRADES_PER_DAY_PAPER
    return V5_MAX_SYMBOL_TRADES_PER_DAY_LIVE

# 开仓最低盈亏比 TP:SL（中长线/全局 fallback；短线见 V5_SCALP_MIN_RR）
V5_MIN_RISK_REWARD: float = float(os.getenv("V5_MIN_RISK_REWARD", "1.8"))

# 按 nature 拆分 RR / min_tp（加密短线 vs 中长线一体）
V5_SCALP_MIN_RR: float = float(os.getenv("V5_SCALP_MIN_RR", "1.4"))
V5_SCALP_MIN_TP_PCT: float = float(os.getenv("V5_SCALP_MIN_TP_PCT", "0.006"))
V5_SCALP_MIN_RR_PAPER: float = float(os.getenv("V5_SCALP_MIN_RR_PAPER", "1.3"))
V5_SCALP_MIN_TP_PCT_PAPER: float = float(os.getenv("V5_SCALP_MIN_TP_PCT_PAPER", "0.005"))
V5_TREND_MIN_RR: float = float(os.getenv("V5_TREND_MIN_RR", "1.8"))
V5_TREND_MIN_RR_PAPER: float = float(os.getenv("V5_TREND_MIN_RR_PAPER", "1.6"))

# 开仓最低止盈距离（中长线/全局；短线用 V5_SCALP_MIN_TP_*）
V5_MIN_TP_PCT: float = float(os.getenv("V5_MIN_TP_PCT", "0.012"))

# scalp 性质最低置信度（58 → 70）
V5_SCALP_MIN_CONFIDENCE: int = int(os.getenv("V5_SCALP_MIN_CONFIDENCE", "70"))

# 趋势仓（trend_follow / position）开仓最低置信度 — 比短线更谨慎
V5_TREND_FOLLOW_MIN_CONFIDENCE: int = int(os.getenv("V5_TREND_FOLLOW_MIN_CONFIDENCE", "50"))

# 单笔最大风险占权益比例（杜绝单笔 -7% 权益的灾难单）
V5_MAX_TRADE_RISK_PCT: float = float(os.getenv("V5_MAX_TRADE_RISK_PCT", "0.015"))
# 短线专用：保证金下限 / 单笔风险上限（动态仓位用，比全局 V5 略宽）
SCALP_MIN_MARGIN_PCT: float = float(os.getenv("SCALP_MIN_MARGIN_PCT", "0.025"))
SCALP_MAX_TRADE_RISK_PCT: float = float(os.getenv("SCALP_MAX_TRADE_RISK_PCT", "0.03"))
# 短线排除 PATTERN/BEHAVIORAL（Loop/Router 共用）；0 可回滚
SCALP_EXCLUDE_PATTERN: bool = os.getenv("SCALP_EXCLUDE_PATTERN", "true").lower() in (
    "1", "true", "yes", "on",
)
# Meta 软进 EV（默认关；仅 usable 模型生效）
SCALP_META_IN_EV: bool = os.getenv("SCALP_META_IN_EV", "false").lower() in (
    "1", "true", "yes", "on",
)
SCALP_META_EV_BLEND: float = float(os.getenv("SCALP_META_EV_BLEND", "0.35"))

# AI 选币后快速策略观察者（pair_selector_watcher）：默认开，每 5 分钟扫活跃 AI 币
PAIR_SELECTOR_WATCHER_ENABLED: bool = os.getenv(
    "PAIR_SELECTOR_WATCHER_ENABLED", "true"
).strip().lower() in ("1", "true", "yes", "on")
PAIR_SELECTOR_WATCHER_INTERVAL_SEC: int = max(
    60, int(os.getenv("PAIR_SELECTOR_WATCHER_INTERVAL_SEC", "300") or 300)
)
# 绑定执行车道：默认关交易（仅调度干跑心跳）；显式 true 才对研究纸盘开仓
PAIR_BINDING_LANE_ENABLED: bool = os.getenv(
    "PAIR_BINDING_LANE_ENABLED", "false"
).strip().lower() in ("1", "true", "yes", "on")
PAIR_BINDING_LANE_INTERVAL_SEC: int = max(
    60, int(os.getenv("PAIR_BINDING_LANE_INTERVAL_SEC", "300") or 300)
)
# 绑定熔断：默认干跑（只报告 would_pause）；显式 true 才 pause
SCALP_CIRCUIT_BREAKER_ENABLED: bool = os.getenv(
    "SCALP_CIRCUIT_BREAKER_ENABLED", "false"
).strip().lower() in ("1", "true", "yes", "on")
SCALP_CIRCUIT_BREAKER_INTERVAL_SEC: int = max(
    120, int(os.getenv("SCALP_CIRCUIT_BREAKER_INTERVAL_SEC", "600") or 600)
)
# ═══════════════════════════════════════════════════════════════════════════════
# OpenCode 智能分析层
# ═══════════════════════════════════════════════════════════════════════════════
# 评估期默认开启（闭环重构）：OpenCode 是统一调节门控松紧的慢循环大脑，
# 默认开以形成「SRR → 分析 → 提案 → 评审 → 应用 → 评估回滚」完整闭环；
# 数据不足时 opencode_bridge 会自动跳过分析，故默认开是安全的。
OPENCODE_ENABLED: bool = os.getenv("OPENCODE_ENABLED", "true").lower() in ("1", "true", "yes", "on")
OPENCODE_SERVER_URL: str = os.getenv("OPENCODE_SERVER_URL", "http://127.0.0.1:4096").strip()
# sidecar 自启托管总开关（默认开）：后端启动时自动拉起 opencode serve，并看门狗重启、退出回收。
# 端口/地址复用上面的 OPENCODE_SERVER_URL，不另设端口配置。关掉则回退「手动跑 PS1」模式。
OPENCODE_SIDECAR_AUTOSTART: bool = os.getenv("OPENCODE_SIDECAR_AUTOSTART", "true").lower() in ("1", "true", "yes", "on")
OPENCODE_AGENT_PLAN: str = os.getenv("OPENCODE_AGENT_PLAN", "plan").strip()
OPENCODE_AGENT_BUILD: str = os.getenv("OPENCODE_AGENT_BUILD", "build").strip()
OPENCODE_AUTO_APPLY_MINOR: bool = os.getenv("OPENCODE_AUTO_APPLY_MINOR", "true").lower() in ("1", "true", "yes", "on")
OPENCODE_PATCH_MAX_DELTA_PCT: float = float(os.getenv("OPENCODE_PATCH_MAX_DELTA_PCT", "0.20"))
OPENCODE_VALIDATION_HOURS: int = int(os.getenv("OPENCODE_VALIDATION_HOURS", "24"))
# True：验证窗口随 Paper Pace 档位变化（turbo 6h / warm 12h / balanced 24h / conservative 48h）
OPENCODE_VALIDATION_USE_PACE: bool = os.getenv("OPENCODE_VALIDATION_USE_PACE", "true").lower() in ("1", "true", "yes", "on")
OPENCODE_MAJOR_ALERT_CHANNELS: str = os.getenv("OPENCODE_MAJOR_ALERT_CHANNELS", "feishu,panel")
OPENCODE_CLI_PATH: str = os.getenv("OPENCODE_CLI_PATH", "opencode").strip()
OPENCODE_BRIDGE_TRANSPORT: str = os.getenv("OPENCODE_BRIDGE_TRANSPORT", "http").strip().lower()
OPENCODE_REQUEST_TIMEOUT_S: int = int(os.getenv("OPENCODE_REQUEST_TIMEOUT_S", "180"))
OPENCODE_MAJOR_ALERT_COOLDOWN_S: int = int(os.getenv("OPENCODE_MAJOR_ALERT_COOLDOWN_S", "3600"))
OPENCODE_MAJOR_CREATE_PROPOSALS: bool = os.getenv("OPENCODE_MAJOR_CREATE_PROPOSALS", "true").lower() in ("1", "true", "yes", "on")
OPENCODE_MAJOR_AUTO_APPLY: bool = os.getenv("OPENCODE_MAJOR_AUTO_APPLY", "false").lower() in ("1", "true", "yes", "on")
OPENCODE_MAJOR_PACE_DOWNSHIFT_STEPS: int = int(os.getenv("OPENCODE_MAJOR_PACE_DOWNSHIFT_STEPS", "1"))
OPENCODE_MAJOR_PACE_FLOOR: str = os.getenv("OPENCODE_MAJOR_PACE_FLOOR", "balanced").strip().lower()
OPENCODE_MODEL: str = os.getenv("OPENCODE_MODEL", "deepseek/deepseek-v4-flash").strip()
OPENCODE_SMALL_MODEL: str = os.getenv("OPENCODE_SMALL_MODEL", "deepseek/deepseek-v4-flash").strip()
OPENCODE_AUTO_REVIEW: bool = os.getenv("OPENCODE_AUTO_REVIEW", "true").lower() in ("1", "true", "yes", "on")
OPENCODE_AGENT_REVIEW: str = os.getenv("OPENCODE_AGENT_REVIEW", "review").strip()
OPENCODE_REVIEW_MODEL: str = os.getenv("OPENCODE_REVIEW_MODEL", OPENCODE_MODEL).strip()
OPENCODE_REVIEW_TIMEOUT_S: int = int(os.getenv("OPENCODE_REVIEW_TIMEOUT_S", "120"))
OPENCODE_MULTI_ROUND_TIMEOUT_S: int = int(os.getenv("OPENCODE_MULTI_ROUND_TIMEOUT_S", "120"))

# ═══════════════════════════════════════════════════════════════════════════════
#  AI 深度进化渐进开关 (Phase 1-7)
#  ═══════════════════════════════════════════════════════════════════════════════
#  Level 0: 纯现有逻辑（默认，向后兼容）
#  Level 1: 深度系统消息 (Task 1)
#  Level 2: + 长上下文扩展 (Task 2)
#  Level 3: + 因子对齐检查 (Task 4)
#  Level 4: + 实时教训注入 + OpenCode 深度复盘 (Task 5-7)
# 2026-06-26: 开启 Level 2 — 解锁 OpenCode 多轮辩论 + 长上下文扩展
AI_EVOLUTION_LEVEL: int = int(os.getenv("AI_EVOLUTION_LEVEL", "2"))

# OpenCode Shadow Worker 开关：允许激进参数在沙盒环境中 A/B 验证
OPENCODE_SHADOW_ENABLED: bool = os.getenv("OPENCODE_SHADOW_ENABLED", "false").lower() in ("1", "true", "yes", "on")
OPENCODE_REVIEW_MIN_CONFIDENCE: float = float(os.getenv("OPENCODE_REVIEW_MIN_CONFIDENCE", "0.7"))
OPENCODE_REVIEW_DEFER_RETRY_S: int = int(os.getenv("OPENCODE_REVIEW_DEFER_RETRY_S", "3600"))

# Paper 节奏控制器
PAPER_PACE_DEFAULT_GEAR: str = os.getenv("PAPER_PACE_DEFAULT_GEAR", "turbo").strip().lower()
PAPER_PACE_EVAL_INTERVAL_S: int = int(os.getenv("PAPER_PACE_EVAL_INTERVAL_S", "1800"))

# 窄训练期全自动（OpenCode 策略训练闭环）
TRAINING_PHASE_AUTO: bool = os.getenv("TRAINING_PHASE_AUTO", "true").lower() in ("1", "true", "yes", "on")
TRAINING_AUTO_LIVE: bool = os.getenv("TRAINING_AUTO_LIVE", "true").lower() in ("1", "true", "yes", "on")
TRAINING_LIVE_ENV: str = os.getenv("TRAINING_LIVE_ENV", "mainnet").strip().lower()
TRAINING_LIVE_MAX_STRATEGIES: int = int(os.getenv("TRAINING_LIVE_MAX_STRATEGIES", "2"))
TRAINING_LIVE_PROBE_SIZE_MULT: float = float(os.getenv("TRAINING_LIVE_PROBE_SIZE_MULT", "0.25"))
TRAINING_AUTO_APPLY_MAJOR: bool = os.getenv("TRAINING_AUTO_APPLY_MAJOR", "true").lower() in ("1", "true", "yes", "on")
NSGA2_ENABLED: bool = os.getenv("NSGA2_ENABLED", "true").lower() in ("1", "true", "yes", "on")

# ══════════════════════════════════════════════════
#  学习系统已统一为 V2（OpenCode 为统一学习中枢）
#  V1 双轨模式已移除（原 LEARNING_INTEGRATION_V2 开关）
# ══════════════════════════════════════════════════

# Shadow worker（Tier C core py 变更）
OPENCODE_SHADOW_PORT: int = int(os.getenv("OPENCODE_SHADOW_PORT", "8001"))
OPENCODE_SHADOW_ENABLED: bool = os.getenv("OPENCODE_SHADOW_ENABLED", "false").lower() in ("1", "true", "yes", "on")

# Alpha 助手 · 飞书双向对话
FEISHU_ASSISTANT_ENABLED: bool = os.getenv("FEISHU_ASSISTANT_ENABLED", "false").lower() in ("1", "true", "yes", "on")
FEISHU_VERIFICATION_TOKEN: str = os.getenv("FEISHU_VERIFICATION_TOKEN", "").strip()
FEISHU_ENCRYPT_KEY: str = os.getenv("FEISHU_ENCRYPT_KEY", "").strip()
ASSISTANT_DAILY_REPORT_HOUR_UTC: int = int(os.getenv("ASSISTANT_DAILY_REPORT_HOUR_UTC", "1"))  # 默认 UTC 01:00 ≈ 北京 09:00

