"""TradeRiskAgent — reviews entries and manages open-position exit risk."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
# [fix] reasoning 模型把深度推理放在 message.reasoning_content，早期 _call_llm 只读
# content 导致整条思维链被丢弃。统一用公共 helper 捞回。
from backend.services.llm_reasoning_helper import extract_reasoning_content_safe

logger = logging.getLogger(__name__)


class TradeRiskAgent:
    def review(
        self,
        *,
        direction_output: Dict[str, Any],
        reports: Dict[str, Any],
        positions: List[Dict[str, Any]],
        symbols: List[str],
        market_envs: Dict[str, Any],
        portfolio: Dict[str, Any],
        hold_timeout_alerts: Optional[List[Dict[str, Any]]] = None,
        strategies: Optional[List[Dict[str, Any]]] = None,
        account_id: Optional[int] = None,
        db=None,
    ) -> Dict[str, Any]:
        if not self._should_call_llm(direction_output, positions):
            return self._fallback(direction_output, positions, symbols)
        prompt = self._build_prompt(
            direction_output, reports, positions, market_envs, portfolio,
            hold_timeout_alerts, strategies, account_id=account_id, db=db,
        )
        result = self._call_llm(prompt, account_id=account_id)
        if result:
            return self._normalize(result, direction_output, symbols)
        return self._fallback(direction_output, positions, symbols)

    def _should_call_llm(self, direction_output: Dict[str, Any], positions: List[Dict[str, Any]]) -> bool:
        for d in direction_output.get("decisions", []):
            if str(d.get("action", "hold")).lower() in ("buy", "sell", "pyramid", "dca"):
                return True
        for p in positions or []:
            health = p.get("trend_health") or {}
            reversal = p.get("reversal_signal") or {}
            pnl = float(p.get("unrealized_pnl") or 0)
            margin = float(p.get("margin") or 0)
            pnl_pct_margin = pnl / margin if margin > 0 else 0
            if pnl_pct_margin > 0.03:
                return True
            if health and float(health.get("score", 100) or 100) <= float(health.get("nature_adjusted_threshold", 40) or 40) + 10:
                return True
            if reversal and reversal.get("level") in ("structure_warning", "confirmed_reversal"):
                return True
            if not (p.get("sl_price") or p.get("stop_loss_price")):
                return True
        return False

    def _build_prompt(self, direction_output, reports, positions, market_envs, portfolio,
                      hold_timeout_alerts, strategies, account_id=None, db=None) -> str:
        from backend.services.analyst_report_builder import compact_report_text
        from backend.services.ai_shared_prompt_context import build_agent_context_block

        context = compact_report_text(
            reports,
            market_envs=market_envs,
            portfolio=portfolio,
            strategies=strategies,
            symbols=list(market_envs.keys()) if isinstance(market_envs, dict) else [],
        )
        strategy_ids = [
            s.get("strategy_id") for s in (strategies or [])
            if isinstance(s, dict) and s.get("strategy_id")
        ]
        feedback_block = build_agent_context_block(
            db=db,
            account_id=account_id,
            strategy_ids=strategy_ids or None,
            role="risk",
        )
        return f"""
你是 TradeRiskAgent，负责审核所有交易动作和已有仓退出风险。

重要规则：
- 新开仓必须审核：可以允许、拒绝、降低仓位(size_multiplier)或降低杠杆(leverage_cap)。
- size_multiplier 范围 0.3~1.0，禁止 >1.0。
- 已有仓只输出 hold/reduce/close/adjust_sl/extend_hold_hours。
- 健康分低只能触发复审，不能单独强制 reduce。
- 规则强制 reduce 需要至少两类证据：健康分低、结构预警、峰值回撤、长周期反向、SL缺失/保护不足。
- pullback 是正常回调，默认 hold 或上移 SL，不要轻易 reduce。
- confirmed_reversal 可 close 或 reduce 50%+。
- reasoning 最多 80 个中文字符。

{feedback_block}

DirectionAgent 输出：
{json.dumps(direction_output, ensure_ascii=False)[:4000]}

hold_timeout_alerts:
{json.dumps(hold_timeout_alerts or [], ensure_ascii=False)[:2000]}

上下文：
{context}

