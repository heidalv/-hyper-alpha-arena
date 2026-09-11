"""
Unified LLM Configuration Service

Provides a centralized service for accessing LLM configurations across all modules.
Any module that needs to make LLM API calls should use this service.

Usage:
    from services.llm_config_service import get_llm_config, get_llm_config_for_account

    # Get the default configuration
    config = get_llm_config()
    
    # Get configuration for a specific account
    config = get_llm_config_for_account(account_id)
    
    # Call LLM with the configuration
    response = await call_llm_api(config, messages)
"""
import os
import sys
import asyncio
import logging
import threading
import time
from typing import Optional, Dict, Any, List, Callable, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone

# Add backend to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database.connection import SessionLocal
from backend.database.models import LLMConfiguration, Account

logger = logging.getLogger(__name__)

# TTL 缓存：避免每次 LLM 调用都查数据库
_config_cache: Dict[str, Any] = {}
_config_cache_lock = threading.Lock()
_CONFIG_CACHE_TTL = 300  # 5 分钟

_llm_semaphore: Optional[threading.Semaphore] = None
_llm_semaphore_lock = threading.Lock()

_LOW_PRIORITY_CALLER_MARKERS = (
    "rebate_arb.",
    "news_intel",
    "newsintel",
    "aifactor",
    "arb_llm",
    # 修复（2026-06-25）：以下辅助分析 caller 占满并发槽（3个），
    # 导致 SwingAgent/TrendAgent 等核心交易决策排队超时 → conf=0% → 中长线不开仓。
    # 将辅助分析标记为低优先级，保证交易决策优先获取槽位。
    "klineanalyst",       # K线分析（453次/天，最多）
    "kline_analyst",
    "whale_tracker",      # 鲸鱼追踪
    "whale_tracker_service",
    "strategy_hypothesis",  # 假设生成
    "ai_trade_journal",   # 交易日志分析
)


def _get_llm_semaphore() -> Optional[threading.Semaphore]:
    """获取全局 LLM 并发信号量。LLM_GLOBAL_MAX_CONCURRENT=0 时返回 None（不限制）。

    DeepSeek 官方并发: v4-pro=500, v4-flash=2500，系统日调用几千次远低于限制，
    不需要本地并发限制。原限制=3 导致辅助分析占满槽位，交易决策排队超时不开仓。
    """
    global _llm_semaphore
    with _llm_semaphore_lock:
        if _llm_semaphore is None:
            try:
                from backend.config.settings import LLM_GLOBAL_MAX_CONCURRENT
                n = int(LLM_GLOBAL_MAX_CONCURRENT)
            except Exception:
                n = 0
            if n <= 0:
                _llm_semaphore = None  # 不限制
                logger.info("[LLM sync] 并发槽未启用（LLM_GLOBAL_MAX_CONCURRENT=0），所有调用直接放行")
            else:
                _llm_semaphore = threading.Semaphore(n)
        return _llm_semaphore


def _is_low_priority_llm_caller(caller: Optional[str]) -> bool:
    c = (caller or "").lower()
    return any(m in c for m in _LOW_PRIORITY_CALLER_MARKERS)


def _acquire_llm_slot(*, caller: Optional[str]) -> bool:
    """获取全局 LLM 并发槽 + MidLong v2 分桶预算；无限制时直接返回 True。"""
    # Phase3：先占分桶（Master/MidLong/Scalp），避免总控与中长线同秒打满
    try:
        from backend.services.full_auto.llm_budget import acquire_llm_budget
        if not acquire_llm_budget(caller=caller):
            return False
    except Exception as _bud_err:
        logger.debug("[LLM sync] budget acquire skip: %s", _bud_err)

    sem = _get_llm_semaphore()
    if sem is None:
        return True  # 全局不限制，分桶已处理（或未启用）
    try:
        from backend.config.settings import LLM_SEMAPHORE_WAIT_SECONDS
        wait_s = float(LLM_SEMAPHORE_WAIT_SECONDS)
    except Exception:
        wait_s = 30.0
    if _is_low_priority_llm_caller(caller):
        # [fix] P1-1: 低优先级 caller 从 1s 放宽到 5s，避免 news_intel 全部超时
        # 高优先级仍用 wait_s (默认 30s)，保证交易决策优先获取槽位
        acquired = sem.acquire(timeout=min(wait_s, 5.0))
        if not acquired:
            logger.debug("[LLM sync] 跳过低优先级调用 caller=%s (并发已满, timeout=%.1fs)", caller, min(wait_s, 5.0))
            try:
                from backend.services.full_auto.llm_budget import release_llm_budget
                release_llm_budget()
            except Exception:
                pass
        return acquired
    acquired = sem.acquire(timeout=max(0.0, wait_s))
    if not acquired:
        try:
            from backend.services.full_auto.llm_budget import release_llm_budget
            release_llm_budget()
        except Exception:
            pass
    return acquired


@dataclass
class LLMConfig:
    """LLM configuration data class."""
    id: int
    name: str
    provider: str
    model: str
    base_url: str
    api_key: str
    is_default: bool = False
    model_deep: Optional[str] = None


def _resolve_tier_model(config: LLMConfiguration, tier: str = "quick") -> str:
    """Pick model name for quick (flash) vs deep (pro) tier.

    Callers pass tier based on task type — users do not pick Flash/Pro manually:
      quick → scalp, execution veto, lightweight classification
      deep  → strategy analysis, debate, OpenCode, master decisions
    """
    if tier == "deep":
        deep = getattr(config, "model_deep", None)
        if deep and str(deep).strip():
            return str(deep).strip()
    return config.model


def _config_to_dataclass(config: LLMConfiguration, tier: str = "quick") -> LLMConfig:
    """Convert ORM model to dataclass."""
    from backend.utils.encryption import decrypt_llm_key
    return LLMConfig(
        id=config.id,
        name=config.name,
        provider=config.provider,
        model=_resolve_tier_model(config, tier),
        base_url=config.base_url,
        api_key=decrypt_llm_key(config.api_key),
        is_default=config.is_default == "true",
        model_deep=getattr(config, "model_deep", None),
    )


def _forbid_shared_platform_llm() -> bool:
    try:
        from backend.config.settings import FORBID_SHARED_PLATFORM_LLM
        return bool(FORBID_SHARED_PLATFORM_LLM)
    except Exception:
        return True


def get_llm_config(
    config_id: Optional[int] = None,
    tier: str = "quick",
    *,
    tenant_id: Optional[int] = None,
    allow_shared: bool = False,
) -> Optional[LLMConfig]:
    """取 LLM 配置。

    多账户规则（FORBID_SHARED_PLATFORM_LLM=true，默认开启）：
      - **禁止**无租户的「全库星标默认 / 任意有 Key 配置」——那是公用 LLM。
      - 传入 ``tenant_id`` 时，只在该租户自己的配置里找默认或指定 id。
      - ``allow_shared=True`` 仅留给显式运维例外（一般不要用）。

    Args:
        config_id: 指定配置 ID（须属于 tenant_id，若提供了租户）。
        tier: "quick" / "deep"。
        tenant_id: 账户所属用户 id（= 租户）。
        allow_shared: 是否允许跨租户公用默认（默认否）。
    """
    if (
        config_id is None
        and tenant_id is None
        and not allow_shared
        and _forbid_shared_platform_llm()
    ):
        # [2026-09-11 观测] 加调用方定位：该警告每 30-60s 出现一次，此前无从
        # 得知是哪个系统级服务在裸调 get_llm_config()（疑似 rebate/whale/宏观
        # 等周期任务），无法评估其 LLM 特性静默降级面。只加日志，不改行为。
        _caller = "-"
        try:
            import traceback as _tb
            _fr = _tb.extract_stack()[-2]
            _caller = f"{_fr.filename.replace(chr(92), '/').split('/')[-1]}:{_fr.lineno}"
        except Exception:
            pass
        logger.warning(
            "[LLM] 拒绝公用默认配置：请为账户配置自有 LLM"
            "（get_llm_config 未传 tenant_id；caller=%s）",
            _caller,
        )
        return None

    cache_key = f"llm_config_{config_id or 'default'}_{tier}_t{tenant_id}_s{int(allow_shared)}"
    now = time.time()

    with _config_cache_lock:
        cached = _config_cache.get(cache_key)
        if cached and now - cached["ts"] < _CONFIG_CACHE_TTL:
            return cached["value"]

    db = SessionLocal()
    try:
        # [2026-08-04 修复] 同 get_llm_config_for_account：LLM 配置权威查询穿透 RLS，
        # 否则后台线程查不到本租户配置，导致 MasterController/TrendAgent/MLTO 规则回退。
        try:
            db.connection().exec_driver_sql("SET app.is_admin = 'on'")
        except Exception:
            pass
        if config_id:
            q = db.query(LLMConfiguration).filter(
                LLMConfiguration.id == config_id,
                LLMConfiguration.is_active == "true",
            )
            if tenant_id is not None:
                q = q.filter(LLMConfiguration.tenant_id == int(tenant_id))
            config = q.first()
        elif tenant_id is not None:
            config = (
                db.query(LLMConfiguration)
                .filter(
                    LLMConfiguration.tenant_id == int(tenant_id),
                    LLMConfiguration.is_default == "true",
                    LLMConfiguration.is_active == "true",
                )
                .first()
            )
            if not config:
                config = (
                    db.query(LLMConfiguration)
                    .filter(
                        LLMConfiguration.tenant_id == int(tenant_id),
                        LLMConfiguration.is_active == "true",
                        LLMConfiguration.api_key != "",
                        LLMConfiguration.api_key.isnot(None),
                    )
                    .first()
                )
        else:
            # 仅 allow_shared 或关闭禁止开关时才走历史「全库默认」
            config = db.query(LLMConfiguration).filter(
                LLMConfiguration.is_default == "true",
                LLMConfiguration.is_active == "true",
            ).first()
            if not config:
                config = db.query(LLMConfiguration).filter(
                    LLMConfiguration.is_active == "true",
                    LLMConfiguration.api_key != "",
                    LLMConfiguration.api_key.isnot(None),
                ).first()

        result = _config_to_dataclass(config, tier=tier) if config else None

        with _config_cache_lock:
            _config_cache[cache_key] = {"value": result, "ts": now}

        return result

    except Exception as e:
        logger.error(f"Failed to get LLM config: {e}")
        return None
    finally:
        db.close()


