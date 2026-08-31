"""选币 ROI 闭环（重设计④，2026-08-28）。

1) backfill_realized_pnl: 回填 removed 选币记录的 realized_pnl（从该币在
   注入窗口内的 paper_positions 已实现盈亏合计）——当前 2765 条 removed
   全部 realized_pnl=NULL，ROI 归因无数据。
2) run_weekly_roi_report: 按 ISO 周聚合 AI 币 vs 固定币净收益，落盘周报
   data/auto_coin_roi_report.json。
3) 自动降级: 连续 2 周 AI 币净收益 < 固定币（且 AI 币为负）→ 关闭该会话
   自动选币（session.auto_coin_enabled=false / 账户 ai_coin_select_enabled
   降级）+ 状态文件，人工复核后可用 AUTO_COIN_ROI_RESET 复位。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_REPORT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data",
    "auto_coin_roi_report.json",
)
_STATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data",
    "auto_coin_roi_state.json",
)


def backfill_realized_pnl(db) -> int:
    """回填 removed 选币的 realized_pnl（注入→移除窗口内的纸面已实现盈亏）。"""
    from sqlalchemy import text

    try:
        rows = db.execute(text(
            "SELECT id, session_id, symbol, created_at FROM auto_coin_selections "
            "WHERE action='removed' AND realized_pnl IS NULL ORDER BY id"
        )).fetchall()
    except Exception as e:
        logger.warning("[AutoCoinROI] 查询失败: %s", e)
        return 0

    filled = 0
    for rid, session_id, symbol, removed_at in rows:
        try:
            injected = db.execute(text(
                "SELECT created_at FROM auto_coin_selections "
                "WHERE session_id=:sid AND symbol=:sym AND action='injected' "
                "AND created_at <= :t ORDER BY created_at DESC LIMIT 1"
            ), {"sid": session_id, "sym": symbol, "t": removed_at}).fetchone()
            if injected is None:
                continue
            pnl_row = db.execute(text(
                # [2026-09-01 口径修复] 闭仓后权威已实现盈亏在 unrealized_pnl
                # （P0-6：closed 状态复用该字段为 realized_pnl 存档，已含分批
                # partial_realized_pnl）。原只读 partial_realized_pnl 会漏掉
                # 一次性全平的仓位（其 pnl 不在 partial 里）→ ROI 归因系统性低估。
                "SELECT COALESCE(SUM(COALESCE(NULLIF(unrealized_pnl,0), "
                "partial_realized_pnl, 0)),0) "
                "FROM paper_positions WHERE symbol=:sym AND status='closed' "
                "AND closed_at BETWEEN :t0 AND :t1"
            ), {"sym": symbol, "t0": injected[0], "t1": removed_at}).fetchone()
            pnl = float(pnl_row[0] or 0.0)
            db.execute(text(
                "UPDATE auto_coin_selections SET realized_pnl=:p WHERE id=:id"
            ), {"p": pnl, "id": rid})
            filled += 1
        except Exception as e:
            logger.debug("[AutoCoinROI] 回填 %s 失败: %s", symbol, e)
            continue
    if filled:
        db.commit()
        logger.info("[AutoCoinROI] realized_pnl 回填 %d 条", filled)
    return filled


def compute_weekly_roi(db, session_id: str, weeks: int = 8) -> List[Dict[str, Any]]:
    """按 ISO 周聚合 AI 币 vs 固定币净收益（AI=auto_coin_selections.realized_pnl，
    固定=会话固定宇宙的 paper_positions 已实现盈亏）。"""
    from sqlalchemy import text

    since = datetime.now() - timedelta(days=weeks * 7)
    try:
        auto_rows = db.execute(text(
            "SELECT created_at, COALESCE(realized_pnl,0) FROM auto_coin_selections "
            "WHERE session_id=:sid AND action='removed' AND created_at >= :t"
        ), {"sid": session_id, "t": since}).fetchall()
    except Exception:
        auto_rows = []

    # 固定宇宙 = 会话固定币（AI 币之外）
    try:
        fixed_syms = set()
        try:
            from backend.services.auto_coin_selector import get_fixed_symbols_for_session
            for tier in ("short", "mid", "long"):
                try:
                    fixed_syms |= {str(s).upper() for s in (get_fixed_symbols_for_session(session_id, tier=tier) or [])}
                except Exception:
                    pass
        except Exception:
            pass
        try:
            auto_syms = {str(r[0]).upper() for r in db.execute(text(
                "SELECT DISTINCT symbol FROM auto_coin_selections WHERE session_id=:sid AND action='injected'"
            ), {"sid": session_id}).fetchall()}
        except Exception:
            auto_syms = set()
        fixed_rows = []
        if fixed_syms:
            placeholders = ",".join([":s%d" % i for i in range(len(fixed_syms))])
            params = {"t": since}
            for i, s in enumerate(fixed_syms):
                params["s%d" % i] = s
            try:
                fixed_rows = db.execute(text(
                    f"SELECT closed_at, COALESCE(partial_realized_pnl,0) FROM paper_positions "
                    f"WHERE symbol IN ({placeholders}) AND status='closed' AND closed_at >= :t"
                ), params).fetchall()
            except Exception:
                fixed_rows = []
    except Exception:
        fixed_rows = []

    def _bucket(rows):
        buckets: Dict[str, Dict[str, float]] = {}
        for ts, pnl in rows:
            d = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
            wk = "%d-W%02d" % d.isocalendar()[:2]
            b = buckets.setdefault(wk, {"pnl": 0.0, "n": 0})
            b["pnl"] += float(pnl or 0.0)
            b["n"] += 1
        return buckets

    auto_b = _bucket(auto_rows)
    fixed_b = _bucket(fixed_rows)
    out = []
    for wk in sorted(set(auto_b) | set(fixed_b)):
        a = auto_b.get(wk, {"pnl": 0.0, "n": 0})
        f = fixed_b.get(wk, {"pnl": 0.0, "n": 0})
        out.append({
            "week": wk,
            "auto_pnl": round(a["pnl"], 4),
            "auto_n": a["n"],
            "fixed_pnl": round(f["pnl"], 4),
            "fixed_n": f["n"],
            "roi_diff": round(a["pnl"] - f["pnl"], 4),
        })
    return out


def _evaluate_downgrade(db, session_id: str, weekly: List[Dict[str, Any]]) -> Optional[str]:
    """连续 2 个完整周 AI 币净收益 < 固定币 → 返回降级原因（当前未完结周不计）。"""
    _cur_wk = "%d-W%02d" % datetime.now().isocalendar()[:2]
    done = [w for w in weekly if w["week"] < _cur_wk]
    neg = [w for w in done if w["roi_diff"] < 0]
    if len(neg) >= 2 and neg[-2]["week"] == done[-2]["week"]:
        return (
            f"连续2周负ROI: {neg[-2]['week']}(diff={neg[-2]['roi_diff']}) "
            f"{neg[-1]['week']}(diff={neg[-1]['roi_diff']})"
        )
    return None


def run_weekly_roi_report(db=None, session_id: Optional[str] = None) -> Dict[str, Any]:
    """周报主入口：回填 → 聚合 → 落盘 → 降级评估。可作定时任务直接调用。"""
    from backend.database.connection import SessionLocal
    from backend.database.models import FullAutoSession

    # [2026-09-01 租户根治] 定时线程无 HTTP 上下文 → ContextVar 未设 → RLS
    # fail-closed → auto_coin_selections(tenant 326)/sessions(326) 全部隐形，
    # 回填恒 0 行（实测 realized_pnl 全 NULL 的根因）。这里按管理员租户设
    # 请求身份，connection.begin 钩子会随每次事务自动 SET LOCAL。
    try:
        from backend.core.tenant import set_request_identity, clear_request_identity
        from backend.services.coin_select_platform_service import resolve_admin_tenant_id
        _roi_tid = resolve_admin_tenant_id() or 326
        set_request_identity(int(_roi_tid))
    except Exception:
        _roi_tid = None

    own_db = db is None
    db = db or SessionLocal()
    try:
        if not session_id:
            try:
                row = db.query(FullAutoSession).filter(
                    FullAutoSession.status == "running",
                ).order_by(FullAutoSession.started_at.desc()).first()
                session_id = str(row.session_id) if row else None
            except Exception:
                session_id = None
        if not session_id:
            return {"ok": False, "error": "no running session"}

        filled = backfill_realized_pnl(db)
        weekly = compute_weekly_roi(db, session_id)
        downgrade = _evaluate_downgrade(db, session_id, weekly)

        try:
            with open(_REPORT_PATH, "w", encoding="utf-8") as f:
                json.dump({"session_id": session_id, "weeks": weekly,
                           "generated_at": datetime.now().isoformat()}, f, ensure_ascii=False, indent=1)
        except Exception as e:
            logger.warning("[AutoCoinROI] 周报落盘失败: %s", e)

        state = {"session_id": session_id, "downgraded": bool(downgrade),
                 "reason": downgrade or "", "updated_at": datetime.now().isoformat()}
        if downgrade:
            try:
                # 降级：关闭该会话自动选币（退回固定宇宙）
                sess = db.query(FullAutoSession).filter(
                    FullAutoSession.session_id == session_id,
                ).first()
                if sess is not None:
                    setattr(sess, "auto_coin_enabled", False)
                    db.commit()
                logger.critical("[AutoCoinROI] 自动降级: %s", downgrade)
            except Exception as e:
                logger.warning("[AutoCoinROI] 降级落库失败: %s", e)
        try:
            with open(_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

        return {"ok": True, "session_id": session_id, "filled": filled,
                "weekly": weekly[-3:], "downgraded": bool(downgrade),
                "reason": downgrade or ""}
    finally:
        if own_db:
            db.close()
        try:
            from backend.core.tenant import clear_request_identity
            clear_request_identity()
        except Exception:
            pass
