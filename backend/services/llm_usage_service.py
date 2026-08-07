"""
LLM Usage Tracking Service

Records token usage and estimates cost for every LLM API call.
Pricing data sourced from each provider's official documentation (2026-04 rates).

Pricing is defined per 1M tokens (input / output) in USD.
"""
import logging
import os
import time
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, case

from backend.database.models import LLMUsageLog, LLMConfiguration, Account

logger = logging.getLogger(__name__)

CNY_USD_RATE = float(os.environ.get("CNY_USD_RATE", "7.25"))


def _cny(yuan: float) -> float:
    """CNY / 1M tokens → USD / 1M tokens"""
    return round(yuan / CNY_USD_RATE, 6)


# DeepSeek 官方价目（CNY / 百万 tokens）— 与文档一致
DEEPSEEK_OFFICIAL_PRICING: List[Dict[str, Any]] = [
    {
        "model_id": "deepseek-v4-flash",
        "display_name": "DeepSeek-V4-Flash",
        "aliases": ["deepseek-chat", "deepseek-v4-flash"],
        "context_length": "1M",
        "max_output_tokens": "384K",
        "input_cache_hit_cny_per_1m": 0.02,
        "input_cache_miss_cny_per_1m": 1.0,
        "output_cny_per_1m": 2.0,
        "concurrency_limit": 2500,
        "doc_url": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
    },
    {
        "model_id": "deepseek-v4-pro",
        "display_name": "DeepSeek-V4-Pro",
        "aliases": ["deepseek-reasoner", "deepseek-v4-pro"],
        "context_length": "1M",
        "max_output_tokens": "384K",
        "input_cache_hit_cny_per_1m": 0.025,
        "input_cache_miss_cny_per_1m": 3.0,
        "output_cny_per_1m": 6.0,
        "concurrency_limit": 500,
        "doc_url": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
    },
]


# 项目模块中文名（call_type → 展示）
MODULE_LABELS: Dict[str, str] = {
    "master_controller": "总控决策 (Master)",
    "MasterController": "总控决策 (Master)",
    "kline_analyst": "K线深度分析",
    "trading_analysts": "交易分析师(未细分)",
    "strategic_analyst": "战略分析师",
    "whale_tracker_service": "巨鲸追踪",
    "ai_decision_service": "AI 交易决策",
    "ai_decision": "AI 交易决策",
    "full_auto_trading_service": "全自动交易",
    "trade_planner_agent": "交易规划器",
    "direction_agent": "方向 Agent",
    "trade_risk_agent": "风控 Agent",
    "strategy_hypothesis_engine": "策略假设引擎",
    "strategy_evolver": "策略进化",
    "news_intelligence_service": "新闻情报",
    "arb_llm_planner": "套利 LLM 规划",
    "ai_config_generator": "套利 AI 配置",
    "auto_coin_selector": "自动选币",
    "report_generator": "战略报告",
    "ai_factor_discovery_service": "因子发现",
    "ai_trade_journal_service": "交易日志 AI",
    "strategy_optimizer_service": "策略优化",
    "deepseek_chat": "通用 DeepSeek 对话",
    "unknown": "未分类",
}


def normalize_provider(provider: str = "", model: str = "", base_url: str = "") -> str:
    """项目仅 DeepSeek：统一 provider 标识。"""
    p = (provider or "").lower()
    m = (model or "").lower()
    u = (base_url or "").lower()
    if "deepseek" in p or "deepseek" in m or "deepseek" in u:
        return "deepseek"
    return p or "deepseek"


def module_key_from_call_type(call_type: Optional[str]) -> str:
    """从 call_type 提取项目模块键（区分 Master / K线 / 其他）。"""
    if not call_type:
        return "unknown"
    ct = call_type.strip()
    if ct.startswith("sync:") or ct.startswith("async:"):
        parts = ct.split(":", 2)
        module_part = parts[1] if len(parts) > 1 else "unknown"
        func_part = parts[2] if len(parts) > 2 else ""

        if module_part.startswith("MasterController"):
            return "master_controller"
        if module_part.startswith("KlineAnalyst"):
            return "kline_analyst"
        if module_part == "trading_analysts":
            if func_part == "_call_llm":
                return "master_controller"
            if func_part == "_llm_deep_analysis":
                return "kline_analyst"
        if module_part == "strategic_analyst":
            return "strategic_analyst"
        if module_part.startswith("rebate_arb."):
            return "arb_llm_planner"
        return module_part.split(":")[0] or "unknown"
    if ct.startswith("ai_decision"):
        return "ai_decision"
    return ct.split(":")[0] or "unknown"


def module_label(module: str) -> str:
    return MODULE_LABELS.get(module, module.replace("_", " "))


def parse_deepseek_usage(usage: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """解析 DeepSeek usage 字段（含硬盘缓存 hit/miss）。"""
    if not usage:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cache_hit": 0,
            "cache_miss": 0,
        }
    hit = int(usage.get("prompt_cache_hit_tokens") or usage.get("cache_hit_tokens") or 0)
    miss = int(usage.get("prompt_cache_miss_tokens") or usage.get("cache_miss_tokens") or 0)
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or prompt + completion)
    if hit == 0 and miss == 0 and prompt > 0:
        miss = prompt
    elif hit > 0 and miss == 0 and prompt > hit:
        miss = max(prompt - hit, 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cache_hit": hit,
        "cache_miss": miss,
    }


# ══════════════════════════════════════════════════════════════════════
#  Per-model pricing: { model_pattern: (input_per_1M, output_per_1M) }
#  Source: official pricing pages as of 2026-04
#  https://openai.com/api/pricing/
#  https://www.anthropic.com/pricing
#  https://ai.google.dev/gemini-api/docs/pricing
#  https://api-docs.deepseek.com/quick_start/pricing
#  https://help.aliyun.com/zh/model-studio/model-pricing
#  https://www.volcengine.com/product/doubao
#  https://platform.kimi.com/docs/pricing/chat
#  https://x.ai/api
#
#  CNY prices converted at ~7.3 CNY/USD (2026-04 rate)
#  NOTE: patterns are matched with "pattern in model_lower" — more specific
#        names (e.g. "gpt-4o-mini") must appear BEFORE shorter ones ("gpt-4o")
# ══════════════════════════════════════════════════════════════════════