def get_llm_config_for_account(account_id: int, tier: str = "quick") -> Optional[LLMConfig]:
    """取**本账户租户**的 LLM。未配置则返回 None，绝不串用别人的 Key。

    账户可在「设置」绑定 llm_config_id / llm_config_id_deep；
    未绑定则用该用户自己的 is_default / 任意自有激活配置。
    """
    if not account_id:
        logger.warning("[LLM] get_llm_config_for_account 缺少 account_id，拒绝公用回退")
        return None

    cache_key = f"llm_config_account_{account_id}_{tier}"
    now = time.time()
    with _config_cache_lock:
        cached = _config_cache.get(cache_key)
        if cached and now - cached["ts"] < _CONFIG_CACHE_TTL:
            return cached["value"]

    db = SessionLocal()
    try:
        # [2026-08-04 修复] LLM 配置解析是权威查询，必须穿透 RLS：
        # 后台线程（APScheduler / ThreadPoolExecutor / QAA v3 裸线程）无 HTTP 租户上下文，
        # begin 钩子读不到 ContextVar 身份 → RLS fail-closed 隐藏 accounts 行 →
        # "无归属用户" → thesis LLM 走规则回退 → direction=neutral / conviction 归零。
        # 这里自建连接后直接对连接设 admin GUC（不动 ContextVar，避免污染调用线程）。
        # 查询仍按 account_id + tenant_id 严格过滤，不会串租户。
        try:
            db.connection().exec_driver_sql("SET app.is_admin = 'on'")
        except Exception:
            pass
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account or not getattr(account, "user_id", None):
            logger.warning("[LLM] account=%s 无归属用户，无法解析自有 LLM", account_id)
            return None

        tenant_id = int(account.user_id)
        config_id = (
            getattr(account, "llm_config_id_deep", None)
            if tier == "deep"
            else getattr(account, "llm_config_id", None)
        )
        config = None
        if config_id:
            config = (
                db.query(LLMConfiguration)
                .filter(
                    LLMConfiguration.id == config_id,
                    LLMConfiguration.is_active == "true",
                    LLMConfiguration.tenant_id == tenant_id,
                )
                .first()
            )
        result = _config_to_dataclass(config, tier=tier) if config else None
        if result is None:
            # [2026-08-23 本地LLM最大化] 分层语义：tier=quick（批量低延迟敏感：
            # KlineAnalyst/flash_veto/复盘等）→ 本地 14B 优先、云端兜底；
            # tier=deep（决策主链路）→ 保持租户默认云端（质量优先）。
            # 账户显式绑定（llm_config_id）优先级最高，不受此分层影响。
            if tier == "quick":
                try:
                    _qf_local, _qf_cloud = get_llm_config_local_first(
                        "trading", account_id=account_id, tier="quick",
                    )
                    result = _qf_local or _qf_cloud
                except Exception as _qf_err:
                    logger.debug("[LLM] quick 本地优先解析失败: %s", _qf_err)
                    result = None
            if result is None:
                result = get_llm_config(tier=tier, tenant_id=tenant_id)

        with _config_cache_lock:
            _config_cache[cache_key] = {"value": result, "ts": now}
        return result
    except Exception as e:
        logger.error(f"Failed to get LLM config for account {account_id}: {e}")
        return None
    finally:
        db.close()


def resolve_llm_for_legacy_account(
    account,
    usage: str = "legacy",
    tier: str = "quick",
) -> Optional[LLMConfig]:
    """[2026-08-23 M0-L4] 旧签名兼容垫片——2026-08-15 LLM 统一重构时函数被删
    但 5 个调用方未同步（ai_prompt_generation / ai_signal_generation /
    kline_ai_analysis / asset_snapshot / hyperliquid_snapshot），导致
    prompt 进化等链路 ImportError 死链。语义与旧实现一致（fail-closed）：
    按账户归属租户走统一解析，账户绑定优先、其次用途绑定，绝不回退旧字段/公用 Key。
    """
    if account is None:
        logger.warning("[LLM] resolve_llm_for_legacy_account: account=None，拒绝解析")
        return None
    aid = None
    tid = None
    try:
        aid = getattr(account, "id", None)
        tid = getattr(account, "user_id", None)
    except Exception:
        pass
    if not aid and not tid:
        logger.error("[LLM-AUTHORITY] legacy account 无 id/user_id，拒绝解析 LLM")
        return None
    if aid:
        cfg = get_llm_config_for_account(int(aid), tier=tier)
        if cfg is not None:
            return cfg
    if tid:
        cfg = get_llm_config_for_usage(usage, tenant_id=int(tid), tier=tier)
        if cfg is not None:
            return cfg
    logger.warning(
        "[LLM-AUTHORITY] legacy account=%s usage=%s tier=%s 统一解析失败（fail-closed）",
        aid, usage, tier,
    )
    return None


# 非交易用途注册表：供「后台指定」LLM 配置使用（设置 → LLM 配置 → 用途分配）。
# key 与 llm_configurations.usage_scope 中逗号分隔的值对应。
LLM_USAGE_REGISTRY = {
    "trading": ("交易决策", "短线/长线策略决策、执行复核等交易主链路（账户绑定优先）"),
    "coin_select": ("VIP共用AI选币", "平台看板选币（仅管理员租户 LLM；交易决策仍用各账户自备 Key）"),
    "factor_mining": ("因子挖掘", "AI 因子发现与生成"),
    "journal": ("交易日志/日报", "交易日志分析、每日复盘日报"),
    "assistant": ("Alpha 助手", "对话助手 / Hermes / OpenCode 侧车"),
    "kline_analysis": ("K线 AI 分析", "K线形态/趋势 AI 解读"),
    "evolution": ("学习进化", "策略进化、遗传优化、回测洞察"),
    "news_intel": ("新闻/情报辅助", "新闻情报、鲸鱼追踪等低优先级辅助分析"),
    # [U1-4/U1-2 2026-08-25] 补注册：scalp_confirm(深挖I) 与 thesis(影子) 的 84/85 绑定
    # 已存在于 DB usage_scope，仅补注册表条目（UI 用途分配列表可见）。
    "scalp_confirm": ("短线LLM确认层", "下单前本地LLM裁决（fail-open，84本地/85云端）"),
    "thesis": ("中长线thesis", "方向论题影子研判（U1-2，本地14B优先/云端兜底）"),
    # [v3 方向2 2026-09-03] ModelGateway 的第三票（DeepSeek 仲裁）与深度任务用途；
    # MiniMax / GLM 两条主传输不经此表（MiniMax 走 MINIMAX_API_KEY 直连，GLM 走 OpenCode sidecar）。
    "deep_analysis": ("深度分析(仲裁票)", "日度简报/周度复盘/择时的 DeepSeek 第三票与单模型兜底（ModelGateway）"),
    "event_impact": ("事件影响评估", "事件触发的方向/强度/半衰期评估（ModelGateway，≤20 次/日/模型）"),
    "param_search": ("参数寻优建议", "喂回放/回测结果，模型只提候选网格不直接改参（ModelGateway）"),
}


def get_llm_config_for_usage(
    usage: str,
    account_id: Optional[int] = None,
    tier: Optional[str] = None,
    *,
    tenant_id: Optional[int] = None,
    provider: Optional[str] = None,
    exclude_provider: Optional[str] = None,
) -> Optional[LLMConfig]:
    """按用途路由 LLM（**必须**落在某一租户，禁止公用默认）。

    优先级：
      1. 本租户内 usage_scope 绑定；
      2. 账户绑定（get_llm_config_for_account）；
      3. 本租户默认配置。
    无 account_id / tenant_id → 返回 None（不再回退全库星标）。

    [模型刀 2026-08-21] provider：精确过滤 provider（如 "ollama"）——只作用于
    第 1 级 scope 绑定查询，供「本地优先/云端降级」两段式解析使用；不传行为不变。
    """
    tier = tier or "quick"
    resolved_tenant = tenant_id
    if resolved_tenant is None and account_id is not None:
        db0 = SessionLocal()
        try:
            acc = db0.query(Account).filter(Account.id == account_id).first()
            if acc and getattr(acc, "user_id", None):
                resolved_tenant = int(acc.user_id)
        except Exception as e:
            logger.warning(f"resolve tenant for account {account_id}: {e}")
        finally:
            db0.close()

    if resolved_tenant is None and account_id is None:
        logger.warning(
            "[LLM] get_llm_config_for_usage(%s) 无账户/租户，拒绝公用 LLM", usage
        )
        return None

    if usage:
        db = SessionLocal()
        try:
            q = db.query(LLMConfiguration).filter(
                LLMConfiguration.is_active == "true",
                LLMConfiguration.usage_scope.isnot(None),
                LLMConfiguration.usage_scope != "",
                LLMConfiguration.usage_scope.like(f"%{usage}%"),
            )
            if resolved_tenant is not None:
                q = q.filter(LLMConfiguration.tenant_id == int(resolved_tenant))
            if provider:
                q = q.filter(LLMConfiguration.provider == str(provider).strip().lower())
            if exclude_provider:
                # [E22 2026-08-25] 本地优先的云端兜底查询排除 ollama：
                # 否则非默认 ollama 绑定(84)在云端查询同样排第一 → cloud==local → 降级失效
                q = q.filter(LLMConfiguration.provider != str(exclude_provider).strip().lower())
            # [2026-08-23 本地LLM最大化] 非默认专属绑定优先于默认兜底：
            # 旧排序 is_default desc 使 id=17(默认,scope=全部) 恒胜出，任何 usage 的
            # 专属本地绑定（如 factor_mining→id84 ollama）都被压死——实测 factor_mining
            # 24h 调用 100% 落 DeepSeek。语义修正：usage 匹配时「非默认专属绑定」意图
            # 强于「默认配置的宽 scope 兜底」。
            config = q.order_by(
                LLMConfiguration.is_default.asc(),
                LLMConfiguration.usage_count.desc(),
                LLMConfiguration.name,
            ).first()
            if config:
                return _config_to_dataclass(config, tier=tier)
        except Exception as e:
            logger.warning(f"Failed to resolve LLM config for usage {usage}: {e}")
        finally:
            db.close()

    if account_id is not None:
        cfg = get_llm_config_for_account(
            account_id, tier="deep" if usage in ("coin_select", "factor_mining", "journal") else tier
        )
        if cfg:
            return cfg

    if resolved_tenant is not None:
        return get_llm_config(tier=tier, tenant_id=int(resolved_tenant))
    return None


