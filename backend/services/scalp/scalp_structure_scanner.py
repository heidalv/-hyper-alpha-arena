"""ScalpStructureScanner — 纯 pandas 结构扫描，无 LLM。"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from backend.services.scalp.scalp_advisory_cache import ScalpAdvisory, scalp_advisory_cache
from backend.services.scalp.structure_stop_calculator import structure_stop_calculator
from backend.services.decision_core.regime_agent import classify_regime

logger = logging.getLogger(__name__)


class ScalpStructureScanner:
    """扫描 swing / 区间 / 猎杀区，写入 ScalpAdvisoryCache。"""

    def scan(self, symbol: str, market_data: Dict[str, Any], orch_data: Optional[Dict] = None) -> ScalpAdvisory:
        sym = (symbol or "").upper()
        klines = (market_data or {}).get("klines")
        swing_low, swing_high, range_pos = structure_stop_calculator.swing_levels(klines)

        stop_clusters: List[str] = []
        try:
            from backend.services.agent_deep_context import build_stop_hunt_block
            block = build_stop_hunt_block(sym, periods=["5m", "15m"])
            for line in (block or "").splitlines():
                if "止损密集区" in line or "止损区@" in line:
                    stop_clusters.extend(
                        [s.strip() for s in line.split(":")[-1].split(";") if s.strip()]
                    )
        except Exception as exc:
            logger.debug("[StructureScanner] stop_hunt %s: %s", sym, exc)

        regime_res = classify_regime(market_data or {})
        orch = orch_data or {}
        long_bias = str(orch.get("long_bias") or "neutral")
        short_bias = str(orch.get("short_bias") or "neutral")
        final_action = str(orch.get("final_action") or orch.get("action") or "wait")

        verdict = "neutral"
        penalty = 0
        notes_parts: List[str] = []

        # [fix 2026-06-30] final_action="wait" 不再判 avoid。
        # 原逻辑把 orchestrator 无信号(wait) 传导成短线 avoid，导致所有币被拦、全天不开仓。
        # wait 只是"中长线暂无方向"，不应硬性阻止短线独立决策。仅 frozen（明确冻结）才 avoid。
        if final_action == "frozen":
            verdict = "avoid"
            penalty += 5
            notes_parts.append(f"orch={final_action}")

        if regime_res.regime == "extreme":
            verdict = "avoid"
            penalty += 10
            notes_parts.append("regime=extreme")

        if range_pos > 0.72:
            verdict = "avoid" if verdict != "allow_short" else verdict
            penalty += 8
            notes_parts.append(f"range_high={range_pos:.2f}")
        elif range_pos < 0.28:
            # [fix 2026-06-30] 低位(range_pos<0.28)不再 penalty。
            # 旧逻辑对低位扣 8 分，但低位恰恰是支撑位附近、做多好时机，
            # 扣分导致所有币在低位时 effective_score 被 veto_band 拦截、全天不开仓。
            # 高位 avoid(追多风险)保留；低位只记录 note 不惩罚。
            notes_parts.append(f"range_low={range_pos:.2f}")

        if long_bias == "bearish" and short_bias == "bearish":
            verdict = "allow_short" if verdict != "avoid" else verdict
            penalty += 5
        elif long_bias == "bullish" and short_bias == "bullish":
            verdict = "allow_long" if verdict != "avoid" else verdict
        elif long_bias == "bullish":
            verdict = "allow_long" if verdict == "neutral" else verdict
        elif long_bias == "bearish":
            verdict = "allow_short" if verdict == "neutral" else verdict

        adv = ScalpAdvisory(
            symbol=sym,
            updated_at=time.time(),
            orch_long_bias=long_bias,
            orch_short_bias=short_bias,
            orch_final_action=final_action,
            regime=regime_res.regime,
            stop_clusters=stop_clusters[:8],
            swing_low_5m=swing_low,
            swing_high_5m=swing_high,
            range_position_5m=range_pos,
            advisory_verdict=verdict,
            penalty=penalty,
            notes="; ".join(notes_parts),
        )
        scalp_advisory_cache.upsert(adv)
        return adv

    @staticmethod
    def parse_cluster_price(cluster_str: str) -> Optional[float]:
        m = re.search(r"@([\d.]+)", cluster_str or "")
        if not m:
            return None
        try:
            return float(m.group(1))
        except ValueError:
            return None


scalp_structure_scanner = ScalpStructureScanner()