PRICING_TABLE: Dict[str, tuple] = {

    # ── OpenAI (2026-04) ─────────────────────────────────────────────
    # GPT-5.4 flagship series
    "gpt-5.4-pro":           (30.00, 180.00),
    "gpt-5.4-nano":          (0.20,    1.25),
    "gpt-5.4-mini":          (0.75,    4.50),
    "gpt-5.4":               (2.50,   15.00),
    # GPT-4.1 series (widely used, excellent value)
    "gpt-4.1-nano":          (0.10,    0.40),
    "gpt-4.1-mini":          (0.40,    1.60),
    "gpt-4.1":               (2.00,    8.00),
    # GPT-4o series (still popular)
    "gpt-4o-mini":           (0.15,    0.60),
    "gpt-4o":                (2.50,   10.00),
    # o-series reasoning models
    "o1-mini":               (3.00,   12.00),
    "o1-pro":                (150.00, 600.00),
    "o1":                    (15.00,  60.00),
    "o3-pro":                (20.00,  80.00),
    "o3-mini":               (1.10,    4.40),
    "o3":                    (2.00,    8.00),
    "o4-mini":               (1.10,    4.40),
    # Legacy GPT-4
    "gpt-4-turbo":           (10.00,  30.00),
    "gpt-4":                 (30.00,  60.00),
    "gpt-3.5-turbo":         (0.50,    1.50),

    # ── Anthropic Claude (2026-04) ───────────────────────────────────
    # Claude 4.x series (latest — 1M context, no long-ctx surcharge)
    "claude-opus-4":         (5.00,   25.00),
    "claude-sonnet-4":       (3.00,   15.00),
    "claude-haiku-4":        (1.00,    5.00),
    # Backward-compat aliases used by some SDKs
    "claude-4-opus":         (5.00,   25.00),
    "claude-4-sonnet":       (3.00,   15.00),
    "claude-4-haiku":        (1.00,    5.00),
    # Claude 3.7 (experimental reasoning)
    "claude-3.7-sonnet":     (3.00,   15.00),
    # Claude 3.5 series
    "claude-3.5-haiku":      (0.80,    4.00),
    "claude-3.5-sonnet":     (3.00,   15.00),
    # Claude 3 legacy
    "claude-3-haiku":        (0.25,    1.25),
    "claude-3-sonnet":       (3.00,   15.00),
    "claude-3-opus":         (15.00,  75.00),

    # ── Google Gemini (2026-04) ──────────────────────────────────────
    # Gemini 3 series (latest flagship, 1M context)
    "gemini-3.1-flash-lite": (0.25,    1.50),
    "gemini-3.1-flash":      (0.50,    3.00),
    "gemini-3.1-pro":        (2.00,   12.00),
    "gemini-3-flash":        (0.50,    3.00),
    "gemini-3-pro":          (2.00,   12.00),
    # Gemini 2.5 series (stable)
    "gemini-2.5-flash":      (0.15,    0.60),
    "gemini-2.5-pro":        (1.25,   10.00),
    # Gemini 2.0 legacy
    "gemini-2.0-flash":      (0.10,    0.40),
    # Gemini 1.5 legacy
    "gemini-1.5-flash":      (0.075,   0.30),
    "gemini-1.5-pro":        (1.25,    5.00),

    # ── DeepSeek V4 (2026-06 官方: https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) ──
    # 价格单位：CNY / 百万 tokens；此处按 CNY_USD_RATE 换算为 USD 供统一记账
    "deepseek-v4-pro":           (_cny(3.0),   _cny(6.0)),    # 缓存未命中输入
    "deepseek-v4-flash":         (_cny(1.0),   _cny(2.0)),
    "deepseek-reasoner":         (_cny(3.0),   _cny(6.0)),   # 思考模式 → Pro 档
    "deepseek-chat":             (_cny(1.0),   _cny(2.0)),   # 非思考 → Flash 档
    "deepseek-v3":               (_cny(1.0),   _cny(2.0)),
    "deepseek-r1":               (_cny(4.0),   _cny(16.0)),  # 旧 R1 估算

    # ── Qwen / Alibaba Cloud (2026-04, USD equiv) ────────────────────
    # Qwen3 latest (dramatically cheaper after 2026 price war)
    "qwen3-235b":            (0.22,    0.88),   # $0.22/$0.88 official
    "qwen3-max":             (0.34,    1.37),   # 2.5 CNY/M → $0.34
    "qwen3-plus":            (0.11,    0.44),   # 0.8 CNY/M → $0.11
    "qwen3-turbo":           (0.04,    0.14),   # 0.3 CNY/M → $0.04
    "qwq-plus":              (0.16,    0.66),   # QwQ reasoning model
    # Qwen2.5 / legacy aliases
    "qwen-max":              (0.34,    1.37),
    "qwen-plus":             (0.11,    0.44),
    "qwen-turbo":            (0.04,    0.14),
    "qwen-long":             (0.07,    0.27),   # 0.5 CNY/M → $0.07
    "qwen-qwq-plus":         (0.16,    0.66),

    # ── Volcengine / Doubao 豆包 (2026-04, USD equiv) ────────────────
    "doubao-2.0-pro":        (0.11,    0.27),   # 0.8/2.0 CNY → $0.11/$0.27
    "doubao-2.0":            (0.11,    0.27),
    "doubao-pro-128k":       (0.68,    1.23),   # 5/9 CNY
    "doubao-pro-32k":        (0.11,    0.27),
    "doubao-lite-32k":       (0.04,    0.08),   # 0.3/0.6 CNY

    # ── Moonshot / Kimi (2026-04) ────────────────────────────────────
    "kimi-k2":               (0.60,    2.50),   # Kimi K2.5 main model
    "moonshot-v1-128k":      (0.82,    0.82),   # 6 CNY → $0.82
    "moonshot-v1-32k":       (0.27,    0.27),   # 2 CNY → $0.27
    "moonshot-v1-8k":        (0.14,    0.14),   # 1 CNY → $0.14

    # ── xAI Grok (2026-04) ──────────────────────────────────────────
    "grok-4":                (2.00,    6.00),   # Grok 4.20
    "grok-3-mini":           (0.30,    0.50),
    "grok-3":                (2.50,   10.00),   # Grok 3 Fast
    "grok-2":                (2.00,   10.00),
}

