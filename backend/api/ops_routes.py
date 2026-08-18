"""统一运维看板事实层 API（/api/ops/*）。

只读优先；干预接口需二次确认语义由前端承担，后端写审计字段。
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ops", tags=["ops"])

# 心跳 SLA（秒）：正常 / 滞后 / 中断
_HB_WARN_SEC = 900
_HB_CRIT_SEC = 3600

# 按任务节奏覆盖的 SLA 阈值（秒）。cron 型任务一天只跑一次
# （main.py: scalp_daily_health 05:30 / scalp_symbol_profile_daily 05:45），
# 若沿用默认 1 小时阈值，当天其余 23 小时会被误报为"中断"。
# warn：距上次成功超过该值判"滞后"（错过一次排期）；crit：超过该值判"中断"（连续错过两次）。
_HB_CADENCE_SEC: Dict[str, Dict[str, int]] = {
    "scalp_daily_health": {"warn": 26 * 3600, "crit": 50 * 3600},
    "scalp_symbol_profile": {"warn": 26 * 3600, "crit": 50 * 3600},
}

# R6-3：P0 报错飞书告警节流（ALERT_P0_ENABLED=true 才生效；同计数 10 分钟内最多一条）
_P0_ALERT_STATE: Dict[str, Any] = {"last_sent": 0.0, "last_count": 0}


def _maybe_alert_p0(p0_count: int) -> None:
    """P0 报错出现时飞书告警（节流 + 静默降级，绝不阻塞报错接口）。"""
    if os.getenv("ALERT_P0_ENABLED", "false").lower() not in ("1", "true", "yes", "on"):
        return
    try:
        now = time.time()
        if p0_count <= 0:
            _P0_ALERT_STATE["last_count"] = 0
            return
        if p0_count == _P0_ALERT_STATE["last_count"] and now - _P0_ALERT_STATE["last_sent"] < 600:
            return
        from backend.services.openclaw_notify import get_notifier

        ok = get_notifier().send_sync(
            f"报错中心检测到 {p0_count} 条 P0（心跳中断级）。请到运维看板 /ops#ops-errors 查看。",
            title=f"⚠️ P0 报错 {p0_count} 条",
            level="critical",
            event_type="system",
        )
        _P0_ALERT_STATE["last_sent"] = now
        _P0_ALERT_STATE["last_count"] = p0_count
        logger.info("[ops/P0-alert] sent=%s count=%s", ok, p0_count)
    except Exception as exc:  # pragma: no cover
        logger.warning("[ops/P0-alert] failed: %s", exc)


def _age_sec(iso_ts: Optional[str]) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:
        return None


def _sla(
    age: Optional[float],
    warn_sec: int = _HB_WARN_SEC,
    crit_sec: int = _HB_CRIT_SEC,
) -> str:
    if age is None:
        return "unknown"
    if age <= warn_sec:
        return "ok"
    if age <= crit_sec:
        return "lag"
    return "down"


def _parse_metrics(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            import json
            return json.loads(raw) or {}
        except Exception:
            return {}
    return {}


def _gate_reasons(metrics: Dict[str, Any], verdict: str | None) -> List[str]:
    """把硬门禁失败翻译成中文原因（表无 gate_reasons 列，从 metrics 推导）。

    入库形态是扁平：{n, profit_factor, t_stat, older_pf, newer_pf}；
    也兼容嵌套 {total, older_half, newer_half}。
    """
    total = metrics.get("total") if isinstance(metrics.get("total"), dict) else metrics
    older = metrics.get("older_half") if isinstance(metrics.get("older_half"), dict) else {}
    newer = metrics.get("newer_half") if isinstance(metrics.get("newer_half"), dict) else {}
    n = int((total or {}).get("n", 0) or metrics.get("n") or 0)
    pf = (total or {}).get("profit_factor", metrics.get("profit_factor"))
    t = (total or {}).get("t_stat", metrics.get("t_stat"))
    older_pf = older.get("profit_factor", metrics.get("older_pf"))
    newer_pf = newer.get("profit_factor", metrics.get("newer_pf"))
    reasons: List[str] = []
    if verdict == "insufficient_data" or n < 100:
        reasons.append(f"样本不足 n={n}<100")
        return reasons
    if pf is None:
        reasons.append("缺 profit_factor")
    elif float(pf) < 1.0:
        reasons.append(f"PF={float(pf):.2f}<1.0")
    if older_pf is None:
        reasons.append("缺前半段 PF")
    elif float(older_pf) < 0.95:
        reasons.append(f"前半PF={float(older_pf):.2f}<0.95")
    if newer_pf is None:
        reasons.append("缺后半段 PF")
    elif float(newer_pf) < 0.95:
        reasons.append(f"后半PF={float(newer_pf):.2f}<0.95")
    if verdict == "promising":
        reasons.append(f"t_stat={t}≤1.0（有希望未过硬门）")
    elif verdict == "fail" and t is not None and float(t) <= 1.0 and not reasons:
        reasons.append(f"t_stat={float(t):.2f}≤1.0")
    if verdict == "pass":
        return ["通过硬门禁"]
    if not reasons:
        reasons.append(f"未达标({verdict or '?'})")
    return reasons


def _meta_train_digest() -> Dict[str, Any]:
    """读 scalp_meta 报告；路径与 trainer 一致（仓库 data/，不是 backend/data/）。"""
    try:
        from backend.services.scalp_meta_trainer import get_report
        data = get_report() or {}
        if data.get("status") == "no_report" and not data.get("oos_auc_lgbm"):
            return {"report": None, "missing": True, "path_hint": "data/scalp_meta_report.json"}
        baseline = data.get("baseline") or {}
        f30 = data.get("filter_top30pct") or {}
        f15 = data.get("filter_top15pct") or {}
        return {
            "report": {
                "usable": data.get("usable"),
                "oos_auc_lgbm": data.get("oos_auc_lgbm"),
                "oos_auc_linear": data.get("oos_auc_linear"),
                "auc": data.get("auc"),
                "n_settled": data.get("n_settled"),
                "n_settled_raw": data.get("n_settled_raw"),
                "pos": data.get("pos"),
                "neg": data.get("neg"),
                "features": data.get("features"),
                "ts": data.get("ts"),
                "status": data.get("status"),
                "error": data.get("error"),
                "note": data.get("note"),
                "gate_reasons": data.get("gate_reasons") or [],
                "baseline": {
                    "win_rate": baseline.get("win_rate"),
                    "net_ret": baseline.get("net_ret"),
                },
                "filter_top30pct": {
                    "win_rate": f30.get("win_rate"),
                    "net_ret": f30.get("net_ret"),
                    "n": f30.get("n"),
                    "coverage": f30.get("coverage"),
                } if f30 else None,
                "filter_top15pct": {
                    "win_rate": f15.get("win_rate"),
                    "net_ret": f15.get("net_ret"),
                    "n": f15.get("n"),
                    "coverage": f15.get("coverage"),
                } if f15 else None,
                "top_importance": (data.get("top_importance") or [])[:5],
            },
            "missing": False,
        }
    except Exception as e:
        return {"report": None, "error": str(e), "missing": True}


def _fixed_pool_digest() -> Dict[str, Any]:
    """固定币池：会话当前固定币 + 全局备选池(user_trading_pairs) + 进化实际用币。"""
    from backend.database.connection import AnalyticsSessionLocal

    # 全局固定币备选池（system_configs.user_trading_pairs）——权威手动配置
    backup_pool: List[str] = []
    try:
        from backend.services.trading_pairs_config import get_user_trading_pairs
        backup_pool = [str(s).upper() for s in (get_user_trading_pairs() or []) if s]
    except Exception:
        backup_pool = []

    training_core: List[str] = []
    try:
        from backend.config.settings import TRAINING_CORE_SYMBOLS
        training_core = [str(s).upper() for s in (TRAINING_CORE_SYMBOLS or []) if s]
    except Exception:
        training_core = ["BTC", "ETH", "SOL", "BNB", "ASTER"]

    session_fixed: List[str] = []
    session_ids: List[str] = []
    try:
        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal
        from backend.services.auto_coin_selector import get_fixed_symbols_for_session

        with system_identity():
            with SessionLocal() as db:
                rows = db.execute(
                    text(
                        "SELECT session_id FROM full_auto_sessions "
                        "WHERE status = 'running'"
                    )
                ).mappings().all()
                for r in rows:
                    sid = str(r["session_id"] or "")
                    if not sid:
                        continue
                    session_ids.append(sid)
                    try:
                        fixed = get_fixed_symbols_for_session(sid, db)
                        session_fixed.extend(str(s).upper() for s in (fixed or []))
                    except Exception:
                        continue
        session_fixed = sorted(set(session_fixed))
    except Exception:
        session_fixed = []

    # 进化实际解析结果（会话固定 ∪ 备选池）
    evo_symbols: List[str] = []
    try:
        from backend.services.evolution.factor_evolution_loop import resolve_evolution_symbols
        evo_symbols = [str(s).upper() for s in (resolve_evolution_symbols() or [])]
    except Exception:
        evo_symbols = list(dict.fromkeys([*session_fixed, *backup_pool])) or list(training_core)

    # 主展示：会话当前固定币；没有会话时退到备选池
    display_symbols = session_fixed or backup_pool or training_core
    _meta = _meta_train_digest()

    out: Dict[str, Any] = {
        "note": "会话固定币=当前启用；备选池=全局交易对配置；进化用两者并集。右侧才是 AI 选币",
        "symbols": display_symbols,
        "backup_pool": backup_pool,
        "evo_symbols": evo_symbols,
        "training_core": training_core,
        "session_fixed": session_fixed,
        "running_sessions": session_ids,
        "evo_4h": {"last_at": None, "actions_7d": {}},
        "evo_5m": {"last_at": None, "actions_7d": {}},
        "meta": _meta.get("report"),
        "meta_missing": bool(_meta.get("missing")),
    }
    try:
        with AnalyticsSessionLocal() as db:
            for key, pred in (
                ("evo_4h", "AND (factor_id IS NULL OR factor_id NOT LIKE 's5m_%')"),
                ("evo_5m", "AND factor_id LIKE 's5m_%'"),
            ):
                last = db.execute(
                    text(
                        f"SELECT max(created_at) AS t FROM factor_evolution_log "
                        f"WHERE created_at IS NOT NULL {pred}"
                    )
                ).mappings().first()
                ts = last["t"] if last else None
                out[key]["last_at"] = ts.isoformat() if hasattr(ts, "isoformat") else (
                    str(ts) if ts else None
                )
                rows = db.execute(
                    text(
                        f"SELECT action, count(*) AS n FROM factor_evolution_log "
                        f"WHERE created_at >= now() - interval '7 days' {pred} "
                        f"GROUP BY action"
                    )
                ).mappings().all()
                out[key]["actions_7d"] = {
                    str(r["action"] or "unknown"): int(r["n"]) for r in rows
                }
            # 因子池状态分布（可交易=0 时要能看见 QUARANTINE，避免误判看板坏了）
            try:
                dist_rows = db.execute(
                    text(
                        "SELECT state, count(*) AS n FROM factor_active_set GROUP BY state"
                    )
                ).mappings().all()
                state_dist = {str(r["state"] or "?"): int(r["n"] or 0) for r in dist_rows}
                out["state_dist"] = state_dist
                out["tradable_factor_rows"] = (
                    int(state_dist.get("PAPER", 0))
                    + int(state_dist.get("SMALL_LIVE", 0))
                    + int(state_dist.get("ACTIVE", 0))
                )
                out["research_factor_rows"] = (
                    out["tradable_factor_rows"]
                    + int(state_dist.get("ORTHO", 0))
                )
                out["quarantine_rows"] = int(state_dist.get("QUARANTINE", 0))
                out["total_factor_rows"] = sum(state_dist.values())
                # 最近隔离原因（帮助理解为何可交易=0）
                qrows = db.execute(
                    text(
                        "SELECT left(coalesce(reason,''), 80) AS reason, count(*) AS n "
                        "FROM factor_evolution_log "
                        "WHERE action = 'quarantine' "
                        "AND created_at >= now() - interval '14 days' "
                        "GROUP BY left(coalesce(reason,''), 80) "
                        "ORDER BY n DESC LIMIT 5"
                    )
                ).mappings().all()
                out["quarantine_reasons"] = [
                    {"reason": r["reason"] or "（无原因）", "n": int(r["n"] or 0)}
                    for r in qrows
                ]
            except Exception as e:
                out["tradable_factor_rows"] = None
                out["state_dist_error"] = str(e)
    except Exception as e:
        out["error"] = str(e)
    return out


def _ai_scan_digest() -> Dict[str, Any]:
    """AI 选币 → pair_selector 快速矩阵扫描进度。"""
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    from backend.services.scalp.pair_selector import ensure_table
    from backend.services.scalp.pair_selector_watcher import _active_auto_symbols

    ensure_table()
    symbols = []
    try:
        symbols = _active_auto_symbols()
    except Exception:
        symbols = []

    hb_detail: Dict[str, Any] = {}
    last_ok = None
    try:
        from backend.services.scalp.scalp_heartbeat import get_heartbeats
        raw = (get_heartbeats() or {}).get("pair_selector_watcher") or {}
        last_ok = raw.get("last_ok_at")
        detail = raw.get("detail") or {}
        if isinstance(detail, str):
            import json
            try:
                detail = json.loads(detail)
            except Exception:
                detail = {}
        hb_detail = detail if isinstance(detail, dict) else {}
    except Exception:
        pass

    by_symbol: List[Dict[str, Any]] = []
    pending: List[str] = []
    scanned_24h: List[str] = []
    pass_24h = 0
    candidates_24h = 0
    with system_identity():
        with SessionLocal() as db:
            try:
                rows = db.execute(
                    text(
                        "SELECT symbol, "
                        "COUNT(*) AS n, "
                        "SUM(CASE WHEN gate_verdict='pass' THEN 1 ELSE 0 END) AS n_pass, "
                        "SUM(CASE WHEN gate_verdict='promising' THEN 1 ELSE 0 END) AS n_prom, "
                        "SUM(CASE WHEN gate_verdict='fail' THEN 1 ELSE 0 END) AS n_fail, "
                        "MAX(generated_at) AS last_at "
                        "FROM pair_strategy_candidates "
                        "WHERE generated_at >= now() - interval '24 hours' "
                        "GROUP BY symbol ORDER BY last_at DESC NULLS LAST"
                    )
                ).mappings().all()
            except Exception as e:
                return {
                    "ai_symbols": symbols,
                    "error": str(e),
                    "pending_scan": symbols,
                    "scanned_24h": [],
                    "by_symbol": [],
                }
            for r in rows:
                sym = str(r["symbol"] or "")
                scanned_24h.append(sym)
                n = int(r["n"] or 0)
                np_ = int(r["n_pass"] or 0)
                candidates_24h += n
                pass_24h += np_
                by_symbol.append({
                    "symbol": sym,
                    "n": n,
                    "n_pass": np_,
                    "n_promising": int(r["n_prom"] or 0),
                    "n_fail": int(r["n_fail"] or 0),
                    "last_at": r["last_at"].isoformat() if r["last_at"] else None,
                })
    pending = [s for s in symbols if s not in set(scanned_24h)]
    started = list(hb_detail.get("started") or [])
    return {
        "note": "AI选币快速矩阵：每 tick 最多扫 1 币；候选≠绑定",
        "ai_symbols": symbols,
        "pending_scan": pending,
        "scanning": started,
        "scanned_24h": scanned_24h,
        "candidates_24h": candidates_24h,
        "pass_24h": pass_24h,
        "last_watcher_at": last_ok,
        "watcher_detail": {
            "checked": hb_detail.get("checked"),
            "started": started,
            "auto_enabled": hb_detail.get("auto_enabled") or [],
        },
        "by_symbol": by_symbol,
        "lane_note": (
            "绑定 running 是历史 pass 晋级后的持久态；"
            "候选窗口被最近一币扫描冲掉时会「看起来只有一个币」"
        ),
    }


@router.get("/pipeline")
def ops_pipeline() -> Dict[str, Any]:
    """挖矿→池→选币→绑定→训练 一页脉搏摘要。"""
    # [perf 2026-08-18] 内部串行调 7 个子摘要，GIL 竞争下实测 7~22s。10s TTL。
    from backend.utils.ttl_cache import ttl_cached

    return ttl_cached("ops_pipeline", 10.0, lambda: _ops_pipeline_impl())


def _ops_pipeline_impl() -> Dict[str, Any]:
    hb = ops_heartbeats()
    pool = ops_factor_pool(view="tradable", limit=5)
    funnel = ops_evolution_funnel(days=7)
    cands = ops_candidates(limit=40)
    binds = ops_bindings(limit=20)
    train = _meta_train_digest()
    fixed = _fixed_pool_digest()
    ai_scan = _ai_scan_digest()
    lane_on = os.getenv("PAIR_BINDING_LANE_ENABLED", "false").lower() in (
        "1", "true", "yes", "on",
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pulse": {
            "heartbeat_ok": sum(1 for x in hb.get("items", []) if x.get("sla") == "ok"),
            "heartbeat_total": len(hb.get("items", [])),
            "tradable_factors": pool.get("total", 0),
            "funnel_promoted_7d": funnel.get("counts", {}).get("promote", 0),
            "candidates_pass": cands.get("pass_count_global", cands.get("pass_count", 0)),
            "candidates_pass_page": cands.get("pass_count", 0),
            "bindings_running": binds.get("status_dist", {}).get("running", 0),
            "lane_trading_enabled": lane_on,
            "meta_usable": bool((train.get("report") or {}).get("usable")),
            "ai_symbols": len(ai_scan.get("ai_symbols") or []),
            "ai_pending_scan": len(ai_scan.get("pending_scan") or []),
            "ai_pass_24h": ai_scan.get("pass_24h", 0),
            "fixed_symbols": fixed.get("symbols") or [],
            "fixed_symbol_count": len(fixed.get("symbols") or []),
            "evo_4h_last": (fixed.get("evo_4h") or {}).get("last_at"),
            "evo_5m_last": (fixed.get("evo_5m") or {}).get("last_at"),
        },
        "heartbeats": hb,
        "funnel_counts": funnel.get("counts", {}),
        "training": train,
        "fixed_pool": fixed,
        "ai_scan": ai_scan,
        "candidates_summary": {
            "by_symbol": cands.get("by_symbol", []),
            "pass_count_global": cands.get("pass_count_global", 0),
            "symbol_count_24h": cands.get("symbol_count_24h", 0),
        },
    }


@router.get("/heartbeats")
def ops_heartbeats() -> Dict[str, Any]:
    # [perf 2026-08-18] 高频轮询 + JSON 详情解析：5s TTL。
    from backend.utils.ttl_cache import ttl_cached

    return ttl_cached("ops_heartbeats", 5.0, lambda: _ops_heartbeats_impl())


def _ops_heartbeats_impl() -> Dict[str, Any]:
    from backend.services.scalp.scalp_heartbeat import get_heartbeats

    raw = get_heartbeats() or {}
    # [2026-08-14] 已知"按配置关闭"的任务：开关关闭时心跳不会更新（任务跳过不
    # touch），按 age 会误报"中断"。此处显式标记 disabled，前端显示"已关闭"。
    _disabled_tasks = set()
    if os.getenv("PAIR_SELECTOR_WATCHER_ENABLED", "true").strip().lower() not in (
        "1", "true", "yes", "on",
    ):
        _disabled_tasks.add("pair_selector_watcher")
    items = []
    for tid, info in sorted(raw.items()):
        age = _age_sec(info.get("last_ok_at"))
        if tid in _disabled_tasks or str(info.get("last_status")) == "disabled":
            sla = "disabled"
        else:
            cad = _HB_CADENCE_SEC.get(tid)
            sla = _sla(age, cad["warn"], cad["crit"]) if cad else _sla(age)
        items.append({
            "task_id": tid,
            "last_ok_at": info.get("last_ok_at"),
            "last_status": info.get("last_status"),
            "detail": info.get("detail") or info.get("detail_json") or {},
            "age_sec": round(age, 1) if age is not None else None,
            "age_human": _human_age(age),
            "sla": sla,
        })
    return {"items": items, "warn_sec": _HB_WARN_SEC, "crit_sec": _HB_CRIT_SEC}


def _human_age(age: Optional[float]) -> str:
    if age is None:
        return "从未"
    if age < 60:
        return f"{int(age)}秒前"
    if age < 3600:
        return f"{int(age // 60)}分钟前"
    if age < 86400:
        return f"{age / 3600:.1f}小时前"
    return f"{age / 86400:.1f}天前"


@router.get("/factor-pool")
def ops_factor_pool(
    view: str = Query("tradable", description="tradable|research|shadow|quarantine"),
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    # [perf 2026-08-18] 高频轮询 + 多组 SQL 聚合：5s TTL。
    from backend.utils.ttl_cache import ttl_cached

    return ttl_cached(
        f"ops_factor_pool:{view}:{limit}", 5.0,
        lambda: _ops_factor_pool_impl(view, limit),
    )


def _ops_factor_pool_impl(view: str, limit: int) -> Dict[str, Any]:
    from backend.database.connection import AnalyticsSessionLocal
    from backend.services.factor_engine.active_set_policy import (
        ActiveSetRole,
        load_factor_active_rows,
        states_for,
    )
    from sqlalchemy import text

    role_map = {
        "tradable": ActiveSetRole.UI_TOP,
        "research": ActiveSetRole.RESEARCH,
        "shadow": ActiveSetRole.SHADOW,
        "quarantine": ActiveSetRole.QUARANTINE,
    }
    role = role_map.get(view.lower(), ActiveSetRole.UI_TOP)
    rows = load_factor_active_rows(role, parse_expr=False, limit=limit)
    items = []
    for r in rows:
        cw = r.get("current_weight") or {}
        w = None
        if isinstance(cw, dict) and cw:
            try:
                w = float(next(iter(cw.values())))
            except Exception:
                w = None
        items.append({
            "factor_id": r.get("factor_id"),
            "state": r.get("state"),
            "source": r.get("source"),
            "icir": r.get("icir"),
            "last_net_ic": r.get("last_net_ic"),
            "online_weight": w,
            "activated_at": (
                r["activated_at"].isoformat()
                if hasattr(r.get("activated_at"), "isoformat")
                else r.get("activated_at")
            ),
            "router_reachable": str(r.get("state")) in states_for(ActiveSetRole.TRADABLE),
        })

    state_dist: Dict[str, int] = {}
    quarantine_reasons: List[Dict[str, Any]] = []
    try:
        with AnalyticsSessionLocal() as db:
            dist_rows = db.execute(
                text("SELECT state, count(*) AS n FROM factor_active_set GROUP BY state")
            ).mappings().all()
            state_dist = {str(r["state"] or "?"): int(r["n"] or 0) for r in dist_rows}
            qrows = db.execute(
                text(
                    "SELECT left(coalesce(reason,''), 100) AS reason, count(*) AS n "
                    "FROM factor_evolution_log "
                    "WHERE action = 'quarantine' "
                    "AND created_at >= now() - interval '14 days' "
                    "GROUP BY left(coalesce(reason,''), 100) "
                    "ORDER BY n DESC LIMIT 5"
                )
            ).mappings().all()
            quarantine_reasons = [
                {"reason": r["reason"] or "（无原因）", "n": int(r["n"] or 0)}
                for r in qrows
            ]
    except Exception as e:
        logger.warning("[Ops] factor-pool state_dist: %s", e)

    tradable_n = (
        int(state_dist.get("PAPER", 0))
        + int(state_dist.get("SMALL_LIVE", 0))
        + int(state_dist.get("ACTIVE", 0))
    )
    research_n = tradable_n + int(state_dist.get("ORTHO", 0))
    quarantine_n = int(state_dist.get("QUARANTINE", 0))

    if role in (ActiveSetRole.UI_TOP, ActiveSetRole.TRADABLE):
        callout = "可交易=PAPER/SMALL_LIVE/ACTIVE（与 Router 热路径一致）"
        if not items and quarantine_n > 0:
            callout = (
                f"可交易=0：库内 {quarantine_n} 行全在隔离区 QUARANTINE，"
                "请切到「隔离」查看（不是看板坏了）"
            )
    elif role == ActiveSetRole.RESEARCH:
        callout = "研究=ORTHO+可交易；不等于可进 Router"
        if not items and quarantine_n > 0:
            callout = (
                f"研究池为空；隔离区有 {quarantine_n} 行，切「隔离」可看"
            )
    elif role == ActiveSetRole.QUARANTINE:
        callout = "隔离区：IC 衰减/复评失败后移出交易面，需进化回路重新晋升"
    else:
        callout = ""

    _pipeline_health = None
    try:
        from backend.services.factor_engine.pipeline_health import collect_factor_pipeline_health
        _pipeline_health = collect_factor_pipeline_health()
    except Exception as _ph_err:
        logger.debug("[Ops] pipeline_health 采集失败: %s", _ph_err)

    return {
        "view": view,
        "role": role.value,
        "states": sorted(states_for(role)),
        "total": len(items),
        "items": items,
        "callout": callout,
        "state_dist": state_dist,
        "counts": {
            "tradable": tradable_n,
            "research": research_n,
            "quarantine": quarantine_n,
            "all": sum(state_dist.values()),
        },
        "quarantine_reasons": quarantine_reasons,
        # [2026-08-14 阶段0] 因子管线健康快照（白名单命中率 / DSR 配置）
        "pipeline_health": _pipeline_health,
    }


# ══════════════════════════════════════════════════════════
#  P0 运维台 · 中线因子概况/挖掘/回测（阶段2-1，与短线运维台对称）
# ══════════════════════════════════════════════════════════

def _midlong_horizon(r: Dict[str, Any]) -> bool:
    return str((r.get("extra") or {}).get("horizon") or "scalp").lower() == "midlong"


# [2026-08-16] K 线预检缓存：GUI 高频轮询本端点，每次 18 次全量 K 线查询
# （9 币 × 4h/1d × ~2400 根）对 DB 池压力大且结果分钟级不变。
# [perf] TTL 60→300s + 批量 COUNT（一次 SQL），预检从 ~5s 降到毫秒级。
_MIDLONG_PREFLIGHT_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_MIDLONG_PREFLIGHT_TTL_SEC = 300.0


@router.get("/midlong-factors")
def ops_midlong_factors() -> Dict[str, Any]:
    """中线因子概况：活跃/候选/拒绝计数、按时间框架、Top 活跃、最近回测证据、
    打分截面 K 线预检、当前闸门参数。只读。"""
    from backend.services.factor_engine.midlong_active_factor_set import (
        midlong_active_factor_set,
    )
    from backend.services.factor_engine.custom_factor_store import custom_factor_store
    from backend.services.coin_select_platform_service import resolve_admin_tenant_id
    from backend.config import settings as _s

    tid = resolve_admin_tenant_id()
    health = midlong_active_factor_set.get_health_snapshot()

    candidates = [
        r for r in custom_factor_store.list_candidates(tenant_id=tid) if _midlong_horizon(r)
    ]
    rejected = [
        r for r in custom_factor_store.list(status="rejected", tenant_id=tid)
        if _midlong_horizon(r)
    ]
    rejected.sort(key=lambda x: float(x.get("scored_at") or 0), reverse=True)

    cand_items = [
        {
            "factor_id": r.get("factor_id"),
            "name": r.get("name"),
            "timeframe": (r.get("extra") or {}).get("timeframe"),
            "note": (r.get("extra") or {}).get("note"),
            "category": r.get("category"),
            "source": r.get("source"),
        }
        for r in candidates
    ]
    rej_items = [
        {
            "factor_id": r.get("factor_id"),
            "name": r.get("name"),
            "timeframe": (r.get("extra") or {}).get("timeframe"),
            "grade": r.get("grade"),
            "scores": r.get("scores") or {},
            "scored_at": r.get("scored_at"),
        }
        for r in rejected[:20]
    ]

    # K 线预检：与打分器同源（get_klines_from_db），确保「能挖」判断真实
    # [2026-08-16] 60s TTL 缓存，避免 GUI 高频轮询重复触发 18 次全量 K 线查询。
    preflight: Dict[str, Any]
    _now = time.time()
    if (
        _MIDLONG_PREFLIGHT_CACHE["data"] is not None
        and (_now - _MIDLONG_PREFLIGHT_CACHE["ts"]) < _MIDLONG_PREFLIGHT_TTL_SEC
    ):
        preflight = dict(_MIDLONG_PREFLIGHT_CACHE["data"])
    else:
        preflight = {"symbols": [], "rows": {}}
        try:
            _syms = [
                s.strip().upper()
                for s in str(_s.FACTOR_SCORER_SYMBOLS if hasattr(_s, "FACTOR_SCORER_SYMBOLS")
                             else "BTC,ETH,SOL").split(",")
                if s.strip()
            ]
            from backend.services.factor_engine.factor_backtest_scorer import (
                midlong_lookback_for,
                midlong_min_bars_for,
            )
            from backend.services.kline_data_service import kline_service

            preflight["symbols"] = _syms
            preflight["need_bars"] = {}
            preflight["min_bars"] = {}
            preflight["effective"] = {}
            # [perf] 批量 COUNT 替代 18 次全量 K 线拉取（单次 SQL，索引直查）。
            _counts = kline_service.count_klines_from_db(
                [(s, tf) for tf in ("4h", "1d") for s in _syms]
            )
            for tf in ("4h", "1d"):
                lb = midlong_lookback_for(tf)
                minb = midlong_min_bars_for(tf)
                preflight["need_bars"][tf] = lb
                preflight["min_bars"][tf] = minb
                preflight["rows"][tf] = {}
                preflight["effective"][tf] = {}
                for sym in _syms:
                    try:
                        n = _counts.get(f"{sym}:{tf}", 0)
                        preflight["rows"][tf][sym] = n
                        # 有效回看 = min(目标, 可用)；不足目标时按现有最大值打分
                        preflight["effective"][tf][sym] = min(lb, n) if n > 0 else 0
                    except Exception:
                        preflight["rows"][tf][sym] = -1
                        preflight["effective"][tf][sym] = 0
            # 「能否挖」只看最小可用根数（min_bars），不再被目标 lookback 卡死
            preflight["ok"] = all(
                (preflight["rows"][tf].get(s) or 0) >= preflight["min_bars"][tf]
                for tf in ("4h", "1d") for s in _syms
            )
            preflight["insufficient"] = {
                tf: [
                    s for s in _syms
                    if (preflight["rows"][tf].get(s) or 0) < preflight["min_bars"][tf]
                ]
                for tf in ("4h", "1d")
            }
        except Exception as e:
            preflight["error"] = str(e)[:150]
        _MIDLONG_PREFLIGHT_CACHE["ts"] = time.time()
        _MIDLONG_PREFLIGHT_CACHE["data"] = dict(preflight)

    return {
        "health": health,
        "candidates": cand_items,
        "candidate_count": len(cand_items),
        "rejected_recent": rej_items,
        "rejected_count": len(rejected),
        "preflight": preflight,
        "gate_config": {
            "lookback": int(getattr(_s, "FACTOR_SCORER_MIDLONG_LOOKBACK", 2400)),
            "lookback_1d": int(getattr(_s, "FACTOR_SCORER_MIDLONG_LOOKBACK_1D", 1000)),
            "fwd_4h": int(getattr(_s, "FACTOR_SCORER_MIDLONG_FWD_4H", 6)),
            "fwd_1d": int(getattr(_s, "FACTOR_SCORER_MIDLONG_FWD_1D", 3)),
            "min_sharpe": float(getattr(_s, "FACTOR_SCORER_MIDLONG_MIN_SHARPE", 0.4)),
            "active_max": int(getattr(_s, "MIDLONG_ACTIVE_FACTOR_MAX", 30)),
            "research_enabled": bool(getattr(_s, "MIDLONG_FACTOR_RESEARCH_ENABLED", True)),
        },
    }


@router.get("/long-trend-v2")
def ops_long_trend_v2(session_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    """长线 V2 规则化状态：每个固定长线币的 L1 状态/score/strength（无 LLM）。只读。"""
    # [perf 2026-08-18] 每币拉 1200 根 1d K 线 + pandas 分类，GIL 竞争下实测 4.5s。
    # 长线状态分钟级稳定：15s TTL 缓存。
    from backend.utils.ttl_cache import ttl_cached

    return ttl_cached(
        f"ops_long_trend_v2:{session_id or ''}", 15.0,
        lambda: _ops_long_trend_v2_impl(session_id),
    )


def _ops_long_trend_v2_impl(session_id: Optional[str]) -> Dict[str, Any]:
    from backend.services.long_trend_v2 import long_v2_enabled
    from backend.services.trend_layer import classify
    from backend.services.kline_data_service import kline_service
    import pandas as pd

    enabled = long_v2_enabled()
    symbols: list = []
    try:
        from backend.services.auto_coin_selector import get_fixed_symbols_for_session
        if session_id:
            symbols = list(get_fixed_symbols_for_session(session_id, tier="long") or [])
    except Exception:
        symbols = []
    if not symbols:
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import FullAutoSession
            db = SessionLocal()
            try:
                db.execute(text("SET app.tenant_id='326'"))
                db.execute(text("SET app.is_admin='on'"))
                sess = db.query(FullAutoSession).filter(
                    FullAutoSession.status.in_(["running", "defensive"])
                ).order_by(FullAutoSession.id.desc()).first()
                if sess:
                    by_tier = getattr(sess, "fixed_symbols_by_tier", None) or {}
                    if isinstance(by_tier, dict):
                        symbols = list(by_tier.get("long") or [])
            finally:
                db.close()
        except Exception:
            symbols = []

    out: list = []
    for s in symbols:
        sym = str(s).upper()
        row = {"symbol": sym, "state": "sideways", "score": 0, "strength": 0.0, "close": None, "note": ""}
        try:
            rows = kline_service.get_klines_from_db(sym, "1d", 1200)
            if not rows or len(rows) < 260:
                row["note"] = "1d 数据不足(<260根)"
            else:
                df = pd.DataFrame(rows)
                for c in ("open", "high", "low", "close"):
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                df = df.dropna(subset=["close", "high", "low"]).reset_index(drop=True)
                c = classify(df)
                row["state"] = c.get("state", "sideways")
                row["score"] = c.get("score", 0)
                row["strength"] = c.get("strength", 0.0)
                row["close"] = c.get("close")
        except Exception as e:
            row["note"] = f"判定异常: {type(e).__name__}"
        out.append(row)

    return {"enabled": enabled, "symbols": out}

@router.post("/factors/llm-propose")
def ops_factor_llm_propose(tier: str = Query("midlong", description="midlong|scalp"), k: int = Query(8, ge=1, le=10)):
    """[M6/P4] LLM 提案层：生成 numpy 公式候选并注册（source=llm，同门禁+更严参数）。"""
    from backend.services.factor_engine.llm_proposal import propose_and_register
    return propose_and_register(tier=tier, k=k)


@router.post("/factors/quick-score")
def ops_factor_quick_score(payload: Dict[str, Any]):
    """[R4 因子工厂] 公式 AST → 秒级诊断 + 门禁预览（只读，不注册不晋升）。

    body = {"ast": {...}, "tier": "midlong"|"scalp"}；响应含 IC/ICIR/衰减/换手/
    与 active 集最大相关 + 门禁阈值预览。口径与正式 score_formula 同一评分函数。
    """
    from backend.services.factor_engine.quick_score import quick_score

    ast = payload.get("ast") if isinstance(payload, dict) else None
    if not isinstance(ast, dict):
        return {"ok": False, "error": "ast 必须为表达式 dict"}
    tier = str(payload.get("tier") or "midlong")
    try:
        return quick_score(ast, tier=tier)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


@router.post("/midlong-factors/mine")
def ops_midlong_mine(validate: bool = Query(True, description="灌库后立即排队样本外回测")):
    """一键快速挖掘：灌库 Alpha101 候选（幂等）→ 后台单飞排队回测。
    返回 seed 统计 + 异步 job 信息（用 GET /api/factors/jobs/{job_id} 轮询）。"""
    from backend.services.factor_engine.alpha101_factors import seed_alpha101

    # [2026-08-14 P2-1/弹药扩源] reopen=True：把 rejected 的 alpha101 重开为
    # candidate（修复"二次挖矿空转"）；同时登记 registry 因子并排队扫描。
    seed = seed_alpha101(["4h", "1d"], reopen=True)
    out: Dict[str, Any] = {"seed": seed, "validate": None, "registry_scan": None}
    try:
        from backend.services.factor_engine.midlong_registry_factors import seed_registry_candidates
        out["registry_seed"] = seed_registry_candidates(["4h", "1d"])
    except Exception as e:
        out["registry_seed"] = {"error": str(e)[:200]}
    if validate:
        from backend.services.factor_engine.factor_jobs import (
            run_validate_alpha101,
            run_scan_registry_midlong,
        )
        job = run_validate_alpha101(limit=80)
        out["validate"] = job.to_dict()
        try:
            job2 = run_scan_registry_midlong(limit=200)
            out["registry_scan"] = job2.to_dict()
        except Exception as e:
            out["registry_scan"] = {"error": str(e)[:200]}
    return out


@router.post("/midlong-factors/prune")
def ops_midlong_prune():
    """对活跃中线因子重跑样本外复检，衰减者退役/降级（当前 active=0 时为无害空跑）。"""
    from backend.services.factor_engine.midlong_active_factor_set import (
        midlong_active_factor_set,
    )
    return midlong_active_factor_set.recheck_and_prune()


@router.get("/evolution-funnel")
def ops_evolution_funnel(days: int = Query(7, ge=1, le=90)) -> Dict[str, Any]:
    # [perf 2026-08-18] 轮询端点：10s TTL。
    from backend.utils.ttl_cache import ttl_cached

    return ttl_cached(f"ops_evolution_funnel:{days}", 10.0, lambda: _ops_evolution_funnel_impl(days))


def _ops_evolution_funnel_impl(days: int) -> Dict[str, Any]:
    from backend.database.connection import AnalyticsSessionLocal

    counts: Dict[str, int] = {}
    rejects: List[Dict[str, Any]] = []
    try:
        with AnalyticsSessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT action, count(*) AS n FROM factor_evolution_log "
                    "WHERE created_at >= now() - (:d || ' days')::interval "
                    "GROUP BY action ORDER BY n DESC"
                ),
                {"d": str(int(days))},
            ).mappings().all()
            counts = {str(r["action"] or "unknown"): int(r["n"]) for r in rows}
            rej = db.execute(
                text(
                    "SELECT factor_id, action, reason, metrics, created_at "
                    "FROM factor_evolution_log "
                    "WHERE created_at >= now() - (:d || ' days')::interval "
                    "AND action IN ('promote_reject','reject','quarantine','deactivate') "
                    "ORDER BY created_at DESC LIMIT 40"
                ),
                {"d": str(int(days))},
            ).mappings().all()
            for r in rej:
                rejects.append({
                    "factor_id": r["factor_id"],
                    "action": r["action"],
                    "reason": r["reason"],
                    "metrics": r["metrics"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                })
    except Exception as e:
        logger.warning("[Ops] evolution-funnel: %s", e)
        return {"days": days, "counts": {}, "rejects": [], "error": str(e)}
    return {"days": days, "counts": counts, "rejects": rejects}


@router.get("/candidates")
def ops_candidates(limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    """候选列表：按币汇总 + 全表 pass + 门禁中文原因；避免「只看见最近一币」。"""
    # [perf 2026-08-18] 高频轮询 + 多组 SQL 聚合：5s TTL。
    from backend.utils.ttl_cache import ttl_cached

    return ttl_cached(f"ops_candidates:{limit}", 5.0, lambda: _ops_candidates_impl(limit))


def _ops_candidates_impl(limit: int) -> Dict[str, Any]:
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    from backend.services.scalp.pair_selector import ensure_table

    ensure_table()
    items: List[Dict[str, Any]] = []
    by_symbol: List[Dict[str, Any]] = []
    pass_global = 0
    promising_global = 0
    fail_global = 0
    symbol_count_24h = 0

    def _row_item(r) -> Dict[str, Any]:
        metrics = _parse_metrics(r.get("metrics_json"))
        verdict = r.get("gate_verdict")
        return {
            "id": int(r["id"]),
            "symbol": r["symbol"],
            "period": r["period"],
            "factor_set": r["factor_set"],
            "gate_verdict": verdict,
            "gate_reasons": _gate_reasons(metrics, verdict),
            "metrics": {
                "n": metrics.get("n") or (metrics.get("total") or {}).get("n"),
                "pf": metrics.get("profit_factor")
                or (metrics.get("total") or {}).get("profit_factor"),
                "t_stat": metrics.get("t_stat")
                or (metrics.get("total") or {}).get("t_stat"),
                "older_pf": metrics.get("older_pf"),
                "newer_pf": metrics.get("newer_pf"),
            },
            "created_at": (
                r["generated_at"].isoformat() if r.get("generated_at") else None
            ),
        }

    with system_identity():
        with SessionLocal() as db:
            try:
                stats = db.execute(
                    text(
                        "SELECT "
                        "COUNT(*) FILTER (WHERE gate_verdict='pass') AS n_pass, "
                        "COUNT(*) FILTER (WHERE gate_verdict='promising') AS n_prom, "
                        "COUNT(*) FILTER (WHERE gate_verdict='fail') AS n_fail, "
                        "COUNT(DISTINCT symbol) FILTER ("
                        "  WHERE generated_at >= now() - interval '24 hours') AS n_sym "
                        "FROM pair_strategy_candidates"
                    )
                ).mappings().first()
                if stats:
                    pass_global = int(stats["n_pass"] or 0)
                    promising_global = int(stats["n_prom"] or 0)
                    fail_global = int(stats["n_fail"] or 0)
                    symbol_count_24h = int(stats["n_sym"] or 0)

                by_rows = db.execute(
                    text(
                        "SELECT symbol, "
                        "COUNT(*) AS n, "
                        "SUM(CASE WHEN gate_verdict='pass' THEN 1 ELSE 0 END) AS n_pass, "
                        "SUM(CASE WHEN gate_verdict='promising' THEN 1 ELSE 0 END) AS n_prom, "
                        "SUM(CASE WHEN gate_verdict='fail' THEN 1 ELSE 0 END) AS n_fail, "
                        "MAX(generated_at) AS last_at "
                        "FROM pair_strategy_candidates "
                        "WHERE generated_at >= now() - interval '24 hours' "
                        "GROUP BY symbol ORDER BY "
                        "SUM(CASE WHEN gate_verdict='pass' THEN 1 ELSE 0 END) DESC, "
                        "last_at DESC NULLS LAST"
                    )
                ).mappings().all()
                for r in by_rows:
                    by_symbol.append({
                        "symbol": r["symbol"],
                        "n": int(r["n"] or 0),
                        "n_pass": int(r["n_pass"] or 0),
                        "n_promising": int(r["n_prom"] or 0),
                        "n_fail": int(r["n_fail"] or 0),
                        "last_at": r["last_at"].isoformat() if r["last_at"] else None,
                    })

                # 优先拉 pass / promising，再补各币最新 fail，避免被单币 27 行冲掉
                preferred = db.execute(
                    text(
                        "SELECT id, symbol, period, factor_set, gate_verdict, "
                        "metrics_json, generated_at "
                        "FROM pair_strategy_candidates "
                        "WHERE gate_verdict IN ('pass','promising') "
                        "ORDER BY "
                        "CASE gate_verdict WHEN 'pass' THEN 0 ELSE 1 END, "
                        "id DESC LIMIT :lim"
                    ),
                    {"lim": max(10, limit // 2)},
                ).mappings().all()
                seen_ids = set()
                for r in preferred:
                    item = _row_item(r)
                    seen_ids.add(item["id"])
                    items.append(item)

                # 每币取最新 1 条（展示多样性）
                latest_per_sym = db.execute(
                    text(
                        "SELECT DISTINCT ON (symbol) id, symbol, period, factor_set, "
                        "gate_verdict, metrics_json, generated_at "
                        "FROM pair_strategy_candidates "
                        "ORDER BY symbol, id DESC"
                    )
                ).mappings().all()
                for r in latest_per_sym:
                    iid = int(r["id"])
                    if iid in seen_ids:
                        continue
                    seen_ids.add(iid)
                    items.append(_row_item(r))
                    if len(items) >= limit:
                        break

                if len(items) < limit:
                    recent = db.execute(
                        text(
                            "SELECT id, symbol, period, factor_set, gate_verdict, "
                            "metrics_json, generated_at "
                            "FROM pair_strategy_candidates ORDER BY id DESC LIMIT :lim"
                        ),
                        {"lim": limit},
                    ).mappings().all()
                    for r in recent:
                        iid = int(r["id"])
                        if iid in seen_ids:
                            continue
                        seen_ids.add(iid)
                        items.append(_row_item(r))
                        if len(items) >= limit:
                            break
            except Exception as e:
                return {
                    "items": [],
                    "pass_count": 0,
                    "pass_count_global": 0,
                    "by_symbol": [],
                    "error": str(e),
                    "callout": "读候选表失败",
                }

    page_pass = sum(1 for x in items if x.get("gate_verdict") == "pass")
    return {
        "items": items[:limit],
        "pass_count": page_pass,
        "pass_count_global": pass_global,
        "promising_count_global": promising_global,
        "fail_count_global": fail_global,
        "symbol_count_24h": symbol_count_24h,
        "by_symbol": by_symbol,
        "total": len(items[:limit]),
        "callout": (
            "列表已按「pass优先 + 每币最新」混合，避免只显示最近扫描的一币；"
            f"全表 pass={pass_global}，24h 涉及 {symbol_count_24h} 个币"
        ),
    }


@router.get("/bindings")
def ops_bindings(limit: int = Query(100, ge=1, le=500)) -> Dict[str, Any]:
    # [perf 2026-08-18] 高频轮询 + 全表读取：5s TTL。
    from backend.utils.ttl_cache import ttl_cached

    return ttl_cached(f"ops_bindings:{limit}", 5.0, lambda: _ops_bindings_impl(limit))


def _ops_bindings_impl(limit: int) -> Dict[str, Any]:
    from backend.services.scalp.scalp_bindings import ensure_tables, list_bindings

    ensure_tables()
    try:
        rows = list_bindings()
    except Exception:
        rows = []
        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal
        with system_identity():
            with SessionLocal() as db:
                raw = db.execute(
                    text("SELECT * FROM pair_strategy_bindings ORDER BY id DESC LIMIT :lim"),
                    {"lim": limit},
                ).mappings().all()
                for r in raw:
                    rows.append(dict(r))
    status_dist: Dict[str, int] = {}
    for r in rows[:limit]:
        st = str(r.get("status") or "unknown")
        status_dist[st] = status_dist.get(st, 0) + 1
    lane_enabled = os.getenv("PAIR_BINDING_LANE_ENABLED", "false").lower() in (
        "1", "true", "yes", "on",
    )
    ai_syms: List[str] = []
    try:
        from backend.services.scalp.pair_selector_watcher import _active_auto_symbols
        ai_syms = _active_auto_symbols()
    except Exception:
        pass
    ai_set = {s.upper() for s in ai_syms}
    enriched = []
    for r in rows[:limit]:
        item = dict(r) if not isinstance(r, dict) else {**r}
        sym = str(item.get("symbol") or "").upper()
        item["in_ai_pool"] = sym in ai_set if ai_set else None
        item["link_note"] = (
            "历史 pass 晋级；与当前候选窗口无关"
            if item.get("status") == "running"
            else item.get("stop_reason")
        )
        enriched.append(item)
    return {
        "items": enriched,
        "status_dist": status_dist,
        "ai_symbols": ai_syms,
        "lane": {
            "PAIR_BINDING_LANE_ENABLED": lane_enabled,
            "note": (
                "干跑=调度只写心跳不开仓；running 绑定会一直挂着，"
                "不是候选列表矛盾，而是「已启用未执行」"
            ),
        },
        "circuit_breaker": {
            "SCALP_CIRCUIT_BREAKER_ENABLED": os.getenv(
                "SCALP_CIRCUIT_BREAKER_ENABLED", "false"
            ).lower() in ("1", "true", "yes", "on"),
        },
    }


@router.get("/training")
def ops_training() -> Dict[str, Any]:
    """元标签 + 固定池进化进度 + AI 快速扫描进度（一眼分清三条链）。"""
    # [perf 2026-08-18] 3 个子摘要串行，GIL 竞争下实测 8.2s。10s TTL。
    from backend.utils.ttl_cache import ttl_cached

    return ttl_cached("ops_training", 10.0, lambda: _ops_training_impl())


def _ops_training_impl() -> Dict[str, Any]:
    meta = _meta_train_digest()
    return {
        **meta,
        "fixed_pool": _fixed_pool_digest(),
        "ai_scan": _ai_scan_digest(),
    }


@router.get("/errors")
def ops_errors(limit: int = Query(100, ge=1, le=500)) -> Dict[str, Any]:
    """中文分级报错中心：系统日志 + 心跳中断 + 车道配置谎言。"""
    # [perf 2026-08-18] GUI 高频轮询：5s TTL 缓存（GIL 竞争下命中≈0ms）。
    from backend.utils.ttl_cache import ttl_cached

    return ttl_cached(f"ops_errors:{limit}", 10.0, lambda: _ops_errors_impl(limit))


def _ops_errors_impl(limit: int) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    try:
        from backend.services.system_logger import system_logger
        logs = system_logger.get_logs(level=None, category=None, limit=limit, min_level="WARNING")
        for lg in logs:
            lvl = str(lg.get("level") or "WARNING").upper()
            sev = "P1" if lvl == "ERROR" else ("P2" if "WARN" in lvl else "P3")
            items.append({
                "severity": sev,
                "source": "system_logs",
                "level": lvl,
                "category": lg.get("category"),
                "message": lg.get("message") or lg.get("msg"),
                "timestamp": lg.get("timestamp") or lg.get("created_at"),
            })
    except Exception as e:
        items.append({
            "severity": "P2",
            "source": "ops",
            "message": f"读取 system_logs 失败: {e}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    hb = ops_heartbeats()
    _hb_cn = {
        "pair_selector_watcher": "AI选币扫描",
        "pair_binding_lane": "币种绑定车道",
        "scalp_chain_health": "短线链路健康",
        "scalp_circuit_breaker": "短线熔断器",
    }
    for h in hb.get("items", []):
        _name = _hb_cn.get(h.get("task_id") or "", h.get("task_id") or "?")
        if h.get("sla") == "down":
            items.append({
                "severity": "P0",
                "source": "heartbeat",
                "message": f"心跳中断：{_name}（{h.get('age_human')}）",
                "timestamp": h.get("last_ok_at"),
                "task_id": h["task_id"],
            })
        elif h.get("sla") == "lag":
            items.append({
                "severity": "P1",
                "source": "heartbeat",
                "message": f"心跳滞后：{_name}（{h.get('age_human')}）",
                "timestamp": h.get("last_ok_at"),
                "task_id": h["task_id"],
            })
        # [2026-08-14] sla=disabled（按配置关闭）不生成故障项，避免"已关闭"
        # 的任务在报错中心反复刷 P0 误导。

    if os.getenv("PAIR_BINDING_LANE_ENABLED", "false").lower() not in ("1", "true", "yes", "on"):
        # 信息项，非故障
        pass

    p0 = sum(1 for x in items if x.get("severity") == "P0")
    p1 = sum(1 for x in items if x.get("severity") == "P1")
    _maybe_alert_p0(p0)
    return {
        "items": items[:limit],
        "counts": {"P0": p0, "P1": p1, "total": len(items)},
    }


@router.get("/health-digest")
def ops_health_digest() -> Dict[str, Any]:
    err = ops_errors(limit=50)
    return {
        "opencode": {
            "available": False,
            "reason": "路由已在 main.py 注释；禁止轮询 /api/opencode/*",
        },
        "errors": err.get("counts", {}),
        "lane_enabled": os.getenv("PAIR_BINDING_LANE_ENABLED", "false").lower() in (
            "1", "true", "yes", "on",
        ),
    }


@router.post("/bindings/{binding_id}/pause")
def ops_pause_binding(binding_id: int) -> Dict[str, Any]:
    from backend.services.scalp.scalp_bindings import disable_binding

    try:
        out = disable_binding(binding_id, reason="ops_manual_pause")
        return {"ok": True, "binding": out}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/candidates/{candidate_id}/enable")
def ops_enable_candidate(candidate_id: int) -> Dict[str, Any]:
    from backend.services.scalp.scalp_bindings import enable_candidate

    try:
        out = enable_candidate(candidate_id)
        return {"ok": True, "binding": out}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
