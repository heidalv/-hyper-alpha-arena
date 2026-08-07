"""TrendAgent 预测复核 — scenario A/B/C 落库、复查快照、平仓命中率评分。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _session(db=None):
    if db is not None:
        return db, False
    try:
        from backend.database.connection import AnalyticsSessionLocal
    except ImportError:
        from database.connection import AnalyticsSessionLocal
    return AnalyticsSessionLocal(), True


def _import_model():
    try:
        from backend.services.strategic_analyst.db_models import TrendPredictionRecord
    except ImportError:
        from services.strategic_analyst.db_models import TrendPredictionRecord
    return TrendPredictionRecord


def _load_snapshots(raw: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


class TrendPredictionService:
    """TrendAgent 走势预测记录的生命周期管理。"""

    def create_from_analysis(
        self,
        *,
        symbol: str,
        paper_position_id: Optional[int],
        entry_price: float,
        analysis: Dict[str, Any],
        db=None,
    ) -> Optional[int]:
        """开仓后写入 scenario A/B/C 与宏观阶段。"""
        if not paper_position_id:
            return None
        if not (analysis.get("scenario_a") or analysis.get("lifecycle")):
            return None

        TrendPredictionRecord = _import_model()
        session, own = _session(db)
        try:
            phase_at_entry = ""
            macro_regime = ""
            try:
                from backend.services.macro_regime_service import macro_regime_service
                _st = macro_regime_service.get_state("GLOBAL")
                phase_at_entry = _st.cycle_phase or ""
                macro_regime = _st.macro_regime or ""
            except Exception:
                pass

            row = TrendPredictionRecord(
                symbol=(symbol or "").upper(),
                paper_position_id=int(paper_position_id),
                lifecycle=(analysis.get("lifecycle") or "")[:32],
                scenario_a=(analysis.get("scenario_a") or "")[:500],
                scenario_b=(analysis.get("scenario_b") or "")[:500],
                scenario_c=(analysis.get("scenario_c") or "")[:500],
                phase_at_entry=phase_at_entry[:32],
                macro_regime=macro_regime[:32],
                entry_price=float(entry_price or 0),
                review_snapshots_json="[]",
                outcome="pending",
            )
            session.add(row)
            session.commit()
            logger.info(
                "[TrendPrediction] 落库 %s pos=%s lifecycle=%s",
                symbol, paper_position_id, row.lifecycle,
            )
            return row.id
        except Exception as exc:
            logger.debug(f"[TrendPrediction] 落库失败: {exc}")
            try:
                session.rollback()
            except Exception:
                pass
            return None
        finally:
            if own:
                session.close()

    def append_review_snapshot(
        self,
        *,
        paper_position_id: int,
        mark_price: float,
        note: str = "",
        db=None,
    ) -> bool:
        """90min 复查后追加价格快照。"""
        TrendPredictionRecord = _import_model()
        session, own = _session(db)
        try:
            row = (
                session.query(TrendPredictionRecord)
                .filter(
                    TrendPredictionRecord.paper_position_id == int(paper_position_id),
                    TrendPredictionRecord.outcome == "pending",
                )
                .order_by(TrendPredictionRecord.id.desc())
                .first()
            )
            if not row:
                return False

            snaps = _load_snapshots(row.review_snapshots_json)
            entry = float(row.entry_price or 0)
            deviation = None
            if entry > 0:
                deviation = round((float(mark_price) - entry) / entry * 100, 2)
            snaps.append({
                "ts": _now_utc().isoformat(),
                "price": float(mark_price),
                "deviation_pct": deviation,
                "note": (note or "")[:200],
            })
            row.review_snapshots_json = json.dumps(snaps[-20:], ensure_ascii=False)
            session.commit()
            return True
        except Exception as exc:
            logger.debug(f"[TrendPrediction] 复查快照失败: {exc}")
            try:
                session.rollback()
            except Exception:
                pass
            return False
        finally:
            if own:
                session.close()

    def score_on_close(
        self,
        *,
        paper_position_id: int,
        exit_price: float,
        close_reason: str = "",
        side: str = "long",
        pnl_pct: float = 0.0,
        db=None,
    ) -> Optional[str]:
        """平仓时评估 scenario 命中率。"""
        TrendPredictionRecord = _import_model()
        session, own = _session(db)
        try:
            row = (
                session.query(TrendPredictionRecord)
                .filter(
                    TrendPredictionRecord.paper_position_id == int(paper_position_id),
                    TrendPredictionRecord.outcome == "pending",
                )
                .order_by(TrendPredictionRecord.id.desc())
                .first()
            )
            if not row:
                return None

            outcome, note = self._score_record(
                side=side,
                entry_price=float(row.entry_price or 0),
                exit_price=float(exit_price or 0),
                pnl_pct=float(pnl_pct or 0),
                close_reason=close_reason,
                scenario_a=row.scenario_a or "",
                scenario_c=row.scenario_c or "",
            )
            row.outcome = outcome
            row.outcome_note = note[:500]
            row.closed_at = _now_utc().replace(tzinfo=None)
            session.commit()
            logger.info(
                "[TrendPrediction] 评分 pos=%s outcome=%s pnl=%.1f%%",
                paper_position_id, outcome, pnl_pct,
            )
            return outcome
        except Exception as exc:
            logger.debug(f"[TrendPrediction] 评分失败: {exc}")
            try:
                session.rollback()
            except Exception:
                pass
            return None
        finally:
            if own:
                session.close()

    @staticmethod
    def _score_record(
        *,
        side: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        close_reason: str,
        scenario_a: str,
        scenario_c: str,
    ) -> Tuple[str, str]:
        """草案算法：方向+幅度 → hit/partial/miss。"""
        del scenario_c  # 预留：未来 NLP 对照 scenario_c 文本

        if entry_price <= 0 or exit_price <= 0:
            if pnl_pct >= 3:
                return "hit", f"盈利{pnl_pct:+.1f}%"
            if pnl_pct <= -3:
                return "miss", f"亏损{pnl_pct:+.1f}%"
            return "partial", "价格数据不足，按盈亏中性判定"

        move_pct = (exit_price - entry_price) / entry_price * 100
        _side = (side or "long").lower()
        direction_ok = (
            (_side in ("long", "buy") and move_pct > 0)
            or (_side in ("short", "sell") and move_pct < 0)
        )

        _cr = (close_reason or "").lower()
        if "scenario_c" in _cr or "tail" in _cr or "尾部" in close_reason:
            return "miss", "触发尾部风险场景"

        if not direction_ok and pnl_pct < -2:
            return "miss", f"方向错误 move={move_pct:+.1f}% pnl={pnl_pct:+.1f}%"

        if direction_ok and abs(pnl_pct) >= 3:
            return "hit", f"主场景方向正确 pnl={pnl_pct:+.1f}% move={move_pct:+.1f}%"

        if direction_ok and pnl_pct > 0:
            return "partial", f"方向对但幅度不足 pnl={pnl_pct:+.1f}%"

        if pnl_pct < -4:
            return "miss", f"亏损超阈值 pnl={pnl_pct:+.1f}%"

        return "partial", f"中性 partial pnl={pnl_pct:+.1f}% scenario_a={scenario_a[:40]}"


trend_prediction_service = TrendPredictionService()