# Provider-level fallback pricing (when model not found in table)
PROVIDER_FALLBACK: Dict[str, tuple] = {
    "openai":      (2.00,   8.00),   # assume gpt-4.1 tier (2026-04)
    "deepseek":    (_cny(1.0),  _cny(2.0)),   # 默认 Flash 档
    "qwen":        (0.11,   0.44),   # assume qwen3-plus
    "volcengine":  (0.11,   0.27),   # assume doubao-2.0
    "moonshot":    (0.60,   2.50),   # assume kimi-k2
    "kimi":        (0.60,   2.50),   # assume kimi-k2
    "anthropic":   (3.00,  15.00),   # assume claude-sonnet-4
    "google":      (2.00,  12.00),   # assume gemini-3.1-pro
    "xai":         (2.00,   6.00),   # assume grok-4
    "custom":      (1.00,   3.00),   # conservative default
}


def _match_pricing(model: str, provider: str = "") -> tuple:
    """Find the best pricing match for a model string."""
    model_lower = model.lower().strip()

    for pattern, price in PRICING_TABLE.items():
        if pattern in model_lower:
            return price

    provider_lower = provider.lower().strip()
    if provider_lower in PROVIDER_FALLBACK:
        return PROVIDER_FALLBACK[provider_lower]

    return PROVIDER_FALLBACK.get("custom", (1.0, 3.0))


def _resolve_deepseek_tier(model: str) -> str:
    m = (model or "").lower()
    if any(k in m for k in ("pro", "reasoner")):
        return "deepseek-v4-pro"
    return "deepseek-v4-flash"


def estimate_cost_cny(
    model: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cache_hit: bool = False,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
) -> float:
    """按 DeepSeek 官方 CNY 价估算（支持缓存 hit/miss 分拆）。"""
    prov = normalize_provider(provider, model)
    if prov == "deepseek" or "deepseek" in (model or "").lower():
        hit = cache_hit_tokens
        miss = cache_miss_tokens
        if hit == 0 and miss == 0:
            if cache_hit:
                hit = prompt_tokens
            else:
                miss = prompt_tokens
        tier = _resolve_deepseek_tier(model)
        spec = next((x for x in DEEPSEEK_OFFICIAL_PRICING if x["model_id"] == tier), DEEPSEEK_OFFICIAL_PRICING[0])
        cost = (
            hit * spec["input_cache_hit_cny_per_1m"]
            + miss * spec["input_cache_miss_cny_per_1m"]
            + completion_tokens * spec["output_cny_per_1m"]
        ) / 1_000_000
        return round(cost, 6)
    usd = estimate_cost(model, provider, prompt_tokens, completion_tokens)
    return round(usd * CNY_USD_RATE, 6)


def get_deepseek_official_pricing() -> Dict[str, Any]:
    return {
        "cny_usd_rate": CNY_USD_RATE,
        "billing_rule": "扣减费用 = token 消耗量 × 模型单价（优先扣赠送余额）",
        "models": DEEPSEEK_OFFICIAL_PRICING,
        "note": "费用按 API 返回的缓存命中/未命中 token 分拆计价；无分拆时按未命中估算（偏保守）。",
    }