def _is_deep_analysis_model(model: str) -> bool:
    m = (model or "").lower()
    return any(x in m for x in ("pro", "reasoner", "thinking", "r1"))


def get_default_model_slug(
    tier: str = "quick",
    usage: Optional[str] = None,
    *,
    account_id: Optional[int] = None,
    tenant_id: Optional[int] = None,
) -> Optional[str]:
    """返回当前**租户**默认模型 slug；无账户/租户时不返回公用模型。"""
    cfg = get_llm_config_for_usage(
        usage or "assistant",
        account_id=account_id,
        tier=tier,
        tenant_id=tenant_id,
    )
    if not cfg:
        cfg = get_llm_config(tier=tier, tenant_id=tenant_id) if tenant_id else None
    if not cfg or not cfg.model:
        return None
    provider = (cfg.provider or "deepseek").strip().lower()
    if provider in ("volcengine", "volcano", "ark"):
        provider = "ark"
    return f"{provider}/{cfg.model.strip()}"


def is_reasoning_model(model: str) -> bool:
    """是否与 ai_decision_service 一致的推理/深度思考模型判定。"""
    m = (model or "").lower()
    return any(x in m for x in [
        "gpt-5", "o1-preview", "o1-mini", "o1-", "o3-", "o4-",
        "deepseek-r1", "deepseek-reasoner",
        # [P0-A] deepseek-v4 全系（v4-pro / v4-flash）都是深度思考模型：
        # 必须用 max_completion_tokens（v4 新 API 不再接受 max_tokens）、
        # 流式输出 reasoning_content 思维链。此前只匹配 v4-pro 而漏掉 v4-flash，
        # 导致 flash 走旧参数 → 返回空响应 → TrendAgent 规则回退 → 中长线长期 hold。
        "deepseek-v4",
        "qwq", "qwen-plus-thinking", "qwen-max-thinking", "qwen3-thinking",
        "claude-4", "claude-sonnet-4-5",
        "gemini-2.5", "gemini-3", "gemini-2.0-flash-thinking",
        "grok-3-mini",
    ])


def _messages_char_len(messages: Optional[List[Dict[str, str]]]) -> int:
    if not messages:
        return 0
    total = 0
    for msg in messages:
        try:
            total += len(str(msg.get("content") or ""))
        except Exception:
            continue
    return total


def should_use_llm_streaming(
    config: Optional["LLMConfig"],
    *,
    messages: Optional[List[Dict[str, str]]] = None,
    max_tokens: Optional[int] = None,
    caller: Optional[str] = None,
) -> bool:
    """是否强制走流式。

    规则：
      - Pro / reasoner / thinking 类深度推理模型必须流式；
      - prompt 很长、预期输出很长也必须流式；
      - report/audit/analysis/assistant/opencode 等长文/深思任务必须流式。
    """
    if not config:
        return False
    if os.getenv("LLM_DISABLE_STREAMING", "false").lower() in ("1", "true", "yes", "on"):
        return False

    force_stream = os.getenv("LLM_ANALYSIS_FORCE_STREAM", "true").lower() in (
        "1", "true", "yes", "on",
    )
    # [2026-08-23 35B MoE 适配] Ollama 一律非流式：/api/generate + think=False
    # 快速通道（流式端点强制思维链且无法关闭，实测 84.5s 超时）。
    if str(getattr(config, "provider", "") or "").lower() == "ollama":
        return False
    model_lower = (config.model or "").lower()
    if force_stream and is_reasoning_model(config.model):
        return True
    if any(x in model_lower for x in (
        "deepseek-reasoner", "deepseek-v4", "reasoner", "thinking", "r1",
    )):
        return True

    prompt_chars = _messages_char_len(messages)
    try:
        long_prompt_chars = int(os.getenv("LLM_STREAM_PROMPT_CHARS", "6000"))
        long_output_tokens = int(os.getenv("LLM_STREAM_MAX_TOKENS", "1800"))
    except Exception:
        long_prompt_chars = 6000
        long_output_tokens = 1800
    if prompt_chars >= long_prompt_chars:
        return True
    if max_tokens is not None and int(max_tokens or 0) >= long_output_tokens:
        return True

    caller_l = (caller or "").lower()
    long_task_markers = (
        "report", "audit", "analysis", "assistant", "opencode", "hermes",
        "journal", "hypothesis", "strategy", "evolution", "deep", "long",
        "narrative", "optimizer",
    )
    if any(marker in caller_l for marker in long_task_markers):
        return True

    return is_reasoning_model(config.model) and os.getenv(
        "LLM_REASONING_USE_STREAM", "true"
    ).lower() in ("1", "true", "yes", "on")


def build_stream_progress_observer(caller: str) -> Callable[[Dict[str, Any]], None]:
    """将流式 chunk 进度写入日志，便于区分「还在思考」vs「真超时」。"""

    def _observe(evt: Dict[str, Any]) -> None:
        event = str(evt.get("event") or "")
        if event in (
            "stream_start",
            "stream_first_chunk",
            "stream_progress",
            "stream_done",
            "stream_safety_cap",
            "stream_empty",
            "stream_error",
            "llm_stream_request",
            "llm_stream_http_error",
        ):
            extra = {k: v for k, v in evt.items() if k != "event"}
            logger.info("[%s] LLM流式 %s %s", caller, event, extra)

    return _observe


def build_httpx_timeout(
    config: Optional["LLMConfig"],
    timeout_override: Optional[float] = None,
    *,
    use_streaming: bool = False,
):
    """流式：read=None 等到 [DONE]；非流式：用 resolve_llm_call_timeout。"""
    import httpx
    if use_streaming:
        try:
            from backend.config.settings import LLM_STREAM_SAFETY_CAP_SECONDS
            cap = float(LLM_STREAM_SAFETY_CAP_SECONDS or 0)
        except Exception:
            cap = float(os.getenv("LLM_STREAM_SAFETY_CAP_SECONDS", "120") or "0")
        read_t = cap if cap > 0 else None
        return httpx.Timeout(connect=15.0, read=read_t, write=120.0, pool=30.0)
    t = resolve_llm_call_timeout(config, timeout_override)
    return httpx.Timeout(t)


def resolve_llm_call_timeout(
    config: Optional["LLMConfig"] = None,
    timeout_override: Optional[float] = None,
) -> float:
    """单次 LLM 调用超时（仅非流式）。流式由 [DONE] 决定结束。"""
    if timeout_override is not None and timeout_override > 0:
        return float(timeout_override)
    try:
        from backend.config.settings import (
            LLM_CALL_TIMEOUT_SECONDS,
            LLM_CALL_TIMEOUT_DEEP_SECONDS,
        )
        base = float(LLM_CALL_TIMEOUT_SECONDS)
        deep_base = float(LLM_CALL_TIMEOUT_DEEP_SECONDS)
    except Exception:
        base = 90.0
        deep_base = 240.0
    if config and is_reasoning_model(config.model):
        return max(deep_base, base * 2, 180.0)
    return base


