"""Shared compact prompt builders for dual-agent decision flow."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def compact_report_text(
    reports: Dict[str, Any],
    *,
    market_envs: Optional[Dict[str, Any]] = None,
    portfolio: Optional[Dict[str, Any]] = None,
    strategies: Optional[List[Dict[str, Any]]] = None,
    symbols: Optional[List[str]] = None,
    max_chars: int = 40000,
) -> str:
    parts: list[str] = []
    for name, report in (reports or {}).items():
        if not report:
            continue
        r = report.to_dict() if hasattr(report, "to_dict") else report
        parts.append(
            f"### {r.get('analyst', name)} risk={r.get('risk_score', 50)}\n"
            f"summary={r.get('summary', '')}\nrecommendation={r.get('recommendation', '')}\n"
            f"signals={json.dumps((r.get('signals') or [])[:12], ensure_ascii=False)[:2000]}"
        )

    positions = (portfolio or {}).get("positions") or []
    if positions:
        pos_lines = []
        for p in positions[:30]:
            if not isinstance(p, dict):
                continue
            pos_lines.append({
                "id": p.get("id"),
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "nature": p.get("trade_nature"),
                "entry": p.get("entry_price"),
                "mark": p.get("mark_price"),
                "pnl": p.get("unrealized_pnl"),
                "margin": p.get("margin"),
                "sl": p.get("sl_price") or p.get("stop_loss_price"),
                "tp": p.get("tp_price") or p.get("take_profit_price"),
                "health": p.get("trend_health"),
                "reversal": p.get("reversal_signal"),
            })
        parts.append("### open_positions\n" + json.dumps(pos_lines, ensure_ascii=False)[:4000])

    if market_envs:
        env_compact = {}
        for sym in (symbols or list(market_envs.keys()))[:40]:
            env = market_envs.get(sym) or {}
            if not isinstance(env, dict):
                continue
            # 修复 BUG D/E/G：注入更多数据 + 修正键名
            # [2026-07-10 数据可信标记] 对占位即误导的字段，把占位值替换为 "N/A"，
            # 让 LLM 明确知道"该项数据不可用"而非"该项是中性值"。占位判定基于
            # dataclass 默认值：funding=0/fear_greed=50/whale=0/ls_ratio=1.0 都是
            # 取数失败时的静默默认，与真实中性无法区分。
            import math as _math
            def _mark_na(v, sentinel, tol=0.0):
                """值等于占位 sentinel（容差 tol）→ 返回 'N/A'，NaN 也返回 'N/A'。"""
                if v is None:
                    return "N/A"
                try:
                    fv = float(v)
                    if _math.isnan(fv):
                        return "N/A"
                    if abs(fv - sentinel) <= tol:
                        return "N/A"
                except (TypeError, ValueError):
                    pass
                return v

            env_compact[sym] = {
                "price": env.get("price") or env.get("current_price") or env.get("last_price"),
                "regime": env.get("regime") or env.get("market_cycle"),  # 修复键名 regime→market_cycle
                "orchestrator": env.get("orchestrator"),
                # 修复 BUG D：注入关键指标供 SwingAgent/TrendAgent 的 LLM 使用
                "trend_direction": env.get("trend_direction"),
                "volatility": env.get("volatility_regime"),
                "atr": _mark_na(env.get("atr_value"), 0.0),  # ATR=0 是占位/秒止损风险
                "funding_rate": _mark_na(env.get("funding_rate"), 0.0),  # 0=取数失败占位
                "fear_greed": _mark_na(env.get("fear_greed") or env.get("sentiment_index"), 50.0, 0.5),  # 50=情绪占位
                "factor_signal": env.get("factor_v3") or env.get("factor_direction"),
                "factor_strength": env.get("factor_strength"),
                "whale_direction": _mark_na(env.get("whale_direction"), 0.0),  # 0=无鲸鱼数据占位
                "derivatives_signal": env.get("derivatives_signal"),
                # 修复 B：多周期 K线指标注入（SwingAgent 看 1h/4h，TrendAgent 看 4h/1d）
                # 原缺失导致 agent 的深度思考没有实际 K线/指标数据
                "indicators_1h": env.get("indicators_1h"),
                "indicators_4h": env.get("indicators_4h"),
                "indicators_1d": env.get("indicators_1d"),
                "klines_1h": env.get("klines_1h"),
                "klines_4h": env.get("klines_4h"),
                "klines_1d": env.get("klines_1d"),
                # Fix 16b: 链上/宏观/期权数据（agent 全维度市场感知）
                "onchain_macro": env.get("onchain_macro"),
                # Fix 21: 教训回流（SwingAgent/TrendAgent 也能看到历史教训）
                "strategy_lessons": env.get("strategy_lessons"),
            }
        parts.append("### market_envs\n" + json.dumps(env_compact, ensure_ascii=False)[:30000])

    if strategies:
        parts.append("### strategy_memories\n" + json.dumps(strategies[:12], ensure_ascii=False)[:3000])

    # 2026-06-19: P1 跨 tier 持仓可见性 — 注入 portfolio 让 LLM 知道其他 tier 的持仓
    if portfolio and isinstance(portfolio, dict):
        _positions = portfolio.get("positions") or portfolio.get("open_positions") or []
        if _positions and isinstance(_positions, list):
            # 精简持仓信息：只保留关键字段（symbol/side/nature/pnl/tier）
            _pos_compact = []
            for p in _positions[:20]:
                if not isinstance(p, dict):
                    continue
                _pos_compact.append({
                    "symbol": p.get("symbol", ""),
                    "side": p.get("side", ""),
                    "nature": p.get("trade_nature", ""),
                    "tier": p.get("timeframe_tier", ""),
                    "pnl_pct": round(p.get("pnl_pct", 0) or 0, 1),
                    "margin": round(p.get("margin", 0) or 0, 0),
                })
            if _pos_compact:
                parts.append("### cross_tier_positions（其他周期持仓，分析时请参考）\n"
                             + json.dumps(_pos_compact, ensure_ascii=False)[:2000])

    text = "\n\n".join(parts)
    return text[:max_chars]
