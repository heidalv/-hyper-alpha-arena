"""ScalpFlashVeto — 分层 5s Flash Veto（仅 35-44 分，fail-open）。"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from backend.config.settings import (
    SCALP_VETO_MODE,
    SCALP_VETO_TIMEOUT_S,
    get_scalp_veto_fail_open,
)

logger = logging.getLogger(__name__)

_VETO_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["accept", "veto", "downsize"]},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
        "size_multiplier": {"type": "number"},
    },
    "required": ["verdict"],
}


@dataclass
class VetoResult:
    verdict: str = "accept"
    confidence: float = 0.0
    rationale: str = ""
    size_multiplier: float = 1.0
    source: str = "fallback"
    latency_ms: int = 0


class ScalpFlashVeto:
    """Quick-tier LLM veto；超时/解析失败 fail-open。"""

    def should_invoke(self, gate_tier: str, needs_veto: bool) -> bool:
        if SCALP_VETO_MODE == "off":
            return False
        return needs_veto and gate_tier == "veto"

    def evaluate(
        self,
        context_pack: Dict[str, Any],
        account_id: int = 0,
        trading_mode: str = "paper",
    ) -> VetoResult:
        t0 = time.time()
        if SCALP_VETO_MODE == "off":
            return VetoResult(source="disabled", latency_ms=0)

        try:
            from backend.services.llm_config_service import (
                get_llm_config_for_account,
                call_llm_api_sync,
            )
            cfg = get_llm_config_for_account(account_id, tier="quick") if account_id else None
            if not cfg:
                return self._fallback(t0, "no_llm_config", trading_mode)

            prompt = self._build_prompt(context_pack)
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是短线交易审查员。只能输出 JSON。"
                        "verdict 只能是 accept/veto/downsize。"
                        "禁止 approve 弱信号、禁止放宽止损、禁止加杠杆。"
                        "downsize 时 size_multiplier 在 0.3-0.9 之间。"
                    ),
                },
                {"role": "user", "content": prompt},
            ]
            resp = call_llm_api_sync(
                cfg,
                messages,
                temperature=0.1,
                max_tokens=256,
                response_format={"type": "json_object"},
                timeout=float(SCALP_VETO_TIMEOUT_S),
                caller="scalp_flash_veto",
                account_id=account_id or None,
            )
            parsed = self._parse_response(resp)
            if parsed:
                parsed.latency_ms = int((time.time() - t0) * 1000)
                parsed.source = "flash_llm"
                return self._clamp_asymmetric(parsed)
        except Exception as exc:
            logger.warning("[ScalpVeto] 异常(%s): %s", trading_mode, exc)

        return self._fallback(t0, "error_or_timeout", trading_mode)

    def _fallback(self, t0: float, reason: str, trading_mode: str = "paper") -> VetoResult:
        # 2026-07-06 整改：是否 fail-open 按 trading_mode 区分（Live 强制 fail-closed），
        # 不再是进程级单一常量。
        if get_scalp_veto_fail_open(trading_mode):
            return VetoResult(
                verdict="accept",
                confidence=0.0,
                rationale=reason,
                source="fallback",
                latency_ms=int((time.time() - t0) * 1000),
            )
        return VetoResult(
            verdict="veto",
            confidence=0.0,
            rationale=f"fail-closed: {reason}",
            source="fallback",
            latency_ms=int((time.time() - t0) * 1000),
        )

    def _build_prompt(self, ctx: Dict[str, Any]) -> str:
        ohlc = ctx.get("recent_5m_ohlc") or []
        ohlc_txt = json.dumps(ohlc[-3:], ensure_ascii=False)
        adv = ctx.get("advisory") or {}
        factor_txt = self._format_factor_breakdown(ctx.get("factor_breakdown"))
        return (
            f"symbol={ctx.get('symbol')} side={ctx.get('side')} score={ctx.get('score')}\n"
            f"entry={ctx.get('entry')} sl={ctx.get('sl')} tp={ctx.get('tp')}\n"
            f"advisory_verdict={adv.get('advisory_verdict')} "
            f"orch_long={adv.get('orch_long_bias')} orch_short={adv.get('orch_short_bias')}\n"
            f"range_position={adv.get('range_position_5m')} regime={adv.get('regime')}\n"
            f"stop_clusters={adv.get('stop_clusters')}\n"
            f"recent_5m={ohlc_txt}\n"
            f"{factor_txt}\n"
            '回复 JSON: {"verdict":"accept|veto|downsize","confidence":0-1,'
            '"rationale":"...","size_multiplier":1.0}'
        )

    def _format_factor_breakdown(self, breakdown: Optional[Dict[str, Any]]) -> str:
        """把因子明细 + cycle_prob(AI概率引擎)信号摘要成一行，供边缘裁决参考。

        2026-07-06：此前 Flash Veto 只能看到一个总分，看不到分数是怎么来的；
        现在把 ScalpFactorRouter 算好的 factor_breakdown（含 cycle_prob_* 字段，
        来自 scalp_fusion_scorer 的 AI 概率引擎融合）一并展示，让LLM边缘裁决时
        能看到"是哪几个因子在起作用、AI概率引擎支持还是反对这个方向"。
        """
        if not breakdown or not isinstance(breakdown, dict):
            return "factor_breakdown=无"
        parts = []
        for k, v in breakdown.items():
            try:
                if isinstance(v, float):
                    parts.append(f"{k}={v:.3f}")
                else:
                    parts.append(f"{k}={v}")
            except Exception:
                continue
        line = "factor_breakdown: " + ", ".join(parts[:12])
        cp_dir = breakdown.get("cycle_prob_dir")
        cp_calib = breakdown.get("cycle_prob_calibration")
        if cp_dir is not None:
            line += (
                f"\nAI概率引擎(cycle_prob): 方向={cp_dir} "
                f"校准质量={cp_calib}（质量越低越不可信，仅供参考）"
            )
        return line

    def _parse_response(self, resp: Optional[Dict]) -> Optional[VetoResult]:
        if not resp:
            return None
        content = ""
        try:
            choices = resp.get("choices") or []
            if choices:
                content = (choices[0].get("message") or {}).get("content") or ""
        except Exception:
            return None
        if not content:
            return None
        try:
            data = json.loads(content.strip())
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start < 0 or end <= start:
                return None
            try:
                data = json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                return None

        verdict = str(data.get("verdict", "accept")).lower()
        if verdict not in ("accept", "veto", "downsize"):
            verdict = "accept"
        return VetoResult(
            verdict=verdict,
            confidence=float(data.get("confidence", 0) or 0),
            rationale=str(data.get("rationale", ""))[:300],
            size_multiplier=float(data.get("size_multiplier", 1.0) or 1.0),
        )

    def _clamp_asymmetric(self, result: VetoResult) -> VetoResult:
        """非对称：只允许 veto/downsize。"""
        if result.verdict == "downsize":
            result.size_multiplier = max(0.3, min(0.9, result.size_multiplier))
        else:
            result.size_multiplier = 1.0
        return result

    def record_audit(
        self,
        db,
        *,
        symbol: str,
        score: int,
        verdict: str,
        latency_ms: int,
        source: str,
        lane_decision_id: str = "",
        account_id: int = 0,
        rationale: str = "",
    ) -> None:
        try:
            from backend.database.models import ScalpVetoAudit
            row = ScalpVetoAudit(
                account_id=account_id or 0,
                symbol=(symbol or "").upper(),
                score=score,
                verdict=verdict,
                latency_ms=latency_ms,
                source=source,
                lane_decision_id=lane_decision_id or "",
                rationale=(rationale or "")[:500],
            )
            db.add(row)
            db.commit()
        except Exception as exc:
            logger.debug("[ScalpVeto] audit write skip: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass


scalp_flash_veto = ScalpFlashVeto()