def estimate_cost(model: str, provider: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost for a single API call."""
    input_rate, output_rate = _match_pricing(model, provider)
    cost = (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000
    return round(cost, 6)


def ensure_llm_usage_schema(db: Session) -> None:
    """补齐 analytics 库 llm_usage_logs 缺失列（旧 SQLite 库无 cache/cny 字段会导致费用全记 0）。"""
    try:
        from sqlalchemy import inspect, text

        bind = db.get_bind()
        insp = inspect(bind)
        if "llm_usage_logs" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("llm_usage_logs")}
        alters = []
        if "prompt_cache_hit_tokens" not in cols:
            alters.append(
                "ADD COLUMN prompt_cache_hit_tokens INTEGER NOT NULL DEFAULT 0"
            )
        if "prompt_cache_miss_tokens" not in cols:
            alters.append(
                "ADD COLUMN prompt_cache_miss_tokens INTEGER NOT NULL DEFAULT 0"
            )
        if "estimated_cost_cny" not in cols:
            alters.append("ADD COLUMN estimated_cost_cny FLOAT NOT NULL DEFAULT 0")
        for stmt in alters:
            db.execute(text(f"ALTER TABLE llm_usage_logs {stmt}"))
        if alters:
            db.commit()
            logger.info("[LLMUsage] Migrated llm_usage_logs schema: %s", alters)
    except Exception as e:
        db.rollback()
        logger.debug("[LLMUsage] Schema ensure skipped: %s", e)


def _cost_for_aggregate_row(
    provider: str,
    model: str,
    prompt: int,
    completion: int,
    cache_hit: int,
    cache_miss: int,
    stored_cny: float,
    global_cache_rate: float,
) -> Tuple[float, float]:
    """模块/场景聚合费用：优先 DB 已存费用，否则按全局缓存命中率分摊。"""
    if stored_cny > 0:
        cny = round(stored_cny, 6)
        return round(cny / CNY_USD_RATE, 6), cny

    hit, miss = cache_hit, cache_miss
    if hit + miss <= 0 and prompt > 0:
        if global_cache_rate > 0:
            hit = int(prompt * global_cache_rate)
            miss = max(prompt - hit, 0)
        else:
            miss = prompt
    return _row_costs(provider, model, prompt, completion, cache_hit=hit, cache_miss=miss)


def _normalize_module_costs_to_total(
    modules_map: Dict[str, Dict[str, Any]], total_cny: float
) -> None:
    """模块费用之和必须与顶部总费用一致，避免「各行相加远大于总额」。"""
    if total_cny <= 0 or not modules_map:
        return
    mod_sum = sum(float(m.get("cost_cny") or 0) for m in modules_map.values())
    if mod_sum <= total_cny * 1.02:
        return
    ratio = total_cny / mod_sum
    for m in modules_map.values():
        m["cost_cny"] = round(float(m["cost_cny"]) * ratio, 4)
        m["cost_usd"] = round(float(m["cost_usd"]) * ratio, 4)


def record_usage(
    db: Session,
    *,
    account_id: Optional[int] = None,
    llm_config_id: Optional[int] = None,
    provider: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    prompt_cache_hit_tokens: int = 0,
    prompt_cache_miss_tokens: int = 0,
    reasoning_tokens: Optional[int] = None,
    estimated_cost_usd: Optional[float] = None,
    estimated_cost_cny: Optional[float] = None,
    call_type: Optional[str] = None,
    duration_ms: Optional[int] = None,
    success: bool = True,
    error_message: Optional[str] = None,
    usage_info: Optional[Dict[str, Any]] = None,
    base_url: str = "",
) -> Optional[LLMUsageLog]:
    """Persist a single LLM usage record."""
    try:
        ensure_llm_usage_schema(db)
        if usage_info:
            kwargs = build_usage_record_kwargs(
                usage_info=usage_info,
                provider=provider,
                model=model,
                base_url=base_url,
                account_id=account_id,
                llm_config_id=llm_config_id,
                call_type=call_type,
                duration_ms=duration_ms,
                success=success,
                error_message=error_message,
                reasoning_tokens=reasoning_tokens,
            )
            provider = kwargs["provider"]
            model = kwargs["model"]
            prompt_tokens = kwargs["prompt_tokens"]
            completion_tokens = kwargs["completion_tokens"]
            total_tokens = kwargs["total_tokens"]
            prompt_cache_hit_tokens = kwargs["prompt_cache_hit_tokens"]
            prompt_cache_miss_tokens = kwargs["prompt_cache_miss_tokens"]
            estimated_cost_usd = kwargs["estimated_cost_usd"]
            estimated_cost_cny = kwargs["estimated_cost_cny"]
            call_type = kwargs["call_type"]

        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens

        prov = normalize_provider(provider, model, base_url)
        if estimated_cost_usd is None or estimated_cost_cny is None:
            estimated_cost_usd, estimated_cost_cny = _row_costs(
                prov, model, prompt_tokens, completion_tokens,
                cache_hit=prompt_cache_hit_tokens,
                cache_miss=prompt_cache_miss_tokens,
            )
        if prompt_cache_hit_tokens == 0 and prompt_cache_miss_tokens == 0 and prompt_tokens > 0:
            prompt_cache_miss_tokens = prompt_tokens

        log = LLMUsageLog(
            account_id=account_id,
            llm_config_id=llm_config_id,
            provider=prov,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            prompt_cache_hit_tokens=prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=prompt_cache_miss_tokens,
            estimated_cost_usd=estimated_cost_usd,
            estimated_cost_cny=estimated_cost_cny,
            # 生产库历史列为 VARCHAR(50)，模型定义虽已放宽到 128，
            # 但未迁移时会触发 StringDataRightTruncation。
            call_type=(call_type or "")[:50] or None,
            duration_ms=duration_ms,
            success="true" if success else "false",
            error_message=error_message[:500] if error_message else None,
        )
        db.add(log)
        db.commit()
        return log
    except Exception as e:
        db.rollback()
        logger.error("Failed to record LLM usage: %s", e)
        return None


def get_usage_summary(db: Session, days: int = 30) -> Dict[str, Any]:
    """Aggregate usage statistics for the dashboard."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    # SQLite stores naive datetimes; use naive cutoff for comparison
    cutoff_naive = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        db.query(
            LLMUsageLog.provider,
            LLMUsageLog.model,
            func.count(LLMUsageLog.id).label("total_calls"),
            func.sum(LLMUsageLog.prompt_tokens).label("total_prompt_tokens"),
            func.sum(LLMUsageLog.completion_tokens).label("total_completion_tokens"),
            func.sum(LLMUsageLog.total_tokens).label("total_tokens"),
            func.sum(LLMUsageLog.estimated_cost_usd).label("total_cost"),
        )
        .filter(LLMUsageLog.created_at >= cutoff_naive)
        .group_by(LLMUsageLog.provider, LLMUsageLog.model)
        .all()
    )

    models = []
    grand_total_cost = 0.0
    grand_total_calls = 0
    grand_total_tokens = 0

    for r in rows:
        cost = float(r.total_cost or 0)
        calls = int(r.total_calls or 0)
        tokens = int(r.total_tokens or 0)
        grand_total_cost += cost
        grand_total_calls += calls
        grand_total_tokens += tokens

        input_rate, output_rate = _match_pricing(r.model, r.provider)

        models.append({
            "provider": r.provider,
            "model": r.model,
            "total_calls": calls,
            "total_prompt_tokens": int(r.total_prompt_tokens or 0),
            "total_completion_tokens": int(r.total_completion_tokens or 0),
            "total_tokens": tokens,
            "total_cost_usd": round(cost, 4),
            "error_count": 0,
            "pricing": {
                "input_per_1m": input_rate,
                "output_per_1m": output_rate,
            },
        })

    # Daily breakdown — use SQLite-compatible date() function
    date_expr = func.date(LLMUsageLog.created_at)
    daily_rows = (
        db.query(
            date_expr.label("day"),
            func.count(LLMUsageLog.id).label("calls"),
            func.sum(LLMUsageLog.total_tokens).label("tokens"),
            func.sum(LLMUsageLog.estimated_cost_usd).label("cost"),
        )
        .filter(LLMUsageLog.created_at >= cutoff_naive)
        .group_by(date_expr)
        .order_by(date_expr)
        .all()
    )

    daily = [
        {
            "date": str(r.day),
            "calls": int(r.calls or 0),
            "tokens": int(r.tokens or 0),
            "cost": round(float(r.cost or 0), 4),
        }
        for r in daily_rows
    ]

    return {
        "period_days": days,
        "grand_total_cost_usd": round(grand_total_cost, 4),
        "grand_total_calls": grand_total_calls,
        "grand_total_tokens": grand_total_tokens,
        "models": sorted(models, key=lambda x: x["total_cost_usd"], reverse=True),
        "daily": daily,
    }


