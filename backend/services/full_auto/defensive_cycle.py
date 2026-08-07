"""防守模式 — 从 monolith _execute_defensive_* 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class DefensiveHost:
    tier_protection: Dict[str, Any]
    default_protection: Dict[str, Any]
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)


def build_defensive_host(svc) -> DefensiveHost:
    from backend.services.full_auto_trading_service import FullAutoTradingService
    return DefensiveHost(
        tier_protection=svc.TIER_PROTECTION,
        default_protection=FullAutoTradingService.DEFAULT_PROTECTION,
        get_trading_account_id=svc._get_trading_account_id,
        append_event=svc._append_event,
    )


def run_defensive_analysis(
    db: Session,
    session,
    market_summary: dict,
    host: DefensiveHost,
) -> None:
    from backend.database.models import Account
    import json as _json

    account = db.query(Account).filter(Account.id == session.account_id).first()
    if not account:
        return
    _trading_acct_id = host.get_trading_account_id(db, session)

    try:
        from backend.services.paper_trading_engine import paper_engine
        from backend.services.market_data import get_last_price as get_latest_price

        positions_list = paper_engine.get_positions(db, _trading_acct_id) or []
        if not positions_list:
            host.append_event(session, "defensive_scan",
                "🛡️ 防守模式 | 当前无持仓，等待回撤恢复后重新开始交易")
            return

        bal_info = paper_engine.get_balance(db, _trading_acct_id) or {}
        total_equity = bal_info.get("total_equity", 10000)

        position_details = []
        for pos in positions_list:
            sym = pos.get("symbol", "")
            entry = pos.get("entry_price", 0) or pos.get("avg_price", 0)
            mark = pos.get("mark_price", 0)
            upnl = pos.get("unrealized_pnl", 0)
            margin = pos.get("margin", 0)
            side = pos.get("side", "")
            size = pos.get("size", 0) or pos.get("quantity", 0)
            lev = pos.get("leverage", 10)

            pnl_pct = (upnl / margin * 100) if margin > 0 else 0

            mkt = market_summary.get(sym, {}) if isinstance(market_summary, dict) else {}
            trend = mkt.get("trend_direction", "unknown") if isinstance(mkt, dict) else "unknown"
            vol = mkt.get("volatility_regime", "normal") if isinstance(mkt, dict) else "normal"
            sentiment = mkt.get("sentiment_index", 50) if isinstance(mkt, dict) else 50

            position_details.append({
                "symbol": sym,
                "side": side,
                "size": round(size, 6),
                "entry_price": round(entry, 4),
                "current_price": round(mark, 4),
                "unrealized_pnl": round(upnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "margin_used": round(margin, 2),
                "leverage": lev,
                "market_trend": trend,
                "volatility": vol,
                "sentiment": sentiment,
            })

        current_dd = getattr(session, "current_drawdown", 0) or 0
        max_dd = session.max_drawdown or 0

        prompt = f"""你是一个专业的加密货币量化交易风控 AI。当前系统已进入 **防守模式**（因为总体回撤超过了安全阈值）。

## 你的任务
分析当前每个持仓，为每个持仓独立做出决策：
- **hold**: 继续持有（趋势仍有利，或已接近止损无需追加操作） ← 优先选择
- **reduce**: 减仓（降低风险敢口，比例不超过25%）
- **close**: 全部平仓（止损/止盈/方向错误）

## 防守模式操作权限约束（波动率感知分层管理）
- 亏损分档阈值会根据币种波动率自动调整：
  - 低波动币(BTC/ETH): 基线阈值 -2%/-5%
  - 中波动币(SOL/BNB等): 阈值×1.5 → -3%/-7.5%
  - 高波动币(VIRTUAL/ASTER等): 阈值×2.5 → -5%/-12.5%
- 轻微亏损(0~-阈值): 只能hold（可建议调整SL）
- 中度亏损(-阈值~-2倍阈值): 允许reduce最多25%
- 严重亏损(<-2倍阈值): 允许close或设紧急SL
- 已减仓≥2次的仓位: 强制hold
⚠️ 高波动币种亏损5%可能只是正常波动，不要恐慌性减仓！请优先选择"hold"，减少不必要的减仓操作。

## 关键原则
1. 每个持仓独立分析，不要因为一个仓亏钱就全部平仓
2. 如果持仓方向与当前市场趋势相反（如做空但市场在涨），应该果断平仓
3. 如果亏损已经很大但趋势正在反转有利，可以继续持有观望
4. 如果仓位小、亏损有限，可以继续持有等待
5. 不要恐慌性操作，像有经验的基金经理一样冷静决策

