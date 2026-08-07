"""多周期确定性共振分数（4h+1d EMA 排列 + RSI 区）— 与 LLM score 加权融合。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class MTFResonance:
    score: int              # 0-100
    aligned: bool           # 4h+1d 同向
    direction: str          # long / short / neutral
    detail: str = ""

    def blend_with_llm(self, llm_score: int, *, llm_weight: float = 0.65) -> int:
        """LLM 分与规则分加权（默认 LLM 65% + 规则 35%）。"""
        llm = max(0, min(100, int(llm_score or 0)))
        rule = max(0, min(100, int(self.score or 0)))
        w = max(0.0, min(1.0, float(llm_weight)))
        return int(round(llm * w + rule * (1.0 - w)))


def _ind(market_data: dict, tf: str) -> dict:
    key = f"indicators_{tf}"
    raw = market_data.get(key) if isinstance(market_data, dict) else None
    return raw if isinstance(raw, dict) else {}


def _ema_dir(ind: dict) -> str:
    trend = (ind.get("ema_trend") or "").lower()
    if trend == "bullish":
        return "long"
    if trend == "bearish":
        return "short"
    ema9 = float(ind.get("ema9") or 0)
    ema21 = float(ind.get("ema21") or 0)
    if ema9 > ema21 > 0:
        return "long"
    if ema9 < ema21 and ema21 > 0:
        return "short"
    return "neutral"


def compute_mtf_resonance(market_data: Optional[dict]) -> MTFResonance:
    """从 market_summary 里的 indicators_4h / indicators_1d 计算共振分。"""
    if not isinstance(market_data, dict):
        return MTFResonance(0, False, "neutral", "no data")

    d4 = _ema_dir(_ind(market_data, "4h"))
    d1 = _ema_dir(_ind(market_data, "1d"))
    rsi4 = float(_ind(market_data, "4h").get("rsi") or 50)
    rsi1 = float(_ind(market_data, "1d").get("rsi") or 50)

    if d4 == "neutral" or d1 == "neutral":
        return MTFResonance(35, False, "neutral", f"4h={d4} 1d={d1}")

    aligned = d4 == d1
    direction = d4 if aligned else "neutral"
    score = 40
    if aligned:
        score = 72
        if direction == "long" and rsi4 >= 45 and rsi1 >= 45:
            score += 8
        elif direction == "short" and rsi4 <= 55 and rsi1 <= 55:
            score += 8
        if 40 <= rsi4 <= 65 and 40 <= rsi1 <= 65:
            score += 5
        score = min(95, score)
    else:
        score = 28

    return MTFResonance(
        score=score,
        aligned=aligned,
        direction=direction,
        detail=f"4h={d4} 1d={d1} rsi4={rsi4:.0f} rsi1={rsi1:.0f}",
    )


def inject_mtf_into_market_summary(market_data: dict) -> Dict[str, Any]:
    """写入 market_summary 供 Agent / 门控只读一次。"""
    mtf = compute_mtf_resonance(market_data)
    market_data["mtf_resonance"] = {
        "score": mtf.score,
        "aligned": mtf.aligned,
        "direction": mtf.direction,
        "detail": mtf.detail,
    }
    return market_data["mtf_resonance"]