只返回 JSON：
{{
  "risk_level": "low/medium/high/critical",
  "decisions": [
    {{
      "symbol": "BTC",
      "action": "hold/buy/sell/pyramid/dca/reduce/close/reject_entry",
      "confidence": 0,
      "reasoning": "原因",
      "trade_nature": "swing",
      "partial_close_pct": null,
      "adjust_sl": null,
      "extend_hold_hours": 0,
      "size_multiplier": 1.0,
      "leverage_cap": null
    }}
  ]
}}
"""

    def _call_llm(self, prompt: str, account_id: Optional[int]) -> Optional[Dict[str, Any]]:
        try:
            from backend.services.llm_config_service import get_llm_config_for_analysis, call_llm_api_sync

            cfg = get_llm_config_for_analysis(account_id)
            if not cfg:
                return None
            import os as _os
            # Pro reasoning 思维链与答案共享额度，4096 偏紧易截断 → 放宽到 8192，可经环境变量覆盖。
            _risk_max_tokens = int(_os.getenv("TRADE_RISK_LLM_MAX_TOKENS", "8192"))
            resp = call_llm_api_sync(
                cfg,
                [
                    {"role": "system", "content": "你是交易风控 Agent，只返回 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.15,
                max_tokens=_risk_max_tokens,
                response_format={"type": "json_object"},
                account_id=account_id,
                caller="TradeRiskAgent:review",
            )
            content = (((resp or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if isinstance(content, list):
                content = "\n".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in content)
            # [fix] 捞回 reasoning 模型的深度推理，不再整体丢弃。
            reasoning_cot = extract_reasoning_content_safe(resp or {})
            _finish = ((((resp or {}).get("choices") or [{}])[0].get("finish_reason")) or "")
            if _finish == "length":
                logger.warning("[TradeRiskAgent] finish_reason=length 推理/答案被截断，考虑调大 TRADE_RISK_LLM_MAX_TOKENS=%d", _risk_max_tokens)
            elif not reasoning_cot:
                logger.info("[TradeRiskAgent] reasoning捞回 0 chars（非推理模型或无思维链）| content %d chars | finish=%s", len(content), _finish)
            else:
                logger.info("[TradeRiskAgent] reasoning捞回 %d chars | content %d chars | finish=%s", len(reasoning_cot), len(content), _finish)
            try:
                result = json.loads(content)
            except Exception:
                match = re.search(r"\{.*\}", str(content), re.S)
                result = json.loads(match.group()) if match else None
            # 透传完整思维链供下游决策记录持久化（上限保护防超大）
            if isinstance(result, dict):
                result["_reasoning_content"] = reasoning_cot[:6000]
            return result
        except Exception as err:
            logger.warning("[TradeRiskAgent] LLM failed: %s", err)
            return None

    def _normalize(self, result: Dict[str, Any], direction_output: Dict[str, Any], symbols: List[str]) -> Dict[str, Any]:
        allowed = {"hold", "buy", "sell", "pyramid", "dca", "reduce", "close", "reject_entry"}
        dir_by_symbol = {d.get("symbol", "").upper(): d for d in direction_output.get("decisions", [])}
        raw_by_symbol = {str((d or {}).get("symbol", "")).upper(): d for d in result.get("decisions", []) if isinstance(d, dict)}
        decisions = []
        for sym in symbols:
            raw = raw_by_symbol.get(sym.upper()) or dir_by_symbol.get(sym.upper()) or {"symbol": sym, "action": "hold"}
            action = str(raw.get("action") or "hold").lower()
            if action not in allowed:
                action = "hold"
            if action == "reject_entry":
                action = "hold"
            size_mult = float(raw.get("size_multiplier") or 1.0)
            size_mult = max(0.3, min(1.0, size_mult))
            lev_cap = raw.get("leverage_cap")
            try:
                lev_cap = int(lev_cap) if lev_cap is not None else None
            except (TypeError, ValueError):
                lev_cap = None
            decisions.append({
                "symbol": sym,
                "action": action,
                "confidence": int(max(0, min(100, float(raw.get("confidence", 0) or 0)))),
                "reasoning": str(raw.get("reasoning") or "TradeRiskAgent审核")[:300],
                "trade_nature": raw.get("trade_nature") or (dir_by_symbol.get(sym.upper()) or {}).get("trade_nature") or "swing",
                "partial_close_pct": raw.get("partial_close_pct"),
                "adjust_sl": raw.get("adjust_sl"),
                "extend_hold_hours": raw.get("extend_hold_hours") or 0,
                "size_multiplier": size_mult,
                "leverage_cap": lev_cap,
                "stop_loss_pct": raw.get("stop_loss_pct") or (dir_by_symbol.get(sym.upper()) or {}).get("stop_loss_pct"),
                "take_profit_pct": raw.get("take_profit_pct") or (dir_by_symbol.get(sym.upper()) or {}).get("take_profit_pct"),
            })
        return {"risk_level": result.get("risk_level", "medium"), "decisions": decisions}

    def _fallback(self, direction_output: Dict[str, Any], positions: List[Dict[str, Any]], symbols: List[str]) -> Dict[str, Any]:
        decisions = {d.get("symbol", "").upper(): dict(d) for d in direction_output.get("decisions", [])}
        for p in positions or []:
            sym = str(p.get("symbol") or "").upper()
            if not sym:
                continue
            health = p.get("trend_health") or {}
            reversal = p.get("reversal_signal") or {}
            action = "hold"
            confidence = 50
            reason = "TradeRiskAgent规则回退"
            if reversal.get("level") == "confirmed_reversal":
                action, confidence, reason = "reduce", 72, "确认反转，规则建议减仓"
            elif health and float(health.get("score", 100) or 100) < float(health.get("nature_adjusted_threshold", 40) or 40):
                action, confidence, reason = "hold", 60, "健康分低，触发复审但不单独减仓"
            decisions[sym] = {
                "symbol": sym,
                "action": action,
                "confidence": confidence,
                "reasoning": reason,
                "trade_nature": p.get("trade_nature") or "swing",
                "partial_close_pct": 30 if action == "reduce" else None,
                "extend_hold_hours": 0,
            }
        normalized = []
        for sym in symbols:
            d = decisions.get(sym.upper(), {"symbol": sym, "action": "hold", "confidence": 0, "reasoning": "无信号"})
            normalized.append(d)
        return {"risk_level": "medium", "decisions": normalized}


trade_risk_agent = TradeRiskAgent()