## 账户总览
- 总权益: ${total_equity:,.2f}
- 当前回撤: {current_dd*100:.1f}%
- 历史最大回撤: {max_dd*100:.1f}%
- 总盈亏: ${session.total_pnl or 0:+.2f}

## 当前持仓
{_json.dumps(position_details, ensure_ascii=False, indent=2)}

## 要求
返回 JSON 数组，每个元素对应一个持仓：
[
  {{
    "symbol": "BTC",
    "action": "close" 或 "reduce" 或 "hold",
    "reasoning": "一句话说明理由（中文）"
  }}
]
只返回 JSON，不要其他文字。"""

        try:
            from backend.services.llm_config_service import (
                get_llm_config_for_analysis, call_llm_api_sync,
            )
            import re

            llm_config = get_llm_config_for_analysis(getattr(session, "account_id", None))
            if not llm_config:
                logger.warning("[FullAuto] 防守模式：无 LLM 配置，使用规则分析")
                run_rule_based_defensive(db, session, positions_list, market_summary, host)
                return

            messages = [
                {"role": "system", "content": "你是加密量化交易风控专家。系统进入防守模式，你负责管理现有仓位。只返回 JSON 数组。"},
                {"role": "user", "content": prompt},
            ]

            ai_response = call_llm_api_sync(
                llm_config, messages, temperature=0.3, max_tokens=500
            )

            if not ai_response:
                run_rule_based_defensive(db, session, positions_list, market_summary, host)
                return

            content = ai_response.get("choices", [{}])[0].get("message", {}).get("content", "")
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if not json_match:
                logger.warning(f"[FullAuto] 防守AI返回格式异常: {content[:200]}")
                run_rule_based_defensive(db, session, positions_list, market_summary, host)
                return

            verdicts = _json.loads(json_match.group())
            run_defensive_verdicts(db, session, _trading_acct_id, verdicts, positions_list, host)

        except Exception as e:
            logger.error(f"[FullAuto] 防守模式AI分析失败: {e}", exc_info=True)
            run_rule_based_defensive(db, session, positions_list, market_summary, host)

    except Exception as e:
        # [fix] rollback 避免 InFailedSqlTransaction 污染后续操作
        try:
            db.rollback()
        except Exception:
            pass
        logger.error(f"[FullAuto] 防守模式执行异常: {e}", exc_info=True)
        host.append_event(session, "defensive_error", f"防守分析异常: {str(e)[:80]}")


def run_defensive_verdicts(
    db: Session,
    session,
    account_id: int,
    verdicts: list,
    positions_list: list,
    host: DefensiveHost,
) -> None:
    from backend.services.paper_trading_engine import paper_engine

    for verdict in verdicts:
        sym = verdict.get("symbol", "")
        action = verdict.get("action", "hold")
        reasoning = verdict.get("reasoning", "")

        pos = None
        for p in positions_list:
            if p.get("symbol", "").upper() == sym.upper():
                pos = p
                break

        if not pos:
            continue

        side = pos.get("side", "")
        upnl = pos.get("unrealized_pnl", 0)

        # ── 整改项2: 防守模式分层管理（defensive verdicts 路径，波动率感知版）────
        from backend.config.settings import DEFENSIVE_TIERED_MODE, REDUCE_MAX_COUNT as _DEF_REDUCE_MAX2
        from backend.config.settings import DEFENSIVE_VOLATILITY_TIERS as _DVT2
        if DEFENSIVE_TIERED_MODE and action in ("reduce", "close"):
            _margin_val = float(pos.get("margin", 0))
            _upnl_val = float(upnl)
            _pnl_pct = (_upnl_val / _margin_val) if _margin_val > 0 else 0
            _pos_reduce_count = int(pos.get("reduce_count", 0))

            # 波动率感知阈值
            _vol_map2 = _DVT2.get("symbol_vol_map", {})
            _vol_mults2 = _DVT2.get("vol_multipliers", {})
            _vt2 = _vol_map2.get(sym.lower(), "mid")
            _vm2 = _vol_mults2.get(_vt2, 1.0)
            _adj_light2 = _DVT2.get("light_pct", 0.02) * _vm2
            _adj_mod2 = _DVT2.get("moderate_pct", 0.05) * _vm2
            _adj_sev2 = _DVT2.get("severe_pct", 0.05) * _vm2

            if _pos_reduce_count >= _DEF_REDUCE_MAX2:
                host.append_event(session, "defensive_reduce_limit",
                    f"🛡️ {sym} 已减仓{_pos_reduce_count}次，defensive下强制hold")
                continue
            if -_adj_light2 < _pnl_pct < 0:
                host.append_event(session, "defensive_light",
                    f"🛡️ {sym} 轻微亏损{_pnl_pct:.1%}[{_vt2}×{_vm2:.1f}，阈值-{_adj_light2:.0%}]，收紧SL而非{action}")
                continue
            if -_adj_mod2 < _pnl_pct <= -_adj_light2 and action == "close":
                host.append_event(session, "defensive_moderate",
                    f"🛡️ {sym} 中度亏损{_pnl_pct:.1%}[阈值-{_adj_light2:.0%}~-{_adj_mod2:.0%}]，defensive下禁止close")
                continue
            if _pnl_pct <= -_adj_sev2 and action == "reduce":
                host.append_event(session, "defensive_severe",
                    f"🛡️ {sym} 深度亏损{_pnl_pct:.1%}[{_vt2}×{_vm2:.1f}，阈值-{_adj_sev2:.0%}]，应设紧急SL而非逐步减仓")
                continue
            if _pnl_pct >= 0:
                host.append_event(session, "defensive_profit_hold",
                    f"🛡️ {sym} 盈利{_pnl_pct:.1%}，defensive下无需{action}")
                continue

        # ── 策略周期感知保护（防守模式同样适用）──
        if action in ("close", "reduce"):
            tier = pos.get("timeframe_tier", "mid")
            tier_cfg = host.tier_protection.get(tier, host.default_protection)
            protect_min = tier_cfg["protect_min"]
            emergency_pct = tier_cfg["emergency_pct"]
            tier_label = {"short": "短线", "mid": "中线", "long": "长线"}.get(tier, "中线")
            opened_at_str = pos.get("opened_at") or ""
            if opened_at_str:
                try:
                    ot = datetime.fromisoformat(str(opened_at_str).replace("Z", "+00:00"))
                    if ot.tzinfo is None:
                        ot = ot.replace(tzinfo=timezone.utc)
                    age_min = (datetime.now(timezone.utc) - ot).total_seconds() / 60.0
                    margin = float(pos.get("margin", 0))
                    pnl_pct = (float(upnl) / margin * 100) if margin > 0 else 0
                    if age_min < protect_min and pnl_pct > emergency_pct:
                        host.append_event(session, "position_protected",
                            f"🛡️ {tier_label}保护(防守) {sym}: 开仓{age_min:.0f}分钟"
                            f"(保护{protect_min}分钟)，忽略{action} | 浮盈亏{pnl_pct:+.1f}%")
                        logger.info(
                            f"[FullAuto] 防守{tier_label}保护 {sym}: age={age_min:.0f}min, "
                            f"pnl={pnl_pct:+.1f}%, skip {action}")
                        continue
                except (ValueError, TypeError):
                    pass

        if action == "close":
            def_strategy_id = pos.get("strategy_id")
            result = paper_engine.close_position(db, account_id, sym, side,
                reason="defensive_close", strategy_id=def_strategy_id)
            if result:
                pnl = result.get("pnl", 0)
                host.append_event(session, "defensive_close",
                    f"🛡️ 防守平仓 {sym} {side} PnL=${pnl:+.2f} | {reasoning}")
                session.total_trades = (session.total_trades or 0) + 1
                logger.info(f"[FullAuto] 防守平仓 {sym} {side}: pnl=${pnl:+.2f}")
            else:
                host.append_event(session, "defensive_close_fail",
                    f"🛡️ 防守平仓失败 {sym} {side} | {reasoning}")

        elif action == "reduce":
            def_strategy_id = pos.get("strategy_id")
            size = pos.get("size", 0) or pos.get("quantity", 0)
            mark = pos.get("mark_price", 0) or pos.get("entry_price", 1)
            notional = size * float(mark)

            _min_notional = max(5, total_equity * 0.05)
            if notional < _min_notional:
                result = paper_engine.close_position(
                    db, account_id, sym, side, reason="defensive_close_tiny",
                    strategy_id=def_strategy_id)
            else:
                # 整改项2: 中度亏损(-2%~-5%)时限制减仓比例为25%
                _d_margin = float(pos.get("margin", 0))
                _d_upnl = float(pos.get("unrealized_pnl", 0))
                _d_pnl_pct = (_d_upnl / _d_margin) if _d_margin > 0 else 0
                _d_ratio = 0.25 if (-0.05 < _d_pnl_pct <= -0.02) else 0.5
                reduce_qty = size * _d_ratio
                remaining_notional = (size - reduce_qty) * float(mark)
                if remaining_notional < _min_notional:
                    result = paper_engine.close_position(
                        db, account_id, sym, side, reason="defensive_close_tiny",
                        strategy_id=def_strategy_id)
                else:
                    result = paper_engine.close_position(
                        db, account_id, sym, side, quantity=reduce_qty,
                        reason="defensive_reduce", strategy_id=def_strategy_id)

            if result:
                pnl = result.get("pnl", 0)
                closed_fully = result.get("closed_fully", False)
                act_desc = "全平(微仓)" if closed_fully else "减仓50%"
                host.append_event(session, "defensive_reduce",
                    f"🛡️ 防守{act_desc} {sym} {side} PnL=${pnl:+.2f} | {reasoning}")
                session.total_trades = (session.total_trades or 0) + 1
                logger.info(f"[FullAuto] 防守{act_desc} {sym} {side}: pnl=${pnl:+.2f}")
                # v3 整改: 防守减仓成功后同步 record_partial_close，
                # 否则 pyramid/dca 的 rebound gate 取不到冷却记录
                try:
                    from backend.config.settings import ENABLE_REDUCE_COOLDOWN as _ENABLE_RCD
                    if _ENABLE_RCD and not closed_fully:
                        from backend.services.reentry_cooldown import record_partial_close as _rpc
                        _def_tier = (pos.get("timeframe_tier") or "mid").strip().lower()
                        if _def_tier not in ("short", "mid", "long"):
                            _def_tier = "mid"
                        _pos_side_rcd = "long" if side in ("buy", "long") else "short"
                        _rpc(host.get_trading_account_id(db, session), sym, _pos_side_rcd, _def_tier, float(pnl or 0))
                except Exception as _rpc_err:
                    logger.debug(f"[FullAuto] defensive record_partial_close 失败(非致命): {_rpc_err}")
            else:
                host.append_event(session, "defensive_reduce_fail",
                    f"🛡️ 防守减仓失败 {sym} {side} | {reasoning}")

        else:
            host.append_event(session, "defensive_hold",
                f"🛡️ 继续持有 {sym} {side} PnL=${upnl:+.2f} | {reasoning}")


def run_rule_based_defensive(
    db: Session,
    session,
    positions_list: list,
    market_summary: dict,
    host: DefensiveHost,
) -> None:
    from backend.config.settings import DEFENSIVE_VOLATILITY_TIERS as _DVT_R
    verdicts = []
    for pos in positions_list:
        sym = pos.get("symbol", "")
        side = pos.get("side", "")
        upnl = pos.get("unrealized_pnl", 0)
        margin = pos.get("margin", 0)
        pnl_pct = (upnl / margin * 100) if margin > 0 else 0

        mkt = market_summary.get(sym, {}) if isinstance(market_summary, dict) else {}
        trend = mkt.get("trend_direction", "unknown") if isinstance(mkt, dict) else "unknown"

        is_trend_against = (
            (side == "buy" and trend in ("bearish", "strongly_bearish")) or
            (side == "sell" and trend in ("bullish", "strongly_bullish"))
        )

        # 波动率感知阈值：根据币种分档调整
        _vol_map_r = _DVT_R.get("symbol_vol_map", {})
        _vol_mults_r = _DVT_R.get("vol_multipliers", {})
        _vt_r = _vol_map_r.get(sym.lower(), "mid")
        _vm_r = _vol_mults_r.get(_vt_r, 1.0)
        _close_threshold = 15 * _vm_r    # 基线-15%，高波动币-37.5%
        _reduce_threshold = 10 * _vm_r   # 基线-10%，高波动币-25%

        if pnl_pct < -_close_threshold and is_trend_against:
            verdicts.append({"symbol": sym, "action": "close",
                "reasoning": f"亏损{pnl_pct:.1f}%且趋势不利({trend})[{_vt_r}×{_vm_r:.1f}]，止损平仓"})
        elif pnl_pct < -_reduce_threshold or is_trend_against:
            verdicts.append({"symbol": sym, "action": "reduce",
                "reasoning": f"亏损{pnl_pct:.1f}%/趋势{trend}[{_vt_r}×{_vm_r:.1f}]，减仓降风险"})
        else:
            verdicts.append({"symbol": sym, "action": "hold",
                "reasoning": f"亏损{pnl_pct:.1f}%[{_vt_r}×{_vm_r:.1f}]，暂时持有观察"})

    run_defensive_verdicts(db, session, host.get_trading_account_id(db, session), verdicts, positions_list, host)