def _parse_sse_chat_completion(
    response,
    progress_observer: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Optional[Dict[str, Any]]:
    """解析 deepseek-reasoner 等模型的 SSE 流式响应。"""
    import json as _json
    import time as _time
    full_content = ""
    reasoning_content = ""
    chunk_count = 0
    usage_info: Optional[Dict[str, Any]] = None  # [2026-08-04 修复] 收集流式 usage
    started_at = _time.time()
    last_progress_at = started_at
    safety_cap_triggered = False  # P0 修复：标记是否被安全阀截断
    try:
        from backend.config.settings import LLM_STREAM_SAFETY_CAP_SECONDS
        safety_cap = float(LLM_STREAM_SAFETY_CAP_SECONDS or 0)
    except Exception:
        safety_cap = float(os.getenv("LLM_STREAM_SAFETY_CAP_SECONDS", "120") or "0")

    def _emit(event: str, **payload: Any) -> None:
        if progress_observer:
            try:
                progress_observer({"event": event, **payload})
            except Exception:
                pass

    try:
        _emit("stream_start")
        for line in response.iter_lines():
            if not line:
                continue
            line_str = (line.decode("utf-8") if isinstance(line, bytes) else str(line)).strip()
            if line_str.startswith("\ufeff"):
                line_str = line_str[1:]  # 去 BOM（部分网关在首行携带）
            # [P0-A] 兼容非 SSE 网关：整行是不带 `data: ` 前缀的合法 JSON 且含 choices
            # （某些 OpenAI 兼容聚合层对 stream=true 仍一次性返回完整 JSON）
            if not line_str.startswith("data: "):
                try:
                    _cand = _json.loads(line_str)
                    if isinstance(_cand, dict) and _cand.get("choices"):
                        _c_msg = ((_cand.get("choices") or [{}])[0].get("message") or {})
                        logger.info(
                            "[LLM sync stream] 收到非 SSE 一次性 JSON 响应（choices=%s）",
                            len(_cand.get("choices") or []),
                        )
                        _emit("stream_done", chunks=1, elapsed_seconds=0.0)
                        return {
                            "choices": [{
                                "message": {
                                    "content": _c_msg.get("content") or "",
                                    "reasoning_content": _c_msg.get("reasoning_content")
                                    or _c_msg.get("reasoning") or "",
                                },
                                "finish_reason": "stop",
                            }],
                            "usage": _cand.get("usage"),
                        }
                except Exception:
                    pass
                continue
            json_str = line_str[6:]
            if json_str.strip() == "[DONE]":
                elapsed = _time.time() - started_at
                logger.info(
                    "[LLM sync stream] 收到 [DONE], chunks=%s, elapsed=%.1fs",
                    chunk_count,
                    elapsed,
                )
                _emit("stream_done", chunks=chunk_count, elapsed_seconds=round(elapsed, 1))
                break
            try:
                data = _json.loads(json_str)
            except _json.JSONDecodeError:
                continue
            chunk_count += 1
            choices = data.get("choices") or []
            # [2026-08-04 修复] DeepSeek 流式响应在末尾 chunk 携带 usage 字段
            # （OpenAI 兼容流式：usage 随最后一个 data 分片下发）。不收集则
            # call_llm_api_sync 里 resp_data.get("usage") 恒为空 → llm_usage_logs
            # 永不写流式调用的用量（08-04 08:42 后日志停更的直接根因）。
            if isinstance(data, dict) and data.get("usage"):
                usage_info = data["usage"]
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            # [P0-A] 兼容嵌套结构：部分网关把内容放在 delta.message.content
            if not isinstance(delta, dict):
                delta = {}
            _delta_msg = delta.get("message")
            if not isinstance(_delta_msg, dict):
                _delta_msg = {}
            _chunk_content = delta.get("content")
            if _chunk_content is None:
                _chunk_content = _delta_msg.get("content")
            full_content += _chunk_content or ""
            # [2026-08-23 本地LLM最大化] Ollama qwen3 流式思维链字段名是
            # delta.reasoning（DeepSeek 是 delta.reasoning_content）——两字段都取。
            reasoning_content += delta.get("reasoning_content") or delta.get("reasoning") or ""
            # [P0-A] 首个 chunk 无内容时记录形状，便于定位网关/模型字段差异
            if chunk_count == 1 and not _chunk_content and not delta.get("reasoning_content"):
                logger.debug(
                    "[LLM sync stream] 首个 chunk 无 content: delta_keys=%s delta=%s",
                    list(delta.keys()) if isinstance(delta, dict) else "?",
                    str(delta)[:200],
                )
            elapsed_now = _time.time() - started_at
            if safety_cap > 0 and elapsed_now >= safety_cap:
                safety_cap_triggered = True  # P0 修复：标记被截断
                logger.warning(
                    "[LLM sync stream] safety cap reached: %.1fs, chunks=%s, content=%s, reasoning=%s",
                    elapsed_now,
                    chunk_count,
                    len(full_content),
                    len(reasoning_content),
                )
                _emit(
                    "stream_safety_cap",
                    chunks=chunk_count,
                    content_chars=len(full_content),
                    reasoning_chars=len(reasoning_content),
                    elapsed_seconds=round(elapsed_now, 1),
                )
                break
            if chunk_count == 1:
                _emit("stream_first_chunk", chunks=chunk_count)
                logger.info("[LLM sync stream] 首个 chunk 已收到")
            now = _time.time()
            if chunk_count % 25 == 0 or now - last_progress_at >= 10:
                last_progress_at = now
                elapsed = now - started_at
                logger.info(
                    "[LLM sync stream] progress chunks=%s content=%s reasoning=%s elapsed=%.1fs",
                    chunk_count,
                    len(full_content),
                    len(reasoning_content),
                    elapsed,
                )
                _emit(
                    "stream_progress",
                    chunks=chunk_count,
                    content_chars=len(full_content),
                    reasoning_chars=len(reasoning_content),
                    elapsed_seconds=round(elapsed, 1),
                )
        if not full_content and not reasoning_content:
            if chunk_count > 0:
                logger.warning(
                    "[LLM sync stream] chunk_count=%s 但 content/reasoning 均为空 — 流式通道未输出任何内容",
                    chunk_count,
                )
            _emit("stream_empty", chunks=chunk_count)
            return None
        # [fix] Ark/deepseek reasoning 模式：答案输出在 reasoning_content，
        # 正式 content 为空。下游一律读 content，导致决策拿不到结论、
        # AI 策略"看起来停止"、循环空转。这里做回退：content 为空时，
        # 用 reasoning_content 作为 content 返回（思维链里含实际分析结论）。
        #
        # P0 修复（2026-07-20）：safety cap 触发时 reasoning 是不完整的思考过程
        # （被中途截断），里面不会有合法 JSON。此时把 reasoning 当正文返回会让
        # MasterController 的 JSON 解析器必然失败，触发 8 次 CRITICAL 降级。
        # 改为：safety cap 触发且 content=0 时直接返回 None，让下游走 fallback。
        effective_content = full_content
        if not full_content and reasoning_content and not safety_cap_triggered:
            effective_content = reasoning_content
            logger.info(
                f"[LLM sync stream] content 为空，回退用 reasoning ({len(reasoning_content)} chars) 作为正文"
            )
        elif not full_content and reasoning_content and safety_cap_triggered:
            # safety cap 截断且 content 为空：reasoning 不完整，不能当 JSON 解析
            logger.warning(
                f"[LLM sync stream] safety cap 截断且 content 为空，reasoning ({len(reasoning_content)} chars) "
                f"不完整，返回 None 让下游 fallback"
            )
            return None
        logger.info(
            f"[LLM sync stream] chunks={chunk_count}, "
            f"content={len(full_content)} chars, reasoning={len(reasoning_content)} chars"
        )
        return {
            "choices": [{
                "message": {
                    "content": effective_content,
                    "reasoning_content": reasoning_content,
                },
                "finish_reason": "stop",
            }],
            "usage": usage_info,
        }
    except Exception as e:
        err_text = str(e).lower()
        transient = (
            "peer closed connection" in err_text
            or "incomplete chunked read" in err_text
            or "connection reset" in err_text
            or "connection aborted" in err_text
            or "timed out" in err_text
            or "remote disconnected" in err_text
        )
        if transient:
            logger.warning(f"[LLM sync stream] connection interrupted: {e}")
        else:
            logger.warning(f"[LLM sync stream] parse failed: {e}")
        _emit("stream_error", error=str(e))
        return None


async def _parse_sse_chat_completion_async(
    response,
    progress_observer: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Optional[Dict[str, Any]]:
    """Async SSE parser for OpenAI-compatible chat completions."""
    import json as _json
    import time as _time
    full_content = ""
    reasoning_content = ""
    chunk_count = 0
    usage_info: Optional[Dict[str, Any]] = None  # [2026-08-04 修复] 收集流式 usage
    started_at = _time.time()
    last_progress_at = started_at

    try:
        from backend.config.settings import LLM_STREAM_SAFETY_CAP_SECONDS
        safety_cap = float(LLM_STREAM_SAFETY_CAP_SECONDS or 0)
    except Exception:
        safety_cap = float(os.getenv("LLM_STREAM_SAFETY_CAP_SECONDS", "120") or "0")

    def _emit(event: str, **payload: Any) -> None:
        if progress_observer:
            try:
                progress_observer({"event": event, **payload})
            except Exception:
                pass

    try:
        _emit("stream_start")
        async for line in response.aiter_lines():
            if not line:
                continue
            line_str = (line if isinstance(line, str) else line.decode("utf-8", errors="replace")).strip()
            if line_str.startswith("\ufeff"):
                line_str = line_str[1:]  # 去 BOM
            # [P0-A] 兼容非 SSE 网关：整行是不带 `data: ` 前缀的合法 JSON 且含 choices
            if not line_str.startswith("data: "):
                try:
                    _cand = _json.loads(line_str)
                    if isinstance(_cand, dict) and _cand.get("choices"):
                        _c_msg = ((_cand.get("choices") or [{}])[0].get("message") or {})
                        logger.info(
                            "[LLM async stream] 收到非 SSE 一次性 JSON 响应（choices=%s）",
                            len(_cand.get("choices") or []),
                        )
                        _emit("stream_done", chunks=1, elapsed_seconds=0.0)
                        return {
                            "choices": [{
                                "message": {
                                    "content": _c_msg.get("content") or "",
                                    "reasoning_content": _c_msg.get("reasoning_content")
                                    or _c_msg.get("reasoning") or "",
                                },
                                "finish_reason": "stop",
                            }],
                            "usage": _cand.get("usage"),
                        }
                except Exception:
                    pass
                continue
            json_str = line_str[6:]
            if json_str.strip() == "[DONE]":
                elapsed = _time.time() - started_at
                _emit("stream_done", chunks=chunk_count, elapsed_seconds=round(elapsed, 1))
                break
            try:
                data = _json.loads(json_str)
            except _json.JSONDecodeError:
                continue
            chunk_count += 1
            choices = data.get("choices") or []
            # [2026-08-04 修复] 同 sync 解析器：收集流式末尾 chunk 的 usage 字段，
            # 保证 async 路径的 llm_usage_logs 也能落库。
            if isinstance(data, dict) and data.get("usage"):
                usage_info = data["usage"]
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            # [P0-A] 兼容嵌套结构：部分网关把内容放在 delta.message.content
            if not isinstance(delta, dict):
                delta = {}
            _delta_msg = delta.get("message")
            if not isinstance(_delta_msg, dict):
                _delta_msg = {}
            _chunk_content = delta.get("content")
            if _chunk_content is None:
                _chunk_content = _delta_msg.get("content")
            full_content += _chunk_content or ""
            # [2026-08-23 本地LLM最大化] Ollama qwen3 流式思维链字段名是
            # delta.reasoning（DeepSeek 是 delta.reasoning_content）——两字段都取。
            reasoning_content += delta.get("reasoning_content") or delta.get("reasoning") or ""

            elapsed_now = _time.time() - started_at
            if safety_cap > 0 and elapsed_now >= safety_cap:
                _emit(
                    "stream_safety_cap",
                    chunks=chunk_count,
                    content_chars=len(full_content),
                    reasoning_chars=len(reasoning_content),
                    elapsed_seconds=round(elapsed_now, 1),
                )
                break
            now = _time.time()
            if chunk_count == 1:
                _emit("stream_first_chunk", chunks=chunk_count)
            if chunk_count % 25 == 0 or now - last_progress_at >= 10:
                last_progress_at = now
                _emit(
                    "stream_progress",
                    chunks=chunk_count,
                    content_chars=len(full_content),
                    reasoning_chars=len(reasoning_content),
                    elapsed_seconds=round(now - started_at, 1),
                )
        effective_content = full_content or reasoning_content
        if not effective_content:
            if chunk_count > 0:
                logger.warning(
                    "[LLM async stream] chunk_count=%s 但 content/reasoning 均为空 — 流式通道未输出任何内容",
                    chunk_count,
                )
            _emit("stream_empty", chunks=chunk_count)
            return None
        return {
            "choices": [{
                "message": {
                    "content": effective_content,
                    "reasoning_content": reasoning_content,
                },
                "finish_reason": "stop",
            }],
            "usage": usage_info,
        }
    except Exception as e:
        _emit("stream_error", error=str(e))
        logger.warning("[LLM async stream] parse failed: %s", e)
        return None


def get_llm_config_for_analysis(
    account_id: Optional[int] = None, tier: str = "deep"
) -> Optional[LLMConfig]:
    """策略分析 / Master：必须账户自有配置，无则 None（禁止公用）。

    v6 12.3 L1：KlineAnalyst 等批量低优先级调用传 tier="quick"（Flash），
    Master 综合决策保持默认 deep（Pro）。
    """
    if not account_id:
        logger.warning("[LLM] get_llm_config_for_analysis 无 account_id")
        return None
    return get_llm_config_for_account(account_id, tier=tier)


def get_llm_config_local_first(
    usage: str,
    account_id: Optional[int] = None,
    tier: str = "quick",
    *,
    tenant_id: Optional[int] = None,
) -> Tuple[Optional[LLMConfig], Optional[LLMConfig]]:
    """[2026-08-23 本地LLM最大化] 两段式解析：本地 Ollama 优先 + 云端降级。

    Returns:
        (primary, fallback)：primary = 本地 ollama 配置（无则 None）；
        fallback = 同 usage 的云端配置/租户默认（与 primary 不同才返回）。

    用法（调用点零成本切换）：
        local, cloud = get_llm_config_local_first("kline_analysis", account_id=aid)
        cfg = local or cloud                     # 或传 cloud 作 call_llm_api 的
        # fallback_config=cloud 实现「本地失败自动云端重试」
    """
    resolved_tenant = tenant_id
    if resolved_tenant is None and account_id is not None:
        db0 = SessionLocal()
        try:
            acc = db0.query(Account).filter(Account.id == account_id).first()
            if acc and getattr(acc, "user_id", None):
                resolved_tenant = int(acc.user_id)
        except Exception as e:
            logger.warning(f"resolve tenant for account {account_id}: {e}")
        finally:
            db0.close()
    if resolved_tenant is None:
        # [2026-08-23 修复] 后台线程（KlineAnalyst 在 _account_id 赋值前的预热、
        # 各类调度任务）无账户上下文 → 管理员租户兜底（与 journal/factor_mining
        # 同款模式），否则 usage 解析拒绝 → 本地 LLM 断供。
        try:
            from backend.services.coin_select_platform_service import resolve_admin_tenant_id
            resolved_tenant = resolve_admin_tenant_id()
        except Exception:
            resolved_tenant = None

    local: Optional[LLMConfig] = None
    try:
        local = get_llm_config_for_usage(
            usage, account_id=None, tier=tier,
            tenant_id=resolved_tenant, provider="ollama",
        )
    except Exception as e:
        logger.debug("[LLM] local_first ollama 解析失败: %s", e)

    cloud: Optional[LLMConfig] = None
    try:
        cloud = get_llm_config_for_usage(
            usage, account_id=None, tier=tier, tenant_id=resolved_tenant,
            exclude_provider="ollama",
        )
    except Exception as e:
        logger.debug("[LLM] local_first cloud 解析失败: %s", e)

    # 本地不可用（未绑定/无 key）→ 直接云端单段
    if local is None or not getattr(local, "api_key", None):
        return None, cloud
    # primary==fallback（usage 只绑了 ollama 且无云端）→ 无降级
    if cloud is not None and getattr(cloud, "id", None) == getattr(local, "id", None):
        return local, None
    return local, cloud


def get_all_active_configs() -> List[LLMConfig]:
    """
    Get all active LLM configurations.
    
    Returns:
        List of LLMConfig objects.
    """
    db = SessionLocal()
    try:
        configs = db.query(LLMConfiguration).filter(
            LLMConfiguration.is_active == "true",
            LLMConfiguration.api_key != "",
            LLMConfiguration.api_key.isnot(None)
        ).order_by(
            LLMConfiguration.is_default.desc(),
            LLMConfiguration.usage_count.desc()
        ).all()
        
        return [_config_to_dataclass(c) for c in configs]
        
    except Exception as e:
        logger.error(f"Failed to get all active LLM configs: {e}")
        return []
    finally:
        db.close()


def increment_usage(config_id: int) -> bool:
    """
    Increment the usage counter for a configuration.
    
    Args:
        config_id: The configuration ID.
    
    Returns:
        True if successful, False otherwise.
    """
    if config_id <= 0:
        return True  # Skip for legacy configs
    
    for attempt in range(3):
        db = SessionLocal()
        try:
            config = db.query(LLMConfiguration).filter(
                LLMConfiguration.id == config_id
            ).first()

            if config:
                config.usage_count = (config.usage_count or 0) + 1
                config.last_used_at = datetime.now(timezone.utc)
                db.commit()
                return True
            return False

        except Exception as e:
            db.rollback()
            if attempt < 2 and "database is locked" in str(e):
                import time as _t
                _t.sleep(0.5 * (attempt + 1))
                continue
            logger.warning(f"Failed to increment usage for config {config_id} (attempt {attempt+1}): {e}")
            return False
        finally:
            db.close()
    return False


_httpx_clients: Dict[str, Any] = {}
_httpx_clients_lock = threading.Lock()
_httpx_sync_clients: Dict[str, Any] = {}
_httpx_sync_clients_lock = threading.Lock()

_MAX_HTTPX_CLIENTS = 20  # 防止无限增长


def _safe_cache_key(base_url: str, api_key: str) -> str:
    """用 api_key 的 SHA256 前 16 位替代明文前 8 位，避免日志泄露。"""
    import hashlib
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    return f"{base_url}_{key_hash}"


def _evict_oldest_client(clients: Dict[str, Any], max_size: int) -> None:
    """当客户端缓存超过 max_size 时，关闭并移除最早的条目。

    兼容两种缓存值形态：
    - 裸 client（sync clients：_httpx_sync_clients）—— 调同步 close()
    - (client, loop_id) 元组（async clients：_httpx_clients，loop 感知后）——
      AsyncClient 只有 async aclose()，同步上下文无法优雅关闭，仅从缓存移除
      交由 GC 回收（httpx 连接池 __del__ 会清理）。
    """
    while len(clients) > max_size:
        oldest_key = next(iter(clients))
        old = clients.pop(oldest_key, None)
        client = old[0] if isinstance(old, tuple) else old
        if client is None or client.is_closed:
            continue
        try:
            # sync httpx.Client 有同步 close()；AsyncClient 没有则跳过（GC 回收）
            if hasattr(client, "close") and callable(getattr(client, "close")):
                client.close()
        except Exception:
            pass


def _resolve_llm_proxy() -> Optional[str]:
    """LLM 出网代理。

    2026-08-04 [P0-A 补强]：**不再继承行情代理**。
    此前会回退读 HTTPS_PROXY/HTTP_PROXY/MARKET_DATA_HTTP_PROXY/BINANCE_*，
    而这些通常是本地 Shadowsocks（127.0.0.1:1080）。该代理对 DeepSeek 的
    SSE 流式长连接不稳定 → `SSL: UNEXPECTED_EOF_WHILE_READING`（约 30s 中断），
    造成 LLM 流式调用空响应 → TrendAgent 规则回退 → 中长线长期 hold/零开仓。

    实测：直连 api.deepseek.com 共享 keepalive 池 10/10 成功、平均 2~3s；
    走 127.0.0.1:1080 代理则频繁 SSL EOF。故 LLM 默认**直连**，
    仅当显式配置 LLM_HTTP_PROXY / LLM_HTTPS_PROXY 时才走代理。
    """
    from backend.config.settings import LLM_HTTP_PROXY, LLM_HTTPS_PROXY
    for key in ("LLM_HTTPS_PROXY", "LLM_HTTP_PROXY"):
        val = (LLM_HTTPS_PROXY if key == "LLM_HTTPS_PROXY" else LLM_HTTP_PROXY) or ""
        if val:
            return val
    return None


def _get_httpx_client(base_url: str, api_key: str):
    """复用 httpx.AsyncClient 连接池，避免每次调用都建立新连接。

    2026-06-17: 修复 "Event loop is closed"（日志 6 次）。
    根因：AsyncClient 绑定创建时的 event loop，被全局缓存复用。当后台 task
    （_run_news_fetch / _run_daily_journal 等 APScheduler 线程）用
    ``new_event_loop() → run_until_complete → loop.close()`` 三段式后，
    缓存的 AsyncClient 仍指向已关闭的 loop，下次复用即报 "Event loop is closed"。

    修复：缓存按 (cache_key, loop_id) 索引。取用时检查当前 running loop id，
    与缓存不符（loop 已关/换了）则关闭旧 client 重建，绑定当前 loop。
    """
    import httpx
    cache_key = _safe_cache_key(base_url, api_key)

    # 拿当前 running loop 的身份标识（仅在协程内有效；同步上下文返回 None）
    try:
        current_loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        # 没有 running loop（同步线程直接调 async client）—— 不应发生，
        # 但兜底：用 0 占位，让缓存按 loop_id 区分，避免误用旧 loop 的 client。
        current_loop_id = 0

    with _httpx_clients_lock:
        entry = _httpx_clients.get(cache_key)
        if entry is not None:
            client, bound_loop_id = entry
            if not client.is_closed and bound_loop_id == current_loop_id:
                return client
            # client 已关闭 或 绑定的 loop 已换 —— 从缓存摘除。
            # 注意：AsyncClient 只有 async 的 aclose()，同步锁里不能 await；
            # 这里仅从缓存移除，让旧 client 随 GC 回收（httpx 连接池在 __del__ 清理）。
            # 不调 client.close()（AsyncClient 无此同步方法，调了会抛 AttributeError 被吞）。
            _httpx_clients.pop(cache_key, None)
        proxy = _resolve_llm_proxy()
        client_kwargs = {
            "timeout": 120.0,
            "limits": httpx.Limits(max_connections=10, max_keepalive_connections=5),
            # [P0-A 补强] 不信任进程环境变量代理（.env 的 HTTPS_PROXY=127.0.0.1:1080
            # Shadowsocks 对 SSE 长连接不稳定）。LLM 默认直连；代理仅经
            # LLM_HTTP_PROXY/LLM_HTTPS_PROXY 显式注入。
            "trust_env": False,
        }
        if proxy:
            client_kwargs["proxy"] = proxy
        client = httpx.AsyncClient(**client_kwargs)
        _httpx_clients[cache_key] = (client, current_loop_id)
        _evict_oldest_client(_httpx_clients, _MAX_HTTPX_CLIENTS)
        return client


def _get_httpx_sync_client(base_url: str, api_key: str):
    """复用 httpx.Client 同步连接池，供 APScheduler 线程等同步上下文使用"""
    import httpx
    cache_key = _safe_cache_key(base_url, api_key)
    with _httpx_sync_clients_lock:
        entry = _httpx_sync_clients.get(cache_key)
        if entry and not entry.is_closed:
            return entry
        proxy = _resolve_llm_proxy()
        client_kwargs = {
            "timeout": httpx.Timeout(connect=15.0, read=600.0, write=60.0, pool=30.0),
            "limits": httpx.Limits(max_connections=10, max_keepalive_connections=5),
            # [P0-A 补强] 同 async client：不读环境代理，LLM 默认直连，
            # 避免本地 Shadowsocks 代理对 SSE 长连接造成 SSL EOF。
            "trust_env": False,
        }
        if proxy:
            client_kwargs["proxy"] = proxy
        client = httpx.Client(**client_kwargs)
        _httpx_sync_clients[cache_key] = client
        return client


# 异步用量记录队列（避免在 LLM 热路径上阻塞数据库写入）
_usage_queue: List[Dict] = []
_usage_queue_lock = threading.Lock()
_MAX_USAGE_QUEUE = 500  # 防止 flush 持续失败导致队列无限增长


def _flush_usage_queue():
    """批量写入用量记录"""
    with _usage_queue_lock:
        if not _usage_queue:
            return
        batch = list(_usage_queue)
        _usage_queue.clear()

    try:
        from backend.services.llm_usage_service import record_usage
        from backend.database.connection import AnalyticsSessionLocal
        db = AnalyticsSessionLocal()
        try:
            # [2026-08-04 修复] llm_usage_logs 表有 RLS；flush 在独立线程执行，
            # 无 HTTP 租户上下文 → WITH CHECK 拒绝 INSERT → 用量记录停更。
            # 对自建连接显式设 admin GUC 穿透（不动 ContextVar，避免污染调用线程）。
            try:
                db.connection().exec_driver_sql("SET app.is_admin = 'on'")
            except Exception:
                pass
            for item in batch:
                record_usage(db, **item)
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"Flush usage queue failed: {e}")


def _enqueue_usage_record(
    config: LLMConfig,
    usage_info: Optional[Dict[str, Any]],
    call_type: str,
    *,
    account_id: Optional[int] = None,
    duration_ms: Optional[int] = None,
    success: bool = True,
) -> None:
    """统一写入用量队列（DeepSeek 缓存 hit/miss + 模块 call_type）。"""
    # [2026-08-23 修复] 原 `if not usage_info: return` 把 Ollama 空 usage 保底
    # 记录（usage_info={}）也拒掉 → 本地调用永远不落 llm_usage_logs（不可观测）。
    # 空 dict 放行：parse_deepseek_usage 对空 dict 解析为 0 tokens，记录仍有效。
    if usage_info is None:
        return
    try:
        from backend.services.llm_usage_service import build_usage_record_kwargs
        payload = build_usage_record_kwargs(
            usage_info=usage_info,
            provider=config.provider or "deepseek",
            model=config.model or "unknown",
            base_url=config.base_url or "",
            account_id=account_id,
            llm_config_id=config.id if config.id > 0 else None,
            call_type=call_type,
            duration_ms=duration_ms,
            success=success,
        )
        with _usage_queue_lock:
            if len(_usage_queue) >= _MAX_USAGE_QUEUE:
                _usage_queue[:] = _usage_queue[-_MAX_USAGE_QUEUE // 2:]
            _usage_queue.append(payload)
        if len(_usage_queue) >= 5:
            threading.Thread(target=_flush_usage_queue, daemon=True).start()
    except Exception as e:
        logger.debug("Enqueue usage record failed: %s", e)


async def call_llm_api(
    config: LLMConfig,
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2000,
    stream: bool = False,
    response_format: Optional[Dict[str, Any]] = None,
    *,
    caller: Optional[str] = None,
    account_id: Optional[int] = None,
    fallback_config: Optional[LLMConfig] = None,
) -> Optional[Dict[str, Any]]:
    """
    Call the LLM API with the given configuration.
    复用 httpx 连接池，异步记录用量，减少 I/O 开销。

    [2026-08-23 本地LLM最大化] fallback_config：主配置失败自动降级云端重试一次。
    """
    if not config:
        logger.error("No LLM configuration provided")
        return None

    async def _fallback(reason: str) -> Optional[Dict[str, Any]]:
        if fallback_config is None:
            return None
        _fb_caller = caller or _detect_caller_module()
        logger.warning(
            "[LLM async] 主配置(%s %s)失败(%s) → 降级云端 %s 重试 caller=%s",
            getattr(config, "provider", "?"), getattr(config, "model", "?"),
            reason, getattr(fallback_config, "model", "?"), _fb_caller,
        )
        try:
            return await call_llm_api(
                fallback_config, messages, temperature, max_tokens, stream,
                response_format,
                caller=caller, account_id=account_id, fallback_config=None,
            )
        except Exception as _fb_err:
            logger.warning("[LLM async] 云端降级重试异常: %s", _fb_err)
            return None
    
    try:
        base_url = config.base_url.rstrip('/')
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}"
        }
        
        _resolved_caller = caller or _detect_caller_module()
        # [2026-08-23 35B MoE 适配] Ollama 走 /api/generate 快速通道（think=False，
        # 实测 63 tok/s）；失败自动降级 fallback_config（云端）。
        if str(getattr(config, "provider", "") or "").lower() == "ollama":
            # [2026-08-28 关键修复] Ollama 本地推理 30-120s：同步 urllib 直接调用会阻塞
            # 整个事件循环（AI 选币平台扫描 5min/轮 → 期间全站请求排队）；改线程池。
            import asyncio as _ai
            _ollama_resp = await _ai.to_thread(
                _call_ollama_generate,
                config, messages, temperature, max_tokens, response_format,
                caller=_resolved_caller, account_id=account_id,
            )
            if _ollama_resp is not None:
                return _ollama_resp
            return await _fallback("ollama_generate_failed")
        is_reasoning = is_reasoning_model(config.model)
        use_streaming = bool(stream) or should_use_llm_streaming(
            config,
            messages=messages,
            max_tokens=max_tokens,
            caller=_resolved_caller,
        )
        
        payload = {
            "model": config.model,
            "messages": messages,
            "stream": use_streaming,
        }

        # [2026-08-23 本地LLM最大化] qwen3 GGUF 强制思维链且 OpenAI 兼容端点不接受
        # /no_think 后缀（400 invalid model name）；改为 max_tokens 自动放大 +
        # reasoning 归一化兜底（见 _effective_max_tokens/_normalize_ollama_response）。

        if is_reasoning:
            payload["max_completion_tokens"] = _effective_max_tokens(config, max_tokens)
        else:
            payload["temperature"] = temperature
            payload["max_tokens"] = _effective_max_tokens(config, max_tokens)

        # DeepSeek V4：按 caller 分层注入思考模式（短线关 / 决策 high / 重任务 max）
        try:
            from backend.services.deepseek_thinking import apply_deepseek_thinking_to_payload
            apply_deepseek_thinking_to_payload(
                payload, model=config.model, caller=_resolved_caller,
            )
        except Exception:
            pass

        if response_format and not use_streaming:
            payload["response_format"] = response_format
        
        client = _get_httpx_client(base_url, config.api_key)
        _async_timeout = build_httpx_timeout(config, None, use_streaming=use_streaming)

        if use_streaming:
            progress_observer = build_stream_progress_observer(f"async:{_resolved_caller}")
            progress_observer({
                "event": "llm_stream_request",
                "model": config.model,
                "caller": _resolved_caller,
                "account_id": account_id,
            })
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=_async_timeout,
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    logger.error(
                        "LLM API error (async stream): %s - %s",
                        response.status_code,
                        body.decode("utf-8", errors="replace")[:500],
                    )
                    return await _fallback(f"http_{response.status_code}")
                resp_data = await _parse_sse_chat_completion_async(
                    response,
                    progress_observer=progress_observer,
                )
        else:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=_async_timeout,
            )
            if response.status_code != 200:
                logger.error(f"LLM API error: {response.status_code} - {response.text}")
                return await _fallback(f"http_{response.status_code}")
            resp_data = response.json()

        if resp_data:
            resp_data = _normalize_ollama_response(resp_data)
            increment_usage(config.id)
            try:
                usage_info = resp_data.get("usage", {})
                # [2026-08-23 本地LLM最大化] Ollama 流式响应不带 usage 字段 →
                # 用量日志静默缺失（成本为 0 但调用不可观测）。用空 usage 记录
                # 保底（provider/model/call_type/success 仍落库）。
                if usage_info or _is_ollama_qwen3(config):
                    _enqueue_usage_record(
                        config,
                        usage_info or {},
                        f"async:{_resolved_caller}",
                        account_id=account_id,
                    )
            except Exception:
                pass
            return resp_data
        return await _fallback("empty_response")
                
    except Exception as e:
        logger.error(f"LLM API call failed: {e}")
        return await _fallback(f"exception_{type(e).__name__}")


