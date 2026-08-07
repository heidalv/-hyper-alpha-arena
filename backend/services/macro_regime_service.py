"""宏观周期心智服务 — 持久化、慢变量演化的 MacroRegimeState。

聚合 StrategicAnalyst + LongTermPlanner + 1d 趋势，经迟滞平滑写入 DB，
供 MTO / TrendAgent / DCP 读取。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CYCLE_PHASES = ("accumulation", "markup", "distribution", "decline")
DIRECTION_CONSTRAINTS = ("long_only", "short_only", "both", "no_trend_long")

MIN_HOLD_HOURS = int(os.getenv("MACRO_REGIME_MIN_HOLD_HOURS", "24"))
TRANSITION_HOLD_HOURS = int(os.getenv("MACRO_REGIME_TRANSITION_HOLD_HOURS", "12"))
PHASE_SWITCH_THRESHOLD = float(os.getenv("MACRO_REGIME_SWITCH_THRESHOLD", "0.15"))
CONF_EMA_ALPHA = float(os.getenv("MACRO_REGIME_CONF_EMA_ALPHA", "0.3"))
BLOCK_TREND_LONG_CONF = float(os.getenv("MACRO_REGIME_BLOCK_TREND_LONG_CONF", "0.6"))

# MarketCycle.value -> 四阶段映射
_MARKET_CYCLE_TO_PHASE = {
    "bull_trend": "markup",
    "bear_trend": "decline",
    "high_volatility": "distribution",
    "low_volatility": "accumulation",
    "accumulation": "accumulation",
    "distribution": "distribution",
    "unknown": "accumulation",
}

_PHASE_TO_CONSTRAINT = {
    "markup": "long_only",
    "decline": "no_trend_long",
    "accumulation": "both",
    "distribution": "no_trend_long",
}

_PHASE_TO_BIAS = {
    "markup": "bullish",
    "decline": "bearish",
    "accumulation": "neutral",
    "distribution": "bearish",
}


@dataclass
class MacroRegimeState:
    """内存中的宏观周期心智快照。"""

    symbol: str = "GLOBAL"
    cycle_phase: str = "accumulation"
    prev_phase: str = ""
    phase_confidence: float = 0.0
    direction_constraint: str = "both"
    macro_regime: str = "neutral"
    risk_on_score: float = 0.0
    evidence: Dict[str, Any] = field(default_factory=dict)
    transition_signal: bool = False
    updated_at: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    source: str = "planner+macro+smoothing"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.updated_at:
            d["updated_at"] = self.updated_at.isoformat()
        if self.valid_until:
            d["valid_until"] = self.valid_until.isoformat()
        return d

    def blocks_trend_long(self) -> bool:
        """是否禁止 trend_follow/position 开多。"""
        if self.direction_constraint == "short_only":
            return True
        if self.direction_constraint == "no_trend_long" and self.phase_confidence >= BLOCK_TREND_LONG_CONF:
            return True
        if self.cycle_phase == "decline" and self.phase_confidence >= BLOCK_TREND_LONG_CONF:
            return True
        if self.macro_regime == "risk_off" and self.phase_confidence >= BLOCK_TREND_LONG_CONF:
            return True
        return False

    def side_hint(self) -> str:
        """TrendAgent 方向锚点。"""
        dc = (self.direction_constraint or "both").lower()
        if dc == "long_only":
            return "long"
        if dc == "short_only":
            return "short"
        phase = (self.cycle_phase or "accumulation").lower()
        if phase == "markup":
            return "long"
        if phase == "decline":
            return "short"
        bias = _PHASE_TO_BIAS.get(phase, "neutral")
        if bias == "bullish":
            return "long"
        if bias == "bearish":
            return "short"
        ros = float(self.risk_on_score or 0)
        if ros <= -0.15:
            return "short"
        if ros >= 0.15:
            return "long"
        return "long"

    def prompt_block(self) -> str:
        """注入 LLM prompt 的宏观周期心智块。"""
        lines = [
            "### 宏观周期心智（慢变量，优先于短线噪声）",
            f"- 周期阶段: {self.cycle_phase} (置信度 {self.phase_confidence:.0%})",
            f"- 方向约束: {self.direction_constraint}",
            f"- 宏观体制: {self.macro_regime} (risk_on_score={self.risk_on_score:+.2f})",
        ]
        if self.transition_signal:
            lines.append("- **体制转换信号: 宏观环境可能转变，谨慎追势**")
        if self.blocks_trend_long():
            lines.append("- **硬约束: 当前阶段禁止开趋势多单 (trend_follow/position buy)**")
        if (self.direction_constraint or "").lower() == "short_only":
            lines.append("- **方向约束: 宏观阶段优先做空/禁多 (short_only)**")
        if self.evidence:
            ev = self.evidence
            parts = []
            for k in ("market_cycle", "fgi", "adx_1d", "sma200_position"):
                if k in ev and ev[k] is not None:
                    parts.append(f"{k}={ev[k]}")
            if parts:
                lines.append(f"- 判定依据: {', '.join(parts)}")
        return "\n".join(lines)


def _default_state(symbol: str = "GLOBAL") -> MacroRegimeState:
    now = datetime.now(timezone.utc)
    return MacroRegimeState(
        symbol=symbol,
        cycle_phase="accumulation",
        phase_confidence=0.0,
        direction_constraint="both",
        macro_regime="neutral",
        updated_at=now,
        valid_until=now + timedelta(hours=MIN_HOLD_HOURS),
    )


def _score_phases(
    *,
    market_cycle: str,
    macro_regime: str,
    risk_on_score: float,
    fgi: Optional[float],
    adx_1d: Optional[float],
    sma200_position: Optional[float],
    position_bias: str = "neutral",
) -> Dict[str, float]:
    scores = {p: 0.0 for p in CYCLE_PHASES}

    phase_from_cycle = _MARKET_CYCLE_TO_PHASE.get(
        (market_cycle or "unknown").lower(), "accumulation"
    )
    scores[phase_from_cycle] += 0.40

    regime = (macro_regime or "neutral").lower()
    if regime == "risk_on":
        scores["markup"] += 0.20
    elif regime == "risk_off":
        scores["decline"] += 0.20
    elif regime == "transition":
        scores["distribution"] += 0.10
        scores["accumulation"] += 0.10

    ros = float(risk_on_score or 0)
    if ros > 0.3:
        scores["markup"] += 0.10
    elif ros < -0.3:
        scores["decline"] += 0.10

    if fgi is not None:
        if fgi < 25:
            scores["accumulation"] += 0.15
        elif fgi > 75:
            scores["distribution"] += 0.15
        elif fgi < 40:
            scores["accumulation"] += 0.08
        elif fgi > 60:
            scores["distribution"] += 0.08

    if adx_1d is not None:
        if adx_1d > 25 and sma200_position is not None:
            if sma200_position > 0.5:
                scores["markup"] += 0.12
            else:
                scores["decline"] += 0.12
        elif adx_1d < 15:
            scores["accumulation"] += 0.10

    pb = (position_bias or "neutral").lower()
    if pb in ("long", "bullish"):
        scores["markup"] += 0.08
    elif pb in ("short", "bearish"):
        scores["decline"] += 0.08

    return scores


def _constraint_for_phase(
    phase: str,
    macro_regime: str,
    phase_confidence: float = 0.0,
) -> str:
    """周期阶段 → 方向约束；高置信 decline/risk_off 升级为 short_only（促空而非仅禁多）。"""
    base = _PHASE_TO_CONSTRAINT.get(phase, "both")
    conf = float(phase_confidence or 0)
    if phase in ("decline", "distribution") and conf >= BLOCK_TREND_LONG_CONF:
        return "short_only"
    if macro_regime == "risk_off":
        if conf >= BLOCK_TREND_LONG_CONF:
            return "short_only"
        if base in ("both", "long_only"):
            return "no_trend_long"
    if macro_regime == "risk_on" and base == "both":
        return "both"
    return base


class MacroRegimeService:
    """宏观周期心智单例服务。"""

    _instance: Optional["MacroRegimeService"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._cache: Dict[str, MacroRegimeState] = {}
        return cls._instance

    def get_state(self, symbol: str = "GLOBAL", db=None) -> MacroRegimeState:
        sym = (symbol or "GLOBAL").upper()
        cache_key = sym if sym in ("GLOBAL", "BTC") else "GLOBAL"

        if cache_key in self._cache:
            return self._cache[cache_key]

        state = self._load_from_db(cache_key, db=db)
        self._cache[cache_key] = state
        return state

    def invalidate_cache(self, symbol: str = "GLOBAL") -> None:
        sym = (symbol or "GLOBAL").upper()
        self._cache.pop(sym, None)
        self._cache.pop("GLOBAL", None)

    def _load_from_db(self, symbol: str, db=None) -> MacroRegimeState:
        try:
            if db is None:
                try:
                    from backend.database.connection import AnalyticsSessionLocal
                except ImportError:
                    from database.connection import AnalyticsSessionLocal
                db = AnalyticsSessionLocal()
                own_session = True
            else:
                own_session = False

            try:
                from backend.services.strategic_analyst.db_models import MacroRegimeStateRecord
            except ImportError:
                from services.strategic_analyst.db_models import MacroRegimeStateRecord

            row = (
                db.query(MacroRegimeStateRecord)
                .filter(MacroRegimeStateRecord.symbol == symbol)
                .order_by(MacroRegimeStateRecord.updated_at.desc())
                .first()
            )
            if own_session:
                db.close()

            if not row:
                return _default_state(symbol)

            evidence = {}
            if row.evidence_json:
                try:
                    evidence = json.loads(row.evidence_json)
                except Exception:
                    pass

            updated_at = row.updated_at
            if updated_at and updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)

            valid_until = row.valid_until
            if valid_until and valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=timezone.utc)

            return MacroRegimeState(
                symbol=row.symbol,
                cycle_phase=row.cycle_phase or "accumulation",
                prev_phase=row.prev_phase or "",
                phase_confidence=float(row.phase_confidence or 0),
                direction_constraint=row.direction_constraint or "both",
                macro_regime=row.macro_regime or "neutral",
                risk_on_score=float(row.risk_on_score or 0),
                evidence=evidence,
                transition_signal=str(row.transition_signal or "").lower() == "true",
                updated_at=updated_at,
                valid_until=valid_until,
                source=row.source or "planner+macro+smoothing",
            )
        except Exception as e:
            logger.debug(f"[MacroRegime] DB 读取失败 {symbol}: {e}")
            return _default_state(symbol)

    def _persist(self, state: MacroRegimeState, db=None) -> None:
        try:
            if db is None:
                try:
                    from backend.database.connection import AnalyticsSessionLocal
                except ImportError:
                    from database.connection import AnalyticsSessionLocal
                db = AnalyticsSessionLocal()
                own_session = True
            else:
                own_session = False

            try:
                from backend.services.strategic_analyst.db_models import MacroRegimeStateRecord
            except ImportError:
                from services.strategic_analyst.db_models import MacroRegimeStateRecord

            now = datetime.now(timezone.utc)
            row = MacroRegimeStateRecord(
                symbol=state.symbol,
                cycle_phase=state.cycle_phase,
                prev_phase=state.prev_phase or None,
                phase_confidence=state.phase_confidence,
                direction_constraint=state.direction_constraint,
                macro_regime=state.macro_regime,
                risk_on_score=state.risk_on_score,
                evidence_json=json.dumps(state.evidence, ensure_ascii=False),
                transition_signal="true" if state.transition_signal else "false",
                updated_at=now,
                valid_until=state.valid_until,
                source=state.source,
            )
            db.add(row)
            db.commit()
            if own_session:
                db.close()

            self._cache[state.symbol] = state
            logger.info(
                f"[MacroRegime] 持久化 {state.symbol}: phase={state.cycle_phase} "
                f"conf={state.phase_confidence:.0%} constraint={state.direction_constraint} "
                f"macro={state.macro_regime}"
            )
        except Exception as e:
            logger.warning(f"[MacroRegime] DB 写入失败: {e}")
            try:
                if own_session:
                    db.rollback()
                    db.close()
            except Exception:
                pass

    def update_from_sources(
        self,
        *,
        strategic_report=None,
        snapshot=None,
        symbol: str = "GLOBAL",
        db=None,
    ) -> MacroRegimeState:
        """从战略报告 + 数据池快照聚合评分，迟滞平滑后落库。"""
        sym = (symbol or "GLOBAL").upper()
        old = self.get_state(sym, db=db)

        macro_regime = "neutral"
        risk_on_score = 0.0
        transition_signal = False
        market_cycle = "unknown"
        position_bias = "neutral"
        cycle_confidence = 0.0
        fgi = None

        if strategic_report is not None:
            ma = getattr(strategic_report, "macro_assessment", None)
            if ma:
                macro_regime = getattr(ma, "regime", "neutral") or "neutral"
                risk_on_score = float(getattr(ma, "risk_on_score", 0) or 0)
                transition_signal = bool(getattr(ma, "regime_transition_signal", False))
            market_cycle = getattr(strategic_report, "market_cycle_phase", "unknown") or "unknown"
            if hasattr(strategic_report, "macro_bias"):
                mb = (strategic_report.macro_bias or "neutral").lower()
                if mb in ("bullish", "long"):
                    position_bias = "long"
                elif mb in ("bearish", "short"):
                    position_bias = "short"
            cycle_confidence = float(getattr(strategic_report, "macro_confidence", 0) or 0)

        adx_1d = None
        sma200_position = None

        if snapshot is not None:
            per_sym = getattr(snapshot, "per_symbol_planning", {}) or {}
            btc_plan = per_sym.get("BTC") or per_sym.get("btc")
            if btc_plan:
                mc = getattr(btc_plan, "market_cycle", None)
                if mc is not None:
                    market_cycle = mc.value if hasattr(mc, "value") else str(mc)
                position_bias = getattr(btc_plan, "position_bias", position_bias) or position_bias
                cycle_confidence = max(
                    cycle_confidence,
                    float(getattr(btc_plan, "cycle_confidence", 0) or 0),
                )
                mr = getattr(btc_plan, "macro_regime", None)
                if mr and mr != "unknown":
                    macro_regime = mr

            try:
                ind = snapshot.indicators.get("BTC", {}) if hasattr(snapshot, "indicators") else {}
                adx_1d = float(ind.get("adx_1d") or ind.get("adx") or 0) or None
                if ind.get("sma200") and ind.get("close"):
                    sma200_position = 1.0 if float(ind["close"]) > float(ind["sma200"]) else 0.0
            except Exception:
                pass

            try:
                intel = getattr(snapshot, "intel_signals", {}) or {}
                btc_intel = intel.get("BTC") or intel.get("btc")
                if btc_intel and hasattr(btc_intel, "fear_greed_index"):
                    fgi = float(btc_intel.fear_greed_index)
            except Exception:
                pass

        if fgi is None and strategic_report is not None:
            try:
                snap_data = getattr(strategic_report, "snapshot_data", {}) or {}
                fgi = snap_data.get("fear_greed_index")
                if fgi is not None:
                    fgi = float(fgi)
            except Exception:
                pass

        scores = _score_phases(
            market_cycle=market_cycle,
            macro_regime=macro_regime,
            risk_on_score=risk_on_score,
            fgi=fgi,
            adx_1d=adx_1d,
            sma200_position=sma200_position,
            position_bias=position_bias,
        )

        best_phase = max(scores, key=scores.get)
        best_score = scores[best_phase]
        raw_conf = min(1.0, max(abs(best_score), cycle_confidence, abs(risk_on_score)))

        now = datetime.now(timezone.utc)
        old_phase = old.cycle_phase or "accumulation"
        old_score = scores.get(old_phase, 0.0)

        hours_since = MIN_HOLD_HOURS
        if old.updated_at:
            hours_since = (now - old.updated_at).total_seconds() / 3600.0

        min_hold = TRANSITION_HOLD_HOURS if transition_signal else MIN_HOLD_HOURS
        new_phase = old_phase
        if best_phase != old_phase:
            if best_score > old_score + PHASE_SWITCH_THRESHOLD and hours_since >= min_hold:
                new_phase = best_phase
            elif old.phase_confidence < 0.25 and best_score > 0.35:
                new_phase = best_phase

        new_conf = (1 - CONF_EMA_ALPHA) * old.phase_confidence + CONF_EMA_ALPHA * raw_conf
        new_conf = min(1.0, max(0.0, new_conf))

        constraint = _constraint_for_phase(new_phase, macro_regime, phase_confidence=new_conf)

        evidence = {
            "market_cycle": market_cycle,
            "macro_regime": macro_regime,
            "risk_on_score": risk_on_score,
            "fgi": fgi,
            "adx_1d": adx_1d,
            "sma200_position": sma200_position,
            "position_bias": position_bias,
            "phase_scores": scores,
            "raw_best_phase": best_phase,
        }

        new_state = MacroRegimeState(
            symbol=sym,
            cycle_phase=new_phase,
            prev_phase=old_phase if new_phase != old_phase else old.prev_phase,
            phase_confidence=new_conf,
            direction_constraint=constraint,
            macro_regime=macro_regime,
            risk_on_score=risk_on_score,
            evidence=evidence,
            transition_signal=transition_signal,
            updated_at=now,
            valid_until=now + timedelta(hours=min_hold),
            source="planner+macro+smoothing",
        )

        self._persist(new_state, db=db)
        if new_phase != old_phase:
            self._notify_mlto_phase_shift(sym, old_phase, new_phase, db=db)
        return new_state

    def _notify_mlto_phase_shift(
        self,
        symbol: str,
        old_phase: str,
        new_phase: str,
        db=None,
    ) -> None:
        """宏观 phase 切换时触发 MLTO thesis 降权重置。"""
        try:
            from backend.config.settings import MIDLONG_THESIS_REGIME_RESET
            if not MIDLONG_THESIS_REGIME_RESET:
                return
            from backend.services.mlto import thesis_store
            n = thesis_store.reset_all_for_macro_phase(
                old_phase, new_phase, macro_symbol=symbol, db=db,
            )
            if n:
                logger.info(
                    "[MacroRegime] MLTO regime_reset: %s→%s, %d thesis(es)",
                    old_phase, new_phase, n,
                )
        except Exception as exc:
            logger.debug("[MacroRegime] MLTO phase hook skip: %s", exc)

    def inject_orchestrator_fields(self, orch: dict, symbol: str = "GLOBAL", db=None) -> dict:
        """将宏观周期心智字段注入 orchestrator dict。"""
        state = self.get_state(symbol, db=db)
        orch = dict(orch or {})
        orch["macro_cycle_phase"] = state.cycle_phase
        orch["macro_phase_confidence"] = state.phase_confidence
        orch["macro_direction_constraint"] = state.direction_constraint
        orch["macro_regime"] = state.macro_regime
        orch["macro_risk_on_score"] = state.risk_on_score
        orch["macro_blocks_trend_long"] = state.blocks_trend_long()
        orch["macro_transition_signal"] = state.transition_signal
        # 宏观 short_only 覆盖编排器 allowed_direction（熊市促空，不只禁多）
        if (state.direction_constraint or "").lower() == "short_only":
            orch["allowed_direction"] = "short_only"
        elif (state.direction_constraint or "").lower() == "long_only":
            orch["allowed_direction"] = "long_only"
        return orch


macro_regime_service = MacroRegimeService()
