"""Agent 量化特征表渲染器（S1-2，对应 04 综合方案 §2.3.8）。

把分散的 memory_block / atr_block / recent_loss_block 统一为一张标准化特征表，
供 SwingAgent / TrendAgent prompt 注入。这是打断方向偏见循环的关键数据源。

设计原则（参考竞品 FreqAI / aria-trading）：
- 喂预处理信号，不喂原始 OHLCV
- 统一 markdown 表格格式，LLM 易读
- 必含"交易记忆"字段（last_close_reason / same_dir_losses_24h / cooldown_remain_sec / blocked_sides）
- 必含风控边界（max_sl_pct / min_rr / max_risk_usd / max_leverage）
- 所有字段 try/except 优雅降级，缺失返回 'unavailable'，绝不阻塞 prompt 构建

调用点：
  swing_agent._build_prompt → render_quant_feature_table(symbol, market_envs, db, account_id, nature="swing")
  trend_agent._build_direction_prompt → render_quant_feature_table(symbol, market_envs, db, account_id, nature="trend_follow")
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _safe_num(v: Any, default: str = "unavailable") -> str:
    """格式化数值，None/异常返回 default。"""
    try:
        if v is None:
            return default
        f = float(v)
        if f != f:  # NaN
            return default
        return f"{f:.4f}"
    except Exception:
        return default


def _safe_pct(v: Any, default: str = "unavailable") -> str:
    """格式化百分比（v 已是小数，如 0.035 = 3.5%）。"""
    try:
        if v is None:
            return default
        f = float(v)
        if f != f:  # NaN
            return default
        return f"{f*100:.2f}%"
    except Exception:
        return default


def _nature_scope(nature: Optional[str]) -> Optional[tuple]:
    """按 agent nature 限定记忆查询范围；None 表示不过滤。"""
    if not nature:
        return None
    n = str(nature).lower()
    if n in ("trend_follow", "position"):
        return ("trend_follow", "position")
    if n in ("swing", "mid"):
        return ("swing",)
    return (n,)


def _get_atr(symbol: str, timeframe: str) -> Tuple[float, float]:
    """获取指定周期的 ATR 绝对值 + ATR%（相对价格）。

    Returns:
        (atr_abs, atr_pct) —— 失败返回 (0.0, 0.0)
    """
    try:
        from backend.services.market_data import market_data_service
        atr_abs = market_data_service.get_latest_atr(symbol, timeframe=timeframe)
        price = market_data_service.get_latest_price(symbol)
        if atr_abs and atr_abs > 0 and price and price > 0:
            return float(atr_abs), float(atr_abs) / float(price)
        return 0.0, 0.0
    except Exception:
        return 0.0, 0.0


def _resolve_atr_pct(ms: Optional[Dict[str, Any]], sym: str, tf: str) -> Tuple[float, float]:
    """ATR% 统一回退链，返回 (atr_pct, atr_abs)。

    顺序：ms 的 atr_{tf}_pct 字段 → indicators_{tf}.atr（绝对值）/price →
    1d 走 midlong_trade_design.estimate_atr_1d_pct（含 K 线估算）→
    market_data_service。消除特征表 0.00% 与深度上下文 K 线 ATR 的口径矛盾。
    """
    ms = ms if isinstance(ms, dict) else {}
    price = float(
        ms.get("current_price") or ms.get("price") or ms.get("mark_price") or 0
    )
    if price <= 0:
        try:
            from backend.services.market_data import market_data_service
            price = float(market_data_service.get_latest_price(sym) or 0)
        except Exception:
            price = 0.0

    v = ms.get(f"atr_{tf}_pct")
    try:
        if v is not None and float(v) > 0:
            return float(v), float(v) * price
    except (TypeError, ValueError):
        pass

    ind = ms.get(f"indicators_{tf}")
    if isinstance(ind, dict):
        a = ind.get("atr") or ind.get("atr_14")
        try:
            if a is not None and float(a) > 0 and price > 0:
                return float(a) / price, float(a)
        except (TypeError, ValueError):
            pass

    if tf == "1d":
        try:
            from backend.services.mlto.midlong_trade_design import estimate_atr_1d_pct
            est = estimate_atr_1d_pct(ms)
            if est and float(est) > 0:
                return float(est), float(est) * price
        except Exception:
            pass

    a_abs, a_pct = _get_atr(sym, tf)
    return (float(a_pct or 0), float(a_abs or 0))


def _get_trade_memory(
    db, symbol: str, account_id: int, limit: int = 5, window_days: int = 14,
    nature: Optional[str] = None,
) -> Dict[str, Any]:
    """查询同币种最近 N 笔交易记录（用于 memory_block + 同方向连亏统计）。

    Returns:
        {
            "recent_trades": [...],      # 最近 N 笔（含 side/pnl/hold_h/close_reason）
            "same_dir_losses_24h": int,  # 24h 同方向连续亏损数
            "last_close_reason": str,    # 最近一笔的平仓原因
            "cooldown_remain_sec": int,  # 冷却剩余秒数（来自 reentry_cooldown）
            "blocked_sides": list,        # 被冷却的方向列表 ["long"] / ["short"] / []
        }
    """
    result: Dict[str, Any] = {
        "recent_trades": [],
        "same_dir_losses_24h": 0,
        "last_close_reason": "",
        "cooldown_remain_sec": 0,
        "blocked_sides": [],
    }
    if db is None or not symbol or not account_id:
        return result

    try:
        from backend.database.models import PaperPosition
        since = datetime.utcnow() - timedelta(days=window_days)
        filters = [
            PaperPosition.symbol == symbol.upper(),
            PaperPosition.status.in_(["closed", "liquidated"]),
            PaperPosition.closed_at >= since,
        ]
        if account_id:
            filters.append(PaperPosition.account_id == account_id)
        _scope = _nature_scope(nature)
        if _scope:
            filters.append(PaperPosition.trade_nature.in_(_scope))
        recent = (
            db.query(PaperPosition)
            .filter(*filters)
            .order_by(PaperPosition.closed_at.desc())
            .limit(limit)
            .all()
        )
        for p in recent:
            side = (p.side or "").lower()
            pnl = float(p.unrealized_pnl or 0)
            hold_h = 0.0
            if p.closed_at and p.opened_at:
                hold_h = (p.closed_at - p.opened_at).total_seconds() / 3600
            result["recent_trades"].append({
                "side": side,
                "pnl": pnl,
                "pnl_pct": _safe_pct(
                    pnl / float(p.margin) if (p.margin and float(p.margin) > 0) else 0
                ),
                "hold_h": round(hold_h, 1),
                "close_reason": (p.close_reason or "")[:30],
                "trade_nature": (p.trade_nature or "")[:12],
            })
        if recent:
            result["last_close_reason"] = (recent[0].close_reason or "")[:40]
    except Exception as e:
        logger.debug("[QuantFeatureTable] %s 交易记忆查询失败: %s", symbol, e)

    # 同方向连续亏损数（24h 内，对 long 和 short 分别统计）
    result["same_dir_losses_24h"] = _count_same_dir_losses_24h(
        db, symbol, account_id, nature=nature
    )

    # 冷却状态（来自 reentry_cooldown）
    result["cooldown_remain_sec"], result["blocked_sides"] = _get_cooldown_status(
        account_id, symbol
    )

    return result


def _count_same_dir_losses_24h(
    db, symbol: str, account_id: int, nature: Optional[str] = None
) -> int:
    """统计同币种 24h 内同方向连续亏损数（取 long/short 中较大者）。

    用于 prompt 告知 LLM"同方向已连续亏 N 次"，触发宪法第 5 条冷却规则。
    """
    try:
        from backend.database.models import PaperPosition
        since = datetime.utcnow() - timedelta(hours=24)
        filters = [
            PaperPosition.symbol == symbol.upper(),
            PaperPosition.status.in_(["closed", "liquidated"]),
            PaperPosition.closed_at >= since,
        ]
        if account_id:
            filters.append(PaperPosition.account_id == account_id)
        _scope = _nature_scope(nature)
        if _scope:
            filters.append(PaperPosition.trade_nature.in_(_scope))
        recent = (
            db.query(PaperPosition)
            .filter(*filters)
            .order_by(PaperPosition.closed_at.desc())
            .limit(10)
            .all()
        )
        if not recent:
            return 0
        # 取最近一笔的方向，统计该方向连续亏损数
        latest_side = (recent[0].side or "").lower()
        if latest_side not in ("long", "short", "buy", "sell"):
            return 0
        latest_dir = "long" if latest_side in ("long", "buy") else "short"
        consecutive = 0
        for p in recent:
            p_dir = "long" if (p.side or "").lower() in ("long", "buy") else "short"
            if p_dir == latest_dir and float(p.unrealized_pnl or 0) < 0:
                consecutive += 1
            else:
                break
        return consecutive
    except Exception as e:
        logger.debug("[QuantFeatureTable] %s 同方向连亏统计失败: %s", symbol, e)
        return 0


def _get_cooldown_status(account_id: int, symbol: str) -> Tuple[int, list]:
    """查询 reentry_cooldown 状态，返回 (剩余秒数, 被屏蔽方向列表)。

    复用现有 reentry_cooldown._state（不重复造轮子）。
    """
    try:
        from backend.services import reentry_cooldown
        blocked_sides = []
        max_remain = 0
        for tier in ("mid", "long"):
            for action in ("buy", "sell"):
                blocked, reason = reentry_cooldown.reopen_blocked(
                    account_id, symbol, action, tier,
                )
                if blocked:
                    side = "long" if action == "buy" else "short"
                    if side not in blocked_sides:
                        blocked_sides.append(side)
                    # 从 reason 提取剩余分钟（粗略，实际剩余时间不精确）
                    # reason 形如 "...约剩 720 分钟..."
                    import re
                    m = re.search(r"约剩\s*(\d+)\s*分钟", reason or "")
                    if m:
                        remain_sec = int(m.group(1)) * 60
                        max_remain = max(max_remain, remain_sec)
        return max_remain, blocked_sides
    except Exception as e:
        logger.debug("[QuantFeatureTable] %s 冷却状态查询失败: %s", symbol, e)
        return 0, []


def _get_orch_biases(market_envs: Dict[str, Any], symbol: str) -> Dict[str, str]:
    """从 orchestrator 取多周期偏向。"""
    result = {"long_bias": "neutral", "mid_bias": "neutral", "short_bias": "neutral"}
    try:
        _ms = (market_envs or {}).get(symbol, {}) if isinstance(market_envs, dict) else {}
        orch = _ms.get("orchestrator", {}) if isinstance(_ms, dict) else {}
        if isinstance(orch, dict):
            result["long_bias"] = (orch.get("long_bias") or "neutral").lower()
            result["mid_bias"] = (orch.get("mid_bias") or "neutral").lower()
            result["short_bias"] = (orch.get("short_bias") or "neutral").lower()
    except Exception:
        pass
    return result


def _compute_align_score(biases: Dict[str, str]) -> Tuple[float, bool]:
    """计算多周期对齐分（0-1）+ 是否对齐。

    long/mid/short 三个偏向一致 → 1.0, aligned=True
    两个一致 → 0.67, aligned=False
    全不一致 → 0.0, aligned=False
    """
    vals = [biases.get("long_bias"), biases.get("mid_bias"), biases.get("short_bias")]
    non_neutral = [v for v in vals if v and v != "neutral"]
    if not non_neutral:
        return 0.0, False
    from collections import Counter
    counts = Counter(non_neutral)
    most_common, most_count = counts.most_common(1)[0]
    score = most_count / 3.0
    aligned = most_count >= 2 and most_common != "neutral"
    return round(score, 2), aligned


def _get_regime(market_envs: Dict[str, Any], symbol: str) -> str:
    """获取市场 regime。"""
    try:
        _ms = (market_envs or {}).get(symbol, {}) if isinstance(market_envs, dict) else {}
        reg = _ms.get("regime", {}) if isinstance(_ms, dict) else {}
        if isinstance(reg, dict):
            return (reg.get("name") or "unknown").lower()
        if hasattr(reg, "regime"):
            return str(reg.regime).lower()
    except Exception:
        pass
    return "unknown"


def render_quant_feature_table(
    symbol: str,
    market_envs: Dict[str, Any],
    db=None,
    account_id: Optional[int] = None,
    nature: str = "swing",
) -> str:
    """渲染标准化量化特征表（markdown 格式）。

    Args:
        symbol: 交易对（大写）
        market_envs: 市场环境 dict（含 orchestrator / indicators_* / regime 等）
        db: 数据库 session（查询交易记忆用）
        account_id: 账户 ID（查询交易记忆 + 冷却状态用）
        nature: swing / trend_follow / position（决定 ATR 周期 + 风控边界）

    Returns:
        markdown 字符串，可直接注入 prompt
    """
    sym = str(symbol).upper()
    nature_l = (nature or "swing").lower()

    # 1. ATR（按 nature 选主周期）
    _ms_q = (
        (market_envs or {}).get(sym) if isinstance(market_envs, dict) else {}
    )
    if not isinstance(_ms_q, dict):
        _ms_q = {}
    if nature_l in ("trend_follow", "position"):
        atr_1d_pct, atr_1d_abs = _resolve_atr_pct(_ms_q, sym, "1d")
        atr_4h_pct, atr_4h_abs = _resolve_atr_pct(_ms_q, sym, "4h")
        primary_atr_pct = atr_1d_pct if atr_1d_pct > 0 else atr_4h_pct
        primary_tf = "1d" if atr_1d_pct > 0 else "4h"
    else:
        atr_1h_pct, atr_1h_abs = _resolve_atr_pct(_ms_q, sym, "1h")
        atr_4h_pct, atr_4h_abs = _resolve_atr_pct(_ms_q, sym, "4h")
        primary_atr_pct = atr_4h_pct if atr_4h_pct > 0 else atr_1h_pct
        primary_tf = "4h" if atr_4h_pct > 0 else "1h"

    # 2. 交易记忆 + 冷却状态
    memory = _get_trade_memory(
        db, sym, account_id or 0, limit=5, window_days=14, nature=nature_l,
    )

    # 3. 多周期偏向 + 对齐分
    biases = _get_orch_biases(market_envs, sym)
    align_score, mtf_aligned = _compute_align_score(biases)

    # 4. regime
    regime = _get_regime(market_envs, sym)

    # 5. 风控边界（按 nature 分化）
    if nature_l in ("trend_follow", "position"):
        risk_bounds = {
            "max_sl_pct": "5.0%-15.0%",
            "min_rr": "3.0",
            "max_risk_usd": "1.5% equity",
            "max_leverage": "3x",
        }
    else:
        risk_bounds = {
            "max_sl_pct": "2.0%-8.0%",
            "min_rr": "2.0",
            "max_risk_usd": "1.0% equity",
            "max_leverage": "5x",
        }

    # 6. 开仓配额：按 nature 限定统计口径，上限读取真实配置（不再写死误导性门槛）
    try:
        from backend.config import settings as _qs
        _trend_daily_cap = int(getattr(_qs, "TREND_DAILY_OPEN_CAP", 15) or 15)
        _trend_week_cap = int(getattr(_qs, "TREND_MAX_OPENS_PER_WEEK", 6) or 6)
    except Exception:
        _trend_daily_cap, _trend_week_cap = 15, 6
    if nature_l in ("trend_follow", "position"):
        _scope = ("trend_follow", "position")
        opens_today = _count_opens_today(db, sym, account_id or 0, scope=_scope)
        opens_week = _count_opens_this_week(db, sym, account_id or 0, scope=_scope)
        _quota_lines = [
            f"- long_opens_today: **{opens_today}** (上限 {_trend_daily_cap}/天)",
            f"- long_opens_week: **{opens_week}** (上限 {_trend_week_cap}/周)",
        ]
    else:
        _scope = ("swing",)
        opens_today = _count_opens_today(db, sym, account_id or 0, scope=_scope)
        opens_week = _count_opens_this_week(db, sym, account_id or 0, scope=_scope)
        _quota_lines = [
            f"- mid_opens_today: **{opens_today}** (无独立日配额，受 V5 全局与组合风控约束)",
            f"- mid_opens_week: **{opens_week}** (参考值)",
        ]

    # ── 拼装 markdown 表 ──
    lines = [
        f"## 量化特征表（{sym} · {nature_l}）",
        "",
        "### 多时间框架偏向",
        f"- long_bias(1d): **{biases['long_bias']}**",
        f"- mid_bias(4h): **{biases['mid_bias']}**",
        f"- short_bias(15m): **{biases['short_bias']}**",
        f"- align_score: **{align_score:.2f}** (3 周期一致=1.0)",
        f"- mtf_aligned: **{mtf_aligned}** (≥2 周期同向)",
        "",
        "### 波动率环境",
        f"- primary ATR timeframe: **{primary_tf}**",
        f"- ATR% ({primary_tf}): **{_safe_pct(primary_atr_pct)}**",
        f"- 建议 SL 距离: 1.5×~3.0× ATR (按 lifecycle 调整)",
        f"- 建议 TP1 距离: ≥1.2× SL 距离",
        f"- 建议 TP3 距离: ≥2.5× SL 距离 (确保正 RR)",
        "",
        "### Regime",
        f"- 当前 regime: **{regime}**",
        f"- {'⚠️ ranging=震荡市,默认 hold,仅 align_score 高+突破放量才可开' if regime=='ranging' else '✓ trending 适合中长线'}",
        "",
        "### 交易记忆（最近 5 笔同币种）",
    ]

    if memory["recent_trades"]:
        lines.append("| # | 方向 | nature | pnl% | 持仓h | close_reason |")
        lines.append("|---|------|--------|------|-------|-------------|")
        for i, t in enumerate(memory["recent_trades"], 1):
            lines.append(
                f"| {i} | {t['side']} | {t['trade_nature']} | {t['pnl_pct']} | "
                f"{t['hold_h']} | {t['close_reason']} |"
            )
    else:
        lines.append("_（暂无历史交易记录）_")

    lines.extend([
        "",
        "### 同方向冷却状态（⚠️ 违反则强制 hold）",
        f"- same_dir_losses_24h: **{memory['same_dir_losses_24h']}** (≥2 则禁止同向)",
        f"- last_close_reason: **{memory['last_close_reason'] or 'none'}**",
        f"- cooldown_remain_sec: **{memory['cooldown_remain_sec']}** (>0 则禁止对应 side)",
        f"- blocked_sides: **{memory['blocked_sides'] or '[]'}**",
    ])

    if memory["same_dir_losses_24h"] >= 2:
        lines.append("- ⚠️ **冷却激活：禁止同方向 buy/sell，必须 hold**")
    elif memory["same_dir_losses_24h"] >= 1:
        lines.append("- ⚠️ 连续 1 次同方向亏损：置信度门槛 +15")

    lines.extend([
        "",
        "### 开仓配额",
        *_quota_lines,
        "",
        "### 风控硬边界（代码层强制）",
        f"- max_sl_pct: **{risk_bounds['max_sl_pct']}**",
        f"- min_rr: **{risk_bounds['min_rr']}**",
        f"- max_risk_usd: **{risk_bounds['max_risk_usd']}**",
        f"- max_leverage: **{risk_bounds['max_leverage']}**",
        "",
    ])

    return "\n".join(lines)


def _count_opens_today(
    db, symbol: str, account_id: int, scope: Optional[tuple] = None
) -> int:
    """统计今日同币种开仓数；scope 限定 trade_nature，None 表示不过滤。"""
    try:
        from backend.database.models import PaperPosition
        since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        filters = [
            PaperPosition.symbol == symbol.upper(),
            PaperPosition.opened_at >= since,
        ]
        if account_id:
            filters.append(PaperPosition.account_id == account_id)
        if scope:
            filters.append(PaperPosition.trade_nature.in_(scope))
        return (
            db.query(PaperPosition)
            .filter(*filters)
            .count()
        )
    except Exception:
        return 0


def _count_opens_this_week(
    db, symbol: str, account_id: int, scope: Optional[tuple] = None
) -> int:
    """统计本周同币种开仓数；scope 限定 trade_nature，None 表示不过滤。"""
    try:
        from backend.database.models import PaperPosition
        since = datetime.utcnow() - timedelta(days=7)
        filters = [
            PaperPosition.symbol == symbol.upper(),
            PaperPosition.opened_at >= since,
        ]
        if account_id:
            filters.append(PaperPosition.account_id == account_id)
        if scope:
            filters.append(PaperPosition.trade_nature.in_(scope))
        return (
            db.query(PaperPosition)
            .filter(*filters)
            .count()
        )
    except Exception:
        return 0


def render_memory_block(
    db, symbol: str, agent_type: str, account_id: int, limit: int = 5,
) -> str:
    """渲染历史教训块（兼容旧调用，内部委托给 _get_trade_memory）。

    这是 task_swing_agent.md 的 {{memory_block}} 变量内容。
    """
    _nature = (
        "trend_follow" if str(agent_type or "").lower() == "trend"
        else "swing" if str(agent_type or "").lower() in ("swing", "mid")
        else None
    )
    memory = _get_trade_memory(
        db, symbol, account_id or 0, limit=limit, window_days=14, nature=_nature,
    )
    if not memory["recent_trades"]:
        return "（暂无历史交易记录）"

    lines = [f"## 历史教训（最近 {len(memory['recent_trades'])} 笔同币种交易）"]
    lines.append("| # | 方向 | pnl% | 持仓h | close_reason |")
    lines.append("|---|------|------|-------|-------------|")
    for i, t in enumerate(memory["recent_trades"], 1):
        lines.append(
            f"| {i} | {t['side']} | {t['pnl_pct']} | {t['hold_h']} | {t['close_reason']} |"
        )
    lines.extend([
        "",
        f"## 同方向连续亏损统计",
        f"- 同币种同方向 24h 内连续亏损次数: **{memory['same_dir_losses_24h']}**",
    ])
    if memory["same_dir_losses_24h"] >= 2:
        lines.append("- ⚠️ 冷却状态: **ACTIVE**（强制 hold 或反转）")
    elif memory["same_dir_losses_24h"] >= 1:
        lines.append("- ⚠️ 冷却状态: **WARNING**（置信度门槛 +15）")
    else:
        lines.append("- 冷却状态: INACTIVE")

    return "\n".join(lines)


def render_atr_block(symbol: str, market_envs: Dict[str, Any]) -> str:
    """渲染 ATR / 波动率块（兼容旧调用）。"""
    sym = str(symbol).upper()
    _ms_a = (market_envs or {}).get(sym) if isinstance(market_envs, dict) else {}
    if not isinstance(_ms_a, dict):
        _ms_a = {}
    price = float(
        _ms_a.get("current_price") or _ms_a.get("price") or _ms_a.get("mark_price") or 0
    )
    if price <= 0:
        try:
            from backend.services.market_data import market_data_service
            price = float(market_data_service.get_latest_price(sym) or 0)
        except Exception:
            price = 0.0
    atr_1h_pct, atr_1h_abs = _resolve_atr_pct(_ms_a, sym, "1h")
    atr_4h_pct, atr_4h_abs = _resolve_atr_pct(_ms_a, sym, "4h")
    atr_1d_pct, atr_1d_abs = _resolve_atr_pct(_ms_a, sym, "1d")

    if not price or not (atr_1h_abs or atr_4h_abs or atr_1d_abs):
        return "## 波动率环境\n（ATR 数据不可用）"

    lines = ["## 波动率环境（建议 SL/TP 基于此）", f"- 当前价: {price:.4f}"]
    for _tf, _abs, _pct in (
        ("1h", atr_1h_abs, atr_1h_pct),
        ("4h", atr_4h_abs, atr_4h_pct),
        ("1d", atr_1d_abs, atr_1d_pct),
    ):
        if _abs and _abs > 0:
            lines.append(f"- {_tf} ATR: {_abs:.4f} ({_pct*100:.2f}%)")
        else:
            lines.append(f"- {_tf} ATR: 不可用")
    lines.extend([
        "- 建议 SL 距离: 1.5×~3.0× ATR (按 lifecycle 调整)",
        "- 建议 TP1 距离: ≥1.2× SL 距离",
        "- 建议 TP3 距离: ≥2.5× SL 距离 (确保正 RR)",
    ])
    return "\n".join(lines)


def render_recent_loss_block(
    db, symbol: str, direction: str, account_id: int, window_hours: int = 24,
    nature: Optional[str] = None,
) -> Dict[str, Any]:
    """渲染最近同方向亏损块（兼容旧调用）。

    Returns:
        {
            "block_text": str,           # markdown 文本
            "cooldown_active": bool,     # 是否激活冷却
            "consecutive_losses": int,   # 同方向连续亏损数
        }
    """
    memory = _get_trade_memory(
        db, symbol, account_id or 0, limit=5, window_days=1, nature=nature,
    )
    consecutive = memory["same_dir_losses_24h"]
    cooldown_active = consecutive >= 2

    block_text = f"""## 最近 {window_hours}h 同币种同方向结果
- 同方向连续亏损次数: {consecutive}
- 冷却状态: {'⚠️ ACTIVE（强制 hold 或反转）' if cooldown_active else 'INACTIVE'}
- blocked_sides: {memory['blocked_sides'] or '[]'}
"""
    return {
        "block_text": block_text,
        "cooldown_active": cooldown_active,
        "consecutive_losses": consecutive,
    }