def _detect_caller_module() -> str:
    """从调用栈推断 LLM 调用的来源模块（深挖第 4 项 2026-05-08）。

    所有 chat_completion_sync / call_llm_api_sync 都共用同一个 call_type，
    导致 llm_usage_logs 看不出"谁在调"。这里走 stack frame 找到第一个不在
    本文件里的调用者，写成 `caller_module:caller_func`。
    """
    import inspect, os.path as _osp
    try:
        for fr in inspect.stack()[1:6]:  # 跳过自身
            fname = _osp.basename(fr.filename or "")
            if fname == "llm_config_service.py":
                continue
            # 去掉 .py 后缀
            mod = fname[:-3] if fname.endswith(".py") else fname
            return f"{mod}:{fr.function}"
    except Exception:
        pass
    return "unknown"


def _is_ollama_qwen3(config: LLMConfig) -> bool:
    try:
        return (
            str(getattr(config, "provider", "") or "").lower() == "ollama"
            and "qwen3" in str(getattr(config, "model", "") or "").lower()
        )
    except Exception:
        return False


def _effective_max_tokens(config: LLMConfig, max_tokens: int) -> int:
    """[2026-08-23 本地LLM最大化] qwen3 强制思维链（/no_think、think=false、
    chat_template_kwargs 实测均无法在该 GGUF 上关闭）：reasoning 会吃掉大半
    token 额度导致 content 为空。自动把额度放大保证答案能生成。"""
    if _is_ollama_qwen3(config) and max_tokens < 1024:
        return 1024
    return max_tokens


