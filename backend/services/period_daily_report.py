"""period_daily_report — 三周期统一日报生成器（2026-08-19 报告观测体系）。

每日为每个会话生成 scap/midlong/long 三周期段日报，每段含：
- 交易/持仓摘要（规则统计）
- 亏损归因块（近 1 天 PnL<0 时自动触发，见 loss_attribution）
- 长线段附加 L1 状态面板 + 当日管理动作流水
LLM 定性分析可选（LLM_PERIOD_DAILY_REVIEW=1）。落库 period_daily_reports（幂等 upsert）。
纯规则 + 非交易路径。
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 进程内动作流水缓冲（长线 V2 管理动作 + 入场闸拦截），日报生成时读取
_ACTION_LOG: deque = deque(maxlen=500)


def log_long_action(symbol: str, action: str, reason: str) -> None:
    try:
        _ACTION_LOG.append({
            "ts": time.time(), "symbol": str(symbol).upper(),
            "action": str(action), "reason": str(reason)[:160],
        })
    except Exception:
        pass


def _drain_actions(since_ts: float) -> List[Dict[str, Any]]:
    return [a for a in list(_ACTION_LOG) if float(a["ts"]) >= since_ts]


def _today_utc() -> str:
    from datetime import datetime
    return datetime.utcnow().strftime("%Y-%m-%d")


def _horizon_of(order) -> str:
    n = str(getattr(order, "trade_nature", None) or "").lower()
    tf = str(getattr(order, "timeframe_tier", None) or "").lower()
    if tf == "long" or n in ("trend_follow", "position"):
        return "long"
    if tf == "mid" or n == "swing":
        return "midlong"
    return "scalp"


def _trades_since(db, account_id: int, horizon: str, hours: int = 24) -> Dict[str, Any]:
    """近 hours 小时该周期的平仓单统计。"""
    from datetime import datetime, timedelta
    from backend.database.models import PaperOrder

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    q = db.query(PaperOrder).filter(
        PaperOrder.account_id == int(account_id),
        PaperOrder.close_reason.isnot(None),
        PaperOrder.filled_at >= cutoff,
    )
    rows = [r for r in q.all() if _horizon_of(r) == horizon]
    pnls = [float(r.pnl or 0.0) for r in rows]
    wins = [p for p in pnls if p > 0]
    return {
        "n_closed": len(rows),
        "total_pnl": round(sum(pnls), 4),
        "win_rate": round(len(wins) / len(pnls), 3) if pnls else 0.0,
        "by_symbol": _group_pnl(rows),
    }


def _group_pnl(rows) -> List[Dict[str, Any]]:
    d: Dict[str, float] = {}
    for r in rows:
        s = str(r.symbol or "?").upper()
        d[s] = d.get(s, 0.0) + float(r.pnl or 0.0)
    items = sorted(d.items(), key=lambda x: -x[1])[:5]
    return [{"symbol": k, "pnl": round(v, 2)} for k, v in items]


def _open_positions(db, account_id: int, horizon: str) -> List[Dict[str, Any]]:
    from backend.database.models import PaperPosition
    poss = db.query(PaperPosition).filter(
        PaperPosition.account_id == int(account_id),
        PaperPosition.status == "open",
    ).all()
    out = []
    for p in poss:
        n = str(getattr(p, "trade_nature", None) or "").lower()
        tf = str(getattr(p, "timeframe_tier", None) or "").lower()
        h = "long" if (tf == "long" or n in ("trend_follow", "position")) else \
            ("midlong" if (tf == "mid" or n == "swing") else "scalp")
        if h != horizon:
            continue
        out.append({
            "symbol": p.symbol, "side": p.side,
            "entry_price": float(p.entry_price or 0),
            "mark_price": float(p.mark_price or 0),
            "unrealized_pnl": round(float(p.unrealized_pnl or 0), 4),
            "sl_price": float(p.sl_price or 0) or None,
            "peak_pnl_pct": float(getattr(p, "peak_pnl_pct", 0) or 0),
            "opened_at": str(getattr(p, "opened_at", None)),
        })
    return out


def _l1_panel(symbols=None) -> Dict[str, Any]:
    """各核心币 L1 状态面板（trend_layer.classify 快照）。"""
    from backend.services import long_trend_v2 as lv2
    syms = symbols or ["BTC", "ETH", "SOL", "BNB", "XRP"]
    panel = {}
    for s in syms:
        try:
            df, c = lv2._get_l1_classification(s)
            if c is None:
                panel[s] = {"state": "n/a", "reason": "数据不足"}
                continue
            panel[s] = {
                "state": c.get("state"), "score": c.get("score"),
                "strength": c.get("strength"), "signals": c.get("signals", {}),
                "target": c.get("target"), "close": c.get("close"),
            }
            # [B1/B2] 趋势起始点（BOCPD 变点）与 Wyckoff 相位（报告观测字段）
            try:
                from backend.services.trend_inception import inception_check
                panel[s]["inception"] = inception_check(df)
            except Exception:
                pass
            try:
                from backend.services.wyckoff_phase import classify_phase as _wp
                panel[s]["wyckoff"] = _wp(df)
            except Exception:
                pass
        except Exception as e:
            panel[s] = {"state": "error", "reason": str(e)[:80]}
    return panel


def build_daily_report(db, account_id: int, symbols=None,
                       date: Optional[str] = None) -> Dict[str, Any]:
    """生成三周期日报 dict（不落库）。"""
    from backend.services.loss_attribution import build_loss_attribution

    report_date = date or _today_utc()
    day0 = time.time() - 86400.0
    sections = {}
    for horizon in ("scalp", "midlong", "long"):
        sec = {
            "trades_24h": _trades_since(db, account_id, horizon),
            "open_positions": _open_positions(db, account_id, horizon),
            "loss_attribution": build_loss_attribution(db, account_id, horizon, days=1),
        }
        if horizon == "long":
            sec["l1_panel"] = _l1_panel(symbols)
            sec["actions_24h"] = _drain_actions(day0)
        sections[horizon] = sec
    return {"report_date": report_date, "account_id": int(account_id), "sections": sections}


def _llm_analysis(db, account_id: int, report: Dict[str, Any]) -> Optional[str]:
    """LLM 对日报做定性分析（可选，失败静默，非交易路径）。"""
    try:
        import os
        if os.getenv("LLM_PERIOD_DAILY_REVIEW", "0").strip().lower() not in ("1", "true", "yes", "on"):
            return None
        from backend.services.llm_config_service import get_llm_config_for_account, call_llm_api_sync
        cfg = get_llm_config_for_account(account_id) if account_id else None
        if not cfg:
            return None
        brief = json.dumps(report, ensure_ascii=False, default=str)[:3500]
        prompt = (
            f"你是加密货币三周期交易系统的日报分析师。以下是今日短线/中线/长线日报数据：\n{brief}\n\n"
            f"请用 3-5 句话总结：各周期今日表现、亏损周期的主要归因、明日应关注的风险点。只输出结论。"
        )
        return call_llm_api_sync(cfg, [{"role": "user", "content": prompt}], caller="PeriodDailyReport")
    except Exception as e:
        logger.debug("[PeriodDailyReport] LLM 分析跳过: %s", e)
        return None


def save_daily_report(db, account_id: int, symbols=None, date: Optional[str] = None) -> Optional[int]:
    """生成并落库日报（按 date+account+horizon 幂等 upsert）。返回行数。"""
    from backend.database.models import PeriodDailyReport

    report = build_daily_report(db, account_id, symbols=symbols, date=date)
    rdate = report["report_date"]
    n = 0
    for horizon, sec in report["sections"].items():
        row = db.query(PeriodDailyReport).filter(
            PeriodDailyReport.account_id == int(account_id),
            PeriodDailyReport.report_date == rdate,
            PeriodDailyReport.horizon == horizon,
        ).first()
        payload = json.dumps(sec, ensure_ascii=False, default=str)
        if row is None:
            row = PeriodDailyReport(account_id=int(account_id), report_date=rdate, horizon=horizon)
            db.add(row)
        row.payload_json = payload
        n += 1
    db.commit()
    # LLM 分析只写长线段（三周期整体总结）
    try:
        _sum = _llm_analysis(db, account_id, report)
        if _sum:
            for h in ("long", "midlong", "scalp"):
                r2 = db.query(PeriodDailyReport).filter(
                    PeriodDailyReport.account_id == int(account_id),
                    PeriodDailyReport.report_date == rdate,
                    PeriodDailyReport.horizon == h,
                ).first()
                if r2 is not None:
                    r2.llm_summary = _sum
            db.commit()
    except Exception:
        db.rollback()
    return n


def run_daily_reports() -> Dict[str, Any]:
    """cron 入口：遍历活跃会话生成日报。表缺失/异常静默降级。"""
    out = {"sessions": 0, "saved": 0, "error": None}
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import FullAutoSession
        db = SessionLocal()
        try:
            sessions = db.query(FullAutoSession).filter(
                FullAutoSession.status.in_(["running", "defensive"])
            ).all()
            out["sessions"] = len(sessions)
            for s in sessions:
                acct = getattr(s, "paper_account_id", None) or getattr(s, "account_id", None)
                if not acct:
                    continue
                try:
                    out["saved"] += int(save_daily_report(db, int(acct)) or 0)
                except Exception as e:
                    logger.warning("[PeriodDailyReport] 会话 %s 日报失败: %s", s.session_id, e)
        finally:
            db.close()
    except Exception as e:
        out["error"] = str(e)[:200]
        logger.warning("[PeriodDailyReport] 日报任务失败: %s", e)
    return out
