"""
Strategy context builder — factor engine, RAG analogies, trading wisdom,
confidence calibration, adaptive trading summary.
"""
from __future__ import annotations

from typing import Any, Dict

from .types import BuildInput, BuildResult


def _build_strategy_context(inp: BuildInput) -> BuildResult:
    """Build strategy-level context: factors, RAG, wisdom, calibration.

    Consumes: inp.account, inp.ordered_symbols, inp.trigger_context,
              inp.db, inp.hyperliquid_state.
    """
    result: Dict[str, Any] = {}

    # Factor engine status — computed by ai_decision_integration
    # [2026-09-17 统一策略修复] 旧实现把 (account.id, symbols, db) 传给 per-symbol API
    # build_factor_context(symbol, klines, market_data)，且读取不存在的 fc.top_factors，
    # 双重错误被裸 except 吞掉 → 备用车道 LLM 恒见 "Factor engine offline"（断点1/2）。
    factor_engine_status, factors_summary = _build_factor_engine(inp)
    result["factor_engine_status"] = factor_engine_status
    result["factors_summary"] = factors_summary
    result["adaptive_trading_summary"] = _build_adaptive_trading(inp)

    # Historical analogies (RAG)
    result["historical_analogies"] = "N/A"  # filled by call_ai_for_decision

    # K-line technical analysis
    result["kline_technical_analysis"] = "N/A"  # filled by call_ai_for_decision

    # Confidence calibration
    result["confidence_calibration"] = "N/A"  # filled by call_ai_for_decision

    # Trading wisdom from backtest evolution
    result["strategy_wisdom"] = _build_strategy_wisdom(inp)

    # Trader personality and mental state
    result["trader_personality"] = _build_trader_personality(inp)
    result["trader_mental_state"] = "Normal — no anomalies detected."

    return result


def _load_klines_df(symbol: str, period: str = "15m", count: int = 200):
    """决策口径取K线转 DataFrame（data_center 同源；失败返回空 df）。"""
    try:
        import pandas as pd

        from backend.services.kline_data_service import kline_service

        rows = kline_service.get_klines_from_db(symbol, period, count=count) or []
        if not rows:
            return None
        df = pd.DataFrame(rows)
        colmap = {}
        for c in df.columns:
            lc = str(c).lower()
            for want in ("open", "high", "low", "close", "volume"):
                if want in lc and want not in colmap:
                    colmap[want] = c
        if "close" not in colmap:
            return None
        df = df.rename(columns={v: k for k, v in colmap.items()})
        for c in ("open", "high", "low", "close", "volume"):
            if c in df.columns:
                df[c] = df[c].astype(float)
        return df
    except Exception:
        return None


def _build_factor_engine(inp: BuildInput):
    """因子引擎状态（per-symbol 正确调用）。返回 (engine_status, factors_summary)。"""
    try:
        from backend.services.ai_decision_integration import build_factor_context

        lines = ["Factor Engine Status:"]
        summary_parts = []
        regimes = []
        got_any = False
        for sym in (inp.ordered_symbols or [])[:5]:
            df = _load_klines_df(sym)
            if df is None or df.empty:
                continue
            try:
                fc = build_factor_context(sym, df, None)
            except Exception:
                continue
            if fc is None:
                continue
            got_any = True
            regimes.append(str(fc.market_regime or "unknown"))
            top = []
            try:
                items = []
                for k, v in (fc.factor_values or {}).items():
                    try:
                        items.append((str(k), float(v)))
                    except (TypeError, ValueError):
                        continue
                items.sort(key=lambda kv: -abs(kv[1]))
                top = items[:5]
            except Exception:
                top = []
            detail = "; ".join(f"{k}={v:.2f}" for k, v in top)
            sel = ",".join(list(fc.selected_factors or [])[:5])
            lines.append(
                f"  - {sym}: regime={fc.market_regime}({float(fc.regime_confidence or 0):.2f})"
                + (f" | {detail}" if detail else "")
                + (f" | active={sel}" if sel else "")
            )
            summary_parts.append(f"{sym}[{fc.market_regime}]")
        if got_any:
            regime = max(set(regimes), key=regimes.count) if regimes else "unknown"
            lines.append(f"  Market Regime: {regime}")
            return "\n".join(lines), "活跃因子状态: " + "; ".join(summary_parts)
    except Exception:
        pass
    return "Factor engine offline — using pure AI judgment.", "N/A"


