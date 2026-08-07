"""DirectionAgent — finds directional opportunities; does not manage exits."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
# [fix] reasoning 模型把深度推理放在 message.reasoning_content，早期 _call_llm 只读
# content 导致整条思维链被丢弃。统一用公共 helper 捞回。
from backend.services.llm_reasoning_helper import extract_reasoning_content_safe

logger = logging.getLogger(__name__)


class DirectionAgent:
    def decide(
        self,
        *,
        reports: Dict[str, Any],
        symbols: List[str],
        market_envs: Dict[str, Any],
        portfolio: Dict[str, Any],
        strategies: Optional[List[Dict[str, Any]]] = None,
        account_id: Optional[int] = None,
        db=None,
    ) -> Dict[str, Any]:
        prompt = self._build_prompt(
            reports, symbols, market_envs, portfolio, strategies,
            account_id=account_id, db=db,
        )
        result = self._call_llm(prompt, account_id=account_id)
        if result:
            return self._normalize(result, symbols)
        return self._fallback(symbols, market_envs)

    def _build_prompt(self, reports, symbols, market_envs, portfolio, strategies,
                      account_id=None, db=None) -> str:
        from backend.services.analyst_report_builder import compact_report_text
        from backend.services.ai_shared_prompt_context import build_agent_context_block

        context = compact_report_text(
            reports,
            market_envs=market_envs,
            portfolio=portfolio,
            strategies=strategies,
            symbols=symbols,
        )
        strategy_ids = [
            s.get("strategy_id") for s in (strategies or [])
            if isinstance(s, dict) and s.get("strategy_id")
        ]
        feedback_block = build_agent_context_block(
            db=db,
            account_id=account_id,
            strategy_ids=strategy_ids or None,
            role="direction",
        )
        return f"""
你是 DirectionAgent，只负责寻找新方向机会，不负责平仓/减仓已有仓。

规则：
- 只输出 buy/sell/hold/pyramid/dca。
- 不要输出 close/reduce。
- 每个 symbol 必须给一条 decision。
- trade_nature 必须是 scalp/intraday/swing/position/trend_follow 之一。
- short/scalp/intraday 开仓 confidence 建议 ≥ 58%。
- reasoning 最多 60 个中文字符。

{feedback_block}

上下文：
{context}

只返回 JSON：
{{
  "market_assessment": "一句话",
  "decisions": [
    {{
      "symbol": "BTC",
      "action": "hold/buy/sell/pyramid/dca",
      "confidence": 0,
      "reasoning": "原因",
      "trade_nature": "swing",
      "expected_hold_hours": 24,
      "stop_loss_pct": 0.03,
      "take_profit_pct": 0.08,
      "risk_reward_ratio": 2.5
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
            # V5 M4: whale/coin-selector 降频省出的预算投给方向判定 —
            # 提高 max_tokens 上限让深度思考模型有充分推理空间。
            # Pro reasoning 思维链与答案共享额度，8192 偏紧易截断 → 放宽到 16384。
            import os as _os
            _dir_max_tokens = int(_os.getenv("DIRECTION_LLM_MAX_TOKENS", "16384"))
            resp = call_llm_api_sync(
                cfg,
                [
                    {"role": "system", "content": (
                        "你是交易方向识别 Agent，只返回 JSON。"
                        "先在内部充分推理（多周期趋势、动量、资金费率、关键位），"
                        "再给出高确信结论；证据不足时明确输出 hold。"
                    )},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=_dir_max_tokens,
                response_format={"type": "json_object"},
                account_id=account_id,
                caller="DirectionAgent:decide",
            )
            content = (((resp or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if isinstance(content, list):
                content = "\n".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in content)
            # [fix] 捞回 reasoning 模型的深度推理，不再整体丢弃。
            reasoning_cot = extract_reasoning_content_safe(resp or {})
            _finish = ((((resp or {}).get("choices") or [{}])[0].get("finish_reason")) or "")
            if _finish == "length":
                logger.warning("[DirectionAgent] finish_reason=length 推理/答案被截断，考虑调大 DIRECTION_LLM_MAX_TOKENS=%d", _dir_max_tokens)
            elif not reasoning_cot:
                logger.info("[DirectionAgent] reasoning捞回 0 chars（非推理模型或无思维链）| content %d chars | finish=%s", len(content), _finish)
            else:
                logger.info("[DirectionAgent] reasoning捞回 %d chars | content %d chars | finish=%s", len(reasoning_cot), len(content), _finish)
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
            logger.warning("[DirectionAgent] LLM failed: %s", err)
            return None

    def _normalize(self, result: Dict[str, Any], symbols: List[str]) -> Dict[str, Any]:
        allowed = {"hold", "buy", "sell", "pyramid", "dca"}
        decisions = []
        by_symbol = {str((d or {}).get("symbol", "")).upper(): d for d in result.get("decisions", []) if isinstance(d, dict)}
        for sym in symbols:
            raw = by_symbol.get(sym.upper(), {})
            action = str(raw.get("action") or "hold").lower()
            if action not in allowed:
                action = "hold"
            decisions.append({
                "symbol": sym,
                "action": action,
                "confidence": int(max(0, min(100, float(raw.get("confidence", 0) or 0)))),
                "reasoning": str(raw.get("reasoning") or "DirectionAgent无明确信号")[:300],
                "trade_nature": raw.get("trade_nature") or "swing",
                "expected_hold_hours": raw.get("expected_hold_hours") or 24,
                "stop_loss_pct": raw.get("stop_loss_pct") or 0.03,
                "take_profit_pct": raw.get("take_profit_pct") or 0.08,
                "risk_reward_ratio": raw.get("risk_reward_ratio") or 2.5,
            })
        return {"market_assessment": result.get("market_assessment", ""), "decisions": decisions}

    def _fallback(self, symbols: List[str], market_envs: Dict[str, Any]) -> Dict[str, Any]:
        decisions = []
        for sym in symbols:
            orch = ((market_envs or {}).get(sym) or {}).get("orchestrator") or {}
            action = str(orch.get("final_action") or "hold").lower()
            if action not in {"buy", "sell", "hold", "pyramid", "dca"}:
                action = "hold"
            conf = float(orch.get("long_conf") or orch.get("mid_conf") or orch.get("confidence") or 0)
            if conf < 55:
                action = "hold"
            decisions.append({
                "symbol": sym,
                "action": action,
                "confidence": int(max(0, min(100, conf))),
                "reasoning": "DirectionAgent规则回退",
                "trade_nature": orch.get("recommended_nature") or "swing",
                "expected_hold_hours": 24,
                "stop_loss_pct": orch.get("sl_pct") or 0.03,
                "take_profit_pct": orch.get("tp_pct") or 0.08,
                "risk_reward_ratio": 2.5,
            })
        return {"market_assessment": "DirectionAgent fallback", "decisions": decisions}


direction_agent = DirectionAgent()