def _cutoff_naive(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _load_account_names() -> Dict[Optional[int], str]:
    try:
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            rows = db.query(Account.id, Account.name).all()
            return {r.id: r.name for r in rows}
        finally:
            db.close()
    except Exception:
        return {}


def _is_deepseek(provider: str, model: str) -> bool:
    return (provider or "").lower() == "deepseek" or "deepseek" in (model or "").lower()


def _row_costs(
    provider: str,
    model: str,
    prompt: int,
    completion: int,
    cache_hit: int = 0,
    cache_miss: int = 0,
) -> Tuple[float, float]:
    """从 token 用量重算费用（支持 DeepSeek 缓存 hit/miss 分拆）。"""
    prov = normalize_provider(provider, model)
    if _is_deepseek(prov, model):
        cny = estimate_cost_cny(
            model, prov, prompt, completion,
            cache_hit_tokens=cache_hit,
            cache_miss_tokens=cache_miss,
        )
        usd = round(cny / CNY_USD_RATE, 6)
        return usd, round(cny, 6)
    usd = estimate_cost(model, prov, prompt, completion)
    cny = round(usd * CNY_USD_RATE, 6)
    return usd, cny


def build_usage_record_kwargs(
    *,
    usage_info: Optional[Dict[str, Any]],
    provider: str,
    model: str,
    base_url: str = "",
    account_id: Optional[int] = None,
    llm_config_id: Optional[int] = None,
    call_type: Optional[str] = None,
    duration_ms: Optional[int] = None,
    success: bool = True,
    error_message: Optional[str] = None,
    reasoning_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """统一构造 record_usage 参数（全项目唯一入口）。"""
    parsed = parse_deepseek_usage(usage_info)
    prov = normalize_provider(provider, model, base_url)
    cost_usd, cost_cny = _row_costs(
        prov, model,
        parsed["prompt_tokens"],
        parsed["completion_tokens"],
        cache_hit=parsed["cache_hit"],
        cache_miss=parsed["cache_miss"],
    )
    return {
        "account_id": account_id,
        "llm_config_id": llm_config_id,
        "provider": prov,
        "model": model or "unknown",
        "prompt_tokens": parsed["prompt_tokens"],
        "completion_tokens": parsed["completion_tokens"],
        "total_tokens": parsed["total_tokens"],
        "prompt_cache_hit_tokens": parsed["cache_hit"],
        "prompt_cache_miss_tokens": parsed["cache_miss"],
        "reasoning_tokens": reasoning_tokens,
        "estimated_cost_usd": cost_usd,
        "estimated_cost_cny": cost_cny,
        "call_type": (call_type or "")[:50] or None,
        "duration_ms": duration_ms,
        "success": success,
        "error_message": error_message,
    }


def _row_cost_cny(provider: str, model: str, prompt: int, completion: int, _usd: float = 0) -> float:
    return _row_costs(provider, model, prompt, completion)[1]


def _fill_daily_gaps(daily_list: List[Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    """补全选定周期内无数据的日期（填 0）。"""
    by_date = {d["date"]: d for d in daily_list}
    today = datetime.now(timezone.utc).date()
    result: List[Dict[str, Any]] = []
    for i in range(days - 1, -1, -1):
        key = str(today - timedelta(days=i))
        if key in by_date:
            result.append(by_date[key])
        else:
            result.append({
                "date": key,
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "tokens": 0,
                "cache_hit_tokens": 0,
                "cache_miss_tokens": 0,
                "cost_usd": 0.0,
                "cost_cny": 0.0,
            })
    return result


def _accumulate_daily_bucket(
    bucket: Dict[str, Any],
    calls: int,
    prompt: int,
    completion: int,
    tokens: int,
    provider: str,
    model: str,
    cache_hit: int = 0,
    cache_miss: int = 0,
) -> None:
    if cache_hit == 0 and cache_miss == 0 and prompt > 0:
        cache_miss = prompt
    usd, cny = _row_costs(provider, model, prompt, completion, cache_hit=cache_hit, cache_miss=cache_miss)
    bucket["calls"] += calls
    bucket["prompt_tokens"] += prompt
    bucket["completion_tokens"] += completion
    bucket["tokens"] += tokens
    bucket["cache_hit_tokens"] = bucket.get("cache_hit_tokens", 0) + cache_hit
    bucket["cache_miss_tokens"] = bucket.get("cache_miss_tokens", 0) + cache_miss
    bucket["cost_usd"] = round(bucket["cost_usd"] + usd, 6)
    bucket["cost_cny"] = round(bucket["cost_cny"] + cny, 6)


def _daily_stats_from_series(daily: List[Dict[str, Any]]) -> Dict[str, Any]:
    active = [d for d in daily if d["calls"] > 0]
    if not daily:
        return {
            "today_cny": 0,
            "yesterday_cny": 0,
            "avg_daily_cny": 0,
            "avg_active_day_cny": 0,
            "active_days": 0,
            "peak_day": None,
        }
    today = daily[-1] if daily else None
    yesterday = daily[-2] if len(daily) >= 2 else None
    total_cny = sum(d["cost_cny"] for d in daily)
    active_cny = sum(d["cost_cny"] for d in active)
    peak = max(active, key=lambda x: x["cost_cny"]) if active else None
    return {
        "today_cny": round(today["cost_cny"], 4) if today else 0,
        "today_calls": today["calls"] if today else 0,
        "yesterday_cny": round(yesterday["cost_cny"], 4) if yesterday else 0,
        "yesterday_calls": yesterday["calls"] if yesterday else 0,
        "avg_daily_cny": round(total_cny / len(daily), 4),
        "avg_active_day_cny": round(active_cny / len(active), 4) if active else 0,
        "active_days": len(active),
        "peak_day": {
            "date": peak["date"],
            "cost_cny": round(peak["cost_cny"], 4),
            "calls": peak["calls"],
            "tokens": peak["tokens"],
        } if peak else None,
    }


def get_billing_dashboard(db: Session, days: int = 30) -> Dict[str, Any]:
    """详细计费仪表盘：总览 + 交易员 + 模型 + 调用场景 + DeepSeek 官方价。"""
    ensure_llm_usage_schema(db)
    cutoff_naive = _cutoff_naive(days)
    account_names = _load_account_names()

    agg = (
        db.query(
            func.count(LLMUsageLog.id),
            func.coalesce(func.sum(LLMUsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.completion_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.total_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.estimated_cost_usd), 0),
            func.coalesce(func.sum(case((LLMUsageLog.success == "false", 1), else_=0)), 0),
        )
        .filter(LLMUsageLog.created_at >= cutoff_naive)
        .one()
    )
    total_calls = int(agg[0] or 0)
    total_prompt = int(agg[1] or 0)
    total_completion = int(agg[2] or 0)
    total_tokens = int(agg[3] or 0)
    failed_calls = int(agg[5] or 0)

    model_rows = (
        db.query(
            LLMUsageLog.provider,
            LLMUsageLog.model,
            func.count(LLMUsageLog.id),
            func.coalesce(func.sum(LLMUsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.completion_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.total_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.estimated_cost_usd), 0),
            func.coalesce(func.sum(case((LLMUsageLog.success == "false", 1), else_=0)), 0),
            func.coalesce(func.avg(LLMUsageLog.duration_ms), 0),
            func.coalesce(func.sum(LLMUsageLog.prompt_cache_hit_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.prompt_cache_miss_tokens), 0),
        )
        .filter(LLMUsageLog.created_at >= cutoff_naive)
        .group_by(LLMUsageLog.provider, LLMUsageLog.model)
        .all()
    )
    models: List[Dict[str, Any]] = []
    total_usd_recalc = 0.0
    total_cny_recalc = 0.0
    total_cache_hit = 0
    total_cache_miss = 0
    for r in model_rows:
        prompt = int(r[3] or 0)
        completion = int(r[4] or 0)
        cache_hit = int(r[9] or 0)
        cache_miss = int(r[10] or 0)
        usd, cny = _row_costs(r.provider, r.model, prompt, completion, cache_hit=cache_hit, cache_miss=cache_miss)
        total_usd_recalc += usd
        total_cny_recalc += cny
        total_cache_hit += cache_hit
        total_cache_miss += cache_miss
        inp_rate, out_rate = _match_pricing(r.model, r.provider)
        models.append({
            "provider": r.provider,
            "model": r.model,
            "total_calls": int(r[2] or 0),
            "failed_calls": int(r[7] or 0),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": int(r[5] or 0),
            "cost_usd": round(usd, 4),
            "cost_cny": round(cny, 4),
            "avg_duration_ms": round(float(r[8] or 0), 1),
            "pricing_usd_per_1m": {"input": inp_rate, "output": out_rate},
        })
    models.sort(key=lambda x: x["cost_cny"], reverse=True)

    total_input_tracked = total_cache_hit + total_cache_miss
    global_cache_rate = (
        total_cache_hit / total_input_tracked if total_input_tracked > 0 else 0.0
    )

    call_type_rows = (
        db.query(
            LLMUsageLog.call_type,
            LLMUsageLog.provider,
            LLMUsageLog.model,
            func.count(LLMUsageLog.id),
            func.coalesce(func.sum(LLMUsageLog.total_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.estimated_cost_usd), 0),
            func.coalesce(func.sum(LLMUsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.completion_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.prompt_cache_hit_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.prompt_cache_miss_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.estimated_cost_cny), 0),
        )
        .filter(LLMUsageLog.created_at >= cutoff_naive)
        .group_by(LLMUsageLog.call_type, LLMUsageLog.provider, LLMUsageLog.model)
        .order_by(func.count(LLMUsageLog.id).desc())
        .limit(100)
        .all()
    )
    call_types = []
    modules_map: Dict[str, Dict[str, Any]] = {}
    for r in call_type_rows:
        prompt = int(r[6] or 0)
        completion = int(r[7] or 0)
        cache_hit = int(r[8] or 0)
        cache_miss = int(r[9] or 0)
        stored_cny = float(r[10] or 0)
        usd, cny = _cost_for_aggregate_row(
            r.provider, r.model, prompt, completion,
            cache_hit, cache_miss, stored_cny, global_cache_rate,
        )
        ct = r.call_type or "unknown"
        call_types.append({
            "call_type": ct,
            "provider": r.provider,
            "model": r.model,
            "calls": int(r[3] or 0),
            "tokens": int(r[4] or 0),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "cache_hit_tokens": cache_hit,
            "cache_miss_tokens": cache_miss,
            "cost_usd": round(usd, 4),
            "cost_cny": round(cny, 4),
        })
        mk = module_key_from_call_type(ct)
        if mk not in modules_map:
            modules_map[mk] = {
                "module": mk,
                "module_label": module_label(mk),
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "tokens": 0,
                "cache_hit_tokens": 0,
                "cache_miss_tokens": 0,
                "cost_usd": 0.0,
                "cost_cny": 0.0,
            }
        mod = modules_map[mk]
        mod["calls"] += int(r[3] or 0)
        mod["prompt_tokens"] += prompt
        mod["completion_tokens"] += completion
        mod["tokens"] += int(r[4] or 0)
        mod["cache_hit_tokens"] += cache_hit
        mod["cache_miss_tokens"] += cache_miss
        mod["cost_usd"] = round(mod["cost_usd"] + usd, 6)
        mod["cost_cny"] = round(mod["cost_cny"] + cny, 6)
    _normalize_module_costs_to_total(modules_map, total_cny_recalc)
    modules = sorted(modules_map.values(), key=lambda x: x["cost_cny"], reverse=True)
    for m in modules:
        m["cost_usd"] = round(m["cost_usd"], 4)
        m["cost_cny"] = round(m["cost_cny"], 4)
        cache_tracked = m["cache_hit_tokens"] + m["cache_miss_tokens"]
        if cache_tracked > 0:
            m["cache_hit_rate"] = round(m["cache_hit_tokens"] / cache_tracked, 4)
        else:
            # 历史记录未写入 cache 分拆时，不展示误导性的 0%
            m["cache_hit_rate"] = None

    date_expr = func.date(LLMUsageLog.created_at)
    daily_detail_rows = (
        db.query(
            date_expr.label("day"),
            LLMUsageLog.provider,
            LLMUsageLog.model,
            func.count(LLMUsageLog.id),
            func.coalesce(func.sum(LLMUsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.completion_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.total_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.prompt_cache_hit_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.prompt_cache_miss_tokens), 0),
        )
        .filter(LLMUsageLog.created_at >= cutoff_naive)
        .group_by(date_expr, LLMUsageLog.provider, LLMUsageLog.model)
        .order_by(date_expr)
        .all()
    )
    daily_map: Dict[str, Dict[str, Any]] = {}
    for r in daily_detail_rows:
        day = str(r.day)
        if day not in daily_map:
            daily_map[day] = {
                "date": day,
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "tokens": 0,
                "cache_hit_tokens": 0,
                "cache_miss_tokens": 0,
                "cost_usd": 0.0,
                "cost_cny": 0.0,
            }
        _accumulate_daily_bucket(
            daily_map[day],
            int(r[3] or 0),
            int(r[4] or 0),
            int(r[5] or 0),
            int(r[6] or 0),
            r.provider,
            r.model,
            cache_hit=int(r[7] or 0),
            cache_miss=int(r[8] or 0),
        )
    daily_billing = _fill_daily_gaps(list(daily_map.values()), days)
    for d in daily_billing:
        d["cost_usd"] = round(d["cost_usd"], 4)
        d["cost_cny"] = round(d["cost_cny"], 4)
    daily_stats = _daily_stats_from_series(daily_billing)

    trader_rows = (
        db.query(
            LLMUsageLog.account_id,
            LLMUsageLog.provider,
            LLMUsageLog.model,
            LLMUsageLog.call_type,
            func.count(LLMUsageLog.id),
            func.coalesce(func.sum(LLMUsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.completion_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.total_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.estimated_cost_usd), 0),
            func.coalesce(func.sum(case((LLMUsageLog.success == "false", 1), else_=0)), 0),
            func.coalesce(func.sum(LLMUsageLog.prompt_cache_hit_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.prompt_cache_miss_tokens), 0),
        )
        .filter(LLMUsageLog.created_at >= cutoff_naive)
        .group_by(
            LLMUsageLog.account_id,
            LLMUsageLog.provider,
            LLMUsageLog.model,
            LLMUsageLog.call_type,
        )
        .all()
    )

    traders_map: Dict[str, Dict[str, Any]] = {}
    for r in trader_rows:
        aid = r.account_id
        key = str(aid) if aid is not None else "none"
        if key not in traders_map:
            label = account_names.get(aid) if aid else None
            traders_map[key] = {
                "account_id": aid,
                "account_name": label or (f"交易员 #{aid}" if aid else "系统 / 未绑定交易员"),
                "total_calls": 0,
                "failed_calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "cost_cny": 0.0,
                "models": {},
                "call_types": {},
                "daily_map": {},
            }
        t = traders_map[key]
        calls = int(r[4] or 0)
        prompt = int(r[5] or 0)
        completion = int(r[6] or 0)
        tokens = int(r[7] or 0)
        failed = int(r[9] or 0)
        cache_hit = int(r[10] or 0)
        cache_miss = int(r[11] or 0)
        usd, cny = _row_costs(r.provider, r.model, prompt, completion, cache_hit=cache_hit, cache_miss=cache_miss)

        t["total_calls"] += calls
        t["failed_calls"] += failed
        t["prompt_tokens"] += prompt
        t["completion_tokens"] += completion
        t["total_tokens"] += tokens
        t["cost_usd"] += usd
        t["cost_cny"] += cny

        mk = f"{r.provider}:{r.model}"
        if mk not in t["models"]:
            t["models"][mk] = {
                "provider": r.provider,
                "model": r.model,
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "tokens": 0,
                "cost_usd": 0.0,
                "cost_cny": 0.0,
            }
        m = t["models"][mk]
        m["calls"] += calls
        m["prompt_tokens"] += prompt
        m["completion_tokens"] += completion
        m["tokens"] += tokens
        m["cost_usd"] = round(m["cost_usd"] + usd, 6)
        m["cost_cny"] = round(m["cost_cny"] + cny, 6)

        ct = r.call_type or "unknown"
        if ct not in t["call_types"]:
            t["call_types"][ct] = {"call_type": ct, "calls": 0, "tokens": 0, "cost_usd": 0.0, "cost_cny": 0.0}
        c = t["call_types"][ct]
        c["calls"] += calls
        c["tokens"] += tokens
        c["cost_usd"] = round(c["cost_usd"] + usd, 6)
        c["cost_cny"] = round(c["cost_cny"] + cny, 6)

    # 按交易员 × 日期聚合
    trader_daily_rows = (
        db.query(
            date_expr.label("day"),
            LLMUsageLog.account_id,
            LLMUsageLog.provider,
            LLMUsageLog.model,
            func.count(LLMUsageLog.id),
            func.coalesce(func.sum(LLMUsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.completion_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.total_tokens), 0),
        )
        .filter(LLMUsageLog.created_at >= cutoff_naive)
        .group_by(date_expr, LLMUsageLog.account_id, LLMUsageLog.provider, LLMUsageLog.model)
        .order_by(date_expr)
        .all()
    )
    for r in trader_daily_rows:
        aid = r.account_id
        key = str(aid) if aid is not None else "none"
        if key not in traders_map:
            continue
        day = str(r.day)
        t = traders_map[key]
        if day not in t["daily_map"]:
            t["daily_map"][day] = {
                "date": day,
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "tokens": 0,
                "cost_usd": 0.0,
                "cost_cny": 0.0,
            }
        _accumulate_daily_bucket(
            t["daily_map"][day],
            int(r[4] or 0),
            int(r[5] or 0),
            int(r[6] or 0),
            int(r[7] or 0),
            r.provider,
            r.model,
        )

    traders: List[Dict[str, Any]] = []
    for t in traders_map.values():
        t["cost_usd"] = round(t["cost_usd"], 4)
        t["cost_cny"] = round(t["cost_cny"], 4)
        t["models"] = sorted(t["models"].values(), key=lambda x: x["cost_cny"], reverse=True)
        t["call_types"] = sorted(t["call_types"].values(), key=lambda x: x["cost_cny"], reverse=True)
        trader_daily = sorted(t.pop("daily_map").values(), key=lambda x: x["date"])
        for d in trader_daily:
            d["cost_usd"] = round(d["cost_usd"], 4)
            d["cost_cny"] = round(d["cost_cny"], 4)
        t["daily"] = trader_daily
        traders.append(t)
    traders.sort(key=lambda x: x["cost_cny"], reverse=True)

    ds_models = [m for m in models if _is_deepseek(m["provider"], m["model"])]
    total_cny = round(total_cny_recalc, 4)
    total_usd = round(total_usd_recalc, 4)
    conservative_cny = 0.0
    for r in model_rows:
        prompt = int(r[3] or 0)
        completion = int(r[4] or 0)
        conservative_cny += estimate_cost_cny(
            r.model, r.provider, prompt, completion,
            cache_hit_tokens=0, cache_miss_tokens=prompt,
        )
    total_input = total_cache_hit + total_cache_miss
    cache_summary = {
        "cache_hit_tokens": total_cache_hit,
        "cache_miss_tokens": total_cache_miss,
        "cache_hit_rate": round(total_cache_hit / total_input, 4) if total_input else 0.0,
        "cost_cny_actual": total_cny,
        "cost_cny_if_all_miss": round(conservative_cny, 4),
        "cache_savings_cny": round(max(conservative_cny - total_cny, 0), 4),
        "has_cache_breakdown": total_cache_hit > 0,
    }

    recent_rows = (
        db.query(LLMUsageLog)
        .filter(LLMUsageLog.created_at >= cutoff_naive)
        .order_by(LLMUsageLog.id.desc())
        .limit(30)
        .all()
    )
    recent = []
    for row in recent_rows:
        hit = int(getattr(row, "prompt_cache_hit_tokens", 0) or 0)
        miss = int(getattr(row, "prompt_cache_miss_tokens", 0) or 0)
        usd, cny = _row_costs(
            row.provider, row.model, row.prompt_tokens, row.completion_tokens,
            cache_hit=hit, cache_miss=miss,
        )
        recent.append({
            "id": row.id,
            "account_id": row.account_id,
            "account_name": account_names.get(row.account_id) or (f"交易员 #{row.account_id}" if row.account_id else "系统"),
            "provider": row.provider,
            "model": row.model,
            "call_type": row.call_type,
            "module": module_key_from_call_type(row.call_type),
            "module_label": module_label(module_key_from_call_type(row.call_type)),
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "cache_hit_tokens": hit,
            "cache_miss_tokens": miss,
            "total_tokens": row.total_tokens,
            "cost_usd": round(usd, 6),
            "cost_cny": round(cny, 6),
            "duration_ms": row.duration_ms,
            "success": row.success == "true",
            "created_at": str(row.created_at) if row.created_at else None,
        })

    return {
        "period_days": days,
        "cny_usd_rate": CNY_USD_RATE,
        "billing_method": "DeepSeek 统一网关 · 按 API 返回的缓存 hit/miss token 分拆计价",
        "provider": "deepseek",
        "deepseek_official": get_deepseek_official_pricing(),
        "cache_summary": cache_summary,
        "summary": {
            "total_calls": total_calls,
            "failed_calls": failed_calls,
            "success_calls": max(total_calls - failed_calls, 0),
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "cost_usd": total_usd,
            "cost_cny": total_cny,
            "avg_cost_cny_per_call": round(total_cny / total_calls, 4) if total_calls else 0,
            **daily_stats,
        },
        "deepseek_summary": {
            "total_calls": sum(m["total_calls"] for m in ds_models),
            "cost_usd": round(sum(m["cost_usd"] for m in ds_models), 4),
            "cost_cny": round(sum(m["cost_cny"] for m in ds_models), 4),
            "models": ds_models,
        },
        "modules": modules,
        "traders": traders,
        "models": models,
        "call_types": call_types,
        "daily": daily_billing,
        "recent_calls": recent,
    }


def get_pricing_table() -> list:
    """Return the full pricing table for display in the frontend."""
    result = []
    for model, (inp, out) in PRICING_TABLE.items():
        provider = "unknown"
        m = model.lower()
        if any(k in m for k in ["gpt", "o1", "o3", "o4"]):
            provider = "OpenAI"
        elif "deepseek" in m:
            provider = "DeepSeek"
        elif any(k in m for k in ["qwen3", "qwen-", "qwq"]):
            provider = "Qwen (阿里云)"
        elif any(k in m for k in ["doubao"]):
            provider = "Volcengine (火山引擎)"
        elif any(k in m for k in ["kimi", "moonshot"]):
            provider = "Kimi (月之暗面)"
        elif "claude" in m:
            provider = "Anthropic"
        elif "gemini" in m:
            provider = "Google"
        elif "grok" in m:
            provider = "xAI"

        result.append({
            "model": model,
            "provider": provider,
            "input_per_1m_tokens": inp,
            "output_per_1m_tokens": out,
        })
    return result