def _build_adaptive_trading(inp: BuildInput) -> str:
    """自适应交易参数（per-symbol ATR 正确调用 build_execution_context）。

    [2026-09-17 统一策略修复] 旧实现传 (account.id, symbols, db)，签名实为
    (symbol, entry_price, side, atr, market_regime, confidence)——恒失败回默认值。
    """
    try:
        from backend.services.ai_decision_integration import build_execution_context

        trigger = inp.trigger_context or {}
        side_hint = str(trigger.get("direction") or trigger.get("side") or "long").lower()
        if side_hint not in ("long", "short"):
            side_hint = "long"
        lines = ["Adaptive Trading Parameters (ATR-advised, 参考非指令):"]
        got_any = False
        for sym in (inp.ordered_symbols or [])[:5]:
            df = _load_klines_df(sym)
            if df is None or len(df) < 20 or "high" not in df or "low" not in df or "close" not in df:
                continue
            try:
                tr = (df["high"] - df["low"]).tail(14).mean()
                atr = float(tr) if tr and tr > 0 else None
                entry = float(df["close"].iloc[-1])
                if not atr or not entry:
                    continue
                ec = build_execution_context(sym, entry, side_hint, atr, "unknown", 0.8)
                if ec is None:
                    continue
                sl_f = float(ec.stop_loss_pct)
                tp_f = float(ec.take_profit_pct)
                rr_f = float(ec.risk_reward_ratio)
                # [修复·守卫] 原公式 TP/SL 基数不一致会出现 TP≤SL 或 RR≤0 的病态参数，
                # 这种行不进 prompt（避免误导 LLM），全部病态则回默认话术。
                if tp_f <= sl_f or rr_f <= 0:
                    continue
                got_any = True
                lines.append(
                    f"  {sym}: SL={sl_f:.1%} TP={tp_f:.1%} "
                    f"Lev={float(ec.recommended_leverage):.0f}x RR={rr_f:.1f}"
                )
            except Exception:
                continue
        if got_any:
            return "\n".join(lines)
    except Exception:
        pass
    return "Adaptive trading parameters: using system defaults."


def _build_strategy_wisdom(inp: BuildInput) -> str:
    """Build strategy wisdom from backtest evolution and trade history."""
    trigger = inp.trigger_context or {}
    strategy_id = trigger.get("ai_strategy_id")
    if not strategy_id or not inp.db:
        return "[风控约束] 遵守全局风控参数。\n[回测经验] 暂无足够历史数据。"

    try:
        from backend.database.models import StrategyTrade, TradingWisdom
        trades = inp.db.query(StrategyTrade).filter(
            StrategyTrade.strategy_id == strategy_id,
            StrategyTrade.pnl.isnot(None),
        ).order_by(StrategyTrade.closed_at.desc()).limit(20).all()

        # Fetch compiled wisdom
        wisdom = inp.db.query(TradingWisdom).filter(
            TradingWisdom.strategy_id == strategy_id
        ).order_by(TradingWisdom.created_at.desc()).first()

        lines = ["[风控约束]", "- 遵守全局风控参数和保证金限制"]
        if trades:
            pnls = [t.pnl for t in trades if t.pnl is not None]
            wins = sum(1 for p in pnls if p > 0)
            lines.append(f"- 策略近期胜率: {wins}/{len(pnls)} ({wins/len(pnls)*100:.0f}%)")

        lines.append("\n[回测经验]")
        if wisdom and wisdom.wisdom_text:
            lines.append(wisdom.wisdom_text[:500])
        else:
            lines.append("- 暂无回测进化经验，建议保守操作。")

        return "\n".join(lines)
    except Exception:
        return "[风控约束] 遵守全局风控参数。\n[回测经验] 数据获取失败。"


def _build_trader_personality(inp: BuildInput) -> str:
    """Build trader personality context string."""
    try:
        from backend.database.models import TraderPersonality
        from backend.config.personality_presets import PERSONALITY_PRESETS

        if inp.db:
            tp = inp.db.query(TraderPersonality).filter(
                TraderPersonality.account_id == inp.account.id
            ).first()
            if tp and tp.preset_id and tp.preset_id in PERSONALITY_PRESETS:
                preset = PERSONALITY_PRESETS[tp.preset_id]
                return (
                    f"Trader Personality: {preset.get('name', tp.preset_id)}\n"
                    f"Style: {preset.get('description', 'Custom')}\n"
                    f"Risk tolerance: {preset.get('risk_tolerance', 'medium')}"
                )
    except Exception:
        pass
    return "Trader Personality: Default (balanced)"
