"""
平仓质量数据闭环（M11 + M12）

M11 — master_close_guard 数据化校准：
  1. record_blocked_close()：master close/reduce 被硬事实门控拦截时，
     把「拦截时刻的仓位状态 + LLM 置信度」落 position_exit_events
     （event_type=master_close_blocked）。
  2. run_close_guard_calibration()：事后对照 —「被拦的 close 如果当时执行了
     会怎样」= 拦截时刻浮盈亏 − 仓位最终实际盈亏。高置信度组反事实收益
     显著为正（拦截让结果更差）→ 写 data/close_guard_runtime.json
     开启高置信度旁路。
  3. high_conf_close_bypass()：门控调用点消费入口。

M12 — 退出路径统一审计：
  paper_trading_engine 的所有平仓（硬 TP/SL、规则分批、AI 复审、defensive）
  都经 close_position/partial → position_exit_events（exit_channel=close reason）。
  run_exit_audit() 按通道聚合盈亏/留存率，写 data/exit_audit_report.json 供回看。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CLOSE_GUARD_RUNTIME_FILE = os.path.join("data", "close_guard_runtime.json")
EXIT_AUDIT_REPORT_FILE = os.path.join("data", "exit_audit_report.json")
_runtime_cache: dict = {"ts": 0.0, "data": {}}

HIGH_CONF_THRESHOLD = 70.0
MIN_CALIBRATION_SAMPLES = 5


# ══════════════════════════════════════════════════
#  M11.1 拦截留痕
# ══════════════════════════════════════════════════

def record_blocked_close(
    db,
    *,
    pos: Dict[str, Any],
    account_id: int,
    action: str,
    confidence: Optional[float],
    reason: str,
) -> None:
    """master close/reduce 被拦截时落 position_exit_events（不抛异常）。"""
    try:
        from backend.database.models import PositionExitEvent

        margin = float(pos.get("margin") or 0)
        upnl = float(pos.get("unrealized_pnl") or 0)
        event = PositionExitEvent(
            position_id=int(pos.get("id") or pos.get("position_id") or 0),
            account_id=int(account_id or 0),
            strategy_id=pos.get("strategy_id"),
            symbol=str(pos.get("symbol") or ""),
            side=str(pos.get("side") or ""),
            trade_nature=pos.get("trade_nature") or pos.get("timeframe_tier"),
            event_type="master_close_blocked",
            price=float(pos.get("mark_price") or 0) or None,
            pnl_at_event=upnl,
            pnl_pct_at_event=(upnl / margin * 100) if margin > 0 else None,
            exit_channel="master_close_guard",
            metadata_json=json.dumps({
                "action": action,
                "confidence": confidence,
                "reason": str(reason)[:300],
            }, ensure_ascii=False),
        )
        db.add(event)
        db.flush()
    except Exception as exc:
        logger.debug(f"[CloseGuardCalib] 拦截留痕失败(非致命): {exc}")
        try:
            db.rollback()
        except Exception:
            pass


# ══════════════════════════════════════════════════
#  M11.2 反事实校准
# ══════════════════════════════════════════════════

def run_close_guard_calibration(lookback_days: int = 14) -> Dict[str, Any]:
    """
    对照「被拦截的 close 如果执行了会怎样」：
      counterfactual_gain = 拦截时刻浮盈亏 − 仓位最终实际盈亏
      （>0 表示当时平掉更好，拦截是错的）
    高置信度组样本足够且平均反事实收益为正 → 开启高置信度旁路。
    """
    from backend.database.connection import SessionLocal
    from backend.database.models import PaperPosition, PositionExitEvent

    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        events = (
            db.query(PositionExitEvent)
            .filter(
                PositionExitEvent.event_type == "master_close_blocked",
                PositionExitEvent.created_at >= cutoff.replace(tzinfo=None),
            )
            .all()
        )

        high_conf: list = []
        low_conf: list = []
        for ev in events:
            pos = db.query(PaperPosition).filter(PaperPosition.id == ev.position_id).first()
            if pos is None or str(pos.status or "") != "closed":
                continue  # 仓位还没平，反事实未知
            final_pnl = float(pos.unrealized_pnl or 0)  # closed 状态复用为 realized
            pnl_at_block = float(ev.pnl_at_event or 0)
            gain = pnl_at_block - final_pnl
            try:
                conf = float(json.loads(ev.metadata_json or "{}").get("confidence") or 0)
            except Exception:
                conf = 0.0
            (high_conf if conf >= HIGH_CONF_THRESHOLD else low_conf).append(gain)

        def _stats(arr: list) -> Dict[str, Any]:
            if not arr:
                return {"n": 0, "avg_gain": 0.0, "positive_ratio": 0.0}
            return {
                "n": len(arr),
                "avg_gain": round(sum(arr) / len(arr), 2),
                "positive_ratio": round(sum(1 for g in arr if g > 0) / len(arr), 3),
            }

        hc = _stats(high_conf)
        lc = _stats(low_conf)
        # 放宽条件：高置信度组 ≥5 样本，且平均反事实收益>0 且 60%+ 的拦截是错的
        enable_bypass = (
            hc["n"] >= MIN_CALIBRATION_SAMPLES
            and hc["avg_gain"] > 0
            and hc["positive_ratio"] >= 0.6
        )
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": lookback_days,
            "high_conf_bypass": enable_bypass,
            "min_confidence": HIGH_CONF_THRESHOLD,
            "high_conf_stats": hc,
            "low_conf_stats": lc,
        }
        try:
            os.makedirs(os.path.dirname(CLOSE_GUARD_RUNTIME_FILE), exist_ok=True)
            with open(CLOSE_GUARD_RUNTIME_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            _runtime_cache["ts"] = 0.0
        except Exception as werr:
            logger.warning(f"[CloseGuardCalib] runtime 写入失败: {werr}")

        logger.info(
            f"[CloseGuardCalib] 校准完成: 高置信组={hc} 低置信组={lc} "
            f"bypass={'开启' if enable_bypass else '关闭'}"
        )
        return payload
    except Exception as exc:
        logger.warning(f"[CloseGuardCalib] 校准失败: {exc}")
        return {}
    finally:
        db.close()


def high_conf_close_bypass(confidence: Optional[float]) -> bool:
    """门控调用点：数据证明高置信度 close 不该拦时返回 True（旁路拦截）。"""
    try:
        from backend.services.decision_core.threshold_resolver import normalize_confidence_pct
        conf = normalize_confidence_pct(confidence)
        now = time.time()
        if now - _runtime_cache["ts"] >= 60:
            data = {}
            if os.path.exists(CLOSE_GUARD_RUNTIME_FILE):
                with open(CLOSE_GUARD_RUNTIME_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            _runtime_cache["data"] = data
            _runtime_cache["ts"] = now
        data = _runtime_cache["data"] or {}
        return bool(data.get("high_conf_bypass")) and conf >= float(
            data.get("min_confidence") or HIGH_CONF_THRESHOLD
        )
    except Exception:
        return False


# ══════════════════════════════════════════════════
#  M12 退出路径统一审计
# ══════════════════════════════════════════════════

def run_exit_audit(lookback_days: int = 30) -> Dict[str, Any]:
    """
    按退出通道聚合 position_exit_events：
    哪条通道（硬 TP/SL、规则分批、AI 复审、defensive…）贡献了利润/亏损。
    结果写 data/exit_audit_report.json。
    """
    from backend.database.connection import SessionLocal
    from backend.database.models import PositionExitEvent

    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        events = (
            db.query(PositionExitEvent)
            .filter(
                PositionExitEvent.event_type.in_(
                    ["final_trade_outcome", "partial_exit_event"]
                ),
                PositionExitEvent.created_at >= cutoff.replace(tzinfo=None),
            )
            .all()
        )

        channels: Dict[str, dict] = {}
        for ev in events:
            ch = str(ev.exit_channel or "unknown")
            agg = channels.setdefault(ch, {
                "n": 0, "total_pnl": 0.0, "wins": 0,
                "retention_sum": 0.0, "retention_n": 0,
            })
            pnl = float(ev.pnl or 0)
            agg["n"] += 1
            agg["total_pnl"] += pnl
            if pnl > 0:
                agg["wins"] += 1
            if ev.retention_ratio is not None:
                agg["retention_sum"] += float(ev.retention_ratio)
                agg["retention_n"] += 1

        report = {}
        for ch, agg in channels.items():
            report[ch] = {
                "n": agg["n"],
                "total_pnl": round(agg["total_pnl"], 2),
                "win_rate": round(agg["wins"] / agg["n"], 3) if agg["n"] else 0.0,
                "avg_retention": (
                    round(agg["retention_sum"] / agg["retention_n"], 3)
                    if agg["retention_n"] else None
                ),
            }

        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": lookback_days,
            "total_events": len(events),
            "by_channel": dict(
                sorted(report.items(), key=lambda kv: kv[1]["total_pnl"])
            ),
        }
        try:
            os.makedirs(os.path.dirname(EXIT_AUDIT_REPORT_FILE), exist_ok=True)
            with open(EXIT_AUDIT_REPORT_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as werr:
            logger.warning(f"[ExitAudit] 报告写入失败: {werr}")

        logger.info(
            f"[ExitAudit] 审计完成: {len(events)} 事件 / {len(report)} 通道"
        )
        return payload
    except Exception as exc:
        logger.warning(f"[ExitAudit] 审计失败: {exc}")
        return {}
    finally:
        db.close()