def _normalize_ollama_response(resp_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """qwen3 思维链模型的 content 可能为空、答案在 reasoning 字段——归一化：
    content 为空时把 reasoning 提升为 content（调用方统一读 content）。"""
    if not resp_data:
        return resp_data
    try:
        choices = resp_data.get("choices") or []
        for ch in choices:
            msg = ch.get("message") or {}
            if isinstance(msg, dict) and not (msg.get("content") or "").strip():
                _reasoning = (msg.get("reasoning") or "").strip()
                if _reasoning:
                    msg["content"] = _reasoning
    except Exception:
        pass
    return resp_data


# ── 整改#13：LLM 语义缓存（默认关；LLM_SEMANTIC_CACHE_ENABLED=true 生效）──
_llm_semantic_cache = None
_llm_cache_lock = threading.Lock()
# [2026-08-23] Ollama generate 并发闸（单模型串行推理，多币并行排队会挤爆线程池）
_ollama_sem = None
# [2026-08-24 槽位治理] 重负载批量调用方（KlineAnalyst 多币并行）限槽，
# 防止其把全局槽全部占满 → scalp_confirm / ai_factor_discovery 永远拿不到槽。
# 「不让单个调用方独占」这个意图仍然正确，故保留本机制，只放宽档位（见下）。
#
# [2026-09-04 实测重定档] 原为硬编码 1 槽。该值来自 35B MoE 时代「Ollama 单模型推理
# 串行、并发排队会挤爆线程池」的判断，对现在的 qwen3:14b 已不成立。实测（2080 Ti
# 22.5G，14b + 7b 双模型常驻）：
#   串行×4 墙钟 10758ms  成功 4/4      并发×4 墙钟  6011ms 成功 4/4（最慢 6.0s）
#   串行×8 墙钟 17526ms  成功 8/8      并发×8 墙钟 11106ms 成功 8/8（最慢 11.1s）
# 即并发不崩且更快，真并行度约 4 —— 超过 4 路开始排队，P95 延迟翻倍，故定档 4。
#
# 旧档位的实际代价：KlineAnalyst 一批扫 3~6 个币，只有 1 个能走本地，其余 3s 抢不到槽
# 就降级云端。日志实测 53 次云端降级里 47 次来自 KlineAnalyst（41 次抢不到槽 + 6 次
# 超时），而同期 GPU 利用率是 0% —— 本地免费算力闲置，却把 DeepSeek 的 5h 窗打到 37/30
# 超支，深度分析反而没模型可用。
_ollama_caller_sems: Dict[str, Any] = {}
_ollama_caller_sems_lock = threading.Lock()
_OLLAMA_HEAVY_CALLER_MARKERS = ("klineanalyst", "mastercontroller")


def _heavy_caller_sem(caller: str):
    _c = str(caller or "").lower()
    if not any(_m in _c for _m in _OLLAMA_HEAVY_CALLER_MARKERS):
        return None
    with _ollama_caller_sems_lock:
        _sem = _ollama_caller_sems.get(_c)
        if _sem is None:
            # 默认 2 = 全局 4 槽的一半：重调用方能并行吃到本地算力，同时始终给
            # 其他调用方留一半，不退回「独占」的老问题。
            _slots = max(1, int(os.getenv("OLLAMA_HEAVY_CALLER_SLOTS", "2") or 2))
            _sem = threading.BoundedSemaphore(_slots)
            _ollama_caller_sems[_c] = _sem
        return _sem


def _call_ollama_generate(
    config: LLMConfig,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
    response_format: Optional[Dict[str, Any]],
    *,
    caller: str,
    account_id: Optional[int] = None,
    timeout: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """[2026-08-23 35B MoE 适配] Ollama 快速通道：/api/generate + think=False。

    背景：qwen3.6-35b:q3（MoE）在 OpenAI 兼容端点强制思维链且无法关闭
    （/no_think 后缀 400、think=false 无效、chat_template_kwargs 无效），
    一次调用 84.5s 超时；而 /api/generate 原生端点 think=False 生效，
    实测纯生成 63 tok/s（比 qwen3:14b 快 51%）。本函数把 messages 合并为
    prompt 走 generate，返回 OpenAI 兼容结构，usage 用原生计数字段。

    [2026-08-23 并发闸] Ollama 单模型推理串行，KlineAnalyst 多币并行会排队
    挤爆线程池（DSH 监管连杀根因）。信号量限并发，抢不到槽即放弃（调用方自动
    降级云端）。[2026-09-04] 「单模型推理串行」已被实测推翻（见 _heavy_caller_sem
    上方的对照数据），并发档位由 OLLAMA_MAX_CONCURRENT 定，默认随之抬到 4。
    """
    global _ollama_sem
    if _ollama_sem is None:
        _ollama_sem = threading.BoundedSemaphore(
            max(1, int(os.getenv("OLLAMA_MAX_CONCURRENT", "4") or 4))
        )
    # 抢槽等待时长。仍保持短等待：本地排队久了不如直接降级云端，短线链路对延迟敏感。
    # 槽位放宽后命中率本身就上来了，不靠拉长等待来硬撑。
    _wait = max(0.5, float(os.getenv("OLLAMA_SLOT_WAIT_SEC", "3") or 3))
    _csem = _heavy_caller_sem(caller)
    if _csem is not None and not _csem.acquire(timeout=_wait):
        logger.warning("[LLM ollama] %s 调用方槽占满，降级云端", caller)
        return None
    try:
        if not _ollama_sem.acquire(timeout=_wait):
            logger.warning("[LLM ollama] %s 并发槽占满，降级云端", caller)
            return None
        try:
            import json as _json
            import urllib.request as _urlreq

            # messages → prompt：system 前置 + user 合并
            parts: List[str] = []
            for m in messages or []:
                role = str(m.get("role") or "")
                content = str(m.get("content") or "")
                if not content:
                    continue
                if role == "system":
                    parts.append(content)
                else:
                    parts.append(content)
            prompt = "\n\n".join(parts)
            if response_format and str(response_format.get("type", "")).endswith("json"):
                prompt = prompt + "\n\n（必须只返回合法JSON，不要其他文字）"

            base = (config.base_url or "http://127.0.0.1:11434/v1").rstrip("/")
            # 去 /v1 得根地址
            if base.endswith("/v1"):
                base = base[:-3]
            payload = {
                "model": config.model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {
                    "num_predict": int(max_tokens or 1024),
                    "temperature": float(temperature or 0.7),
                },
            }
            req = _urlreq.Request(
                base + "/api/generate",
                data=_json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            # [2026-09-04] 深度分析调用方（ModelGateway 交叉验证）用其显式传入的
            # timeout；其余调用方行为完全不变。
            #
            # 原实现无视 timeout 形参、一律 min(..., 8s)。8s 是为 KlineAnalyst 那类
            # 「多币并行、拿不到就赶紧降级云端」的高频短调用定的，对它们仍然正确；
            # 但深度分析要输出 1600+ tokens，实测 8.078s 就被掐断 → 返回 None →
            # 上层报「ollama 空响应」，signal_review / anomaly 的本地票 100% 失败。
            # 且深度分析没有云端可降级 —— 补本地票正是因为云端不可用。
            _deep_caller = any(
                _m in str(caller or "").lower()
                for _m in ("model_gateway", "analysis.", "agents.")
            )
            if _deep_caller and timeout and float(timeout) > 0:
                _ollama_timeout = min(
                    float(timeout),
                    float(os.getenv("OLLAMA_DEEP_CALL_TIMEOUT_SEC", "300") or 300),
                )
            else:
                _ollama_timeout = min(
                    float(resolve_llm_call_timeout(config) or 90),
                    float(os.getenv("OLLAMA_CALL_TIMEOUT_SEC", "8") or 8),
                )
            # 本地 Ollama 直连，禁用任何代理（否则走系统代理报 Privoxy 500）
            _opener = _urlreq.build_opener(_urlreq.ProxyHandler({}))
            with _opener.open(req, timeout=_ollama_timeout) as r:
                raw = _json.loads(r.read().decode("utf-8"))
            content = str(raw.get("response") or "").strip()
            if not content:
                logger.warning("[LLM ollama] %s 空响应 model=%s", caller, config.model)
                return None
            usage = {
                "prompt_tokens": int(raw.get("prompt_eval_count") or 0),
                "completion_tokens": int(raw.get("eval_count") or 0),
            }
            increment_usage(config.id)
            _enqueue_usage_record(
                config, usage, f"sync:{caller}", account_id=account_id,
                duration_ms=int((raw.get("total_duration") or 0) / 1e6),
            )
            return {
                "choices": [{
                    "message": {"content": content, "reasoning": ""},
                    "finish_reason": "stop",
                }],
                "usage": usage,
            }
        except Exception as e:
            logger.warning("[LLM ollama] %s generate 调用失败: %s", caller, e)
            return None
        finally:
            try:
                _ollama_sem.release()
            except Exception:
                pass
    finally:
        if _csem is not None:
            try:
                _csem.release()
            except Exception:
                pass



def _maybe_get_llm_cache():
    """惰性构造语义缓存单例；未启用或构造失败返回 None（透传）。"""
    if os.getenv("LLM_SEMANTIC_CACHE_ENABLED", "false").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    global _llm_semantic_cache
    if _llm_semantic_cache is None:
        with _llm_cache_lock:
            if _llm_semantic_cache is None:
                try:
                    from backend.services.ai.semantic_cache import SemanticCache
                    _ttl = float(os.getenv("LLM_CACHE_TTL_SECONDS", "60"))
                    _thr = float(os.getenv("LLM_CACHE_THRESHOLD", "0.95"))
                    _llm_semantic_cache = SemanticCache(ttl_seconds=_ttl, similarity_threshold=_thr)
                except Exception:
                    return None
    return _llm_semantic_cache


def _build_llm_cache_key(model, messages, temperature, max_tokens, response_format) -> str:
    import json as _json
    try:
        return _json.dumps({
            "m": model, "msgs": messages, "t": temperature,
            "mt": max_tokens, "rf": response_format,
        }, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return f"{model}|{messages}|{temperature}|{max_tokens}|{response_format}"


def call_llm_api_sync(
    config: LLMConfig,
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2000,
    response_format: Optional[Dict[str, Any]] = None,
    timeout: Optional[float] = None,
    *,
    caller: Optional[str] = None,
    account_id: Optional[int] = None,
    progress_observer: Optional[Callable[[Dict[str, Any]], None]] = None,
    bypass_cache: bool = False,
    fallback_config: Optional[LLMConfig] = None,
) -> Optional[Dict[str, Any]]:
    """同步版 LLM API 调用，供 APScheduler 线程等同步上下文使用。
    避免 async event loop 管理问题（Event loop is closed）。

    timeout: 覆盖默认超时（秒）。不传则使用 LLM_CALL_TIMEOUT_SECONDS 环境变量或 120s。
    caller: 调用方标识；不传则从 stack frame 自动推断。
    account_id: 关联账户 ID，便于追踪是谁在烧 token。
    bypass_cache: 跳过语义缓存——用于 MLTO thesis_update 等需每标的独立判断的场景，
        避免不同 symbol 的高相似度 prompt 互相命中（SOL/ETH/BTC 结构相似 → 误命中）。
    """
    if not config:
        logger.error("No LLM configuration provided")
        return None

    # ── 整改#13：语义缓存命中 → 直接返回，跳过 API/并发槽（省 token & 延迟）──
    _cache = None if bypass_cache else _maybe_get_llm_cache()
    _cache_key = None
    if _cache is not None:
        try:
            _cache_key = _build_llm_cache_key(config.model, messages, temperature, max_tokens, response_format)
            _hit = _cache.get(_cache_key)
            if _hit:
                import json as _json
                logger.info("[LLM sync][Cache#13] 命中，跳过 API caller=%s", caller or _detect_caller_module())
                return _json.loads(_hit)
        except Exception:
            _cache_key = None

    _resolved_caller = caller or _detect_caller_module()
    if not _acquire_llm_slot(caller=_resolved_caller):
        logger.warning(
            "[LLM sync] 并发槽等待超时，跳过 caller=%s",
            _resolved_caller,
        )
        return None

    def _fallback(reason: str) -> Optional[Dict[str, Any]]:
        """[2026-08-23 本地LLM最大化] 主配置失败 → 自动降级云端重试一次。"""
        if fallback_config is None:
            return None
        logger.warning(
            "[LLM sync] 主配置(%s %s)失败(%s) → 降级云端 %s 重试 caller=%s",
            getattr(config, "provider", "?"), getattr(config, "model", "?"),
            reason, getattr(fallback_config, "model", "?"), _resolved_caller,
        )
        try:
            return call_llm_api_sync(
                fallback_config, messages, temperature, max_tokens,
                response_format, timeout,
                caller=caller, account_id=account_id,
                progress_observer=progress_observer,
                bypass_cache=bypass_cache,
                fallback_config=None,
            )
        except Exception as _fb_err:
            logger.warning("[LLM sync] 云端降级重试异常: %s", _fb_err)
            return None

    try:
        # [2026-08-23 35B MoE 适配] Ollama 走 /api/generate 快速通道（think=False）；
        # 失败自动降级 fallback_config（云端）。
        if str(getattr(config, "provider", "") or "").lower() == "ollama":
            _ollama_resp = _call_ollama_generate(
                config, messages, temperature, max_tokens, response_format,
                caller=_resolved_caller, account_id=account_id, timeout=timeout,
            )
            if _ollama_resp is not None:
                return _ollama_resp
            return _fallback("ollama_generate_failed")
        base_url = config.base_url.rstrip('/')

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}"
        }

        is_reasoning = is_reasoning_model(config.model)
        use_streaming = should_use_llm_streaming(
            config,
            messages=messages,
            max_tokens=max_tokens,
            caller=_resolved_caller,
        )

        payload = {
            "model": config.model,
            "messages": messages,
            "stream": use_streaming,
        }

        # [2026-08-23 本地LLM最大化] qwen3 GGUF 强制思维链且 OpenAI 兼容端点不接受
        # /no_think 后缀（400 invalid model name）；改为 max_tokens 自动放大 +
        # reasoning 归一化兜底（见 _effective_max_tokens/_normalize_ollama_response）。

        if is_reasoning:
            payload["max_completion_tokens"] = _effective_max_tokens(config, max_tokens)
        else:
            payload["temperature"] = temperature
            payload["max_tokens"] = _effective_max_tokens(config, max_tokens)

        # DeepSeek V4：按 caller 分层注入思考模式（短线关 / 决策 high / 重任务 max）
        try:
            from backend.services.deepseek_thinking import apply_deepseek_thinking_to_payload
            apply_deepseek_thinking_to_payload(
                payload, model=config.model, caller=_resolved_caller,
            )
        except Exception:
            pass

        if response_format and not use_streaming:
            payload["response_format"] = response_format

        client = _get_httpx_sync_client(base_url, config.api_key)
        _timeout = build_httpx_timeout(config, timeout, use_streaming=use_streaming)

        if use_streaming:
            _resolved_caller = caller or _detect_caller_module()
            logger.info(
                "[LLM sync] 流式调用 %s caller=%s "
                "(read=%ss)",
                config.model,
                _resolved_caller,
                "无上限直到[DONE]" if getattr(_timeout, "read", None) is None else _timeout.read,
            )
            if progress_observer:
                progress_observer({
                    "event": "llm_stream_request",
                    "model": config.model,
                    "caller": _resolved_caller,
                    "account_id": account_id,
                })
            with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=_timeout,
            ) as response:
                if response.status_code != 200:
                    body = response.read().decode("utf-8", errors="replace")
                    logger.error(f"LLM API error (sync stream): {response.status_code} - {body[:500]}")
                    if progress_observer:
                        progress_observer({
                            "event": "llm_stream_http_error",
                            "status_code": response.status_code,
                            "body": body[:500],
                        })
                    return _fallback(f"http_{response.status_code}")
                resp_data = _parse_sse_chat_completion(response, progress_observer=progress_observer)
        else:
            response = client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=_timeout,
            )
            if response.status_code != 200:
                logger.error(f"LLM API error (sync): {response.status_code} - {response.text}")
                return _fallback(f"http_{response.status_code}")
            resp_data = response.json()

        if resp_data:
            resp_data = _normalize_ollama_response(resp_data)
            increment_usage(config.id)
            try:
                usage_info = resp_data.get("usage", {})
                # [2026-08-23 本地LLM最大化] Ollama 流式响应不带 usage 字段 →
                # 用量日志静默缺失。空 usage 保底记录（可观测性）。
                if usage_info or _is_ollama_qwen3(config):
                    _resolved_caller = caller or _detect_caller_module()
                    _enqueue_usage_record(
                        config,
                        usage_info or {},
                        f"sync:{_resolved_caller}",
                        account_id=account_id,
                    )
            except Exception:
                pass
            # ── 整改#13：回填语义缓存 ──
            if _cache is not None and _cache_key:
                try:
                    import json as _json
                    _cache.set(_cache_key, _json.dumps(resp_data, ensure_ascii=False, default=str))
                except Exception:
                    pass
            return resp_data
        return _fallback("empty_response")

    except Exception as e:
        logger.error(f"LLM API call failed (sync): {e}")
        return _fallback(f"exception_{type(e).__name__}")
    finally:
        try:
            _sem = _get_llm_semaphore()
            if _sem is not None:
                _sem.release()
        except Exception:
            pass
        try:
            from backend.services.full_auto.llm_budget import release_llm_budget
            release_llm_budget()
        except Exception:
            pass


def get_config_for_module(module_name: str) -> Optional[LLMConfig]:
    """已废弃：禁止模块级公用 LLM。请改用 get_llm_config_for_account。"""
    logger.warning(
        "[LLM] get_config_for_module(%s) 已禁用公用回退，请传 account_id",
        module_name,
    )
    return None


# Export public API
__all__ = [
    'LLMConfig',
    'get_llm_config',
    'get_llm_config_for_account',
    'get_llm_config_for_usage',
    'get_llm_config_for_analysis',
    'get_all_active_configs',
    'increment_usage',
    'call_llm_api',
    'call_llm_api_sync',
    'get_config_for_module',
    'is_reasoning_model',
    'should_use_llm_streaming',
    'build_stream_progress_observer',
    'build_httpx_timeout',
    'resolve_llm_call_timeout',
]
