"""
AI Decision Service - Handles AI model API calls for trading decisions
"""
import logging
import random
import json
import time
import re
from decimal import Decimal
from typing import Any, Dict, Optional, List
from datetime import datetime, timezone

import requests
from sqlalchemy.orm import Session

from backend.database.models import Position, Account, AIDecisionLog
from backend.services.asset_calculator import calc_positions_value
from backend.services.news_feed import fetch_latest_news
from backend.repositories.strategy_repo import set_last_trigger
from backend.services.system_logger import system_logger
from repositories import prompt_repo

# ── Phase 3B 集成：规则引擎决策管道 ──
from backend.services.signal_confirmation_engine import SignalConfirmationEngine, ConfirmationResult
from backend.services.position_sizer import PositionSizer, PositionSizeResult
from backend.services.rule_based_decision_engine import RuleBasedDecisionEngine, LLMSentimentInput, RuleDecision
# [refactor] reasoning 提取逻辑提炼为公共 helper，供中长线 agent（swing/trend/direction/ # trade_risk）统一复用，保持单一真相源。此处仅保留同名别名，内部逻辑不变。
from backend.services.llm_reasoning_helper import (
    extract_text_from_message,
    extract_reasoning_content_safe,
    build_reasoning_snapshot,
)
# 历史调用点使用带下划线前缀的名字，保留别名兼容
_extract_text_from_message = extract_text_from_message
_extract_reasoning_content_safe = extract_reasoning_content_safe

_signal_engine = SignalConfirmationEngine()
_position_sizer = PositionSizer()
_rule_engine = RuleBasedDecisionEngine()


logger = logging.getLogger(__name__)

#  mode API keys that should be skipped
DEMO_API_KEYS = {
    "default-key-please-update-in-settings",
    "default",
    "via-llm-config-library",
    "",
    None
}


def resolve_account_llm_config(db: Session, account: Account) -> bool:
    """Resolve LLM configuration for an account from the LLM Config Library.

    If the account has a linked ``llm_config_id``, load the corresponding
    ``LLMConfiguration`` and inject ``api_key``, ``model``, and ``base_url``
    into the account object (in-memory only, not committed).

    Returns True if a valid LLM config was resolved successfully.
    """
    if not getattr(account, 'llm_config_id', None):
        return False

    try:
        from backend.database.models import LLMConfiguration
        llm_cfg = db.query(LLMConfiguration).filter(
            LLMConfiguration.id == account.llm_config_id,
            LLMConfiguration.is_active == "true"
        ).first()
        if llm_cfg and llm_cfg.api_key:
            from backend.utils.encryption import decrypt_llm_key
            logger.info(f"Resolved LLM config '{llm_cfg.name}' for account '{account.name}'")
            account.api_key = decrypt_llm_key(llm_cfg.api_key)
            account.model = llm_cfg.model
            account.base_url = llm_cfg.base_url
            return True
        else:
            logger.warning(
                f"LLM config id={account.llm_config_id} for account '{account.name}' "
                f"not found or inactive/missing api_key"
            )
            return False
    except Exception as e:
        logger.warning(f"Failed to resolve LLM config for account '{account.name}': {e}")
        return False

SUPPORTED_SYMBOLS: Dict[str, str] = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "DOGE": "Dogecoin",
    "XRP": "Ripple",
    "BNB": "Binance Coin",
    "AVNT": "Aviation",
    "XPL": "XPL",
    "VIRTUAL": "Virtual Protocol",
    "ASTER": "Aster",
}


class SafeDict(dict):
    def __missing__(self, key):  # type: ignore[override]
        return "N/A"


def _format_currency(value: Optional[float], precision: int = 2, default: str = "N/A") -> str:
    try:
        if value is None:
            return default
        return f"{float(value):,.{precision}f}"
    except (TypeError, ValueError):
        return default


def _format_quantity(value: Optional[float], precision: int = 6, default: str = "0") -> str:
    try:
        if value is None:
            return default
        return f"{float(value):.{precision}f}"
    except (TypeError, ValueError):
        return default


def _get_metric_unit(metric: str) -> str:
    """Get the unit for a signal metric type."""
    # Percentage-based metrics
    percent_metrics = {
        "oi_delta", "price_change_percent", "volume_change_percent",
        "funding", "funding_rate"
    }
    # Ratio-based metrics (no unit, just a number)
    # taker_ratio is now log-transformed, symmetric around 0
    ratio_metrics = {"depth_ratio", "order_imbalance", "imbalance", "taker_ratio"}
    # USD-based metrics
    usd_metrics = {"oi", "cvd", "volume", "taker_volume"}

    metric_lower = metric.lower() if metric else ""
    if metric_lower in percent_metrics or "percent" in metric_lower:
        return "%"
    elif metric_lower in usd_metrics:
        return ""  # USD values are typically formatted separately
    elif metric_lower in ratio_metrics:
        return ""  # Ratios are dimensionless
    return ""


