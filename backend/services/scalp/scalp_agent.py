"""ScalpAgent — 轻量参谋摘要（不跑 full analysis）。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ScalpAgent:
    """可选 10min 慢参谋，写 advisory.notes；热路径不调用。"""

    def summarize_advisory(
        self,
        symbol: str,
        advisory_dict: Dict[str, Any],
        account_id: int = 0,
    ) -> Optional[str]:
        try:
            from backend.services.llm_config_service import (
                get_llm_config_for_account,
                call_llm_api_sync,
            )
            cfg = get_llm_config_for_account(account_id, tier="quick") if account_id else None
            if not cfg:
                return None
            prompt = (
                f"用一句中文总结 {symbol} 短线参谋："
                f"verdict={advisory_dict.get('advisory_verdict')} "
                f"long={advisory_dict.get('orch_long_bias')} "
                f"short={advisory_dict.get('orch_short_bias')} "
                f"range={advisory_dict.get('range_position_5m')} "
                f"regime={advisory_dict.get('regime')}"
            )
            resp = call_llm_api_sync(
                cfg,
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=80,
                timeout=8.0,
                caller="scalp_agent_summary",
                account_id=account_id or None,
            )
            if not resp:
                return None
            choices = resp.get("choices") or []
            if not choices:
                return None
            return ((choices[0].get("message") or {}).get("content") or "").strip()[:200]
        except Exception as exc:
            logger.debug("[ScalpAgent] summary skip: %s", exc)
            return None


scalp_agent = ScalpAgent()