def _build_session_context(account: Account) -> str:
    """Build session context (legacy format for backward compatibility)"""
    now = datetime.now(timezone.utc)
    runtime_minutes = "N/A"

    created_at = getattr(account, "created_at", None)
    if isinstance(created_at, datetime):
        created = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        runtime_minutes = str(max(0, int((now - created).total_seconds() // 60)))

    lines = [
        f"TRADER_ID: {account.name}",
        f"MODEL: {account.model or 'N/A'}",
        f"RUNTIME_MINUTES: {runtime_minutes}",
        "INVOCATION_COUNT: N/A",
        f"CURRENT_TIME_UTC: {now.isoformat()}",
    ]
    return "\n".join(lines)


def _calculate_runtime_minutes(account: Account) -> str:
    """Calculate runtime minutes for Alpha Arena style prompts"""
    created_at = getattr(account, "created_at", None)
    if isinstance(created_at, datetime):
        now = datetime.now(timezone.utc)
        created = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        return str(max(0, int((now - created).total_seconds() // 60)))
    return "0"


def _calculate_total_return_percent(account: Account) -> str:
    """Calculate total return percentage"""
    initial_cash = float(getattr(account, "initial_cash", 0) or 10000)
    current_total = float(getattr(account, "current_cash", 0))

    # Add positions value if available
    try:
        from services.asset_calculator import calc_positions_value
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            positions_value = calc_positions_value(db, account.id)
            current_total += positions_value
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Error calculating positions value for account {account.id}: {e}")
        pass

    if initial_cash > 0:
        return_pct = ((current_total - initial_cash) / initial_cash) * 100
        return f"{return_pct:+.2f}"
    return "0.00"


def _build_holdings_detail(positions: Dict[str, Dict[str, Any]]) -> str:
    """Build detailed holdings list for Alpha Arena style prompts"""
    if not positions:
        return "- None (all cash)"

    lines = []
    for symbol, data in positions.items():
        qty = data.get('quantity', 0)
        avg_cost = data.get('avg_cost', 0)
        current_value = data.get('current_value', 0)

        lines.append(
            f"- {symbol}: {_format_quantity(qty)} units @ ${_format_currency(avg_cost, precision=4)} avg "
            f"(current value: ${_format_currency(current_value)})"
        )

    return "\n".join(lines)


def _build_market_prices(
    prices: Dict[str, float],
    symbol_order: Optional[List[str]] = None,
    symbol_names: Optional[Dict[str, str]] = None,
) -> str:
    """Build simple market prices list for Alpha Arena style prompts"""
    order = symbol_order or list(SUPPORTED_SYMBOLS.keys())
    lines = []
    for symbol in order:
        price = prices.get(symbol)
        display_name = (symbol_names or {}).get(symbol)
        label = symbol if not display_name or display_name == symbol else f"{symbol} ({display_name})"
        if price:
            lines.append(f"{label}: ${_format_currency(price, precision=4)}")
        else:
            lines.append(f"{label}: N/A")

    return "\n".join(lines)


def _normalize_symbol_metadata(
    symbol_metadata: Optional[Dict[str, Any]],
    fallback_symbols: List[str],
) -> Dict[str, Dict[str, Optional[str]]]:
    """Normalize symbol metadata into a consistent mapping."""
    normalized: Dict[str, Dict[str, Optional[str]]] = {}

    if symbol_metadata:
        for raw_symbol, meta in symbol_metadata.items():
            symbol = str(raw_symbol).upper()
            if isinstance(meta, dict):
                normalized[symbol] = {
                    "name": meta.get("name") or meta.get("display_name") or symbol,
                    "type": meta.get("type") or meta.get("category"),
                }
            else:
                display = str(meta).strip()
                normalized[symbol] = {
                    "name": display or symbol,
                    "type": None,
                }

    for symbol in fallback_symbols:
        normalized.setdefault(
            symbol,
            {
                "name": SUPPORTED_SYMBOLS.get(symbol, symbol),
                "type": None,
            },
        )

    if not normalized:
        for symbol, display in SUPPORTED_SYMBOLS.items():
            normalized[symbol] = {"name": display, "type": None}

    return normalized


def _build_account_state(portfolio: Dict[str, Any]) -> str:
    positions: Dict[str, Dict[str, Any]] = portfolio.get("positions", {})
    lines = [
        f"Available Cash (USD): {_format_currency(portfolio.get('cash'))}",
        f"Frozen Cash (USD): {_format_currency(portfolio.get('frozen_cash'))}",
        f"Total Assets (USD): {_format_currency(portfolio.get('total_assets'))}",
        "",
        "Open Positions:",
    ]

    if positions:
        for symbol, data in positions.items():
            lines.append(
                f"- {symbol}: qty={_format_quantity(data.get('quantity'))}, "
                f"avg_cost={_format_currency(data.get('avg_cost'))}, "
                f"current_value={_format_currency(data.get('current_value'))}"
            )
    else:
        lines.append("- None")

    return "\n".join(lines)


def _build_sampling_data(samples: Optional[List], target_symbol: Optional[str], sampling_interval: Optional[int] = None) -> str:
    """Build sampling pool data section for Alpha Arena style prompts (single symbol)"""
    if not samples or not target_symbol:
        return "No sampling data available."

    interval_text = f"{sampling_interval}-second intervals" if sampling_interval else "unknown intervals"
    lines = [
        f"Multi-timeframe price data for {target_symbol} ({interval_text}, oldest to newest):",
        f"Total samples: {len(samples)}",
        ""
    ]

    # Format samples in Alpha Arena style - chronological order (oldest to newest)
    for i, sample in enumerate(samples):
        timestamp = sample.get('datetime', 'N/A')
        price = sample.get('price', 0)
        # Format timestamp to be more readable
        if timestamp != 'N/A':
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                time_str = dt.strftime('%H:%M:%S')
            except (ValueError, TypeError, AttributeError):
                time_str = timestamp
        else:
            time_str = 'N/A'

        lines.append(f"T-{len(samples)-i-1}: ${price:.6f} ({time_str})")

    # Calculate price momentum and trend
    if len(samples) >= 2:
        first_price = samples[0].get('price', 0)
        last_price = samples[-1].get('price', 0)
        if first_price > 0:
            change_pct = ((last_price - first_price) / first_price) * 100
            trend = "BULLISH" if change_pct > 0 else "BEARISH" if change_pct < 0 else "NEUTRAL"
            lines.append("")
            lines.append(f"Price momentum: {change_pct:+.3f}% ({trend})")
            lines.append(f"Range: ${first_price:.6f} → ${last_price:.6f}")

    return "\n".join(lines)


def _build_multi_symbol_sampling_data(symbols: List[str], sampling_pool, sampling_interval: Optional[int] = None) -> str:
    """Build sampling pool data for multiple symbols (Alpha Arena style)"""
    if not symbols:
        return "No symbols selected for sampling data."

    sections = []
    interval_text = f"{sampling_interval}-second intervals" if sampling_interval else "unknown intervals"

    for symbol in symbols:
        samples = sampling_pool.get_samples(symbol)
        if not samples:
            sections.append(f"{symbol}: No sampling data available")
            continue

        lines = [
            f"{symbol} ({interval_text}, oldest to newest):",
            f"Total samples: {len(samples)}",
            ""
        ]

        # Format samples
        for i, sample in enumerate(samples):
            timestamp = sample.get('datetime', 'N/A')
            price = sample.get('price', 0)
            if timestamp != 'N/A':
                try:
                    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    time_str = dt.strftime('%H:%M:%S')
                except:
                    time_str = timestamp
            else:
                time_str = 'N/A'

            lines.append(f"T-{len(samples)-i-1}: ${price:.6f} ({time_str})")

        # Calculate momentum
        if len(samples) >= 2:
            first_price = samples[0].get('price', 0)
            last_price = samples[-1].get('price', 0)
            if first_price > 0:
                change_pct = ((last_price - first_price) / first_price) * 100
                trend = "BULLISH" if change_pct > 0 else "BEARISH" if change_pct < 0 else "NEUTRAL"
                lines.append("")
                lines.append(f"Price momentum: {change_pct:+.3f}% ({trend})")
                lines.append(f"Range: ${first_price:.6f} → ${last_price:.6f}")

        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _build_market_snapshot(
    prices: Dict[str, float],
    positions: Dict[str, Dict[str, Any]],
    symbol_order: Optional[List[str]] = None,
) -> str:
    lines: List[str] = []
    order = symbol_order or list(SUPPORTED_SYMBOLS.keys())
    for symbol in order:
        price = prices.get(symbol)
        position = positions.get(symbol, {})

        parts = [f"{symbol}: price={_format_currency(price, precision=4)}"]
        if position:
            parts.append(f"qty={_format_quantity(position.get('quantity'))}")
            parts.append(f"avg_cost={_format_currency(position.get('avg_cost'), precision=4)}")
            parts.append(f"position_value={_format_currency(position.get('current_value'))}")
        else:
            parts.append("position=flat")

        lines.append(", ".join(parts))

    return "\n".join(lines) if lines else "No market data available."


SYMBOL_PLACEHOLDER = "__SYMBOL_SET__"
OUTPUT_FORMAT_JSON = (
    '{\n'
    '  "decisions": [\n'
    '    {\n'
    '      "operation": "buy" | "sell" | "hold" | "close",\n'
    '      "symbol": "<' + SYMBOL_PLACEHOLDER + '>",\n'
    '      "confidence": <float 0.0-1.0>,\n'
    '      "target_portion_of_balance": <float 0.0-1.0>,\n'
    '      "leverage": <integer 1-20>,\n'
    '      "max_price": <number, required for "buy" operations>,\n'
    '      "min_price": <number, required for "sell"/"close" operations>,\n'
    '      "time_in_force": "Ioc",\n'
    '      "take_profit_price": <number, REQUIRED for "buy"/"sell", take profit trigger price>,\n'
    '      "stop_loss_price": <number, REQUIRED for "buy"/"sell", stop loss trigger price>,\n'
    '      "reason": "<string explaining primary signals>",\n'
    '      "trading_strategy": "<string covering thesis, risk controls, and exit plan>"\n'
    '    }\n'
    '  ]\n'
    '}'
)

# Placeholder for max leverage in output format template
MAX_LEVERAGE_PLACEHOLDER = "__MAX_LEVERAGE__"

# 完整的 OUTPUT FORMAT 模板，包含所有要求和示例
# 使用双括号转义 JSON 字面量以避免 format_map() 冲突
OUTPUT_FORMAT_COMPLETE = """请仅使用以下 JSON 模式响应（始终输出 `decisions` 数组，即使为空）：
{{
  "decisions": [
    {{
      "operation": "buy" | "sell" | "hold" | "close",
      "symbol": "<__SYMBOL_SET__>",
      "confidence": <浮点数 0.0-1.0, 你对该决策的置信度>,
      "target_portion_of_balance": <浮点数 0.0-1.0>,
      "leverage": <整数 1-__MAX_LEVERAGE__>,
      "max_price": <数字, "buy" 操作必填>,
      "min_price": <数字, "sell"/"close" 操作必填>,
      "time_in_force": "Ioc",
      "take_profit_price": <数字, "buy"/"sell" 操作必填, 设置止盈触发价格>,
      "stop_loss_price": <数字, "buy"/"sell" 操作必填, 设置止损触发价格>,
      "reason": "<解释主要信号的字符串>",
      "trading_strategy": "<涵盖交易论点、风控和出场计划的字符串>",
      "risk_scenario": "<如果判断错了: 最大可能亏损多少 什么情况下会错 错了怎么办 buy/sell必填, hold可用空字符串>"
    }}
  ]
}}

关键输出要求：
- 【强制·角色】你是"审核者"而非"决策者"：因子引擎已计算方向信号({factor_guidance})，你的任务是审核这些信号是否合理、结合新闻/宏观否决或确认，而非从头判断方向
- 【强制】交易对列表: __SYMBOL_SET__ - 每个都需要一个决策条目
- 【强制】对于所有 "buy" 和 "sell" 操作，必须提供 take_profit_price 和 stop_loss_price
- 【强制】TP/SL 价格必须基于当前市场价格合理设置（建议 TP +3%~10%, SL -3%~-5%）
- 【强制·方向一致性】若上下文中包含方向约束（allowed_direction），所有品种的 buy/sell 决策必须与之一致：long_only 时只能 buy 或 hold，short_only 时只能 sell 或 hold，违背则必须输出 HOLD
- 【强制·禁止翻转】若上下文显示某标的近期刚平仓或刚换方向，该标的应输出 HOLD 观望，不得立即反向开仓
- 输出必须是单个、有效的 JSON 对象
- 不要使用 markdown 代码块（不要用 ```json``` 包裹）
- JSON 前后不要有任何解释性文本
- JSON 对象外不要有注释或其他内容
- 确保所有 JSON 字段都正确引用和格式化
- 响应前双重检查 JSON 语法

多个交易对完整决策的输出示例（必须为每个监控的交易对输出决策）：
{{
  "decisions": [
    {{
      "operation": "buy",
      "symbol": "BTC",
      "confidence": 0.78,
      "target_portion_of_balance": 0.3,
      "leverage": 15,
      "max_price": 49500,
      "time_in_force": "Ioc",
      "take_profit_price": 52000,
      "stop_loss_price": 47500,
      "reason": "$48k 支撑位稳固，强势看涨动量，RSI 从超卖区恢复",
      "trading_strategy": "使用 30% 余额开立 15 倍杠杆多头仓位。止盈设在 $52k 阻力位 (+5%)，止损设在 $47.5k 摆动低点下方 (-4%)。使用 IOC 立即执行。",
      "risk_scenario": "如果错了: 最大亏损约 4% 仓位(止损)，发生在支撑位假突破时。若放量跌破 $47.5k 应手动平仓"
    }},
    {{
      "operation": "sell",
      "symbol": "ETH",
      "confidence": 0.65,
      "target_portion_of_balance": 0.2,
      "leverage": 12,
      "min_price": 3125,
      "take_profit_price": 2980,
      "stop_loss_price": 3250,
      "reason": "ETH 永续资金费率转为较高的负值，动量减弱",
      "trading_strategy": "开立小型空头对冲仓位，等待 ETH 相对 BTC 恢复强势。止盈 $2980 (-5%)，止损 $3250 (上方阻力)。"
    }},
    {{
      "operation": "hold",
      "symbol": "SOL",
      "confidence": 0.35,
      "target_portion_of_balance": 0,
      "leverage": 8,
      "reason": "SOL 处于盘整区间，无明确方向性信号，等待突破",
      "trading_strategy": "观望等待，关注 $120 支撑和 $140 阻力的突破情况。"
    }}
  ]
}}

字段类型要求：
- decisions: 数组（每个支持的交易对一个条目；不操作时包含分配为零的 HOLD 条目）
- operation: 字符串（"buy" 做多、"sell" 做空、"hold" 或 "close"）
- symbol: 字符串（必须是以下之一: __SYMBOL_SET__）
- confidence: 数字（浮点数 0.0-1.0，你对该决策的置信度，必填。buy/sell 至少 0.6 以上，hold 通常 0.3-0.5）
- target_portion_of_balance: 数字（浮点数 0.0 到 1.0，HOLD 时为 0，buy/sell 时建议 0.10-0.35）
- leverage: 整数（5 到 __MAX_LEVERAGE__，必填字段，合约交易最低5倍）
- max_price: 数字（"buy" 操作和平空仓时必填。这是你愿意支付的最高价格。）
- min_price: 数字（"sell" 操作和平多仓时必填。这是你愿意接受的最低价格。）
- take_profit_price: 数字（"buy"/"sell" 操作必填，止盈触发价格，建议 +3%~10%）
- stop_loss_price: 数字（"buy"/"sell" 操作必填，止损触发价格，建议 -3%~-5%）
- reason: 字符串，解释关键催化剂、风险或信号（无严格长度限制，但保持精简）
- trading_strategy: 字符串，涵盖入场论点、杠杆理由、强平意识和出场计划"""


DECISION_TASK_TEXT = (
    "你是在 Hyper Alpha Arena 系统化交易系统中的决策分析组件。\n"
    "- 【核心原则】只在高置信度（≥3个独立指标同向确认）时才输出 BUY/SELL，其余情况输出 HOLD。\n"
    "- 宁可错过机会，不可在信号不明确时开仓——低质量交易是亏损的主要来源。\n"
    "- 单次开仓最多使用账户余额的35%，不得超过此上限。\n"
    "- 杠杆范围 5x 至 __MAX_LEVERAGE__x（与输出格式中的 leverage 字段一致），止损价格为必填项，缺失则输出 HOLD。\n"
    "- 【方向一致性】若系统已给出方向约束（如 allowed_direction），所有 BUY/SELL 必须与之一致，违背则输出 HOLD。\n"
    "- 【禁止频繁翻转】同一标的不得在短时间内反复切换多空方向，若上一笔刚平仓，优先 HOLD 观望。\n"
    "- 【持仓时间纪律】严格按周期管理持仓：短线仓(Short)持有不超过8小时，中线仓(Mid)不超过24小时，长线仓(Long)不超过7天。"
    " 持仓接近或超过上限时，必须优先评估是否平仓，不得无理由继续HOLD。"
    " 如果仓位已有可观盈利且趋势仍在，可适当延长但必须说明理由。\n"
    "- 连续2次亏损后，下一笔仓位缩减50%。\n"
    "- 置信度字段 confidence（0~1）必须填写，低于0.6的决策自动降级为 HOLD。\n"
    "- 【置信度诚实】你的置信度必须真实反映你对这笔交易的确信程度。"
    " 系统会追踪你的置信度与实际胜率的关联——虚报高置信度会降低你的评价。\n"
    "- 当数据缺失（标记为 N/A）时，承认不确定性，输出 HOLD。\n"
)


# P1-2: 置信度校准缓存（避免每次 prompt 都查询 DB）
_calibration_cache: Dict[str, Any] = {"data": None, "ts": 0}
_CALIBRATION_CACHE_TTL = 3600  # 1 小时缓存


def _get_confidence_calibration_report() -> str:
    """生成 LLM 置信度与实际胜率的校准报告，注入 prompt 让 LLM 知道自己的预测质量。"""
    global _calibration_cache
    now = __import__("time").time()
    if _calibration_cache["data"] is not None and now - _calibration_cache["ts"] < _CALIBRATION_CACHE_TTL:
        return _calibration_cache["data"]

    try:
        from backend.database.connection import SessionLocal
        from backend.database.dialect import dialect
        from sqlalchemy import text
        db = SessionLocal()
        try:
            rows = db.execute(text("""
                SELECT
                    CASE
                        WHEN confidence < 0.6 THEN '0.0-0.6'
                        WHEN confidence < 0.7 THEN '0.6-0.7'
                        WHEN confidence < 0.8 THEN '0.7-0.8'
                        WHEN confidence < 0.9 THEN '0.8-0.9'
                        ELSE '0.9-1.0'
                    END as conf_bucket,
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
                FROM strategy_trades
                WHERE confidence IS NOT NULL
                  AND created_at >= """ + dialect.datetime_now_minus(30) + """
                GROUP BY conf_bucket
                ORDER BY conf_bucket
            """)).fetchall()
            if not rows:
                _calibration_cache["data"] = ""
                _calibration_cache["ts"] = now
                return ""

            report_lines = [
                "═══════════════════════════════",
                "📈 置信度校准报告（你过去30天的预测质量）",
                "═══════════════════════════════",
            ]
            total_all = 0
            wins_all = 0
            for bucket, cnt, wins in rows:
                wr = float(wins) / float(cnt) * 100 if cnt else 0
                report_lines.append(f"  置信度 {bucket}: {wr:.0f}% 胜率 ({int(cnt)} 笔)")
                total_all += int(cnt)
                wins_all += int(wins)

            overall_wr = wins_all / total_all * 100 if total_all else 0
            report_lines.append(f"  整体胜率: {overall_wr:.0f}% ({total_all} 笔)")
            report_lines.append("提示: 你的置信度必须真实反映确信程度，系统在追踪准确率。")
            report_lines.append("═══════════════════════════════\n")

            result = "\n".join(report_lines)
            _calibration_cache["data"] = result
            _calibration_cache["ts"] = now
            return result
        finally:
            db.close()
    except Exception as _e:
        logger.debug(f"[Calibration] 置信度校准报告生成失败(非致命): {_e}")
        return ""


def _build_prompt_context(
    account: Account,
    portfolio: Dict[str, Any],
    prices: Dict[str, float],
    news_section: str,
    samples: Optional[List] = None,
    target_symbol: Optional[str] = None,
    hyperliquid_state: Optional[Dict[str, Any]] = None,
    binance_state: Optional[Dict[str, Any]] = None,
    *,
    db: Optional[Session] = None,
    symbol_metadata: Optional[Dict[str, Any]] = None,
    symbol_order: Optional[List[str]] = None,
    sampling_interval: Optional[int] = None,
    environment: str = "mainnet",
    template_text: Optional[str] = None,
    trigger_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build complete prompt context for AI decision-making.

    ⚠️ CRITICAL: This is the SINGLE and ONLY function responsible for building
    prompt context variables. ALL prompt template variable generation MUST happen
    here to ensure consistency between preview and actual AI decision execution.

    DO NOT create separate context-building logic elsewhere. If you need to add
    new template variables, add them here.

    Args:
        account: Trading account
        portfolio: Portfolio data with positions
        prices: Current market prices
        news_section: Latest news summary
        samples: Legacy price samples (deprecated)
        target_symbol: Legacy single symbol (deprecated)
        hyperliquid_state: Real-time Hyperliquid account state
        binance_state: Real-time Binance account state
        db: Database session (required for leverage settings lookup)
        symbol_metadata: Symbol display names and metadata
        symbol_order: Ordered list of symbols
        sampling_interval: Sampling interval in seconds
        environment: Trading environment (mainnet/testnet)
        template_text: Prompt template text for parsing K-line variables
        trigger_context: Context about what triggered this decision (signal or scheduled)

    Returns:
        Complete context dictionary ready for template.format_map()
    """
    base_portfolio = portfolio or {}
    base_positions = base_portfolio.get("positions") or {}
    positions: Dict[str, Dict[str, Any]] = {symbol: dict(data) for symbol, data in base_positions.items()}

    symbol_source = symbol_metadata or SUPPORTED_SYMBOLS
    base_order = symbol_order or list(symbol_source.keys())
    ordered_symbols: List[str] = []
    seen_symbols = set()
    for sym in base_order:
        symbol_upper = str(sym).upper()
        if not symbol_upper or symbol_upper in seen_symbols:
            continue
        seen_symbols.add(symbol_upper)
        ordered_symbols.append(symbol_upper)
    if not ordered_symbols:
        ordered_symbols = list(SUPPORTED_SYMBOLS.keys())

    normalized_symbol_metadata = _normalize_symbol_metadata(symbol_metadata, ordered_symbols)
    symbol_display_map = {
        symbol: normalized_symbol_metadata.get(symbol, {}).get("name") or SUPPORTED_SYMBOLS.get(symbol, symbol)
        for symbol in ordered_symbols
    }
    selected_symbols_detail_lines = []
    for symbol in ordered_symbols:
        info = normalized_symbol_metadata.get(symbol, {})
        display_name = info.get("name") or symbol
        symbol_type = info.get("type")
        if symbol_type:
            selected_symbols_detail_lines.append(f"- {symbol}: {display_name} ({symbol_type})")
        else:
            selected_symbols_detail_lines.append(f"- {symbol}: {display_name}")
    selected_symbols_detail = "\n".join(selected_symbols_detail_lines) if selected_symbols_detail_lines else "None configured"
    selected_symbols_csv = ", ".join(ordered_symbols) if ordered_symbols else "N/A"
    output_symbol_choices = "|".join(ordered_symbols) if ordered_symbols else "SYMBOL"

    # NOTE: environment parameter is now passed from caller (call_ai_for_decision)

    # Use Hyperliquid state if provided (indicates Hyperliquid trading mode)
    if hyperliquid_state and environment in ("testnet", "mainnet"):
        hl_positions = hyperliquid_state.get("positions", []) or []
        positions = {}
        for pos in hl_positions:
            symbol = (pos.get("coin") or "").upper()
            if not symbol:
                continue

            quantity = float(pos.get("szi", 0) or 0)
            entry_px = float(pos.get("entry_px", 0) or 0)
            current_value = float(pos.get("position_value", 0) or 0)

            positions[symbol] = {
                "quantity": quantity,
                "avg_cost": entry_px,
                "current_value": current_value,
                "unrealized_pnl": float(pos.get("unrealized_pnl", 0) or 0),
                "leverage": pos.get("leverage"),
                "liquidation_price": pos.get("liquidation_px"),
            }

        portfolio = {
            "cash": float(hyperliquid_state.get("available_balance", 0) or 0),
            "frozen_cash": float(hyperliquid_state.get("used_margin", 0) or 0),
            "total_assets": float(hyperliquid_state.get("total_equity", 0) or 0),
            "positions": positions,
        }
    else:
        portfolio = {
            "cash": base_portfolio.get("cash"),
            "frozen_cash": base_portfolio.get("frozen_cash"),
            "total_assets": base_portfolio.get("total_assets"),
            "positions": positions,
        }

    now = datetime.now(timezone.utc)

    # Legacy format variables (for backward compatibility with existing templates)
    account_state = _build_account_state(portfolio)
    market_snapshot = _build_market_snapshot(prices, positions, ordered_symbols)
    session_context = _build_session_context(account)
    sampling_data = _build_sampling_data(samples, target_symbol, sampling_interval)

    # New Alpha Arena style variables
    runtime_minutes = _calculate_runtime_minutes(account)
    current_time_utc = now.isoformat() + "Z"
    total_return_percent = _calculate_total_return_percent(account)
    available_cash = _format_currency(portfolio.get('cash'))
    total_account_value = _format_currency(portfolio.get('total_assets'))
    holdings_detail = _build_holdings_detail(positions)
    market_prices = _build_market_prices(prices, ordered_symbols, symbol_display_map)
    # Legacy format (kept for backward compatibility with old templates)
    output_format_legacy = OUTPUT_FORMAT_JSON.replace(SYMBOL_PLACEHOLDER, output_symbol_choices or "SYMBOL")

    # Hyperliquid-specific context - Get leverage settings from unified function
    # This ensures leverage values match the wallet configuration for the current environment
    if db:
        from services.hyperliquid_environment import get_leverage_settings
        try:
            leverage_settings = get_leverage_settings(db, account.id, environment)
            max_leverage = leverage_settings["max_leverage"]
            default_leverage = leverage_settings["default_leverage"]
        except Exception as e:
            logger.warning(f"Failed to get leverage settings for account {account.id}: {e}, using fallback")
            max_leverage = getattr(account, "max_leverage", 20)
            default_leverage = getattr(account, "default_leverage", 10)
    else:
        logger.warning(f"No db session provided to _build_prompt_context, using Account table fallback for leverage")
        max_leverage = getattr(account, "max_leverage", 20)
        default_leverage = getattr(account, "default_leverage", 10)

    # Override leverage with TraderPersonality if available
    if db:
        try:
            from backend.database.models import TraderPersonality
            tp = db.query(TraderPersonality).filter(
                TraderPersonality.account_id == account.id
            ).first()
            if tp and tp.preferred_leverage and tp.max_leverage:
                default_leverage = tp.preferred_leverage
                max_leverage = tp.max_leverage
                logger.debug(
                    f"[PromptCtx] 性格杠杆覆盖: default={default_leverage}x, max={max_leverage}x "
                    f"(personality={tp.display_name})"
                )
        except Exception as tp_err:
            logger.debug(f"[PromptCtx] 性格杠杆读取失败: {tp_err}")

    # Build complete output format with placeholders replaced
    output_format = OUTPUT_FORMAT_COMPLETE.replace(SYMBOL_PLACEHOLDER, output_symbol_choices or "SYMBOL").replace(MAX_LEVERAGE_PLACEHOLDER, str(max_leverage))

    # Use hyperliquid_state to determine if this is Hyperliquid trading mode
    if hyperliquid_state and environment in ("testnet", "mainnet"):
        trading_environment = f"Platform: Hyperliquid Perpetual Contracts | Environment: {environment.upper()}"

        # Read global margin mode setting
        _global_is_isolated = True
        try:
            from backend.database.models import SystemConfig
            _margin_cfg = db.query(SystemConfig).filter(SystemConfig.key == "global_margin_mode").first()
            if _margin_cfg and _margin_cfg.value == "cross":
                _global_is_isolated = False
        except Exception:
            pass

        if _global_is_isolated:
            _margin_label = "ISOLATED"
            _margin_desc = "each position has independent margin"
            _margin_risk = "Each position is isolated — one liquidation does NOT affect other positions"
        else:
            _margin_label = "CROSS"
            _margin_desc = "all positions share account margin"
            _margin_risk = "Cross margin — all positions share balance, higher capital efficiency but liquidation risk is shared"

        if environment == "mainnet":
            real_trading_warning = "⚠️ REAL MONEY TRADING - All decisions execute on live markets"
            operational_constraints = f"""- Perpetual contract trading with {_margin_label} margin ({_margin_desc})
- Maximum position size: ≤ 25% of available balance per trade
- Leverage range: {default_leverage}x to {max_leverage}x (default: {default_leverage}x)
- Margin call threshold: 80% margin usage (CRITICAL - will auto-liquidate)
- Default stop loss: -10% from entry (adjust based on leverage and volatility)
- Default take profit: +20% from entry (adjust based on risk/reward)
- Liquidation protection: NEVER exceed 70% margin usage
- Risk management: {_margin_risk}"""
        else:  # testnet
            real_trading_warning = "Testnet simulation environment (using test funds)"
            operational_constraints = f"""- Perpetual contract trading with {_margin_label} margin ({_margin_desc}, testnet mode)
- Default position size: ≤ 30% of available balance per trade
- Leverage range: {default_leverage}x to {max_leverage}x (default: {default_leverage}x)
- Margin call threshold: 80% margin usage
- Default stop loss: -8% from entry (adjust based on leverage)
- Default take profit: +15% from entry
- Liquidation protection: avoid exceeding 70% margin usage
- {_margin_risk}"""

        leverage_constraints = f"- Leverage range: {default_leverage}x to {max_leverage}x (default: {default_leverage}x)"
        margin_info = f"\nMargin Mode: {_margin_label} margin ({_margin_desc})"
    else:
        trading_environment = "Platform: Paper Trading Simulation"
        real_trading_warning = "Sandbox environment (no real funds at risk)"
        operational_constraints = """- No pyramiding or position size increases without explicit exit plan
- Default risk per trade: ≤ 20% of available cash
- Default stop loss: -5% from entry (adjust based on volatility)
- Default take profit: +10% from entry (adjust based on signals)"""
        leverage_constraints = ""
        margin_info = ""

    # Process Hyperliquid account state if provided
    if hyperliquid_state:
        total_equity = _format_currency(hyperliquid_state.get('total_equity'))
        available_balance = _format_currency(hyperliquid_state.get('available_balance'))
        used_margin = _format_currency(hyperliquid_state.get('used_margin', 0))
        margin_usage_percent = f"{hyperliquid_state.get('margin_usage_percent', 0):.1f}"
        maintenance_margin = _format_currency(hyperliquid_state.get('maintenance_margin', 0))

        # Build positions detail from Hyperliquid positions
        hl_positions = hyperliquid_state.get('positions', [])
        if hl_positions:
            pos_lines = []
            for pos in hl_positions:
                symbol = pos.get('coin', 'UNKNOWN')
                size = float(pos.get('szi', 0))
                direction = "Long" if size > 0 else "Short"
                abs_size = abs(size)
                entry_px = float(pos.get('entry_px', 0))
                unrealized_pnl = float(pos.get('unrealized_pnl', 0))
                leverage = float(pos.get('leverage', 1))
                position_max_leverage = float(pos.get('max_leverage', 10))  # Renamed to avoid conflict with account max_leverage
                margin_used = float(pos.get('margin_used', 0))
                position_value = float(pos.get('position_value', 0))
                roe = float(pos.get('return_on_equity', 0))
                funding_total = float(pos.get('cum_funding_all_time', 0))
                liquidation_px = float(pos.get('liquidation_px', 0))
                leverage_type = pos.get('leverage_type', 'cross') or 'cross'

                # Position timing information (NEW)
                opened_at_str = pos.get('opened_at_str')
                holding_duration_str = pos.get('holding_duration_str')

                # Get current market price for this symbol
                current_price = prices.get(symbol, entry_px)

                # Format values
                pnl_str = f"+${unrealized_pnl:,.2f}" if unrealized_pnl >= 0 else f"-${abs(unrealized_pnl):,.2f}"
                roe_str = f"+{roe:.2f}%" if roe >= 0 else f"{roe:.2f}%"
                funding_str = f"+${funding_total:.4f}" if funding_total >= 0 else f"-${abs(funding_total):.4f}"
                leverage_type_str = leverage_type.capitalize()

                # Calculate distance to liquidation
                if liquidation_px > 0 and current_price > 0:
                    liq_distance_pct = abs(current_price - liquidation_px) / current_price * 100
                    liq_warning = " ⚠️" if liq_distance_pct < 10 else ""
                else:
                    liq_distance_pct = 0
                    liq_warning = ""

                # Build position timing line
                timing_line = ""
                tier_tag = ""
                timeout_warn = ""
                _pos_tier = pos.get("timeframe_tier", "mid")
                _tier_label = {"short": "Short", "mid": "Mid", "long": "Long"}.get(_pos_tier, "Mid")
                tier_tag = f"[{_tier_label}] "
                if opened_at_str and holding_duration_str:
                    timing_line = f"  Opened: {opened_at_str} | Holding: {holding_duration_str}\n"
                    try:
                        from backend.config.settings import TIER_PROTECTION_PARAMS as _TPP
                        _mh_sec = _TPP.get(_pos_tier, {}).get("max_hold_sec", 0)
                        if _mh_sec > 0:
                            _opened_dt = datetime.fromisoformat(str(opened_at_str).replace("Z", "+00:00"))
                            if _opened_dt.tzinfo is None:
                                _opened_dt = _opened_dt.replace(tzinfo=timezone.utc)
                            _age_sec = (datetime.now(timezone.utc) - _opened_dt).total_seconds()
                            _max_h = _mh_sec / 3600
                            _age_h = _age_sec / 3600
                            if _age_sec > _mh_sec:
                                timeout_warn = f"  ⏰ TIMEOUT: {_age_h:.1f}h > {_max_h:.0f}h limit, MUST evaluate close!\n"
                            elif _age_sec > _mh_sec * 0.75:
                                timeout_warn = f"  ⚠️ Near timeout: {_age_h:.1f}h / {_max_h:.0f}h limit, evaluate exit soon.\n"
                    except Exception:
                        pass

                # D7: 持仓紧急程度标注
                _urgency_tag = ""
                _position_val = position_value if position_value > 0 else abs(unrealized_pnl * 10) or margin_used * leverage
                if unrealized_pnl < 0 and _position_val > 0:
                    _loss_ratio = abs(unrealized_pnl) / _position_val
                    if _loss_ratio > 0.05 or roe < -20:
                        _urgency_tag = "🔴 紧急 "
                    elif _loss_ratio > 0.02 or roe < -5:
                        _urgency_tag = "🟡 关注 "
                elif unrealized_pnl > 0 and roe > 15:
                    _urgency_tag = "✅ 良好 "

                pos_lines.append(
                    f"{_urgency_tag}- {symbol}: {tier_tag}{direction} {abs_size:.4f} units @ ${entry_px:,.2f} avg\n"
                    f"{timing_line}"
                    f"{timeout_warn}"
                    f"  Mark price: ${current_price:,.2f} | Position value: ${position_value:,.2f}\n"
                    f"  Unrealized P&L: {pnl_str} ({roe_str} ROE)\n"
                    f"  Leverage: {leverage:.0f}x {leverage_type_str} (max {position_max_leverage:.0f}x) | Margin: ${margin_used:,.2f}\n"
                    f"  Liquidation: ${liquidation_px:,.2f} ({liq_distance_pct:.1f}% away){liq_warning} | Funding: {funding_str}"
                )
            positions_detail = "\n".join(pos_lines)
        else:
            positions_detail = "No open positions"
    # Process Binance account state if provided
    elif binance_state:
        total_equity = _format_currency(binance_state.get('total_balance'))
        available_balance = _format_currency(binance_state.get('available_balance'))
        used_margin = _format_currency(binance_state.get('margin_used', 0))
        # Calculate margin usage percentage
        total_balance = float(binance_state.get('total_balance', 0) or 0)
        margin_used = float(binance_state.get('margin_used', 0) or 0)
        if total_balance > 0:
            margin_usage_percent = f"{(margin_used / total_balance) * 100:.1f}"
        else:
            margin_usage_percent = "0"
        maintenance_margin = _format_currency(binance_state.get('maintenance_margin', 0))

        # Build positions detail from Binance positions
        binance_positions = binance_state.get('positions', [])
        if binance_positions:
            pos_lines = []
            for pos in binance_positions:
                symbol = pos.get('symbol', 'UNKNOWN')
                position_amt = float(pos.get('positionAmt', 0))
                if position_amt == 0:
                    continue  # Skip empty positions

                direction = "Long" if position_amt > 0 else "Short"
                abs_size = abs(position_amt)
                entry_px = float(pos.get('avgPrice', 0))
                unrealized_pnl = float(pos.get('unRealizedProfit', 0))
                leverage = float(pos.get('leverage', 1))
                margin_used = float(pos.get('isolatedMargin', 0))
                position_value = abs_size * float(pos.get('markPrice', entry_px))

                # Get current market price
                current_price = prices.get(symbol, entry_px)

                # Format values
                pnl_str = f"+${unrealized_pnl:,.2f}" if unrealized_pnl >= 0 else f"-${abs(unrealized_pnl):,.2f}"
                roe = margin_used > 0 and (unrealized_pnl / margin_used * 100) or 0
                roe_str = f"+{roe:.2f}%" if roe >= 0 else f"{roe:.2f}%"

                # D7: 持仓紧急程度标注
                _urgency_tag = ""
                _position_val = position_value if position_value > 0 else abs(unrealized_pnl * 10) or margin_used * leverage
                if unrealized_pnl < 0 and _position_val > 0:
                    _loss_ratio = abs(unrealized_pnl) / _position_val
                    if _loss_ratio > 0.05 or roe < -20:
                        _urgency_tag = "🔴 紧急 "
                    elif _loss_ratio > 0.02 or roe < -5:
                        _urgency_tag = "🟡 关注 "
                elif unrealized_pnl > 0 and roe > 15:
                    _urgency_tag = "✅ 良好 "

                pos_lines.append(
                    f"- {symbol}: {direction} {abs_size:.4f} units @ ${entry_px:,.2f} avg\n"
                    f"  Mark price: ${current_price:,.2f} | Position value: ${position_value:,.2f}\n"
                    f"  Unrealized P&L: {pnl_str} ({roe_str} ROE)\n"
                    f"  Leverage: {leverage:.0f}x | Margin: ${margin_used:,.2f}"
                )
            positions_detail = "\n".join(pos_lines) if pos_lines else "No open positions"
        else:
            positions_detail = "No open positions"

    # Fallback when no exchange state is provided
    else:
        total_equity = "N/A"
        available_balance = "N/A"
        used_margin = "N/A"
        margin_usage_percent = "0"
        maintenance_margin = "N/A"
        positions_detail = "No open positions"

    # ============================================================================
    # RECENT TRADES HISTORY SUMMARY
    # ============================================================================
    # Build recent closed trades summary to help AI understand trading patterns
    # and avoid flip-flop behavior (rapid position reversals)
    recent_trades_summary = "No recent trade history available"
    if hyperliquid_state and environment in ("testnet", "mainnet"):
        try:
            # Get trading client to fetch recent closed trades
            from services.hyperliquid_trading_client import HyperliquidTradingClient
            from backend.database.connection import SessionLocal

            # Get account's Hyperliquid wallet configuration
            with SessionLocal() as db_session:
                from backend.database.models import HyperliquidWallet
                wallet = db_session.query(HyperliquidWallet).filter(
                    HyperliquidWallet.account_id == account.id,
                    HyperliquidWallet.environment == environment,
                    HyperliquidWallet.is_active == "true"
                ).first()

                if wallet:
                    # Decrypt private key
                    from utils.encryption import decrypt_private_key
                    try:
                        private_key = decrypt_private_key(wallet.private_key_encrypted)
                    except Exception as decrypt_error:
                        logger.error(f"Failed to decrypt private key: {decrypt_error}")
                        recent_trades_summary = "Error: Failed to decrypt wallet private key"
                        raise

                    # Initialize trading client
                    client = HyperliquidTradingClient(
                        account_id=account.id,
                        private_key=private_key,
                        environment=environment,
                        wallet_address=wallet.wallet_address
                    )

                    # Get recent closed trades (last 5)
                    recent_trades = client.get_recent_closed_trades(db_session, limit=5)

                    # Get open orders
                    open_orders = client.get_open_orders(db_session)

                    # Build recent trades section
                    trades_section = ""
                    if recent_trades:
                        trade_lines = ["Recent closed trades (last 5 positions):"]
                        for trade in recent_trades:
                            symbol = trade.get('symbol', 'UNKNOWN')
                            side = trade.get('side', 'Unknown')
                            close_time = trade.get('close_time', 'N/A')
                            close_price = trade.get('close_price', 0)
                            realized_pnl = trade.get('realized_pnl', 0)
                            direction = trade.get('direction', '')

                            pnl_str = f"+${realized_pnl:,.2f}" if realized_pnl >= 0 else f"-${abs(realized_pnl):,.2f}"
                            trade_lines.append(
                                f"- {symbol} {side}: Closed at {close_time} @ ${close_price:,.2f} | P&L: {pnl_str} | {direction}"
                            )
                        trades_section = "\n".join(trade_lines)
                    else:
                        trades_section = "Recent closed trades: No recent closed trades found"

                    # Build open orders section
                    orders_section = ""
                    if open_orders:
                        # Limit to 10 most recent orders to avoid prompt bloat
                        display_orders = open_orders[:10]
                        order_lines = [f"\nOpen orders ({len(open_orders)} pending):"]
                        for order in display_orders:
                            symbol = order.get('symbol', 'UNKNOWN')
                            direction = order.get('direction', 'Unknown')
                            order_type = order.get('order_type', 'Limit')
                            order_id = order.get('order_id', 'N/A')
                            price = order.get('price', 0)
                            size = order.get('size', 0)
                            order_value = order.get('order_value', 0)
                            reduce_only = "Yes" if order.get('reduce_only', False) else "No"
                            trigger_condition = order.get('trigger_condition')
                            order_time = order.get('order_time', 'N/A')

                            # Build trigger info
                            trigger_info = f"Trigger: {trigger_condition}" if trigger_condition else "Trigger: None"

                            order_lines.append(
                                f"- {symbol} {direction}: {order_type} Order #{order_id} @ ${price:,.2f} | "
                                f"Size: {size:.5f} | Value: ${order_value:,.2f} | Reduce Only: {reduce_only} | "
                                f"{trigger_info} | Placed: {order_time}"
                            )
                        orders_section = "\n".join(order_lines)
                    else:
                        orders_section = "\nOpen orders: No open orders"

                    # Combine both sections (Open Orders first, then Recent Trades)
                    recent_trades_summary = orders_section + "\n\n" + trades_section
                else:
                    recent_trades_summary = "Wallet not configured for this environment"
        except Exception as e:
            logger.warning(f"Failed to get recent trades summary: {e}", exc_info=True)
            recent_trades_summary = f"Error fetching trade history: {str(e)[:100]}"

    # ============================================================================ # D7: 决策链记忆 — LLM 获得连贯思维（自己过去判断的对错反思）
    # ============================================================================
    decision_chain_context = ""
    try:
        from backend.database.models import AIDecisionLog
        from backend.database.connection import AnalyticsSessionLocal
        _dc_db = AnalyticsSessionLocal()
        try:
            _recent_logs = _dc_db.query(AIDecisionLog).filter(
                AIDecisionLog.account_id == account.id,
            ).order_by(AIDecisionLog.created_at.desc()).limit(5).all()
            
            if _recent_logs:
                _dc_lines = [
                    "═══════════════════════════════",
                    "🔗 决策链记忆（你最近5次判断 — 请反思对错）",
                    "═══════════════════════════════",
                ]
                for _log in _recent_logs:
                    _age_min = int((datetime.now(timezone.utc) - _log.created_at.replace(tzinfo=timezone.utc)).total_seconds() / 60)
                    _dc_lines.append(f"\n[{-_age_min}min] {_log.symbol}: 你判断 {_log.decision} (confidence={getattr(_log, 'confidence', 0):.0%})")
                    if _log.decision_reason:
                        _dc_lines.append(f"       理由: {_log.decision_reason[:100]}")
                    _dc_lines.append(f"       当时市场: {getattr(_log, 'market_context', 'N/A')[:80]}")
                
                _dc_lines.append("\n═══════════════════════════════")
                decision_chain_context = "\n".join(_dc_lines)
                logger.info(f"[PromptCtx] 决策链记忆注入: {len(_recent_logs)}条")
        finally:
            _dc_db.close()
    except Exception as _dc_err:
        logger.debug(f"[PromptCtx] 决策链记忆构建跳过: {_dc_err}")

    # ============================================================================ # D7: 环境感知 — 时间/周几/宏观提醒
    # ============================================================================
    env_awareness = ""
    try:
        _now = datetime.now(timezone.utc)
        _weekday = ["周一","周二","周三","周四","周五","周六","周日"][_now.weekday()]
        _hour = _now.hour
        _session = "亚洲" if 0 <= _hour < 8 else ("欧洲" if 8 <= _hour < 14 else "美洲")
        _weekend = _now.weekday() >= 5
        _env_lines = [
            "═══════════════════════════════",
            f"📅 当前时间: {_weekday} {_now.strftime('%H:%M')} UTC ({_session}时段)",
        ]
        if _weekend:
            _env_lines.append("⚠️ 周末: 成交量通常偏低，谨慎开仓")
        if _hour < 2 or _hour > 22:
            _env_lines.append("🌙 低流动性时段: 滑点风险偏高，考虑缩仓")
        _env_lines.append("═══════════════════════════════")
        env_awareness = "\n".join(_env_lines)
    except Exception:
        pass

    # ============================================================================
    # K-LINE AND TECHNICAL INDICATORS PROCESSING
    # ============================================================================
    # Process K-line and technical indicator variables if template_text is provided.
    # This ensures that variables like {BTC_klines_15m}, {BTC_MACD_15m}, etc.
    # are properly populated with real data instead of showing "N/A".
    #
    # IMPORTANT: This processing MUST stay inside _build_prompt_context to ensure
    # preview and AI decision execution use the same logic.
    kline_context = {}
    if template_text:
        try:
            from backend.database.connection import SessionLocal
            variable_groups = _parse_kline_indicator_variables(template_text)
            if variable_groups:
                from services.exchange_config import get_active_exchange, get_exchange_for_account
                if account and hasattr(account, 'id'):
                    exchange = get_exchange_for_account(account.id)
                else:
                    exchange = get_active_exchange()
                logger.info(f"Using {exchange} as data source for K-line/indicators")
                
                with SessionLocal() as db:
                    kline_context = _build_klines_and_indicators_context(
                        variable_groups, db, environment, exchange=exchange
                    )
                logger.debug(f"Built K-line context with {len(kline_context)} variables")
        except Exception as e:
            logger.warning(f"Failed to build K-line context: {e}", exc_info=True)

    # ============================================================================
    # TRIGGER CONTEXT FORMATTING
    # ============================================================================
    # Format trigger context into structured text for AI prompt.
    # This tells the AI what triggered this decision (signal or scheduled).
    trigger_context_text = ""
    if trigger_context:
        trigger_type = trigger_context.get("trigger_type", "unknown")
        lines = [f"=== TRIGGER CONTEXT ===", f"trigger_type: {trigger_type}"]

        if trigger_type == "signal":
            pool_name = trigger_context.get("signal_pool_name", "Unknown")
            pool_logic = trigger_context.get("pool_logic", "OR")
            trigger_symbol = trigger_context.get("trigger_symbol", "N/A")
            lines.append(f"signal_pool_name: {pool_name}")
            lines.append(f"pool_logic: {pool_logic}")
            lines.append(f"trigger_symbol: {trigger_symbol}")

            triggered_signals = trigger_context.get("triggered_signals", [])
            if triggered_signals:
                lines.append("triggered_signals:")
                for sig in triggered_signals:
                    # Support both "signal_name" (from signal_detection_service) and "name" (fallback)
                    sig_name = sig.get("signal_name") or sig.get("name", "Unknown Signal")
                    description = sig.get("description")
                    metric = sig.get("metric", "N/A")
                    time_window = sig.get("time_window", "N/A")

                    lines.append(f"  - name: {sig_name}")
                    if description:
                        lines.append(f"    description: {description}")

                    # Special handling for taker_volume composite signal
                    if metric == "taker_volume":
                        direction = sig.get("actual_direction") or sig.get("direction", "N/A")
                        buy = sig.get("buy", 0)
                        sell = sig.get("sell", 0)
                        ratio = sig.get("ratio", 0)
                        ratio_threshold = sig.get("ratio_threshold", 1.5)
                        volume_threshold = sig.get("volume_threshold", 0)
                        # Calculate dominant side multiplier for clarity
                        if direction == "buy" and ratio > 0:
                            multiplier = ratio
                            dominant = "buyers"
                        elif direction == "sell" and ratio > 0:
                            multiplier = 1 / ratio if ratio > 0 else 0
                            dominant = "sellers"
                        else:
                            multiplier = ratio
                            dominant = "N/A"
                        lines.append(f"    metric: taker_volume")
                        lines.append(f"    direction: {direction}")
                        lines.append(f"    taker_buy: ${buy/1e6:.2f}M")
                        lines.append(f"    taker_sell: ${sell/1e6:.2f}M")
                        lines.append(f"    dominant: {dominant} {multiplier:.2f}x (threshold: {ratio_threshold}x)")
                    else:
                        # Standard single-value signal
                        operator = sig.get("operator", "N/A")
                        threshold = sig.get("threshold", "N/A")
                        actual_value = sig.get("current_value") or sig.get("actual_value", "N/A")

                        unit = _get_metric_unit(metric)
                        metric_display = f"{metric} ({unit})" if unit else metric
                        threshold_display = f"{threshold}{unit}" if unit else str(threshold)
                        value_display = f"{actual_value:.4f}{unit}" if isinstance(actual_value, (int, float)) and unit else str(actual_value)

                        lines.append(f"    metric: {metric_display}")
                        lines.append(f"    time_window: {time_window}")
                        lines.append(f"    condition: {operator} {threshold_display}")
                        lines.append(f"    current_value: {value_display}")
        elif trigger_type == "scheduled":
            interval = trigger_context.get("trigger_interval", "N/A")
            interval_min = f" ({interval/60:.1f}min)" if isinstance(interval, (int, float)) else ""
            lines.append(f"trigger_interval: {interval}s{interval_min}")

        elif trigger_type == "autonomous":
            lines.append(f"source: {trigger_context.get('source', 'full_auto')}")

        # 编排器多周期方向分析（从 Full Auto 注入）
        orch_dirs = trigger_context.get("orchestrator_directions", {})
        if orch_dirs:
            lines.append("")
            lines.append("=== MULTI-TIMEFRAME ORCHESTRATOR ANALYSIS ===")
            lines.append("以下是量化编排器对各交易对的多周期综合研判，请将其作为重要参考依据：")
            for sym, od in orch_dirs.items():
                side = od.get("side", "neutral")
                action = od.get("action", "")
                pos_pct = od.get("position_pct", 0)
                l_bias = od.get("long_bias", "neutral")
                l_conf = od.get("long_confidence", 0)
                m_bias = od.get("mid_bias", "neutral")
                m_conf = od.get("mid_confidence", 0)
                s_bias = od.get("short_bias", "neutral")
                s_conf = od.get("short_confidence", 0)
                lines.append(f"  {sym}:")
                lines.append(f"    综合方向: {side} (建议仓位: {pos_pct:.0%})")
                lines.append(f"    建议动作: {action}")
                lines.append(f"    长线: {l_bias} (置信度: {l_conf:.0%})")
                lines.append(f"    中线: {m_bias} (置信度: {m_conf:.0%})")
                lines.append(f"    短线: {s_bias} (置信度: {s_conf:.0%})")
                if side in ("long", "short"):
                    lines.append(f"    ⚡ 编排器明确建议 {'做多' if side == 'long' else '做空'}，"
                                 f"请重点评估该方向的入场机会，除非你有充分理由反对。")
            lines.append("")

        # 情报系统数据（来自 strategy_coordinator 注入）
        intelligence = trigger_context.get("intelligence")
        if intelligence:
            lines.append("")
            lines.append("=== INTELLIGENCE DATA ===")
            sent = intelligence.get("sentiment", {})
            if sent:
                lines.append(f"sentiment_index: {sent.get('index', 50):.0f}/100")
                lines.append(f"sentiment_zone: {sent.get('zone', 'neutral')}")
                lines.append(f"fear_greed_index: {sent.get('fear_greed', 50):.0f}")
            news = intelligence.get("news", {})
            if news and news.get("top_event"):
                lines.append(f"news_impact: {news.get('impact', 0):.2f}")
                lines.append(f"top_news: {news.get('top_event', '')}")
            whale = intelligence.get("whale", {})
            if whale:
                lines.append(f"whale_direction: {whale.get('direction', 0):.2f}")
                lines.append(f"whale_interpretation: {whale.get('interpretation', 'N/A')}")
            deriv = intelligence.get("derivatives", {})
            if deriv:
                lines.append(f"derivatives_signal: {deriv.get('signal', 'neutral')}")
                lines.append(f"funding_rate: {deriv.get('funding_rate', 0):.6f}")

        # 市场环境数据（来自 strategy_coordinator 注入）
        market_env = trigger_context.get("market_environment")
        if market_env:
            lines.append("")
            lines.append("=== MARKET ENVIRONMENT ===")
            macro = market_env.get("macro", {})
            if macro:
                lines.append(f"market_cycle: {macro.get('market_cycle', 'unknown')}")
                lines.append(f"cycle_confidence: {macro.get('cycle_confidence', 0):.0%}")
                lines.append(f"risk_budget: {macro.get('risk_budget', 0.5):.0%}")
            micro = market_env.get("micro", {})
            if micro:
                lines.append(f"volatility: {micro.get('volatility_regime', 'normal')} ({micro.get('volatility_value', 0):.4f})")
                lines.append(f"trend: {micro.get('trend_direction', 'neutral')} (strength={micro.get('trend_strength', 0):.2f})")
                lines.append(f"liquidity: {micro.get('liquidity_score', 1):.2f}")
            guidance = market_env.get("guidance", "")
            if guidance:
                lines.append(f"guidance: {guidance}")

        trigger_context_text = "\n".join(lines)

    # ============================================================================
    # Market Regime Classification Variables
    # ============================================================================
    # Variables provided:
    # - {market_regime} - summary of all symbols (default 5m timeframe)
    # - {market_regime_description} - indicator calculation methodology
    # - {BTC_market_regime}, {ETH_market_regime} - per-symbol (default 5m)
    # - {BTC_market_regime_1m}, {BTC_market_regime_5m}, {BTC_market_regime_15m}, {BTC_market_regime_1h}
    # - {market_regime_1m}, {market_regime_5m}, {market_regime_15m}, {market_regime_1h}
    market_regime_context = {}

    # Indicator calculation description for AI understanding
    market_regime_context["market_regime_description"] = """Market Regime Indicator Definitions:
- cvd_ratio: CVD / (Taker Buy + Taker Sell). Positive = net buying pressure, negative = net selling
- oi_delta: Open Interest change percentage over the period
- taker: Taker Buy/Sell ratio. >1 = aggressive buying, <1 = aggressive selling
- rsi: RSI(14) momentum indicator. >70 overbought, <30 oversold
- price_atr: (Close - Open) / ATR. Measures price movement relative to volatility

Regime Types:
- breakout: Strong directional move with volume confirmation
- absorption: Large orders absorbed without price impact (potential reversal)
- stop_hunt: Wick beyond range then reversal (liquidity grab)
- exhaustion: Extreme RSI with diverging CVD (trend weakening)
- trap: Price breaks level but CVD/OI diverge (false breakout)
- continuation: Trend continuation with aligned indicators
- noise: No clear pattern, low conviction"""

    if db:
        try:
            from services.market_regime_service import get_market_regime
            supported_timeframes = ["1m", "5m", "15m", "1h"]

            def format_regime_text(symbol, tf, result):
                """Format regime result with symbol and timeframe context"""
                regime = result['regime']
                direction = result['direction']
                conf = result['confidence']
                ind = result.get('indicators', {})
                if not ind:
                    return f"[{symbol}/{tf}] {regime} ({direction}) conf={conf:.2f} | insufficient data"
                return (
                    f"[{symbol}/{tf}] {regime} ({direction}) conf={conf:.2f} | "
                    f"cvd_ratio={ind.get('cvd_ratio', 0):.3f}, oi_delta={ind.get('oi_delta', 0):.3f}%, "
                    f"taker={ind.get('taker_ratio', 1):.2f}, rsi={ind.get('rsi', 50):.1f}"
                )

            for tf in supported_timeframes:
                tf_regime_lines = []
                for symbol in ordered_symbols:
                    regime_result = get_market_regime(db, symbol, tf)
                    regime_text = format_regime_text(symbol, tf, regime_result)
                    market_regime_context[f"{symbol}_market_regime_{tf}"] = regime_text
                    tf_regime_lines.append(f"- {regime_text}")
                market_regime_context[f"market_regime_{tf}"] = "\n".join(tf_regime_lines) if tf_regime_lines else "N/A"

            # Default variables (5m) for backward compatibility
            for symbol in ordered_symbols:
                market_regime_context[f"{symbol}_market_regime"] = market_regime_context.get(f"{symbol}_market_regime_5m", "N/A")
            market_regime_context["market_regime"] = market_regime_context.get("market_regime_5m", "N/A")

        except Exception as e:
            logger.warning(f"Failed to get market regime data: {e}")
            market_regime_context["market_regime"] = "N/A"
    else:
        market_regime_context["market_regime"] = "N/A"

    # ============================================================================
    # UNIFIED DATA POOL & STRATEGY ORCHESTRATOR INTEGRATION
    # ============================================================================
    # Use unified data pool to ensure data consistency across all modules.
    # Integrate strategy orchestrator for long-term planning and short-term tactics.
    strategy_context = {}
    try:
        from services.unified_data_pool import get_unified_data_pool
        
        data_pool = get_unified_data_pool()
        
        # Capture unified snapshot for consistent data
        snapshot = data_pool.capture_snapshot(
            symbols=ordered_symbols[:5],  # Top 5 symbols
            account_id=account.id if account else None,
            environment=environment,
            include_klines=True,
            include_strategy=True,
        )
        
        if snapshot:
            # Get strategy context from snapshot
            strat_ctx = data_pool.get_strategy_context()
            
            # Build strategy orchestrator summary
            strategy_lines = [
                "=== 策略编排层分析 ===",
                "",
                "【中长期规划】",
                f"市场周期: {strat_ctx.get('market_cycle', 'unknown')} (置信度: {strat_ctx.get('cycle_confidence', '0%')})",
                f"仓位偏向: {strat_ctx.get('position_bias', 'neutral')}",
                f"建议杠杆: {strat_ctx.get('recommended_leverage', 10)}x",
                f"最大仓位: {strat_ctx.get('max_position_size', '25%')}",
                f"每日止损: {strat_ctx.get('max_daily_loss', '5%')}",
                f"关键支撑: {strat_ctx.get('key_support', 'N/A')}",
                f"关键阻力: {strat_ctx.get('key_resistance', 'N/A')}",
            ]
            
            if strat_ctx.get('regime_warning'):
                strategy_lines.append(strat_ctx['regime_warning'])
            
            strategy_lines.extend([
                "",
                "【短期战术】",
                f"建议动作: {strat_ctx.get('tactical_action', 'wait')}",
                f"战术置信度: {strat_ctx.get('tactical_confidence', '0%')}",
                f"入场时机: {strat_ctx.get('entry_timing', 'standard')}",
                f"市场状态: {strat_ctx.get('market_condition', 'quiet')}",
                f"建议止损: {strat_ctx.get('suggested_stop_loss', 'N/A')}",
                f"建议止盈: {strat_ctx.get('suggested_take_profit', 'N/A')}",
            ])
            
            strategy_context["strategy_orchestrator_summary"] = "\n".join(strategy_lines)
            strategy_context["market_cycle"] = strat_ctx.get('market_cycle', 'unknown')
            strategy_context["position_bias"] = strat_ctx.get('position_bias', 'neutral')
            strategy_context["tactical_action"] = strat_ctx.get('tactical_action', 'wait')
            strategy_context["tactical_confidence"] = strat_ctx.get('tactical_confidence', '0%')
            strategy_context["strategy_snapshot_id"] = snapshot.snapshot_id

            _bias = strat_ctx.get('position_bias', 'neutral')
            if _bias in ('long', 'bullish'):
                strategy_context["direction_constraint"] = (
                    "【方向约束: long_only】系统多周期分析偏多，所有标的只允许 buy 或 hold，禁止 sell 开空。")
            elif _bias in ('short', 'bearish'):
                strategy_context["direction_constraint"] = (
                    "【方向约束: short_only】系统多周期分析偏空，所有标的只允许 sell 或 hold，禁止 buy 开多。")
            else:
                strategy_context["direction_constraint"] = (
                    "【方向约束: both】系统多周期分析方向中性，buy/sell 均可，但需独立高置信度支撑。")
            
            # Get factors summary for primary symbol
            primary_symbol = ordered_symbols[0] if ordered_symbols else "BTC"
            strategy_context["factors_summary"] = data_pool.get_factors_summary(primary_symbol)
            
            logger.info(f"[StrategyOrchestrator] 策略上下文已生成: 周期={strat_ctx.get('market_cycle')}, 偏向={strat_ctx.get('position_bias')}")
        else:
            strategy_context["strategy_orchestrator_summary"] = "策略编排层: 数据快照不可用"
            
    except Exception as strat_err:
        logger.warning(f"Failed to integrate strategy orchestrator: {strat_err}")
        strategy_context["strategy_orchestrator_summary"] = f"策略编排层: 集成错误 - {str(strat_err)[:50]}"

    # 保证默认/DB 模板中的占位符始终存在（编排快照失败时避免 format() KeyError）
    strategy_context.setdefault("factors_summary", "（暂无技术面因子摘要）")
    strategy_context.setdefault("strategy_orchestrator_summary", "策略编排层: 无数据")

    # ============================================================================
    # FACTOR ENGINE & ADAPTIVE EXECUTION INTEGRATION
    # ============================================================================
    # Integrate factor engine for quantitative signals and adaptive executor
    # for dynamic stop-loss/take-profit and position sizing recommendations.
    adaptive_context = {}
    try:
        from services.ai_decision_integration import (
            build_factor_context,
            build_execution_context,
            format_factor_summary,
            format_execution_summary,
            add_adaptive_context_to_prompt
        )
        from services.factor_engine import factor_engine, get_factor_weighting, MarketRegime
        
        # Get K-line data for factor calculation
        klines_data = {}
        if db:
            from backend.database.models import CryptoKline
            from backend.database.connection import MarketSessionLocal
            from datetime import timedelta
            # Use naive UTC to match SQLite storage (no timezone info)
            lookback_time = datetime.now(timezone.utc) - timedelta(hours=24)
            
            mkt_db = MarketSessionLocal()
            try:
                for symbol in ordered_symbols[:12]:  # D7: 因子扩展到12品种（原5→12，利用缓存）
                    try:
                        # M1 收口：经 kline_data_service 读数据中心存储
                        from backend.services.kline_data_service import kline_service as _ks
                        rows = _ks.get_klines_from_db(symbol, "5m", 288) or []
                        if rows:
                            import pandas as pd
                            klines_data[symbol] = pd.DataFrame([{
                                'open': float(k['open']),
                                'high': float(k['high']),
                                'low': float(k['low']),
                                'close': float(k['close']),
                                'volume': float(k.get('volume') or 0),
                                'timestamp': k['timestamp']
                            } for k in rows])
                    except Exception as kl_err:
                        logger.debug(f"Failed to get K-lines for {symbol}: {kl_err}")
            finally:
                mkt_db.close()
        
        # Build adaptive context for all symbols
        factor_summaries = []
        for symbol in ordered_symbols[:12]:  # D7: 扩展因子摘要到12品种
            price = prices.get(symbol, 0)
            klines = klines_data.get(symbol)
            
            if price <= 0:
                continue
            
            try:
                # Build factor context
                factor_ctx = build_factor_context(symbol, klines)
                
                # Calculate ATR for execution context
                atr = price * 0.02  # Default 2%
                if klines is not None and not klines.empty:
                    try:
                        atr_value = factor_engine.compute_atr(klines)
                        if atr_value and atr_value > 0:
                            atr = atr_value
                    except Exception:
                        pass
                
                # Infer direction from positions or factor context
                _inferred_dir = 'long'
                try:
                    _hl_positions = (hyperliquid_state or {}).get("positions", [])
                    for _hp in _hl_positions:
                        _hp_sym = (_hp.get("coin") or _hp.get("symbol") or "").upper()
                        if _hp_sym == symbol.upper():
                            _szi = float(_hp.get("szi", 0))
                            if _szi < 0:
                                _inferred_dir = 'short'
                            break
                    else:
                        for _pp in (portfolio or {}).get("positions", []):
                            if (_pp.get("symbol") or "").upper() == symbol.upper():
                                _qty = float(_pp.get("quantity", 0))
                                if _qty < 0:
                                    _inferred_dir = 'short'
                                break
                except Exception:
                    pass

                exec_ctx = build_execution_context(
                    symbol, price, _inferred_dir, atr,
                    factor_ctx.market_regime,
                    factor_ctx.regime_confidence
                )
                
                # Store per-symbol adaptive parameters
                adaptive_context[f"{symbol}_factor_regime"] = factor_ctx.market_regime
                adaptive_context[f"{symbol}_regime_confidence"] = f"{factor_ctx.regime_confidence:.1%}"
                adaptive_context[f"{symbol}_position_size_rec"] = f"{exec_ctx.position_size_pct:.1%}"
                adaptive_context[f"{symbol}_stop_loss_rec"] = f"{exec_ctx.stop_loss_pct:.2%}"
                adaptive_context[f"{symbol}_take_profit_rec"] = f"{exec_ctx.take_profit_pct:.2%}"
                adaptive_context[f"{symbol}_risk_reward"] = f"{exec_ctx.risk_reward_ratio:.2f}"
                
                # V3 整合: 决策融合引擎 — 将因子信号+编排器+仓位融合为建议动作
                try:
                    from backend.services.ai_decision_integration import compute_fusion_decision
                    _pos_side = _inferred_dir  # 'long' or 'short'
                    _orch_action = (trigger_context or {}).get("orchestrator_action")
                    _fusion = compute_fusion_decision(
                        symbol=symbol, klines=klines,
                        position_side=_pos_side,
                        orchestrator_action=_orch_action,
                    )
                    if _fusion:
                        adaptive_context[f"{symbol}_fusion_action"] = _fusion["action"]
                        adaptive_context[f"{symbol}_fusion_confidence"] = f"{_fusion['confidence']:.2f}"
                        adaptive_context[f"{symbol}_fusion_reasoning"] = _fusion.get("reasoning", "")
                except Exception:
                    pass
                
                factor_summaries.append(
                    f"- {symbol}: {factor_ctx.market_regime} (置信度: {factor_ctx.regime_confidence:.0%}) | "
                    f"仓位: {exec_ctx.position_size_pct:.0%} | RR: {exec_ctx.risk_reward_ratio:.1f}"
                )
            except Exception as symbol_err:
                logger.debug(f"Failed to build adaptive context for {symbol}: {symbol_err}")
        
        # Build overall adaptive trading summary
        if factor_summaries:
            adaptive_context["adaptive_trading_summary"] = "=== 自适应交易参数 ===\n" + "\n".join(factor_summaries)
            adaptive_context["factor_engine_status"] = f"因子引擎活跃: {len(factor_summaries)}个品种已分析"
        else:
            adaptive_context["adaptive_trading_summary"] = "=== 自适应交易参数 ===\n暂无可用数据"
            adaptive_context["factor_engine_status"] = "因子引擎: 数据不足"
    except ImportError as import_err:
        logger.debug(f"Factor engine integration not available: {import_err}")
        adaptive_context["adaptive_trading_summary"] = "N/A"
        adaptive_context["factor_engine_status"] = "因子引擎未启用"
    except Exception as adaptive_err:
        logger.warning(f"Failed to build adaptive context: {adaptive_err}")
        adaptive_context["adaptive_trading_summary"] = "N/A"
        adaptive_context["factor_engine_status"] = f"因子引擎错误: {str(adaptive_err)[:50]}"

    # ══════════════════════════════════════════════════ # D7: 量化因子引导 — 因子引擎→LLM 结构化指导
    # ══════════════════════════════════════════════════
    factor_guidance = ""
    try:
        from services.ai_decision_integration import build_factor_guidance_for_prompt
        factor_guidance = build_factor_guidance_for_prompt(
            symbols=ordered_symbols,
            klines_data=klines_data if 'klines_data' in dir() else {},
            prices=prices,
        )
    except Exception as _fg_err:
        logger.warning(f"[PromptCtx] 因子引导构建失败: {_fg_err}")

    # ============================================================================ # Intelligence Signal — 情报信号融合（自动注入，无需 trigger_context 传递）
    # ============================================================================
    intelligence_signal_text = ""
    try:
        from backend.services.intelligence_signal_engine import intelligence_signal_engine
        primary_symbol = ordered_symbols[0] if ordered_symbols else "BTC"
        intel_signal = intelligence_signal_engine.compute_trading_signal(primary_symbol)
        intelligence_signal_text = intel_signal.to_prompt_text()
        logger.debug(f"[AI] 情报信号注入: {primary_symbol} → {intel_signal.direction} ({intel_signal.confidence}%)")
    except Exception as intel_err:
        logger.debug(f"[AI] 情报信号注入跳过: {intel_err}")
        intelligence_signal_text = "=== INTELLIGENCE TRADING SIGNAL ===\nstatus: unavailable"

    context = {
        # Legacy variables (for Default prompt and backward compatibility)
        "account_state": account_state,
        "market_snapshot": market_snapshot,
        "session_context": session_context,
        "sampling_data": sampling_data,
        "decision_task": DECISION_TASK_TEXT,
        "output_format": output_format,
        "prices_json": json.dumps(prices, indent=2, sort_keys=True),
        "portfolio_json": json.dumps(portfolio, indent=2, sort_keys=True),
        "portfolio_positions_json": json.dumps(positions, indent=2, sort_keys=True),
        "news_section": news_section,
        "account_name": account.name,
        "model_name": account.model or "",
        # New Alpha Arena style variables (for Pro prompt)
        "runtime_minutes": runtime_minutes,
        "current_time_utc": current_time_utc,
        "total_return_percent": total_return_percent,
        "available_cash": available_cash,
        "total_account_value": total_account_value,
        "holdings_detail": positions_detail if hyperliquid_state else holdings_detail,
        "market_prices": market_prices,
        "selected_symbols_csv": selected_symbols_csv,
        "selected_symbols_detail": selected_symbols_detail,
        "selected_symbols_count": len(ordered_symbols),
        # Hyperliquid-specific variables
        "trading_environment": trading_environment,
        "real_trading_warning": real_trading_warning,
        "operational_constraints": operational_constraints,
        "leverage_constraints": leverage_constraints,
        "margin_info": margin_info,
        "environment": environment,
        "max_leverage": max_leverage,
        "default_leverage": default_leverage,
        # Hyperliquid account state (dynamic from API)
        "total_equity": total_equity,
        "available_balance": available_balance,
        "used_margin": used_margin,
        "margin_usage_percent": margin_usage_percent,
        "maintenance_margin": maintenance_margin,
        "positions_detail": positions_detail,
        # Recent trades history (NEW - helps AI understand trading patterns)
        "recent_trades_summary": recent_trades_summary,
        # D7: 决策链记忆 — LLM 获得短期记忆连贯性
        "decision_chain": decision_chain_context,
        # D7: 环境感知 — 时间/周几/流动性提醒
        "env_awareness": env_awareness,
        # D7: 量化因子引导 — 因子引擎计算的结构化信号
        "factor_guidance": factor_guidance,
        # Trigger context (signal or scheduled trigger information)
        "trigger_context": trigger_context_text,
        # Intelligence signal (情报信号融合 — 费率/OI/清算/鲸鱼/新闻综合方向)
        "intelligence_signal": intelligence_signal_text,
        # K-line and technical indicator variables (dynamically generated)
        **kline_context,
        # Market Regime classification variables (multi-timeframe)
        **market_regime_context,
        # Factor engine and adaptive execution variables
        **adaptive_context,
        # Strategy orchestrator variables (NEW - long-term planning + short-term tactics)
        **strategy_context,
    }

    # P1-2: 注入置信度校准反馈 — 让 LLM 知道自己过去的置信度预测质量
    try:
        context["confidence_calibration"] = _get_confidence_calibration_report()
    except Exception as _cal_err:
        logger.debug(f"[PromptCtx] 置信度校准报告获取失败: {_cal_err}")
        context["confidence_calibration"] = ""

    # 注入仓位管理器的交易员状态上下文（含性格档案）
    try:
        from backend.services.position_memory_manager import position_manager
        trader_state_ctx = position_manager.get_ai_context(db, account.id)
        context["trader_mental_state"] = trader_state_ctx or ""
    except Exception as pm_err:
        logger.debug(f"[PromptCtx] 仓位管理器上下文获取失败: {pm_err}")
        context["trader_mental_state"] = ""

    # 注入交易员性格角色扮演指令
    try:
        from backend.database.models import TraderPersonality
        tp = db.query(TraderPersonality).filter(
            TraderPersonality.account_id == account.id
        ).first()
        if tp and tp.custom_prompt:
            context["trader_personality"] = tp.custom_prompt
        elif tp:
            lines = []
            if tp.benchmark_trader:
                lines.append(f"你正在模拟 {tp.benchmark_trader} 的交易风格。")
            if tp.description:
                lines.append(tp.description)
            if tp.special_skills:
                lines.append(f"你的专长: {tp.special_skills}")
            context["trader_personality"] = "\n".join(lines)
        else:
            context["trader_personality"] = ""
    except Exception as tp_err:
        logger.debug(f"[PromptCtx] 性格注入失败: {tp_err}")
        context["trader_personality"] = ""

    # ══════════════════════════════════════════════════ # K 线与技术指标数据注入（主动模式，不依赖模板变量） # 为所有监控 symbol 生成 15m/1h/4h K 线摘要 + 关键指标
    # ══════════════════════════════════════════════════
    try:
        from backend.database.models import CryptoKline
        from backend.database.connection import MarketSessionLocal
        from services.exchange_config import get_active_exchange, get_exchange_for_account
        import pandas as pd
        import numpy as np

        _kline_exchange = get_exchange_for_account(account.id) if account and hasattr(account, 'id') else get_active_exchange()
        _kline_sections = []

        mkt_db = MarketSessionLocal() if db else None
        try:
            for _sym in ordered_symbols[:3]:  # 最多3个symbol
                _sym_lines = [f"\n--- {_sym} K线与技术指标 ---"]
                for _period, _limit in [("15m", 96), ("1h", 48), ("4h", 24)]:
                    _lookback_dt = datetime.now(timezone.utc) - timedelta(hours={"15m": 24, "1h": 48, "4h": 96}[_period])
                    _lookback_ts = int(_lookback_dt.timestamp())
                    # M1 收口：经 kline_data_service 读数据中心存储
                    try:
                        from backend.services.kline_data_service import kline_service as _ks2
                        _raw = _ks2.get_klines_from_db(
                            _sym, _period, _limit, exchange=_kline_exchange
                        ) or []
                    except Exception:
                        _raw = []

                if not _raw:
                    continue

                # 构建 DataFrame
                _df = pd.DataFrame([{
                    'open': float(k['open']),
                    'high': float(k['high']),
                    'low': float(k['low']),
                    'close': float(k['close']),
                    'volume': float(k.get('volume') or 0),
                } for k in reversed(_raw)])

                _c = _df['close'].values
                _h = _df['high'].values
                _l = _df['low'].values
                _v = _df['volume'].values
                _cur = float(_c[-1])
                _prev = float(_c[-2]) if len(_c) >= 2 else _cur

                # K线走势摘要（最近10根）
                _recent = _df.tail(10)
                _kline_lines = []
                for _i, _row in _recent.iterrows():
                    _chg = (_row['close'] - _row['open']) / _row['open'] * 100 if _row['open'] > 0 else 0
                    _dir = "+" if _row['close'] >= _row['open'] else "-"
                    _kline_lines.append(f"  {_dir} O:{_row['open']:.2f} H:{_row['high']:.2f} L:{_row['low']:.2f} C:{_row['close']:.2f} ({_dir}{abs(_chg):.2f}%) Vol:{_row['volume']:,.0f}")

                # 技术指标计算
                _indicators = []
                # RSI(14)
                if len(_c) >= 15:
                    _delta = np.diff(_c)
                    _gain = np.where(_delta > 0, _delta, 0)
                    _loss = np.where(_delta < 0, -_delta, 0)
                    _avg_gain = np.mean(_gain[-14:])
                    _avg_loss = np.mean(_loss[-14:])
                    _rs = _avg_gain / _avg_loss if _avg_loss > 0 else 100
                    _rsi = 100 - (100 / (1 + _rs))
                    _indicators.append(f"RSI(14)={_rsi:.1f}")

                # EMA(9), EMA(21)
                if len(_c) >= 21:
                    _ema9 = float(pd.Series(_c).ewm(span=9, adjust=False).mean().iloc[-1])
                    _ema21 = float(pd.Series(_c).ewm(span=21, adjust=False).mean().iloc[-1])
                    _ema_trend = "看多" if _ema9 > _ema21 else "看空"
                    _indicators.append(f"EMA9={_ema9:.2f} EMA21={_ema21:.2f} 趋势={_ema_trend}")

                # ATR(14)
                if len(_h) >= 15:
                    _tr = np.maximum(_h[1:] - _l[1:], np.maximum(abs(_h[1:] - _c[:-1]), abs(_l[1:] - _c[:-1])))
                    _atr = float(np.mean(_tr[-14:]))
                    _atr_pct = _atr / _cur * 100 if _cur > 0 else 0
                    _indicators.append(f"ATR(14)={_atr:.2f} ({_atr_pct:.2f}%)")

                # MACD(12,26,9)
                if len(_c) >= 35:
                    _ema12 = pd.Series(_c).ewm(span=12, adjust=False).mean()
                    _ema26 = pd.Series(_c).ewm(span=26, adjust=False).mean()
                    _macd_line = (_ema12 - _ema26).iloc[-1]
                    _signal = (_ema12 - _ema26).ewm(span=9, adjust=False).mean().iloc[-1]
                    _hist = _macd_line - _signal
                    _indicators.append(f"MACD={_macd_line:.4f} Signal={_signal:.4f} Hist={_hist:.4f}")

                # 布林带(20,2)
                if len(_c) >= 20:
                    _ma20 = float(np.mean(_c[-20:]))
                    _std20 = float(np.std(_c[-20:]))
                    _bb_upper = _ma20 + 2 * _std20
                    _bb_lower = _ma20 - 2 * _std20
                    _bb_pos = (_cur - _bb_lower) / (_bb_upper - _bb_lower) * 100 if _bb_upper != _bb_lower else 50
                    _indicators.append(f"BOLL上={_bb_upper:.2f} 中={_ma20:.2f} 下={_bb_lower:.2f} 位置={_bb_pos:.0f}%")

                # 成交量分析
                _vol_ma = float(np.mean(_v[-20:])) if len(_v) >= 20 else float(np.mean(_v))
                _vol_ratio = float(_v[-1]) / _vol_ma if _vol_ma > 0 else 1.0
                _indicators.append(f"Vol={float(_v[-1]):,.0f} 均量={_vol_ma:,.0f} 比率={_vol_ratio:.1f}x")

                # 区间统计
                _period_high = float(np.max(_h))
                _period_low = float(np.min(_l))
                _period_chg = (_cur - float(_c[0])) / float(_c[0]) * 100 if float(_c[0]) > 0 else 0

                _sym_lines.append(f"  [{_period}] 最近{len(_df)}根 最新={_cur:.2f} 变化={_period_chg:+.2f}% 高={_period_high:.2f} 低={_period_low:.2f}")
                _sym_lines.append(f"  指标: {' | '.join(_indicators)}")
                _sym_lines.append(f"  最近K线:")
                _sym_lines.extend(_kline_lines[-5:])  # 最近5根

            if len(_sym_lines) > 1:
                _kline_sections.append("\n".join(_sym_lines))
        finally:
            if mkt_db:
                mkt_db.close()

        if _kline_sections:
            context["kline_technical_analysis"] = "\n".join(_kline_sections)
            logger.info(f"[PromptCtx] K线技术分析注入成功: {len(_kline_sections)} symbols")
        else:
            context["kline_technical_analysis"] = "暂无K线数据"
    except Exception as _kl_err:
        logger.debug(f"[PromptCtx] K线技术分析注入跳过: {_kl_err}")
        context["kline_technical_analysis"] = ""

    # ══════════════════════════════════════════════════ # RAG 历史类比注入（ExperienceRetriever → prompt） # 从 ChromaDB 语义检索历史交易决策、策略教训、 # 交易智慧、交易记忆和静态知识库，注入AI决策上下文
    # ══════════════════════════════════════════════════
    try:
        from backend.services.experience_retriever import experience_retriever

        regime_for_rag = None
        if trigger_context:
            _mkt_env = trigger_context.get("market_environment", {})
            if _mkt_env:
                _micro = _mkt_env.get("micro", {})
                regime_for_rag = _micro.get("volatility_regime", None)

        rag_context = experience_retriever.format_for_prompt(
            db=db,
            symbols=ordered_symbols,
            regime=regime_for_rag,
        )
        if rag_context:
            context["historical_analogies"] = rag_context
            logger.info(
                f"[PromptCtx] RAG历史类比注入成功: "
                f"{len(rag_context)}字符, symbols={ordered_symbols[:3]}"
            )
        else:
            context["historical_analogies"] = ""
    except Exception as rag_err:
        logger.debug(f"[PromptCtx] RAG历史类比注入跳过: {rag_err}")
        context["historical_analogies"] = ""

    # ══════════════════════════════════════════════════
    # S2: 决策性能历史上下文注入 (DecisionPerformanceContext) # 让 LLM 看到当前 symbol/tier/nature 组合的历史实盘表现
    # ══════════════════════════════════════════════════
    try:
        if db is not None and ordered_symbols:
            from backend.services.decision_performance_context import get_compact_context
            perf_parts = []
            for sym in ordered_symbols[:5]:  # 最多 5 个币种
                ctx = get_compact_context(db, symbol=sym)
                if ctx:
                    perf_parts.append(ctx)
            if perf_parts:
                context["decision_performance_history"] = "\n---\n".join(perf_parts)
                logger.info(
                    f"[PromptCtx] DecisionPerformanceContext 注入成功: "
                    f"{len(perf_parts)} symbols"
                )
            else:
                context["decision_performance_history"] = ""
        else:
            context["decision_performance_history"] = ""
    except Exception as perf_err:
        logger.debug(f"[PromptCtx] DecisionPerformanceContext 注入跳过: {perf_err}")
        context["decision_performance_history"] = ""

    # Fix 5: 教训回流闭环 —— 把 StrategyMemory.key_lessons 注入 prompt # 这是"学习→进步"闭环的关键缺失环： # decision_feedback_service 写了 71 条 lessons 到 DB，但开仓时从未读回来。
    try:
        context["strategy_lessons"] = _load_strategy_lessons(db, ordered_symbols)
    except Exception as lessons_err:
        logger.debug(f"[PromptCtx] 教训回流注入跳过: {lessons_err}")
        context["strategy_lessons"] = ""

    return context


def _load_strategy_lessons(db: Optional[Session], symbols: List[str], limit: int = 8) -> str:
    """从 StrategyMemory.key_lessons 加载最近教训，格式化为 prompt 段落。

    只取该账户策略的教训，按相关性（symbol 匹配）和时效性排序。
    """
    if not db or not symbols:
        return ""
    try:
        from backend.database.models import StrategyMemory
        mems = (
            db.query(StrategyMemory)
            .filter(StrategyMemory.total_trades >= 3)
            .order_by(StrategyMemory.updated_at.desc().nullslast())
            .limit(30)
            .all()
        )
        if not mems:
            return ""
        lines = []
        sym_set = {s.upper() for s in symbols}
        # 优先匹配当前 symbols 的教训，其次通用教训
        scored = []
        for m in mems:
            lessons = m.key_lessons if isinstance(m.key_lessons, list) else []
            if not lessons:
                continue
            # updated_at 转 epoch 用于排序（None → 0）
            ts = m.updated_at.timestamp() if m.updated_at else 0
            for l in lessons[-3:]:  # 每策略只取最近3条
                if not isinstance(l, dict):
                    continue
                sym = str(l.get("symbol", "")).upper()
                score = 2 if sym in sym_set else (1 if not sym or sym == "*" else 0)
                scored.append((score, ts, l))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        seen = set()
        for _, _, l in scored[:limit]:
            lesson_text = str(l.get("lesson", ""))[:120]
            if not lesson_text or lesson_text in seen:
                continue
            seen.add(lesson_text)
            sym = l.get("symbol", "*")
            ltype = l.get("type", "lesson")
            sev = l.get("severity", "")
            tag = f"[{ltype}" + (f"/{sev}" if sev else "") + "]"
            lines.append(f"- {tag} {sym}: {lesson_text}")
        if not lines:
            return ""
        return "## 历史教训（来自交易复盘，决策时请参考）\n" + "\n".join(lines)
    except Exception:
        return ""


def _is_default_api_key(api_key: str) -> bool:
    """Check if the API key is a default/placeholder key that should be skipped"""
    return api_key in DEMO_API_KEYS


def _get_portfolio_data(db: Session, account: Account) -> Dict:
    """Get current portfolio positions and values"""
    positions = db.query(Position).filter(
        Position.account_id == account.id,
        Position.market == "CRYPTO"
    ).all()
    
    portfolio = {}
    for pos in positions:
        if float(pos.quantity) > 0:
            portfolio[pos.symbol] = {
                "quantity": float(pos.quantity),
                "avg_cost": float(pos.avg_cost),
                "current_value": float(pos.quantity) * float(pos.avg_cost)
            }
    
    return {
        "cash": float(account.current_cash),
        "frozen_cash": float(account.frozen_cash),
        "positions": portfolio,
        "total_assets": float(account.current_cash) + calc_positions_value(db, account.id)
    }


def build_chat_completion_endpoints(base_url: str, model: Optional[str] = None) -> List[str]:
    """Build a list of possible chat completion endpoints for an OpenAI-compatible API.

    Supports Deepseek-specific behavior where both `/chat/completions` and `/v1/chat/completions`
    might be valid, depending on how the base URL is configured.
    Returns:
        List of decision dictionaries (one per symbol action) or None if generation failed.
    """
    if not base_url:
        return []

    normalized = base_url.strip().rstrip('/')
    if not normalized:
        return []

    endpoints: List[str] = []
    base_lower = normalized.lower()
    endpoints.append(f"{normalized}/chat/completions")

    is_deepseek = "deepseek.com" in base_lower

    if is_deepseek:
        # Deepseek 官方同时支持 https://api.deepseek.com/chat/completions 和 /v1/chat/completions。
        if base_lower.endswith('/v1'):
            without_v1 = normalized[:-3]
            endpoints.append(f"{without_v1}/chat/completions")
        else:
            endpoints.append(f"{normalized}/v1/chat/completions")

    # Use dict to preserve order while removing duplicates
    deduped = list(dict.fromkeys(endpoints))
    return deduped


# [refactor] _extract_text_from_message / _extract_reasoning_content_safe 已迁移到 # backend.services.llm_reasoning_helper。本文件顶部已 import 并赋同名别名
# （_extract_text_from_message = extract_text_from_message），历史调用点直接解析到别名， # 不再保留 wrapper，避免两处维护。


def _apply_legacy_injections(tpl_text: str) -> str:
    """Apply string-replace injections for legacy prompt templates.
    
    This is the OLD behavior preserved for backward compatibility.
    Non-legacy templates skip this entirely — they declare required_placeholders.
    
    Returns modified template text.
    """
    # Factor engine + adaptive trading
    if (
        "{adaptive_trading_summary}" not in tpl_text
        and "{factors_summary}" not in tpl_text
    ):
        _inject = (
            "\n\n=== 因子引擎与自适应参数（系统自动注入） ===\n"
            "{factor_engine_status}\n\n{adaptive_trading_summary}\n\n"
            "=== 技术面因子摘要 ===\n{factors_summary}\n"
        )
        if "=== 输出格式 ===" in tpl_text:
            tpl_text = tpl_text.replace(
                "=== 输出格式 ===", _inject + "\n=== 输出格式 ===", 1
            )
        else:
            tpl_text = tpl_text + _inject

    # RAG historical analogies
    if "{historical_analogies}" not in tpl_text:
        _rag_inject = (
            "\n\n=== 历史类比与知识参考（RAG 语义检索） ===\n"
            "{historical_analogies}\n"
        )
        if "=== 输出格式 ===" in tpl_text:
            tpl_text = tpl_text.replace(
                "=== 输出格式 ===", _rag_inject + "\n=== 输出格式 ===", 1
            )
        else:
            tpl_text = tpl_text + _rag_inject

    # K-line technical analysis
    if "{kline_technical_analysis}" not in tpl_text:
        _kl_inject = (
            "\n\n=== K 线与技术指标分析（系统实时计算） ===\n"
            "{kline_technical_analysis}\n"
        )
        if "=== 输出格式 ===" in tpl_text:
            tpl_text = tpl_text.replace(
                "=== 输出格式 ===", _kl_inject + "\n=== 输出格式 ===", 1
            )
        else:
            tpl_text = tpl_text + _kl_inject

    # Confidence calibration
    if "{confidence_calibration}" not in tpl_text:
        _cal_inject = "\n\n{confidence_calibration}\n"
        if "=== 输出格式 ===" in tpl_text:
            tpl_text = tpl_text.replace(
                "=== 输出格式 ===", _cal_inject + "\n=== 输出格式 ===", 1
            )
        else:
            tpl_text = tpl_text + _cal_inject

    return tpl_text


def call_ai_for_decision(
    db: Session,
    account: Account,
    portfolio: Dict,
    prices: Dict[str, float],
    samples: Optional[List] = None,
    target_symbol: Optional[str] = None,
    symbols: Optional[List[str]] = None,
    hyperliquid_state: Optional[Dict[str, Any]] = None,
    binance_state: Optional[Dict[str, Any]] = None,
    symbol_metadata: Optional[Dict[str, Any]] = None,
    trigger_context: Optional[Dict[str, Any]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Call AI model API to get trading decision

    Args:
        db: Database session
        account: Trading account
        portfolio: Portfolio data
        prices: Market prices
        samples: Legacy single-symbol samples (deprecated, use symbols instead)
        target_symbol: Legacy single symbol (deprecated, use symbols instead)
        symbols: List of symbols to include sampling data for (preferred method)
        hyperliquid_state: Optional Hyperliquid account state for real trading
        binance_state: Optional Binance account state for real trading
        symbol_metadata: Optional mapping of symbol -> display name overrides
        trigger_context: Optional context about what triggered this decision (signal or scheduled)
    """
    # Resolve LLM config from library if account uses placeholder or no key
    if not account.api_key or account.api_key in DEMO_API_KEYS:
        resolve_account_llm_config(db, account)

    # Paper trading / autonomous 策略不需要真实交易所 API key
    is_paper = getattr(account, 'trading_mode', 'live') == 'paper'
    is_autonomous = (trigger_context or {}).get('trigger_type') == 'autonomous'
    if _is_default_api_key(account.api_key) and not is_paper and not is_autonomous:
        logger.info(f"Skipping AI trading for account {account.name} - using default API key")
        return None

    # Paper / autonomous 模式仍需要有效的 LLM API key
    if not account.api_key and (is_paper or is_autonomous):
        logger.error(f"Account {account.name} has no LLM API key configured (paper/autonomous mode)")
        return None

    # IMPORTANT: Get global trading mode at the start
    from services.hyperliquid_environment import get_global_trading_mode
    global_environment = get_global_trading_mode(db)

    try:
        news_summary = fetch_latest_news()
        news_section = news_summary if news_summary else "No recent CoinJournal news available."
    except Exception as err:  # pragma: no cover - defensive logging
        logger.warning("Failed to fetch latest news: %s", err)
        news_section = "No recent CoinJournal news available."

    # ── Prompt Template Resolution (三层优先级) ── # 1. 策略进化后的 PromptTemplate（通过 trigger_context.ai_strategy_id → AIStrategy.master_prompt_template_id） # 2. 账户绑定的 PromptTemplate（AccountPromptBinding） # 3. 系统默认 PromptTemplate
    template = None
    _evolved_source = ""
    try:
        _strategy_id = (trigger_context or {}).get("ai_strategy_id")
        if _strategy_id and db:
            from backend.database.models import AIStrategy as _AS
            _strat = db.query(_AS).filter(_AS.strategy_id == _strategy_id).first()
            if _strat and _strat.master_prompt_template_id:
                from backend.database.models import PromptTemplate as _PT
                template = db.query(_PT).filter(_PT.id == _strat.master_prompt_template_id).first()
                if template:
                    _evolved_source = f"evolved(strategy={_strategy_id}, tpl={template.id})"
    except Exception as _evo_err:
        logger.debug(f"Evolved prompt lookup failed: {_evo_err}")

    if not template:
        template = prompt_repo.get_prompt_for_account(db, account.id)

    if not template:
        try:
            template = prompt_repo.ensure_default_prompt(db)
        except ValueError as exc:
            logger.error("Prompt template resolution failed: %s", exc)
            return None

    if _evolved_source:
        logger.info(f"[AI-Decision] 使用进化后提示词: {_evolved_source}")
    else:
        logger.debug(f"[AI-Decision] 使用提示词: {template.key} (id={template.id})")

    # Build context with multi-symbol support
    active_symbol_metadata = symbol_metadata or SUPPORTED_SYMBOLS
    symbol_order = symbols if symbols else list(active_symbol_metadata.keys())

    # ── Build context using PromptContextBuilder (decomposed from _build_prompt_context) ──
    from services.prompt_context import PromptContextBuilder, BuildInput

    _sampling_interval = None
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import GlobalSamplingConfig
        with SessionLocal() as _sdb:
            _scfg = _sdb.query(GlobalSamplingConfig).first()
            if _scfg:
                _sampling_interval = _scfg.sampling_interval
    except Exception as e:
        logger.warning(f"Failed to get sampling interval: {e}")

    _builder = PromptContextBuilder()
    context = _builder.build(BuildInput(
        account=account,
        db=db,
        portfolio=portfolio,
        prices=prices,
        hyperliquid_state=hyperliquid_state,
        binance_state=binance_state,
        environment=global_environment,
        symbol_metadata=active_symbol_metadata,
        symbol_order=symbol_order,
        samples=samples if not symbols else None,
        target_symbol=target_symbol if not symbols else None,
        sampling_interval=_sampling_interval,
        template_text=template.template_text,
        trigger_context=trigger_context,
    ))

    # Multi-symbol sampling data (appended separately for backward compat)
    if symbols:
        try:
            from services.sampling_pool import sampling_pool
            context["sampling_data"] = _build_multi_symbol_sampling_data(
                symbols, sampling_pool, _sampling_interval
            )
        except Exception:
            context["sampling_data"] = "N/A"

    # ── Prompt Template Post-Processing ──
    # Two paths: legacy (string-replace injection) vs non-legacy (declared placeholders)
    _tpl_text = template.template_text or ""
    _is_legacy = getattr(template, "is_legacy", "true") != "false"
    _required = getattr(template, "required_placeholders", None) or []

    if _is_legacy:
        logger.debug(
            "[PromptInjection] Template '%s' is legacy — using string-replace injection. "
            "Migrate to non-legacy by setting is_legacy=false and declaring required_placeholders.",
            template.key,
        )
        _tpl_text = _apply_legacy_injections(_tpl_text)
    else:
        # Non-legacy template: validate declared placeholders exist in context
        _missing = [ph for ph in _required if "{" + ph + "}" not in _tpl_text]
        if _missing:
            logger.warning(
                "[PromptInjection] Template '%s' declares required_placeholders=%s "
                "but these are missing from template_text: %s",
                template.key, _required, _missing,
            )

    try:
        prompt = _tpl_text.format_map(SafeDict(context))
    except Exception as exc:  # pragma: no cover - fallback rendering
        logger.error("Failed to render prompt template '%s': %s", template.key, exc)
        prompt = _tpl_text

    logger.debug("Using prompt template '%s' for account %s", template.key, account.id)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {account.api_key}",
    }

    # Use OpenAI-compatible chat completions format
    # Detect model type for appropriate parameter handling
    model_lower = (account.model or "").lower()

    # Reasoning models that don't support temperature parameter
    # Support multi-vendor reasoning models: OpenAI, DeepSeek, Qwen, Claude, Gemini, Grok
    is_reasoning_model = any(
        marker in model_lower for marker in [
            "gpt-5", "o1-preview", "o1-mini", "o1-", "o3-", "o4-",  # OpenAI
            "deepseek-r1", "deepseek-reasoner", "deepseek-v4",  # DeepSeek (v4=flash/pro)
            "qwq", "qwen-plus-thinking", "qwen-max-thinking", "qwen3-thinking", "qwen-turbo-thinking",  # Qwen
            "claude-4", "claude-sonnet-4-5",  # Claude (extended thinking)
            "gemini-2.5", "gemini-3", "gemini-2.0-flash-thinking",  # Gemini (thinking mode)
            "grok-3-mini"  # Grok (only mini has reasoning_content)
        ]
    )

    # New models that use max_completion_tokens instead of max_tokens
    # Note: DeepSeek models (including v4/reasoner) use max_tokens, not max_completion_tokens
    is_deepseek_model = "deepseek" in model_lower
    is_new_model = not is_deepseek_model and (
        is_reasoning_model or any(marker in model_lower for marker in ["gpt-4o"])
    )

    payload = {
        "model": account.model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
    }

    # Reasoning models (GPT-5, o1, o3, o4) don't support custom temperature
    # Only add temperature parameter for non-reasoning models
    if not is_reasoning_model:
        payload["temperature"] = 0.3  # Phase 3B §修复⑦: 0.7 → 0.3（降低随机性）

    # Use max_completion_tokens for newer models
    # Use max_tokens for older models (GPT-3.5, GPT-4, GPT-4-turbo, Deepseek)
    # Modern models have large context windows, allocate generous token budgets
    if is_new_model:
        # Reasoning models (GPT-5/o1) need more tokens for internal reasoning
        payload["max_completion_tokens"] = 5000
    else:
        # Regular models (GPT-4, Deepseek, Claude, etc.)
        payload["max_tokens"] = 5000

    # For GPT-5 family set reasoning_effort to balance latency and quality
    if "gpt-5" in model_lower:
        payload["reasoning_effort"] = "low"

    # DeepSeek V4：交易账户路径同样分层注入思考模式
    if "deepseek" in model_lower:
        try:
            from backend.services.deepseek_thinking import apply_deepseek_thinking_to_payload
            apply_deepseek_thinking_to_payload(
                payload,
                model=account.model,
                caller=f"ai_decision:{getattr(account, 'name', '') or account.id}",
            )
        except Exception:
            pass

    # Enable streaming for deepseek-reasoner to handle high-load scenarios
    # DeepSeek official recommendation: use streaming to avoid 30s timeout during high load
    use_streaming = (account.model == "deepseek-reasoner")
    if use_streaming:
        payload["stream"] = True

    try:
        endpoints = build_chat_completion_endpoints(account.base_url, account.model)
        if not endpoints:
            logger.error("No valid API endpoint built for account %s", account.name)
            system_logger.log_error(
                "API_ENDPOINT_BUILD_FAILED",
                f"Failed to build API endpoint for {account.name} (model: {account.model})",
                {"account": account.name, "model": account.model, "base_url": account.base_url},
            )
            return None

        # Retry logic for rate limiting and transient errors
        max_retries = 3
        response = None
        success = False

        # ── Prompt 大小监控 ──
        _prompt_messages = payload.get("messages", [])
        _total_chars = sum(len(m.get("content", "")) for m in _prompt_messages if isinstance(m.get("content"), str))
        _est_tokens = _total_chars // 4
        if _est_tokens > 6000:
            logger.warning(
                f"[AI Decision] Large prompt: ~{_est_tokens} tokens for {account.model}, "
                f"may cause timeout")
        # 截断超长消息防止超时
        for m in _prompt_messages:
            content = m.get("content", "")
            if isinstance(content, str) and len(content) > 16000:
                m["content"] = content[:16000] + "\n...[truncated]"

        # ── Timeout 配置 ──
        try:
            from backend.config.settings import LLM_CALL_TIMEOUT_SECONDS
            _base_timeout = float(LLM_CALL_TIMEOUT_SECONDS)
        except Exception:
            _base_timeout = 120.0

        if is_reasoning_model:
            request_timeout = max(_base_timeout * 2, 240.0)  # 推理模型至少 240s
        else:
            request_timeout = _base_timeout

        for endpoint in endpoints:
            for attempt in range(max_retries):
                try:
                    response = requests.post(
                        endpoint,
                        headers=headers,
                        json=payload,
                        timeout=request_timeout,
                        verify=not endpoint.startswith(("http://", "https://localhost", "https://127.0.0.1")),  # D7: 仅对本地/HTTP关闭SSL
                        stream=use_streaming,  # Enable streaming reception for deepseek-reasoner
                    )

                    if response.status_code == 200:
                        success = True
                        break  # Success, exit retry loop

                    if response.status_code == 429:
                        # Rate limited, wait and retry
                        wait_time = (2**attempt) + random.uniform(0, 1)  # Exponential backoff with jitter
                        logger.warning(
                            "AI API rate limited for %s (attempt %s/%s), waiting %.1fs…",
                            account.name,
                            attempt + 1,
                            max_retries,
                            wait_time,
                        )
                        if attempt < max_retries - 1:
                            time.sleep(wait_time)
                            continue

                        logger.error(
                            "AI API rate limited after %s attempts for endpoint %s: %s",
                            max_retries,
                            endpoint,
                            response.text,
                        )
                        break

                    logger.warning(
                        "AI API returned status %s for endpoint %s: %s",
                        response.status_code,
                        endpoint,
                        response.text,
                    )
                    break  # Try next endpoint if available
                except requests.RequestException as req_err:
                    if attempt < max_retries - 1:
                        wait_time = (2**attempt) + random.uniform(0, 1)
                        logger.warning(
                            "AI API request failed for endpoint %s (attempt %s/%s), retrying in %.1fs: %s",
                            endpoint,
                            attempt + 1,
                            max_retries,
                            wait_time,
                            req_err,
                        )
                        time.sleep(wait_time)
                        continue

                    logger.warning(
                        "AI API request failed after %s attempts for endpoint %s: %s",
                        max_retries,
                        endpoint,
                        req_err,
                    )
                    break
            if success:
                break

        if not success or not response:
            logger.error("All API endpoints failed for account %s (%s)", account.name, account.model)
            system_logger.log_error(
                "AI_API_ALL_ENDPOINTS_FAILED",
                f"All API endpoints failed for {account.name}",
                {
                    "account": account.name,
                    "model": account.model,
                    "endpoints_tried": [str(ep) for ep in endpoints],
                    "max_retries": max_retries,
                },
            )
            return None

        # Handle streaming response for deepseek-reasoner
        if use_streaming:
            try:
                full_content = ""
                reasoning_content = ""
                chunk_count = 0

                # Parse SSE stream
                for line in response.iter_lines():
                    if line:
                        line_str = line.decode('utf-8')

                        # SSE format: "data: {...}"
                        if line_str.startswith('data: '):
                            json_str = line_str[6:]  # Remove "data: " prefix

                            # Check for [DONE] marker
                            if json_str.strip() == '[DONE]':
                                break

                            try:
                                data = json.loads(json_str)
                                chunk_count += 1

                                # Extract content from delta
                                if data.get('choices'):
                                    delta = data['choices'][0].get('delta', {})
                                    content = delta.get('content') or ''
                                    reasoning = delta.get('reasoning_content') or ''

                                    full_content += content
                                    reasoning_content += reasoning

                            except json.JSONDecodeError as e:
                                logger.warning(f"JSON decode error in streaming response: {e}")
                                continue

                # Construct complete response object (simulate non-streaming format)
                result = {
                    "choices": [{
                        "message": {
                            "content": full_content,
                            "reasoning_content": reasoning_content
                        },
                        "finish_reason": "stop"
                    }]
                }

                logger.info(f"Streaming response completed: {chunk_count} chunks, content: {len(full_content)} chars, reasoning: {len(reasoning_content)} chars")

            except Exception as stream_err:
                logger.error(f"Failed to parse streaming response: {stream_err}")
                return None
        else:
            # Non-streaming response (existing logic)
            result = response.json()

        # ── Record token usage for billing dashboard ──
        try:
            usage_data = result.get("usage", {})
            if usage_data:
                from backend.services.llm_usage_service import record_usage
                p_tokens = usage_data.get("prompt_tokens", 0)
                c_tokens = usage_data.get("completion_tokens", 0)
                t_tokens = usage_data.get("total_tokens", p_tokens + c_tokens)
                r_tokens = usage_data.get("reasoning_tokens") or usage_data.get("completion_tokens_details", {}).get("reasoning_tokens")
                provider_name = ""
                base = (account.base_url or "").lower()
                if "openai" in base:
                    provider_name = "openai"
                elif "deepseek" in base:
                    provider_name = "deepseek"
                elif "dashscope" in base or "aliyun" in base:
                    provider_name = "qwen"
                elif "volces" in base or "volcengine" in base:
                    provider_name = "volcengine"
                elif "moonshot" in base:
                    provider_name = "moonshot"
                elif "anthropic" in base:
                    provider_name = "anthropic"
                elif "googleapis" in base or "generativelanguage" in base:
                    provider_name = "google"
                else:
                    provider_name = "custom"
                # LLMUsageLog is AnalyticsBase — use AnalyticsSessionLocal
                try:
                    from backend.database.connection import AnalyticsSessionLocal as _ADB
                    _usage_db = _ADB()
                    record_usage(
                        _usage_db,
                        account_id=account.id,
                        llm_config_id=getattr(account, "llm_config_id", None),
                        provider=provider_name,
                        model=account.model or "unknown",
                        reasoning_tokens=r_tokens,
                        call_type="ai_decision",
                        success=True,
                        usage_info=usage_data,
                        base_url=account.base_url or "",
                    )
                    _usage_db.close()
                except Exception as _usage_db_err:
                    logger.debug("Usage tracking (analytics db) skipped: %s", _usage_db_err)
        except Exception as _usage_err:
            logger.debug("Usage tracking skipped: %s", _usage_err)

        # Extract text from OpenAI-compatible response format
        if "choices" in result and len(result["choices"]) > 0:
            choice = result["choices"][0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "")
            reasoning_text = _extract_text_from_message(message.get("reasoning"))

            # [refactor] 多厂商 reasoning 提取逻辑已迁移到
            # backend.services.llm_reasoning_helper.extract_reasoning_content_safe # （文件顶部已 import 为同名 _extract_reasoning_content_safe）。 # 原闭包逻辑一字未改地搬过去了，这里直接调用，消除两处维护。
            # Extract reasoning content for later merging
            api_reasoning_content = _extract_reasoning_content_safe(result)

            # Check if response was truncated due to length limit
            if finish_reason == "length":
                logger.warning("AI response was truncated due to token limit. Consider increasing max_tokens.")
                # Try to get content from reasoning field if available (some models put partial content there)
                raw_content = message.get("reasoning") or message.get("content")
            else:
                raw_content = message.get("content")

            text_content = _extract_text_from_message(raw_content)

            if not text_content and reasoning_text:
                # Some providers keep reasoning separately even on normal completion
                text_content = reasoning_text
            elif not text_content and api_reasoning_content:
                # Fallback: DeepSeek Reasoner may put JSON in reasoning_content
                text_content = api_reasoning_content
                logger.info("Using reasoning_content as fallback for empty content (DeepSeek Reasoner)")

            if not text_content:
                logger.error(
                    "Empty content in AI response: %s",
                    {k: v for k, v in result.items() if k != "usage"},
                )
                return None

            # Try to extract JSON from the text
            # Sometimes AI might wrap JSON in markdown code blocks
            raw_decision_text = text_content.strip()
            cleaned_content = raw_decision_text
            if "```json" in cleaned_content:
                cleaned_content = cleaned_content.split("```json")[1].split("```")[0].strip()
            elif "```" in cleaned_content:
                cleaned_content = cleaned_content.split("```")[1].split("```")[0].strip()

            # Handle potential JSON parsing issues with escape sequences
            try:
                decision = json.loads(cleaned_content)
            except json.JSONDecodeError as parse_err:
                logger.warning("Initial JSON parse failed: %s", parse_err)
                logger.warning("Problematic content: %s...", cleaned_content[:200])

                cleaned = (
                    cleaned_content.replace("\n", " ")
                    .replace("\r", " ")
                    .replace("\t", " ")
                )
                cleaned = cleaned.replace("“", '"').replace("”", '"')
                cleaned = cleaned.replace("‘", "'").replace("’", "'")
                cleaned = cleaned.replace("–", "-").replace("—", "-").replace("‑", "-")

                try:
                    decision = json.loads(cleaned)
                    cleaned_content = cleaned
                    logger.info("Successfully parsed AI decision after cleanup")
                except json.JSONDecodeError:
                    logger.error("JSON parsing failed after cleanup, attempting manual extraction")
                    logger.error(f"Original AI response: {text_content[:1000]}...")
                    logger.error(f"Cleaned content: {cleaned[:1000]}...")
                    operation_match = re.search(r'"operation"\s*:\s*"([^"]+)"', text_content, re.IGNORECASE)
                    symbol_match = re.search(r'"symbol"\s*:\s*"([^"]+)"', text_content, re.IGNORECASE)
                    portion_match = re.search(r'"target_portion_of_balance"\s*:\s*([0-9.]+)', text_content)
                    reason_match = re.search(r'"reason"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', text_content, re.DOTALL)

                    if operation_match and symbol_match and portion_match:
                        decision = {
                            "operation": operation_match.group(1),
                            "symbol": symbol_match.group(1),
                            "target_portion_of_balance": float(portion_match.group(1)),
                            "reason": reason_match.group(1) if reason_match else "AI response parsing issue",
                        }
                        logger.info("Successfully recovered AI decision via manual extraction")
                        cleaned_content = json.dumps(decision)
                    else:
                        logger.error("Unable to extract required fields from AI response")
                        logger.error(f"Regex match results - operation: {operation_match.group(1) if operation_match else None}, symbol: {symbol_match.group(1) if symbol_match else None}, portion: {portion_match.group(1) if portion_match else None}, reason: {reason_match.group(1)[:100] if reason_match else None}...")
                        return None

            # Normalize into a list of decisions
            if isinstance(decision, dict) and isinstance(decision.get("decisions"), list):
                decision_entries = decision.get("decisions") or []
            elif isinstance(decision, list):
                decision_entries = decision
            elif isinstance(decision, dict):
                decision_entries = [decision]
            else:
                logger.error(f"AI response has unsupported structure: {type(decision)}")
                return None

            snapshot_source = cleaned_content if "cleaned_content" in locals() and cleaned_content else raw_decision_text

            structured_decisions: List[Dict[str, Any]] = []
            for idx, raw_entry in enumerate(decision_entries):
                if not isinstance(raw_entry, dict):
                    logger.warning(
                        "Skipping decision entry %s for account %s because it is %s instead of dict",
                        idx,
                        account.name,
                        type(raw_entry),
                    )
                    continue

                entry = dict(raw_entry)
                strategy_details = entry.get("trading_strategy")

                # Merge API reasoning content with trading_strategy
                # Priority: API reasoning (from reasoning models) > trading_strategy (from prompt) > fallback reasoning_text # [refactor] 合并逻辑统一走 llm_reasoning_helper.build_reasoning_snapshot， # 与中长线 agent 保持一致的三级优先级。
                entry["_prompt_snapshot"] = prompt
                entry["_reasoning_snapshot"] = build_reasoning_snapshot(
                    api_reasoning_content,
                    strategy_details if isinstance(strategy_details, str) else "",
                    reasoning_text or "",
                )

                # 🔥 CRITICAL: Validate and enforce TP/SL for buy/sell operations
                operation = entry.get("operation", "").lower()
                if operation in ["buy", "sell"]:
                    symbol = entry.get("symbol", "")
                    current_price = prices.get(symbol, 0) if prices else 0

                    # Check if TP/SL are missing
                    take_profit = entry.get("take_profit_price")
                    stop_loss = entry.get("stop_loss_price")

                    if not take_profit or not stop_loss:
                        # Auto-calculate TP/SL if missing — tier-aware defaults (V5.1)
                        if current_price > 0:
                            try:
                                from backend.config.settings import (
                                    TIER_TP_SL_DEFAULTS, TIER_TP_SL_DEFAULTS_V2, RISK_USE_TIER_TP_SL_V2
                                )
                                _tier = entry.get("tier", entry.get("timeframe_tier", "mid"))
                                # V2 优先（按 vol-band 分层，默认取 mid-vol 覆盖最广场景）
                                if RISK_USE_TIER_TP_SL_V2:
                                    _vd = TIER_TP_SL_DEFAULTS_V2.get("mid", {})
                                    _td = _vd.get(_tier, _vd.get("mid", {"tp_pct": 0.065, "sl_pct": 0.045}))
                                else:
                                    _td = TIER_TP_SL_DEFAULTS.get(_tier, TIER_TP_SL_DEFAULTS["mid"])
                            except Exception:
                                _tier = 'mid'
                                _td = {"tp_pct": 0.07, "sl_pct": 0.035}  # V5.1 mid fallback

                            # V5.1 杠杆缩放（与 coordinator lev_scale 公式保持一致）
                            _lev = float(entry.get("leverage", 8))
                            _lev_scale = 1.0 / max(_lev ** 0.15, 1.0)
                            _tp_scaled = _td["tp_pct"] * _lev_scale
                            _sl_scaled = _td["sl_pct"] * _lev_scale

                            if operation == "buy":
                                entry["take_profit_price"] = take_profit or round(current_price * (1 + _tp_scaled), 2)
                                entry["stop_loss_price"] = stop_loss or round(current_price * (1 - _sl_scaled), 2)
                            else:  # sell
                                entry["take_profit_price"] = take_profit or round(current_price * (1 - _tp_scaled), 2)
                                entry["stop_loss_price"] = stop_loss or round(current_price * (1 + _sl_scaled), 2)

                            logger.warning(
                                f"[TP/SL AUTO-FIX] {account.name} {operation.upper()} {symbol}: "
                                f"Missing TP/SL, auto-calculated - "
                                f"TP=${entry['take_profit_price']} SL=${entry['stop_loss_price']} "
                                f"(based on current price ${current_price})"
                            )
                        else:
                            logger.error(
                                f"[TP/SL VALIDATION ERROR] {account.name} {operation.upper()} {symbol}: "
                                f"Missing TP/SL and cannot auto-calculate (no current price available). "
                                f"This violates mandatory TP/SL requirement."
                            )

                entry["_raw_decision_text"] = snapshot_source
                structured_decisions.append(entry)

            if not structured_decisions:
                logger.error("AI response for %s contained no usable decision entries", account.name)
                return None

            # ══════════════════════════════════════════════════════════════ # Phase 3B 集成：规则引擎决策管道 # LLM 输出只作为"情绪输入"，最终决策由规则引擎执行 # 调用链：signal_confirmation → position_sizer → rule_engine → 覆盖entry # 编排器方向作为 fallback（当三维确认不足时）
            # ══════════════════════════════════════════════════════════════
            _orch_dirs = (trigger_context or {}).get("orchestrator_directions", {})
            filtered_decisions = []
            for entry in structured_decisions:
                try:
                    symbol = (entry.get("symbol") or "").upper()
                    if not symbol:
                        filtered_decisions.append(entry)
                        continue

                    # 1. 提取 LLM 情绪输入（不再让 LLM 直接决定方向）
                    llm_op = (entry.get("operation") or "hold").lower()
                    llm_conf = float(entry.get("confidence") or 0.5)
                    # 将 LLM 的 buy/sell/hold 映射为情绪分数
                    if llm_op == "buy":
                        llm_score = min(1.0, llm_conf)
                    elif llm_op == "sell":
                        llm_score = max(-1.0, -llm_conf)
                    else:
                        llm_score = 0.0
                    # 黑天鹅检测：LLM reason 中含有明显崩盘信号词
                    reason_text = (entry.get("reason") or "").lower()
                    black_swan_keywords = ["black swan", "黑天鹅", "flash crash", "exchange hack",
                                           "liquidation cascade", "market halt", "极端风险"]
                    is_black_swan = any(kw in reason_text for kw in black_swan_keywords)
                    llm_sentiment = LLMSentimentInput(
                        score=llm_score,
                        is_black_swan=is_black_swan,
                        black_swan_reason=reason_text[:200] if is_black_swan else "",
                        confidence=llm_conf,
                    )

                    # 2. 三维信号确认 — 主动补全三维数据
                    current_price = (prices or {}).get(symbol, 0)
                    hl_state = hyperliquid_state or {}

                    # 维度1: 技术面 — 获取1h K线（至少60根供EMA50计算）
                    klines_1h_data = None
                    try:
                        from backend.services.kline_data_service import kline_data_service
                        klines_1h_data = kline_data_service.get_klines_from_db(symbol, "1h", count=100)
                        if klines_1h_data:
                            logger.info(f"[Phase3B] {symbol} 获取到 {len(klines_1h_data)} 根1h K线")
                    except Exception as e:
                        logger.debug(f"[Phase3B] {symbol} K线获取失败: {e}")

                    # 维度2: 订单流 — 从情报引擎获取衍生品数据
                    derivatives_data = {
                        "funding_rate": hl_state.get("funding_rate", 0.0),
                        "open_interest": hl_state.get("open_interest", 0),
                    }
                    try:
                        from backend.services.derivatives_analytics_service import derivatives_analytics
                        deriv_snap = derivatives_analytics.get_snapshot(symbol)
                        if deriv_snap:
                            derivatives_data["funding_rate"] = deriv_snap.funding_rate or 0
                            derivatives_data["open_interest"] = deriv_snap.oi_total or 0
                            derivatives_data["oi_change_1h_pct"] = deriv_snap.oi_change_1h or 0
                            derivatives_data["price_change_1h_pct"] = deriv_snap.oi_change_1h or 0  # 近似替代
                            derivatives_data["liquidation_1h_long"] = deriv_snap.liquidation_1h_long or 0
                            derivatives_data["liquidation_1h_short"] = deriv_snap.liquidation_1h_short or 0
                    except Exception as _deriv_err:
                        logger.debug(f"[Phase3B] 衍生品数据获取失败 {symbol}: {_deriv_err}")

                    whale_data_dict = None
                    try:
                        from backend.services.whale_tracker_service import whale_tracker
                        ws = whale_tracker.get_whale_signal(symbol)
                        if ws:
                            whale_data_dict = {
                                "direction": ws.direction if ws.direction else 0,
                                "total_usd": ws.total_usd if ws.total_usd else 0,
                                "count": ws.activities_count if ws.activities_count else 0,
                            }
                    except Exception as _whale_err:
                        logger.debug(f"[Phase3B] 鲸鱼数据获取失败 {symbol}: {_whale_err}")

                    # 维度3: 情绪面 — 从情绪综合服务获取
                    sentiment_data_dict = None
                    try:
                        from backend.services.sentiment_composite_service import sentiment_composite
                        sent = sentiment_composite.calculate(symbol)
                        if sent:
                            sentiment_data_dict = {
                                "index": sent.index if sent.index else 50,
                                "zone": sent.zone if sent.zone else "neutral",
                                "fear_greed": sent.factors.get("fear_greed_index", 50) if sent.factors else 50,
                            }
                    except Exception as _sent_err:
                        logger.debug(f"[Phase3B] 情绪数据获取失败 {symbol}: {_sent_err}")

                    confirmation: ConfirmationResult = _signal_engine.evaluate(
                        symbol=symbol,
                        klines_1h=klines_1h_data,
                        derivatives_data=derivatives_data,
                        whale_data=whale_data_dict,
                        sentiment_data=sentiment_data_dict,
                        regime=None,
                    )

                    # Data-insufficient guard: if ALL dimensions lack data,
                    # the LLM decision is unreliable — force HOLD to prevent blind trading.
                    _all_dims_empty = all(
                        d.direction == 0 and d.strength == 0
                        for d in confirmation.dimensions.values()
                    )
                    if _all_dims_empty and confirmation.action == "HOLD":
                        _has_klines = klines_1h_data and len(klines_1h_data) >= 55
                        if not _has_klines:
                            _op = entry.get("operation", "hold").lower()
                            if _op in ("buy", "sell"):
                                logger.warning(
                                    f"[Phase3B] {symbol}: 三维数据均不足，拒绝LLM的{_op.upper()}决策，"
                                    f"降级为HOLD防止盲开"
                                )
                                entry["operation"] = "hold"
                                entry["confidence"] = 0
                            else:
                                logger.info(
                                    f"[Phase3B] {symbol}: 三维数据均不足，LLM决策={_op}，保持原样"
                                )
                            filtered_decisions.append(entry)
                            continue

                    # 3. 仓位计算
                    total_equity = float(hl_state.get("total_equity", 0) or 0)
                    if total_equity <= 0:
                        # fallback：从 portfolio 取资产
                        total_equity = float((portfolio or {}).get("total_assets", 0) or 0)
                    funding_rate = float(hl_state.get("funding_rate", 0.0) or 0.0)
                    consecutive_losses = 0
                    try:
                        from backend.services.risk_control_service import get_risk_control_service
                        _rcs = get_risk_control_service()
                        consecutive_losses = _rcs._count_consecutive_losses(db, account.id)
                    except Exception:
                        pass
                    position_sizing: PositionSizeResult = _position_sizer.calculate_position_size(
                        account_equity=total_equity,
                        signal_strength=confirmation.strength if confirmation.strength > 0 else 0.5,
                        atr_percent=0.015,   # 默认 1.5%，后续可接入实时 ATR
                        funding_rate=funding_rate,
                        consecutive_losses=consecutive_losses,
                    )

                    # 4. 风控检查
                    risk_passed = True
                    risk_responses = []
                    try:
                        from backend.services.risk_control_service import get_risk_control_service
                        _rcs = get_risk_control_service()
                        order_value = position_sizing.position_size_usd

                        # 从 hyperliquid_state 获取真实持仓（修复 positions=[] bug）
                        _hl_positions_raw = hl_state.get("positions", []) or []
                        _risk_positions = []
                        for _rp in _hl_positions_raw:
                            if isinstance(_rp, dict):
                                _risk_positions.append(_rp)
                        if not _risk_positions and total_equity > 0:
                            _avail = float(hl_state.get("available_balance", 0) or 0)
                            if _avail < total_equity * 0.9:
                                logger.warning(
                                    f"[RuleEngine] 风控持仓为空但可用余额偏低"
                                    f"(equity={total_equity:.0f}, avail={_avail:.0f})，"
                                    f"可能持仓数据缺失")

                        _risk_op = entry.get("operation", "hold").lower()
                        risk_passed, risk_responses = _rcs.check_all(
                            db=db,
                            account_id=account.id,
                            symbol=symbol,
                            operation=_risk_op if _risk_op in ("buy", "sell") else "buy",
                            order_value=order_value,
                            total_equity=total_equity,
                            available_balance=float(hl_state.get("available_balance", 0) or total_equity),
                            positions=_risk_positions,
                        )
                    except Exception as e:
                        logger.warning(f"[RuleEngine] 风控检查异常（跳过），symbol={symbol}: {e}")

                    # D2: 决策一致性门控检查 (flip-flop / confidence volatility / overtrade)
                    _consistency_blocked = False
                    try:
                        from backend.services.decision_consistency_gate import get_consistency_gate
                        _gate = get_consistency_gate()
                        _market_regime = entry.get("market_regime", "unknown") or "unknown"
                        _consistency_check = _gate.check(
                            account_id=account.id,
                            symbol=symbol,
                            action=entry.get("operation", "hold"),
                            confidence=float(entry.get("confidence", 0.5)),
                            market_regime=_market_regime,
                        )
                        if not _consistency_check.passed:
                            logger.warning(
                                f"[ConsistencyGate] {account.name} {symbol}: "
                                f"决策拦截 — {_consistency_check.reason}"
                            )
                            _consistency_blocked = True
                            entry["operation"] = "hold"
                            entry["confidence"] = 0
                            entry["_consistency_blocked"] = True
                            entry["_consistency_reason"] = _consistency_check.reason
                            filtered_decisions.append(entry)
                            continue  # 跳过规则引擎，直接 HOLD
                    except Exception as _cg_err:
                        logger.debug(f"[ConsistencyGate] 检查异常(放行): {_cg_err}")

                    # 5. 规则引擎最终决策
                    rule_decision: RuleDecision = _rule_engine.decide(
                        symbol=symbol,
                        confirmation=confirmation,
                        position_sizing=position_sizing,
                        risk_check=(risk_passed, risk_responses),
                        llm_sentiment=llm_sentiment,
                        current_price=current_price,
                    )

                    # 6. 编排器 Fallback：当规则引擎 HOLD 但编排器有明确方向时覆盖 # 编排器中期置信度分布：强信号0.45~0.85，弱信号0.35，中性0.25
                    # 门槛设为0.30：过滤掉中性(0.25)，保留弱信号(0.35)和强信号
                    if rule_decision.action == "HOLD" and symbol in _orch_dirs:
                        od = _orch_dirs[symbol]
                        orch_side = od.get("side", "")
                        orch_mid_conf = od.get("mid_confidence", 0)
                        orch_mid_bias = od.get("mid_bias", "neutral")
                        if orch_side in ("long", "short") and orch_mid_conf >= 0.30:
                            fallback_action = "BUY" if orch_side == "long" else "SELL"
                            fallback_conf = min(0.5, orch_mid_conf * 0.6)
                            fallback_lev = 8.0 if fallback_conf < 0.35 else 12.0
                            fallback_size = total_equity * 0.06 * fallback_conf * fallback_lev
                            logger.info(
                                f"[RuleEngine] {account.name} {symbol}: "
                                f"三维确认=HOLD → 编排器Fallback={fallback_action} "
                                f"(orch_side={orch_side}, mid_conf={orch_mid_conf:.2f}, "
                                f"lev={fallback_lev}x)"
                            )
                            rule_decision = RuleDecision(
                                action=fallback_action,
                                symbol=symbol,
                                direction=1 if orch_side == "long" else -1,
                                position_size_usd=fallback_size,
                                leverage=fallback_lev,
                                confidence=fallback_conf,
                                reason=f"编排器Fallback: {orch_mid_bias}({orch_mid_conf:.0%}), 三维确认不足",
                            )

                    # 7. 用规则引擎输出覆盖 LLM 的 operation 和仓位字段
                    original_op = entry.get("operation", "hold")
                    if rule_decision.action in ("BUY", "SELL", "HOLD", "EMERGENCY_CLOSE_ALL"):
                        action_map = {
                            "BUY": "buy", "SELL": "sell",
                            "HOLD": "hold", "EMERGENCY_CLOSE_ALL": "close"
                        }
                        new_op = action_map[rule_decision.action]
                        if new_op != original_op:
                            logger.info(
                                f"[RuleEngine] {account.name} {symbol}: "
                                f"LLM={original_op.upper()} → 规则引擎={rule_decision.action} "
                                f"(blocked_by={rule_decision.blocked_by or 'none'}, "
                                f"confirmation={confirmation.confirmation_level})"
                            )
                        entry["operation"] = new_op
                        entry["_rule_engine_action"] = rule_decision.action
                        entry["_rule_engine_reason"] = rule_decision.reason
                        entry["_confirmation_level"] = confirmation.confirmation_level
                        entry["_confirmed_dimensions"] = confirmation.confirmed_dimensions
                        # D1: 决策来源追踪 (llm | rule_engine | hybrid)
                        _llm_contributed = (
                            llm_sentiment is not None and abs(llm_sentiment.score) >= 0.2
                        )
                        entry["_decision_source"] = (
                            "hybrid" if _llm_contributed else "rule_engine"
                        )
                        # 规则引擎 confidence 写回（关键：决策日志才能显示真实置信度）
                        if rule_decision.confidence > 0:
                            entry["confidence"] = round(rule_decision.confidence * 100, 1)
                        if rule_decision.action in ("BUY", "SELL") and total_equity > 0:
                            rule_portion = min(0.20, rule_decision.position_size_usd / total_equity)
                            entry["target_portion_of_balance"] = round(rule_portion, 4)
                            entry["leverage"] = max(5, min(20, rule_decision.leverage))

                        # 当操作从 hold 被覆盖为 buy/sell 时，更新 reason 和 reasoning
                        if new_op != original_op and new_op in ("buy", "sell"):
                            llm_original_reason = entry.get("reason", "")
                            override_reason = rule_decision.reason or ""
                            entry["reason"] = (
                                f"[规则引擎覆盖] {override_reason}\n"
                                f"[LLM原始分析] {llm_original_reason}"
                            )
                            llm_reasoning = entry.get("_reasoning_snapshot", "")
                            entry["_reasoning_snapshot"] = (
                                f"== 规则引擎决策 ==\n"
                                f"动作: {new_op.upper()} | 置信度: {entry.get('confidence', 0)}%\n"
                                f"原因: {override_reason}\n"
                                f"确认级别: {confirmation.confirmation_level} "
                                f"(维度数: {confirmation.confirmed_dimensions})\n\n"
                                f"== LLM 原始推理 ==\n{llm_reasoning}"
                            )

                            # Phase 3B hold→buy/sell 后自动补齐 TP/SL — tier-aware (V5.1)
                            if not entry.get("take_profit_price") or not entry.get("stop_loss_price"):
                                if current_price > 0:
                                    try:
                                        from backend.config.settings import (
                                            TIER_TP_SL_DEFAULTS, TIER_TP_SL_DEFAULTS_V2, RISK_USE_TIER_TP_SL_V2
                                        )
                                        _p3b_tier = entry.get("tier", getattr(strategy, 'timeframe_tier', None) or 'mid')
                                        # V2 优先（按 vol-band 分层，默认取 mid-vol 覆盖最广场景）
                                        if RISK_USE_TIER_TP_SL_V2:
                                            _vd = TIER_TP_SL_DEFAULTS_V2.get("mid", {})
                                            _p3b_defaults = _vd.get(_p3b_tier, _vd.get("mid", {"tp_pct": 0.065, "sl_pct": 0.045}))
                                        else:
                                            _p3b_defaults = TIER_TP_SL_DEFAULTS.get(_p3b_tier, TIER_TP_SL_DEFAULTS["mid"])
                                    except Exception:
                                        _p3b_defaults = {"tp_pct": 0.07, "sl_pct": 0.035}  # V5.1 mid fallback

                                    # V5.1 杠杆缩放（与 coordinator lev_scale 公式保持一致）
                                    _p3b_lev = float(entry.get("leverage", 8))
                                    _p3b_lev_scale = 1.0 / max(_p3b_lev ** 0.15, 1.0)
                                    _p3b_tp = _p3b_defaults["tp_pct"] * _p3b_lev_scale
                                    _p3b_sl = _p3b_defaults["sl_pct"] * _p3b_lev_scale

                                    if new_op == "buy":
                                        entry["take_profit_price"] = entry.get("take_profit_price") or round(current_price * (1 + _p3b_tp), 2)
                                        entry["stop_loss_price"] = entry.get("stop_loss_price") or round(current_price * (1 - _p3b_sl), 2)
                                    else:
                                        entry["take_profit_price"] = entry.get("take_profit_price") or round(current_price * (1 - _p3b_tp), 2)
                                        entry["stop_loss_price"] = entry.get("stop_loss_price") or round(current_price * (1 + _p3b_sl), 2)
                                    logger.info(
                                        f"[Phase3B TP/SL] {symbol}: "
                                        f"TP=${entry['take_profit_price']} SL=${entry['stop_loss_price']} "
                                        f"(price=${current_price})"
                                    )
                except Exception as e:
                    logger.warning(f"[RuleEngine] 规则引擎执行异常（保留LLM原始决策），symbol={entry.get('symbol')}: {e}", exc_info=True)

                filtered_decisions.append(entry)

            structured_decisions = filtered_decisions
            # ══════════════════════════════════════════════════════════════

            logger.info(f"AI decisions for {account.name}: {structured_decisions}")
            return structured_decisions

        logger.error(f"Unexpected AI response format: {result}")
        return None
        
    except requests.RequestException as err:
        logger.error(f"AI API request failed: {err}")
        return None
    except json.JSONDecodeError as err:
        logger.error(f"Failed to parse AI response as JSON: {err}")
        # Try to log the content that failed to parse
        try:
            if 'text_content' in locals():
                logger.error(f"Content that failed to parse: {text_content[:500]}")
        except Exception:
            pass
        return None
    except Exception as err:
        logger.error(f"Unexpected error calling AI: {err}", exc_info=True)
        return None


# ═══════════════════════════════════════════════════════════════════════════ # P1-6: LLM 降级路径 — 超时/失败时切到规则引擎决策
# ═══════════════════════════════════════════════════════════════════════════

_LLM_FALLBACK_TIMEOUT_SECONDS = 15.0  # 历史默认值(未真正生效，见下方修复说明)
# [2026-07-11 修复] 原来的 15s 从未真正生效：旧代码用 `with ThreadPoolExecutor` 包裹
# future.result(timeout=15)，即使 15s 后抛出 TimeoutError，`with` 退出时的
# executor.shutdown(wait=True) 仍会阻塞到 LLM 真正响应完成(reasoning 模型最长 240s+重试)。 # 修复后 15s 会真正生效——但当前生产账户使用的 deepseek-v4-flash 等推理模型， # 正常响应时间本身就可能超过 15s，若直接让 15s 生效，会导致几乎所有决策都被 # 误判为"超时"而降级成规则引擎/纯观望，交易质量和频率反而变差。 # 现在 DB 会话已经和 LLM 调用解耦（后台线程用独立连接），不再需要用短超时来
# "抢救"数据库连接，因此把实际生效的超时放宽到覆盖推理模型真实超时(240s)+ # 一次重试的余量，只在 LLM 真的失联时才降级，不误伤正常但稍慢的推理调用。
_LLM_FALLBACK_TIMEOUT_SECONDS_EFFECTIVE = 260.0

# [2026-07-11 修复#2 - 线程泄漏根因] 上面的修复实现了"真正超时"，但每次调用都
# `concurrent.futures.ThreadPoolExecutor(max_workers=1)` 现开现用，超时后
# `shutdown(wait=False)` 意味着这个后台线程被直接"抛弃"——它不属于任何有限大小 # 的池子，只能靠 LLM 自己在最多 260s 后结束才会退出。本函数每个 tick 会对多个
# symbol/tier 各调用一次（K线分析师、中长线Agent等），调用频率(每30~45s一轮) # 远高于 260s 的线程存活时间，导致后台线程"生成速度 > 死亡速度"，无限堆积。 # 实测重启后仅约1小时进程线程数就从几十涨到450+，正是本函数的锅——这也是用户反馈 # "数据库连接越来越慢、总是堵塞卡死"的真正根因：不是数据库慢，是 Python 进程里 # 堆积的线程在疯狂抢 GIL，导致包括简单 DB 查询接口在内的所有请求都要排队等 CPU。 # 修复：改用一个模块级、大小固定的共享线程池。超时的调用不再"新开一个线程扔掉"， # 而是复用池子里的 worker；池子满时新任务在队列里排队等待，而不是无限开新 OS 线程。
_LLM_FALLBACK_POOL_SIZE = 8
_llm_fallback_pool = None


def _get_llm_fallback_pool():
    global _llm_fallback_pool
    if _llm_fallback_pool is None:
        import concurrent.futures as _cf
        _llm_fallback_pool = _cf.ThreadPoolExecutor(
            max_workers=_LLM_FALLBACK_POOL_SIZE,
            thread_name_prefix="llm_fallback",
        )
    return _llm_fallback_pool


def call_ai_for_decision_with_fallback(
    db: Session,
    account: Account,
    portfolio: Dict,
    prices: Dict[str, float],
    samples: Optional[List] = None,
    target_symbol: Optional[str] = None,
    symbols: Optional[List[str]] = None,
    hyperliquid_state: Optional[Dict[str, Any]] = None,
    binance_state: Optional[Dict[str, Any]] = None,
    symbol_metadata: Optional[Dict[str, Any]] = None,
    trigger_context: Optional[Dict[str, Any]] = None,
    fallback_timeout: float = _LLM_FALLBACK_TIMEOUT_SECONDS_EFFECTIVE,
) -> Optional[List[Dict[str, Any]]]:
    """LLM 决策 + 超时降级包装 (P1-6)

    1. 在独立线程中调用 call_ai_for_decision，总超时 fallback_timeout 秒
    2. 超时或失败时，降级到纯规则引擎决策（5个确定性分析师多数投票）
    3. 降级仓位 = 正常仓位的 50%
    """
    import concurrent.futures

    _symbols = symbols or ([target_symbol] if target_symbol else list((symbol_metadata or SUPPORTED_SYMBOLS).keys()))

    # [2026-07-11 修复] 根因修复：
    # 1) 原代码用 `with ThreadPoolExecutor(...) as executor:` 包裹 submit+result(timeout=...)。
    #    即使 future.result() 在 fallback_timeout 秒后抛 TimeoutError 被我们捕获，
    #    `with` 块退出时仍会调用 executor.shutdown(wait=True)，阻塞到后台线程里的 #    call_ai_for_decision 真正跑完为止（可长达 90~240s+重试）。这个"超时"从未真正生效， #    导致本函数调用方（run_trading_cycle）传入的 db 会话被一路占用到 LLM 真实响应完成， #    是数据库 idle-in-transaction 堆积、后端periodically卡死的根因。
    # 2) 后台线程调用 call_ai_for_decision 时复用了调用方的 db（非线程安全的 Session对象）， #    一旦真正实现"不等待就返回"，后台线程和主线程会同时使用同一个 db，产生连接损坏风险。 # 修复：后台线程使用独立的 DB 会话（重新按 id 查询 account，避免跨线程共享 ORM 对象），
    # 且 shutdown(wait=False) 让主线程在 fallback_timeout 后立刻真正返回； # 后台线程即使继续跑，也只占用它自己独立的连接，用完自行关闭，不再拖累主流程。
    _account_id = getattr(account, "id", None)

    def _call_with_isolated_session():
        from backend.database.connection import SessionLocal as _ThreadSessionLocal

        _thread_db = _ThreadSessionLocal()
        try:
            _thread_account = account
            if _account_id is not None:
                _fresh = _thread_db.query(Account).filter(Account.id == _account_id).first()
                if _fresh is not None:
                    _thread_account = _fresh
            return call_ai_for_decision(
                db=_thread_db,
                account=_thread_account,
                portfolio=portfolio,
                prices=prices,
                samples=samples,
                target_symbol=target_symbol,
                symbols=symbols,
                hyperliquid_state=hyperliquid_state,
                binance_state=binance_state,
                symbol_metadata=symbol_metadata,
                trigger_context=trigger_context,
            )
        finally:
            _thread_db.close()

    # ── 尝试 LLM 调用（带超时，超时后真正放行，不阻塞主线程/主db会话）── # [2026-07-11 修复#2] 不再每次 new 一个 executor：改用共享有界池，从根上堵住 # "线程生成速度 > 死亡速度"的无限堆积（详见上方 _get_llm_fallback_pool 注释）。
    executor = _get_llm_fallback_pool()
    try:
        future = executor.submit(_call_with_isolated_session)
        try:
            result = future.result(timeout=fallback_timeout)
            if result:
                return result
            logger.warning(
                f"[LLM Fallback] LLM 返回空决策 {_symbols}, 降级到规则引擎"
            )
        except concurrent.futures.TimeoutError:
            logger.warning(
                f"[LLM Fallback] LLM 调用超时({fallback_timeout}s), "
                f"降级到规则引擎 (symbols={_symbols})；后台线程使用独立连接继续跑，"
                f"结果到达后会被丢弃，不影响主流程"
            )
        except Exception as _inner_err:
            logger.warning(
                f"[LLM Fallback] LLM 调用异常: {_inner_err}, "
                f"降级到规则引擎 (symbols={_symbols})"
            )
    except Exception as _outer_err:
        logger.warning(
            f"[LLM Fallback] 线程池启动失败: {_outer_err}, "
            f"降级到规则引擎 (symbols={_symbols})"
        )
    # [2026-07-11 修复#2] 不再调用 executor.shutdown()：executor 现在是模块级共享池， # 生命周期跨越整个进程，不属于本次调用，不能在这里关掉（否则下一次调用会报 # "cannot schedule new futures after shutdown"）。超时未完成的任务会继续占用 # 该共享池里的一个 worker 直到自然结束，但线程总数被 _LLM_FALLBACK_POOL_SIZE # 硬顶住，不会再无限增长。后台线程用的是独立 db 连接（_call_with_isolated_session # 内自行 close），不会影响本函数调用方持有的 db 会话。

    # ── 降级: 禁止规则引擎伪造开仓，仅 hold（持仓由 SL/TP 管理）──
    from backend.config.settings import BLOCK_FALLBACK_OPENS
    if BLOCK_FALLBACK_OPENS:
        hold_only = [
            {
                "symbol": s,
                "action": "hold",
                "confidence": 0,
                "reasoning": (
                    "[LLM降级] 无大模型输出，禁止规则引擎假开仓；"
                    "仅观望，已有仓位靠止盈止损"
                ),
            }
            for s in _symbols
        ]
        logger.warning(
            f"[LLM Fallback] LLM 不可用，返回 hold-only {len(hold_only)} 条 (symbols={_symbols})"
        )
        return hold_only

    try:
        fallback_decisions = _build_rule_based_decisions(
            db=db,
            account=account,
            portfolio=portfolio,
            prices=prices,
            symbols=_symbols,
            hyperliquid_state=hyperliquid_state,
            trigger_context=trigger_context,
        )
        if fallback_decisions:
            logger.info(
                f"[LLM Fallback] 规则引擎降级(旧模式): "
                f"{len(fallback_decisions)} 个决策 (symbols={_symbols})"
            )
            return fallback_decisions
    except Exception as _fe:
        logger.error(f"[LLM Fallback] 规则引擎降级也失败: {_fe}", exc_info=True)

    return None


def _build_rule_based_decisions(
    db: Session,
    account: Account,
    portfolio: Dict,
    prices: Dict[str, float],
    symbols: List[str],
    hyperliquid_state: Optional[Dict[str, Any]] = None,
    trigger_context: Optional[Dict[str, Any]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """纯规则引擎决策构建器 (P1-6 降级路径)

    不依赖 LLM API，直接使用 SignalConfirmationEngine → PositionSizer
    → RiskControl → RuleBasedDecisionEngine 管线生成交易决策。

    降级仓位 = 正常仓位的 50%（保守原则）
    """
    decisions = []

    hl_state = hyperliquid_state or {}
    total_equity = float(hl_state.get("total_equity", 0) or 0)
    if total_equity <= 0:
        total_equity = float((portfolio or {}).get("total_assets", 10000) or 10000)

    for symbol in symbols:
        try:
            current_price = (prices or {}).get(symbol, 0)

            # ── Step 1: 三维信号确认 ──
            klines_1h_data = None
            try:
                from backend.services.kline_data_service import kline_data_service
                klines_1h_data = kline_data_service.get_klines_from_db(symbol, "1h", count=100)
            except Exception:
                pass

            derivatives_data = {
                "funding_rate": hl_state.get("funding_rate", 0.0),
                "open_interest": hl_state.get("open_interest", 0),
            }
            try:
                from backend.services.derivatives_analytics_service import derivatives_analytics
                deriv_snap = derivatives_analytics.get_snapshot(symbol)
                if deriv_snap:
                    derivatives_data["funding_rate"] = deriv_snap.funding_rate or 0
                    derivatives_data["open_interest"] = deriv_snap.oi_total or 0
            except Exception:
                pass

            whale_data_dict = None
            try:
                from backend.services.whale_tracker_service import whale_tracker
                ws = whale_tracker.get_whale_signal(symbol)
                if ws:
                    whale_data_dict = {
                        "direction": ws.direction if ws.direction else 0,
                        "total_usd": ws.total_usd if ws.total_usd else 0,
                        "count": ws.activities_count if ws.activities_count else 0,
                    }
            except Exception:
                pass

            sentiment_data_dict = None
            try:
                from backend.services.sentiment_composite_service import sentiment_composite
                sent = sentiment_composite.calculate(symbol)
                if sent:
                    sentiment_data_dict = {
                        "index": sent.index if sent.index else 50,
                        "zone": sent.zone if sent.zone else "neutral",
                        "fear_greed": sent.factors.get("fear_greed_index", 50) if sent.factors else 50,
                    }
            except Exception:
                pass

            confirmation: ConfirmationResult = _signal_engine.evaluate(
                symbol=symbol,
                klines_1h=klines_1h_data,
                derivatives_data=derivatives_data,
                whale_data=whale_data_dict,
                sentiment_data=sentiment_data_dict,
                regime=None,
            )

            # ── Step 2: 数据不足保护 ──
            _all_dims_empty = all(
                d.direction == 0 and d.strength == 0
                for d in confirmation.dimensions.values()
            )
            if _all_dims_empty and confirmation.action == "HOLD":
                _has_klines = klines_1h_data and len(klines_1h_data) >= 55
                if not _has_klines:
                    decisions.append({
                        "symbol": symbol,
                        "operation": "hold",
                        "confidence": 0,
                        "reason": "[降级模式] 三维数据均不足，保持HOLD",
                        "is_fallback": True,
                    })
                    continue

            # ── Step 3: 仓位计算（保守50%）──
            funding_rate = float(hl_state.get("funding_rate", 0.0) or 0.0)
            consecutive_losses = 0
            try:
                from backend.services.risk_control_service import get_risk_control_service
                _rcs = get_risk_control_service()
                consecutive_losses = _rcs._count_consecutive_losses(db, account.id)
            except Exception:
                pass

            position_sizing: PositionSizeResult = _position_sizer.calculate_position_size(
                account_equity=total_equity,
                signal_strength=confirmation.strength if confirmation.strength > 0 else 0.5,
                atr_percent=0.015,
                funding_rate=funding_rate,
                consecutive_losses=consecutive_losses,
            )

            # ── Step 4: 风控检查 ──
            risk_passed = True
            risk_responses = []
            try:
                from backend.services.risk_control_service import get_risk_control_service
                _rcs = get_risk_control_service()
                order_value = position_sizing.position_size_usd
                _risk_op = "buy" if confirmation.direction >= 0 else "sell"
                risk_passed, risk_responses = _rcs.check_all(
                    db=db,
                    account_id=account.id,
                    symbol=symbol,
                    operation=_risk_op,
                    order_value=order_value,
                    total_equity=total_equity,
                    available_balance=float(hl_state.get("available_balance", 0) or total_equity),
                    positions=hl_state.get("positions", []) or [],
                )
            except Exception:
                pass

            # ── Step 5: 规则引擎决策（无LLM情绪）──
            rule_decision: RuleDecision = _rule_engine.decide(
                symbol=symbol,
                confirmation=confirmation,
                position_sizing=position_sizing,
                risk_check=(risk_passed, risk_responses),
                llm_sentiment=None,  # 降级模式: 无LLM情绪
                current_price=current_price,
            )

            # ── Step 6: 降级仓位缩放 50% ──
            _fallback_scale = 0.5
            fallback_size = rule_decision.position_size_usd * _fallback_scale
            fallback_leverage = max(5, min(15, rule_decision.leverage))

            # ── Step 7: 组装决策 dict ──
            action_map = {"BUY": "buy", "SELL": "sell", "HOLD": "hold", "EMERGENCY_CLOSE_ALL": "close"}
            operation = action_map.get(rule_decision.action, "hold")

            reason = f"[降级模式|仓位×{_fallback_scale:.0%}] {rule_decision.reason}"
            if rule_decision.blocked_by:
                reason = f"[降级模式|拦截:{rule_decision.blocked_by}] {rule_decision.reason}"

            decision_entry = {
                "symbol": symbol,
                "operation": operation,
                "confidence": round(rule_decision.confidence * 100, 1),
                "reason": reason,
                "leverage": fallback_leverage,
                "target_portion_of_balance": round(
                    min(0.10, fallback_size / total_equity) if total_equity > 0 else 0, 4
                ),
                "_rule_engine_action": rule_decision.action,
                "_rule_engine_reason": rule_decision.reason,
                "_confirmation_level": confirmation.confirmation_level,
                "_confirmed_dimensions": confirmation.confirmed_dimensions,
                "_fallback_scaled": True,
                "_decision_source": "rule_engine",  # D1: 纯规则引擎降级路径，非LLM
                "is_fallback": True,
            }

            # ── 止盈/止损(做多/做空自动方向) ──
            if operation in ("buy", "sell") and current_price > 0:
                try:
                    from backend.config.settings import TIER_TP_SL_DEFAULTS
                    _defaults = TIER_TP_SL_DEFAULTS.get("mid", {"tp_pct": 0.08, "sl_pct": 0.03})
                except Exception:
                    _defaults = {"tp_pct": 0.08, "sl_pct": 0.03}
                if operation == "buy":
                    decision_entry["take_profit_price"] = round(current_price * (1 + _defaults["tp_pct"]), 2)
                    decision_entry["stop_loss_price"] = round(current_price * (1 - _defaults["sl_pct"]), 2)
                else:
                    decision_entry["take_profit_price"] = round(current_price * (1 - _defaults["tp_pct"]), 2)
                    decision_entry["stop_loss_price"] = round(current_price * (1 + _defaults["sl_pct"]), 2)
            else:
                decision_entry["take_profit_price"] = None
                decision_entry["stop_loss_price"] = None

            decisions.append(decision_entry)

        except Exception as _sym_err:
            logger.warning(f"[LLM Fallback] 规则引擎决策失败 {symbol}: {_sym_err}", exc_info=True)
            decisions.append({
                "symbol": symbol,
                "operation": "hold",
                "confidence": 0,
                "reason": f"[降级模式] 规则引擎异常: {_sym_err}",
                "is_fallback": True,
            })

    return decisions if decisions else None


def save_ai_decision(
    db: Session,
    account: Account,
    decision: Dict,
    portfolio: Dict,
    executed: bool = False,
    order_id: Optional[int] = None,
    wallet_address: Optional[str] = None,
    # Decision tracking fields for analysis chain
    prompt_template_id: Optional[int] = None,
    signal_trigger_id: Optional[int] = None,
    hyperliquid_order_id: Optional[str] = None,
    tp_order_id: Optional[str] = None,
    sl_order_id: Optional[str] = None,
    ai_strategy_id: Optional[str] = None,
    # 三周期独立分析（由 MultiTimeframeOrchestrator 注入，也可从 decision dict 的 _orchestrator 或 orchestrator 键读取）
    orchestrator_info: Optional[Dict] = None,
) -> Optional[int]:
    """Save AI decision to the decision log. Returns the log entry ID."""
    try:
        # 提取三周期 orchestrator 数据: 优先从显式参数, 其次从 decision dict
        _orch = orchestrator_info or {}
        if not _orch:
            _orch = decision.get("_orchestrator") or decision.get("orchestrator") or {}
        _short_bias = _orch.get("short_bias") if isinstance(_orch, dict) else None
        _short_conf = _orch.get("short_confidence") if isinstance(_orch, dict) else None
        _mid_bias = _orch.get("mid_bias") if isinstance(_orch, dict) else None
        _mid_conf = _orch.get("mid_confidence") if isinstance(_orch, dict) else None
        _long_bias = _orch.get("long_bias") if isinstance(_orch, dict) else None
        _long_conf = _orch.get("long_confidence") if isinstance(_orch, dict) else None

        operation = decision.get("operation", "").lower() if decision.get("operation") else ""
        symbol_raw = decision.get("symbol")
        symbol = symbol_raw.upper() if symbol_raw else None
        target_portion = float(decision.get("target_portion_of_balance", 0)) if decision.get("target_portion_of_balance") is not None else 0.0
        reason = decision.get("reason", "No reason provided")
        prompt_snapshot = decision.get("_prompt_snapshot")
        reasoning_snapshot = decision.get("_reasoning_snapshot")
        raw_decision_snapshot = decision.get("_raw_decision_text")
        decision_snapshot_structured = None
        try:
            decision_payload = {k: v for k, v in decision.items() if not k.startswith("_")}
            decision_snapshot_structured = json.dumps(decision_payload, indent=2, ensure_ascii=False)
        except Exception:
            decision_snapshot_structured = raw_decision_snapshot

        if (not reasoning_snapshot or not reasoning_snapshot.strip()) and isinstance(raw_decision_snapshot, str):
            candidate = raw_decision_snapshot.strip()
            extracted_reasoning: Optional[str] = None
            if candidate:
                # Try to strip JSON payload to keep narrative reasoning only
                json_start = candidate.find('{')
                json_end = candidate.rfind('}')
                if json_start != -1 and json_end != -1 and json_end > json_start:
                    prefix = candidate[:json_start].strip()
                    suffix = candidate[json_end + 1 :].strip()
                    parts = [part for part in (prefix, suffix) if part]
                    if parts:
                        extracted_reasoning = '\n\n'.join(parts)
                else:
                    extracted_reasoning = candidate if not candidate.startswith('{') else None

            if extracted_reasoning:
                reasoning_snapshot = extracted_reasoning

        # Calculate previous portion for the symbol # 计算当前symbol在组合中的仓位占比
        prev_portion = 0.0
        if symbol:  # 所有操作都计算prev_portion，不仅限于sell/hold
            positions = portfolio.get("positions", {})
            if symbol in positions:
                symbol_value = positions[symbol]["current_value"]
                total_balance = portfolio["total_assets"]
                if total_balance > 0:
                    prev_portion = symbol_value / total_balance

        # Get Hyperliquid environment for decision tagging
        # IMPORTANT: Always use global trading mode for accurate logging
        from services.hyperliquid_environment import get_global_trading_mode
        hyperliquid_environment = get_global_trading_mode(db)

        # Create decision log entry
        decision_log = AIDecisionLog(
            account_id=account.id,
            reason=reason,
            operation=operation,
            symbol=symbol,
            prev_portion=Decimal(str(prev_portion)),
            target_portion=Decimal(str(target_portion)),
            total_balance=Decimal(str(portfolio["total_assets"])),
            executed="true" if executed else "false",
            order_id=order_id,
            prompt_snapshot=prompt_snapshot,
            reasoning_snapshot=reasoning_snapshot,
            decision_snapshot=decision_snapshot_structured or raw_decision_snapshot,
            hyperliquid_environment=hyperliquid_environment,
            wallet_address=wallet_address,
            # Decision tracking fields for analysis chain
            prompt_template_id=prompt_template_id,
            signal_trigger_id=signal_trigger_id,
            hyperliquid_order_id=hyperliquid_order_id,
            tp_order_id=tp_order_id,
            sl_order_id=sl_order_id,
            ai_strategy_id=ai_strategy_id,
            strategy_version=None,
            decision_quality_score=None,
            decision_source=decision.get("_decision_source", "llm"),  # D1: 决策来源追踪
            # 三周期独立分析
            short_bias=_short_bias,
            short_confidence=_short_conf,
            mid_bias=_mid_bias,
            mid_confidence=_mid_conf,
            long_bias=_long_bias,
            long_confidence=_long_conf,
        )

        # Save AIDecisionLog to Analytics DB (AIDecisionLog is on AnalyticsBase)
        from backend.database.connection import AnalyticsSessionLocal
        analytics_db = AnalyticsSessionLocal()
        try:
            analytics_db.add(decision_log)
            analytics_db.commit()
            analytics_db.refresh(decision_log)
        finally:
            analytics_db.close()

        # Core DB operations (set_last_trigger, wisdom tracking)
        try:
            if decision_log.decision_time:
                set_last_trigger(db, account.id, decision_log.decision_time)

            symbol_str = symbol if symbol else "N/A"
            logger.info(f"Saved AI decision log for account {account.name}: {operation} {symbol_str} "
                       f"prev_portion={prev_portion:.4f} target_portion={target_portion:.4f} executed={executed}")

            system_logger.log_ai_decision(
                account_name=account.name,
                model=account.model,
                operation=operation,
                symbol=symbol,
                reason=reason,
                success=executed
            )

            # 追踪注入的交易智慧
            try:
                from backend.services.wisdom_tracker import wisdom_tracker
                prompt_text = prompt_snapshot or ""
                wids = wisdom_tracker.parse_wisdom_ids_from_response(prompt_text)
                if wids:
                    wisdom_tracker.record_wisdom_usage(db, decision_log.id, wids)
                    db.commit()
            except Exception:
                pass

        except Exception as db_err:
            logger.error(f"Failed Core DB operations for AI decision: {db_err}")
            db.rollback()
            raise

        # Second try block: WebSocket broadcast (non-critical - failures are ok)
        # Only attempt broadcast if database commit succeeded
        try:
            import asyncio
            from backend.api.ws import broadcast_model_chat_update

            broadcast_data = {
                "id": decision_log.id,
                "account_id": account.id,
                "account_name": account.name,
                "model": account.model,
                "decision_time": decision_log.decision_time.isoformat() if hasattr(decision_log.decision_time, 'isoformat') else str(decision_log.decision_time),
                "operation": decision_log.operation.upper() if decision_log.operation else "HOLD",
                "symbol": decision_log.symbol,
                "reason": decision_log.reason,
                "prev_portion": float(decision_log.prev_portion),
                "target_portion": float(decision_log.target_portion),
                "total_balance": float(decision_log.total_balance),
                "executed": decision_log.executed == "true",
                "order_id": decision_log.order_id,
                "prompt_snapshot": decision_log.prompt_snapshot,
                "reasoning_snapshot": decision_log.reasoning_snapshot,
                "decision_snapshot": decision_log.decision_snapshot,
                "wallet_address": decision_log.wallet_address,
            }

            # Check if there's a running event loop
            try:
                loop = asyncio.get_running_loop()
                # Event loop is running, create task
                loop.create_task(broadcast_model_chat_update(broadcast_data))
            except RuntimeError:
                # No running event loop, run synchronously
                asyncio.run(broadcast_model_chat_update(broadcast_data))

        except Exception as broadcast_err:
            logger.warning(f"Failed to broadcast AI decision update (non-critical): {broadcast_err}")

        return decision_log.id

    except Exception as err:
        logger.error(f"Failed to prepare AI decision log: {err}")
        raise


def mark_decision_executed(db: Session, log_id: int, strategy_id: str = None) -> None:
    """将 ai_decision_logs 的 executed 标记为 true"""
    from backend.database.connection import AnalyticsSessionLocal
    analytics_db = AnalyticsSessionLocal()
    try:
        log = analytics_db.query(AIDecisionLog).filter(AIDecisionLog.id == log_id).first()
        if log:
            log.executed = "true"
            if strategy_id:
                log.ai_strategy_id = strategy_id
            analytics_db.commit()
    except Exception as e:
        logger.warning(f"Failed to mark decision {log_id} as executed: {e}")
        analytics_db.rollback()
    finally:
        analytics_db.close()


def get_active_ai_accounts(db: Session) -> List[Account]:
    """Get all active AI accounts that are not using default API key"""
    accounts = db.query(Account).filter(
        Account.is_active == "true",
        Account.account_type == "AI",
        Account.auto_trading_enabled == "true"
    ).all()
    
    if not accounts:
        return []
    
    # Filter out default accounts
    valid_accounts = [acc for acc in accounts if not _is_default_api_key(acc.api_key)]
    
    if not valid_accounts:
        logger.debug("No valid AI accounts found (all using default keys)")
        return []
        
    return valid_accounts


def _parse_kline_indicator_variables(template_text: str) -> Dict[str, Dict[str, Any]]:
    """
    Parse K-line and indicator variables from prompt template.

    Extracts variables like:
    - {BTC_klines_15m}(200) - K-line data
    - {BTC_RSI14_15m} - Technical indicators
    - {BTC_market_data} - Market ticker data
    - {BTC_CVD_15m} - Market flow indicators (CVD, TAKER, OI, FUNDING, DEPTH)

    Returns grouped by (symbol, period) for optimization:
    {
        ('BTC', '15m'): {
            'klines': {'count': 200},
            'indicators': ['RSI14', 'MACD'],
            'flow_indicators': ['CVD', 'TAKER'],
            'market_data': True
        },
        ('BTC', None): {
            'market_data': True
        }
    }
    """
    # Pattern for K-line variables: {SYMBOL_klines_PERIOD}(COUNT)
    kline_pattern = r'\{([A-Z]+)_klines_(\w+)\}(?:\((\d+)\))?'

    # Pattern for indicator variables: {SYMBOL_INDICATOR_PERIOD}
    # Supports: RSI14, RSI7, MACD, STOCH, MA, EMA, BOLL, ATR14, VWAP, OBV
    indicator_pattern = r'\{([A-Z]+)_(RSI\d+|MACD|STOCH|MA\d*|EMA\d*|BOLL|ATR\d+|VWAP|OBV)_(\w+)\}'

    # Pattern for market flow variables: {SYMBOL_FLOW_PERIOD}
    # Supports: CVD, TAKER, OI, OI_DELTA, FUNDING, DEPTH, IMBALANCE
    # Note: OI_DELTA must come before OI in the pattern to match correctly
    flow_pattern = r'\{([A-Z]+)_(CVD|TAKER|OI_DELTA|OI|FUNDING|DEPTH|IMBALANCE)_(\w+)\}'

    # Pattern for market data: {SYMBOL_market_data}
    market_data_pattern = r'\{([A-Z]+)_market_data\}'

    grouped = {}

    def _ensure_key(key):
        if key not in grouped:
            grouped[key] = {
                'klines': None,
                'indicators': [],
                'flow_indicators': [],
                'market_data': False
            }

    # Parse K-line variables
    for match in re.finditer(kline_pattern, template_text):
        symbol = match.group(1)
        if symbol == "SYMBOL":
            continue  # Skip documentation placeholder
        period = match.group(2)
        count = int(match.group(3)) if match.group(3) else 500  # Default 500

        key = (symbol, period)
        _ensure_key(key)
        grouped[key]['klines'] = {'count': count}

        logger.debug(f"Found K-line variable: {symbol}_klines_{period}({count})")

    # Parse indicator variables
    for match in re.finditer(indicator_pattern, template_text):
        symbol = match.group(1)
        if symbol == "SYMBOL":
            continue  # Skip documentation placeholder
        indicator = match.group(2)
        period = match.group(3)

        key = (symbol, period)
        _ensure_key(key)

        # Handle compound indicators (MA, EMA expand to multiple)
        if indicator == 'MA':
            grouped[key]['indicators'].extend(['MA5', 'MA10', 'MA20'])
        elif indicator == 'EMA':
            grouped[key]['indicators'].extend(['EMA20', 'EMA50', 'EMA100'])
        else:
            grouped[key]['indicators'].append(indicator)

        logger.debug(f"Found indicator variable: {symbol}_{indicator}_{period}")

    # Parse market flow variables
    for match in re.finditer(flow_pattern, template_text):
        symbol = match.group(1)
        if symbol == "SYMBOL":
            continue  # Skip documentation placeholder
        flow_indicator = match.group(2)
        period = match.group(3)

        key = (symbol, period)
        _ensure_key(key)
        grouped[key]['flow_indicators'].append(flow_indicator)

        logger.debug(f"Found flow indicator variable: {symbol}_{flow_indicator}_{period}")
    
    # Parse market data variables
    for match in re.finditer(market_data_pattern, template_text):
        symbol = match.group(1)
        if symbol == "SYMBOL":
            continue  # Skip documentation placeholder

        key = (symbol, None)
        _ensure_key(key)
        grouped[key]['market_data'] = True

        logger.debug(f"Found market data variable: {symbol}_market_data")

    # Remove duplicates from indicators and flow_indicators lists
    for key in grouped:
        grouped[key]['indicators'] = list(set(grouped[key]['indicators']))
        grouped[key]['flow_indicators'] = list(set(grouped[key]['flow_indicators']))

    logger.info(f"Parsed {len(grouped)} groups of K-line/indicator/flow/market-data variables")
    return grouped


def _format_single_indicator(indicator_name: str, indicator_data: Any) -> str:
    """
    Format a single technical indicator for prompt injection.

    Args:
        indicator_name: Name of the indicator (e.g., 'RSI14', 'MACD')
        indicator_data: Calculated indicator data

    Returns:
        Formatted string for prompt
    """
    if not indicator_data:
        return "N/A (Insufficient data for calculation)"

    try:
        if indicator_name.startswith('RSI'):
            # RSI format: value + interpretation + last 5 values
            values = indicator_data if isinstance(indicator_data, list) else []
            if not values:
                return "N/A"

            current = values[-1]
            last_5 = values[-5:] if len(values) >= 5 else values

            # Interpret RSI value
            if current > 70:
                interpretation = "Overbought"
            elif current < 30:
                interpretation = "Oversold"
            else:
                interpretation = "Neutral"

            result = [
                f"{indicator_name}: {current:.2f} ({interpretation})",
                f"{indicator_name} last 5: {', '.join(f'{v:.2f}' for v in last_5)}"
            ]
            return "\n".join(result)

        elif indicator_name == 'MACD':
            # MACD format: MACD line, Signal line, Histogram + interpretation
            macd_line = indicator_data.get('macd', [])
            signal_line = indicator_data.get('signal', [])
            histogram = indicator_data.get('histogram', [])

            if not macd_line or not signal_line or not histogram:
                return "N/A"

            current_macd = macd_line[-1]
            current_signal = signal_line[-1]
            current_hist = histogram[-1]
            last_5_hist = histogram[-5:] if len(histogram) >= 5 else histogram

            # Interpret MACD
            momentum = "Bullish momentum" if current_hist > 0 else "Bearish momentum"

            result = [
                f"MACD Line: {current_macd:.4f}",
                f"Signal Line: {current_signal:.4f}",
                f"Histogram: {current_hist:.4f} ({momentum})",
                f"Histogram last 5: {', '.join(f'{v:.4f}' for v in last_5_hist)}"
            ]
            return "\n".join(result)

        elif indicator_name.startswith('MA') or indicator_name.startswith('EMA'):
            # Moving average format: current value + last 5 values
            values = indicator_data if isinstance(indicator_data, list) else []
            if not values:
                return "N/A"

            current = values[-1]
            last_5 = values[-5:] if len(values) >= 5 else values

            result = [
                f"{indicator_name}: {current:.2f}",
                f"{indicator_name} last 5: {', '.join(f'{v:.2f}' for v in last_5)}"
            ]
            return "\n".join(result)

        elif indicator_name == 'BOLL':
            # Bollinger Bands format: Upper, Middle, Lower bands
            upper = indicator_data.get('upper', [])
            middle = indicator_data.get('middle', [])
            lower = indicator_data.get('lower', [])

            if not upper or not middle or not lower:
                return "N/A"

            result = [
                f"Upper Band: {upper[-1]:.2f}",
                f"Middle Band: {middle[-1]:.2f}",
                f"Lower Band: {lower[-1]:.2f}",
                f"Band Width: {(upper[-1] - lower[-1]):.2f}"
            ]
            return "\n".join(result)

        elif indicator_name.startswith('ATR'):
            # ATR format: current value + interpretation
            values = indicator_data if isinstance(indicator_data, list) else []
            if not values:
                return "N/A"

            current = values[-1]
            avg_atr = sum(values[-20:]) / min(len(values), 20) if values else 0

            volatility = "High volatility" if current > avg_atr * 1.2 else "Normal volatility"

            result = [
                f"{indicator_name}: {current:.2f} ({volatility})",
                f"20-period average: {avg_atr:.2f}"
            ]
            return "\n".join(result)

        elif indicator_name == 'STOCH':
            # Stochastic Oscillator format: %K and %D lines + interpretation
            k_line = indicator_data.get('k', [])
            d_line = indicator_data.get('d', [])

            if not k_line or not d_line:
                return "N/A"

            current_k = k_line[-1]
            current_d = d_line[-1]
            last_5_k = k_line[-5:] if len(k_line) >= 5 else k_line

            # Interpret Stochastic
            if current_k > 80:
                interpretation = "Overbought"
            elif current_k < 20:
                interpretation = "Oversold"
            else:
                interpretation = "Neutral"

            result = [
                f"%K Line: {current_k:.2f} ({interpretation})",
                f"%D Line: {current_d:.2f}",
                f"%K last 5: {', '.join(f'{v:.2f}' for v in last_5_k)}"
            ]
            return "\n".join(result)

        elif indicator_name == 'VWAP':
            # VWAP format: current value + comparison with price
            values = indicator_data if isinstance(indicator_data, list) else []
            if not values:
                return "N/A"

            current = values[-1]
            last_5 = values[-5:] if len(values) >= 5 else values

            result = [
                f"VWAP: {current:.2f}",
                f"VWAP last 5: {', '.join(f'{v:.2f}' for v in last_5)}",
                f"Note: Price above VWAP suggests bullish sentiment, below suggests bearish"
            ]
            return "\n".join(result)

        elif indicator_name == 'OBV':
            # OBV format: current value + trend
            values = indicator_data if isinstance(indicator_data, list) else []
            if not values:
                return "N/A"

            current = values[-1]
            last_5 = values[-5:] if len(values) >= 5 else values

            # Determine trend
            if len(values) >= 2:
                trend = "Rising" if current > values[-2] else "Falling"
            else:
                trend = "N/A"

            result = [
                f"OBV: {current:.0f} ({trend})",
                f"OBV last 5: {', '.join(f'{v:.0f}' for v in last_5)}"
            ]
            return "\n".join(result)

        else:
            return "N/A"

    except Exception as e:
        logger.error(f"Error formatting indicator {indicator_name}: {e}")
        return "N/A"


def _format_flow_indicator(indicator_name: str, indicator_data: Any) -> str:
    """
    Format a market flow indicator for prompt injection.

    Args:
        indicator_name: Name of the flow indicator (e.g., 'CVD', 'TAKER', 'OI')
        indicator_data: Calculated flow indicator data dict

    Returns:
        Formatted string for prompt (objective data only, no interpretations)
    """
    if not indicator_data:
        return "N/A (Insufficient data for calculation)"

    try:
        period = indicator_data.get("period", "")

        if indicator_name == "CVD":
            current = indicator_data.get("current", 0)
            last_5 = indicator_data.get("last_5", [])
            cumulative = indicator_data.get("cumulative", 0)

            result = [
                f"CVD ({period}): {_format_usd(current)}",
                f"CVD last 5: {', '.join(_format_usd(v) for v in last_5)}",
                f"Cumulative: {_format_usd(cumulative)}"
            ]
            return "\n".join(result)

        elif indicator_name == "TAKER":
            import math
            buy = indicator_data.get("buy", 0)
            sell = indicator_data.get("sell", 0)
            ratio = indicator_data.get("ratio", 1.0)
            ratio_last_5 = indicator_data.get("ratio_last_5", [])
            volume_last_5 = indicator_data.get("volume_last_5", [])

            # Calculate log ratio: positive = buyers dominate, negative = sellers dominate
            log_ratio = math.log(ratio) if ratio > 0 else 0

            result = [
                f"Taker Buy: {_format_usd(buy)} | Taker Sell: {_format_usd(sell)}",
                f"Buy/Sell Ratio: {ratio:.2f}x (log: {log_ratio:+.2f})",
                f"Ratio last 5: {', '.join(f'{r:.2f}x' for r in ratio_last_5)}",
                f"Volume last 5: {', '.join(_format_usd(v) for v in volume_last_5)}"
            ]
            return "\n".join(result)

        elif indicator_name == "OI":
            current = indicator_data.get("current", 0)
            last_5 = indicator_data.get("last_5", [])
            is_stale = indicator_data.get("stale", False)
            age_minutes = indicator_data.get("age_minutes", 0)

            result = [f"Open Interest: {_format_usd(current)}"]
            if is_stale and age_minutes > 0:
                result[0] += f" (data from {age_minutes}min ago)"
            result.append(f"OI last 5: {', '.join(_format_usd(v) for v in last_5)}")
            return "\n".join(result)

        elif indicator_name == "OI_DELTA":
            current = indicator_data.get("current", 0)
            last_5 = indicator_data.get("last_5", [])
            is_stale = indicator_data.get("stale", False)
            expanded_window = indicator_data.get("expanded_window", 0)

            result = [f"OI Delta ({period}): {current:+.2f}%"]
            if is_stale and expanded_window > 0:
                result[0] += f" (expanded {expanded_window}x window)"
            result.append(f"OI Delta last 5: {', '.join(f'{c:+.2f}%' for c in last_5)}")
            return "\n".join(result)

        elif indicator_name == "FUNDING":
            current = indicator_data.get("current", 0)
            last_5 = indicator_data.get("last_5", [])
            annualized = indicator_data.get("annualized", 0)

            result = [
                f"Funding Rate: {current:.4f}%",
                f"Annualized: {annualized:.2f}%",
                f"Funding last 5: {', '.join(f'{f:.4f}%' for f in last_5)}"
            ]
            return "\n".join(result)

        elif indicator_name == "DEPTH":
            bid = indicator_data.get("bid", 0)
            ask = indicator_data.get("ask", 0)
            ratio = indicator_data.get("ratio", 1.0)
            ratio_last_5 = indicator_data.get("ratio_last_5", [])
            spread = indicator_data.get("spread")

            result = [
                f"Bid Depth: {_format_usd(bid)} | Ask Depth: {_format_usd(ask)}",
                f"Depth Ratio (Bid/Ask): {ratio:.2f}",
                f"Ratio last 5: {', '.join(f'{r:.2f}' for r in ratio_last_5)}"
            ]
            if spread is not None:
                result.append(f"Spread: {spread:.4f}")
            return "\n".join(result)

        elif indicator_name == "IMBALANCE":
            current = indicator_data.get("current", 0)
            last_5 = indicator_data.get("last_5", [])

            result = [
                f"Order Imbalance: {current:+.3f}",
                f"Imbalance last 5: {', '.join(f'{v:+.3f}' for v in last_5)}"
            ]
            return "\n".join(result)

        else:
            return "N/A"

    except Exception as e:
        logger.error(f"Error formatting flow indicator {indicator_name}: {e}")
        return "N/A"


def _format_usd(value: float) -> str:
    """Format USD value with appropriate unit (K, M, B)"""
    if value is None:
        return "N/A"
    abs_val = abs(value)
    sign = "+" if value >= 0 else "-"
    if abs_val >= 1_000_000_000:
        return f"{sign}${abs_val/1_000_000_000:.2f}B"
    elif abs_val >= 1_000_000:
        return f"{sign}${abs_val/1_000_000:.2f}M"
    elif abs_val >= 1_000:
        return f"{sign}${abs_val/1_000:.2f}K"
    else:
        return f"{sign}${abs_val:.2f}"


def _build_klines_and_indicators_context(
    variable_groups: Dict[str, Dict[str, Any]],
    db: Session,
    environment: str = "mainnet",
    exchange: str = "CRYPTO"
) -> Dict[str, str]:
    """
    Build K-line and indicator context for prompt filling.

    Args:
        variable_groups: Parsed variable groups from _parse_kline_indicator_variables
        db: Database session
        environment: Trading environment (mainnet/testnet)
        exchange: Data source exchange ("CRYPTO" for Hyperliquid, "binance" for Binance)

    Returns:
        Dict mapping variable names to formatted strings
    """
    from services.market_data import get_kline_data, get_ticker_data
    from services.technical_indicators import calculate_indicators
    from services.kline_ai_analysis_service import _format_klines_summary

    context = {}
    
    # 记录使用的数据源
    logger.info(f"Building K-line context using exchange: {exchange}")

    for (symbol, period), requirements in variable_groups.items():
        try:
            # Handle market data (no period)
            if period is None and requirements['market_data']:
                logger.info(f"Processing market data for {symbol} in {environment} (exchange: {exchange})")
                try:
                    ticker = get_ticker_data(symbol, exchange, environment)
                    if ticker:
                        market_data_lines = [
                            f"Symbol: {symbol}",
                            f"Price: ${ticker['price']:.2f}",
                            f"24h Change: {ticker['change24h']:+.2f} ({ticker['percentage24h']:+.2f}%)",
                            f"24h Volume: ${ticker['volume24h']:,.0f}",
                        ]
                        if 'open_interest' in ticker:
                            market_data_lines.append(f"Open Interest: ${ticker['open_interest']:,.0f}")
                        if 'funding_rate' in ticker:
                            market_data_lines.append(f"Funding Rate: {ticker['funding_rate']:.6f}%")

                        var_name = f"{symbol}_market_data"
                        context[var_name] = "\n".join(market_data_lines)
                        logger.debug(f"Added market data variable: {var_name}")
                except Exception as ticker_err:
                    logger.warning(f"Failed to get ticker data for {symbol}: {ticker_err}")
                continue

            # Process K-lines and indicators (has period)
            logger.info(f"Processing {symbol} {period} for environment: {environment} (exchange: {exchange})")

            # Always fetch 500 candles for accurate indicator calculation
            kline_data = get_kline_data(
                symbol=symbol,
                market=exchange,  # 使用传入的exchange参数
                period=period,
                count=500,
                environment=environment
            )

            if not kline_data:
                logger.warning(f"No K-line data for {symbol} {period} in {environment}")
                continue

            # Process K-line variables
            if requirements['klines']:
                count = requirements['klines']['count']
                # Take last N candles for display
                display_klines = kline_data[-count:] if len(kline_data) >= count else kline_data
                formatted_klines = _format_klines_summary(display_klines)

                # Variable name: {BTC_klines_15m}
                var_name = f"{symbol}_klines_{period}"
                context[var_name] = formatted_klines
                logger.debug(f"Added K-line variable: {var_name} ({len(display_klines)} candles)")

            # Calculate and process indicators
            if requirements['indicators']:
                indicators_to_calc = requirements['indicators']
                calculated = calculate_indicators(kline_data, indicators_to_calc)

                # Track compound indicators (MA, EMA) for merged output
                ma_indicators = []
                ema_indicators = []

                for indicator_name in indicators_to_calc:
                    indicator_data = calculated.get(indicator_name)
                    formatted = _format_single_indicator(indicator_name, indicator_data)

                    # Variable name: {BTC_RSI14_15m}
                    var_name = f"{symbol}_{indicator_name}_{period}"
                    context[var_name] = formatted
                    logger.debug(f"Added indicator variable: {var_name}")

                    # Track for compound output
                    if indicator_name.startswith('MA') and indicator_name[2:].isdigit():
                        ma_indicators.append((indicator_name, formatted))
                    elif indicator_name.startswith('EMA') and indicator_name[3:].isdigit():
                        ema_indicators.append((indicator_name, formatted))

                # Generate compound MA variable: {BTC_MA_15m}
                if ma_indicators:
                    ma_lines = []
                    for ind_name, ind_formatted in sorted(ma_indicators):
                        ma_lines.append(f"**{ind_name}**")
                        ma_lines.append(ind_formatted)
                        ma_lines.append("")
                    compound_var = f"{symbol}_MA_{period}"
                    context[compound_var] = "\n".join(ma_lines).strip()
                    logger.debug(f"Added compound MA variable: {compound_var}")

                # Generate compound EMA variable: {BTC_EMA_15m}
                if ema_indicators:
                    ema_lines = []
                    for ind_name, ind_formatted in sorted(ema_indicators):
                        ema_lines.append(f"**{ind_name}**")
                        ema_lines.append(ind_formatted)
                        ema_lines.append("")
                    compound_var = f"{symbol}_EMA_{period}"
                    context[compound_var] = "\n".join(ema_lines).strip()
                    logger.debug(f"Added compound EMA variable: {compound_var}")

            # Process market flow indicators
            if requirements.get('flow_indicators'):
                from services.market_flow_indicators import get_flow_indicators_for_prompt

                flow_indicators_to_calc = requirements['flow_indicators']
                flow_data = get_flow_indicators_for_prompt(
                    db=db,
                    symbol=symbol,
                    period=period,
                    indicators=flow_indicators_to_calc
                )

                for flow_name in flow_indicators_to_calc:
                    flow_indicator_data = flow_data.get(flow_name)
                    formatted = _format_flow_indicator(flow_name, flow_indicator_data)

                    # Variable name: {BTC_CVD_15m}
                    var_name = f"{symbol}_{flow_name}_{period}"
                    context[var_name] = formatted
                    logger.debug(f"Added flow indicator variable: {var_name}")

        except Exception as e:
            logger.error(f"Error processing {symbol} {period}: {e}", exc_info=True)
            continue

    # ═══ 注入策略风控参数 + 交易智慧（融合模式核心）═══
    strategy_risk_constraints = ""
    strategy_wisdom = ""
    ai_strategy_id = (trigger_context or {}).get("ai_strategy_id")

    try:
        if db and ai_strategy_id:
            from backend.database.models import AIStrategy
            ai_strat = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == ai_strategy_id
            ).first()
            if ai_strat:
                lines = ["[当前策略风控约束 — 必须遵守]"]
                if ai_strat.stop_loss_pct:
                    lines.append(f"  - 单笔止损上限: {ai_strat.stop_loss_pct*100:.1f}%")
                if ai_strat.take_profit_pct:
                    lines.append(f"  - 止盈目标: {ai_strat.take_profit_pct*100:.1f}%")
                if ai_strat.max_position_size:
                    lines.append(f"  - 最大仓位: {ai_strat.max_position_size*100:.0f}%")
                if ai_strat.default_leverage:
                    lines.append(f"  - 杠杆倍数: {ai_strat.default_leverage}x")
                if ai_strat.max_daily_loss:
                    lines.append(f"  - 单日最大亏损: {ai_strat.max_daily_loss*100:.1f}%")
                lines.append("  请在做出交易决策时严格遵守以上约束。")
                strategy_risk_constraints = "\n".join(lines)

                # 精准注入关联模板的交易智慧（而非所有模板）
                source_tpl_id = None
                genome = ai_strat.genome or {}
                if isinstance(genome, dict):
                    source_tpl_id = genome.get("source_template_id")

                if source_tpl_id:
                    from backend.services.backtest_insight_compiler import insight_compiler
                    w = insight_compiler.get_active_wisdom(db, source_tpl_id)
                    if w:
                        strategy_wisdom = w
    except Exception as e:
        logger.debug(f"Strategy params injection skipped: {e}")

    # 如果没有精准匹配到关联模板智慧，回退到通用智慧
    if not strategy_wisdom:
        try:
            if db:
                from backend.services.backtest_insight_compiler import insight_compiler
                from backend.database.models import StrategyTemplate
                templates = db.query(StrategyTemplate).filter(
                    StrategyTemplate.is_active == True
                ).order_by(StrategyTemplate.rating.desc()).limit(3).all()
                wisdom_parts = []
                for tpl in templates:
                    w = insight_compiler.get_active_wisdom(db, tpl.template_id)
                    if w:
                        wisdom_parts.append(w)
                if wisdom_parts:
                    strategy_wisdom = "\n\n".join(wisdom_parts)
        except Exception as e:
            logger.debug(f"Trading wisdom injection skipped: {e}")

    # 合并：风控硬约束 + 交易经验软建议
    combined_wisdom = ""
    if strategy_risk_constraints:
        combined_wisdom += strategy_risk_constraints
    if strategy_wisdom:
        if combined_wisdom:
            combined_wisdom += "\n\n"
        combined_wisdom += strategy_wisdom

    context["strategy_wisdom"] = combined_wisdom

    logger.info(f"Built context with {len(context)} variables for environment: {environment}")
    return context


# ══════════════════════════════════════════════════════════════ #  D7: 轻量级 LLM 聊天接口 — 供 trade_planner_agent 等模块直接调用
# ══════════════════════════════════════════════════════════════
def call_deepseek_chat(
    messages: list,
    response_format: dict = None,
    max_tokens: int = 4000,
    temperature: float = 0.7,
    model: str = None,
    *,
    caller: str = "deepseek_chat",
    account_id: Optional[int] = None,
) -> str:
    """调用 DeepSeek（走统一 LLM 网关，自动记录用量与缓存命中）。"""
    from backend.services.llm_config_service import get_llm_config, call_llm_api_sync, LLMConfig

    config = get_llm_config()
    if not config:
        raise RuntimeError("call_deepseek_chat: 无可用 LLM 配置")
    if model:
        config = LLMConfig(
            id=config.id,
            name=config.name,
            provider=config.provider or "deepseek",
            model=model,
            base_url=config.base_url,
            api_key=config.api_key,
            is_default=config.is_default,
        )
    resp = call_llm_api_sync(
        config,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        caller=caller,
        account_id=account_id,
    )
    if not resp or not resp.get("choices"):
        raise RuntimeError("call_deepseek_chat: 空响应")
    choice = resp["choices"][0]
    message = choice.get("message", {})
    return message.get("content") or message.get("reasoning") or ""
