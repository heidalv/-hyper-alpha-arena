# -*- coding: utf-8 -*-
"""[F56] 车道注册表（lane_registry）—— 复合策略的单一事实源。

设计依据：《复合策略与交易系统全面改造设计_V2》§2.2 / §3.3

职责：
  1. 登记每条车道（lane_id / mode / status / edge_metric / risk_budget / health）；
  2. 承载**晋升判定**（paper → live / live → paper / 退役），判定条件写死在
     `PROMOTION_CRITERIA`，任何车道不得绕过；
  3. 为前端「套利中心 · 总览/车道」两页提供唯一数据源。

约定：
  - `mode`: paper（模拟盘直跑＝影子）| live | disabled
  - `status`: active | paused | stopped
  - `edge_json`: {gross_bp, cost_bp, net_bp, t, n, folds:[{net_bp,t,n}], max_dd_pct,
                 fill_rate_ratio, as_of}
    —— 全部为**扣费后**口径；缺字段视为未验证。
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ensured = False
_ensure_lock = threading.Lock()

# ── 晋升判定阈值（与设计文档 §3.3 一致，改动须同步文档） ──
PROMOTION_CRITERIA: Dict[str, Dict[str, Any]] = {
    "edge_verified": {"threshold": None, "label": "边际数据来自可信来源"},
    "min_trades": {"threshold": 200, "label": "成交 ≥200 笔"},
    "folds_positive": {"threshold": 4, "label": "滚动 4 折净期望全 > 0"},
    "fold_t": {"threshold": 2.0, "label": "每折 t > 2"},
    "max_drawdown_pct": {"threshold": 1.5, "label": "最大回撤 < 权益 1.5%"},
    "fill_rate_ratio": {"threshold": 0.30, "label": "真实成交率 ≥ 离线模拟 30%"},
    "net_positive": {"threshold": 0.0, "label": "净期望 > 0"},
}

# 只有这些来源的 edge 才允许参与晋升判定。
# 教训：曾有一条人工写入的假 edge（n=250、四折 t=3.0）留在生产 registry 里，
# 让一条**从未跑过**的车道显示「晋级就绪」。来源标记 + fail-closed 是止血措施。
EDGE_SOURCES = ("f59_replay", "paper_shadow", "live", "hedged_backtest")

# ── 初始车道清单（来自设计文档 §1.1） ──
DEFAULT_LANES: List[Dict[str, Any]] = [
    {
        "lane_id": "mm_asterdex",
        "mode": "paper",
        "status": "stopped",          # 未启动前为 stopped
        "risk": {"budget_pct": 45.0, "max_symbol_exposure_pct": 5.0,
                 "max_net_exposure_pct": 30.0, "daily_loss_stop_pct": 1.0},
        "meta": {"name": "L1 被动做市 · Asterdex", "edge_source": "spread+mkr",
                 "symbols": ["BTC", "ETH", "BNB", "XRP", "SOL", "DOGE"],
                 "note": "Aster maker 0%；挂宽 ≥5bp；离线已证正(F52/F53)"},
    },
    {
        "lane_id": "carry_basis",
        "mode": "paper",
        "status": "stopped",
        "risk": {"budget_pct": 25.0, "max_net_exposure_pct": 20.0,
                 "daily_loss_stop_pct": 1.0},
        "meta": {"name": "L2 资金费/基差 carry", "edge_source": "funding+basis",
                 "note": "裸永续为负(F43)；需对冲腿（现货或跨所）"},
    },
    {
        "lane_id": "xvenue_spread",
        "mode": "paper",
        "status": "stopped",
        "risk": {"budget_pct": 15.0, "max_net_exposure_pct": 15.0,
                 "daily_loss_stop_pct": 1.0},
        "meta": {"name": "L3 跨所价差", "edge_source": "cross-venue",
                 "note": "待验证；费率差仅 0.1–0.3bp，需同步盘口"},
    },
    {
        "lane_id": "liq_reversal",
        "mode": "paper",
        "status": "stopped",
        "risk": {"budget_pct": 10.0, "max_net_exposure_pct": 10.0,
                 "daily_loss_stop_pct": 1.0},
        "meta": {"name": "L4 清算冲击回复", "edge_source": "liquidation",
                 "note": "未验证"},
    },
    {
        "lane_id": "scalp_directional",
        "mode": "disabled",
        "status": "stopped",
        "risk": {"budget_pct": 0.0},
        "meta": {"name": "L5 方向性短线（旧 scalp）", "edge_source": "-",
                 "note": "当前退役（SCALP_OPEN_DISABLED=true）；非永久——晋升判定恢复可用"},
    },
]


def ensure_table() -> None:
    """建表（每进程一次）。遵循 scalp_heartbeat 的既有模式，避免 DDL 锁排队。"""
    global _ensured
    if _ensured:
        return
    with _ensure_lock:
        if _ensured:
            return
        try:
            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    db.execute(text(
                        "CREATE TABLE IF NOT EXISTS lane_registry ("
                        " lane_id VARCHAR(64) PRIMARY KEY,"
                        " mode VARCHAR(16) NOT NULL DEFAULT 'paper',"
                        " status VARCHAR(16) NOT NULL DEFAULT 'stopped',"
                        " edge_json JSONB,"
                        " risk_json JSONB,"
                        " health_json JSONB,"
                        " promotion_json JSONB,"
                        " meta_json JSONB,"
                        " created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
                        " updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                    ))
                    db.commit()
            _ensured = True
        except Exception as e:
            logger.warning("[LaneRegistry] ensure_table 失败: %s", e)


def _db():
    """返回 (system_identity 上下文管理器实例, SessionLocal)。

    注意：`system_identity` 是 ContextDecorator **实例**，用法是 `with system_identity:`
    （不加括号）—— 加括号会走 ContextDecorator.__call__(func) 而报 missing 'func'。
    """
    from backend.database.connection import SessionLocal
    from backend.core.tenant import system_identity

    return system_identity, SessionLocal


def _row_to_dict(r: Any) -> Dict[str, Any]:
    def _j(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, (dict, list)):
            return v
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return None

    edge = _j(r[3])
    return {
        "lane_id": r[0], "mode": r[1], "status": r[2],
        "edge": edge, "risk": _j(r[4]), "health": _j(r[5]),
        # promotion 以**当前 edge 重算**为准：缓存列只作历史留痕，
        # 否则 edge 被清空/更新后仍会显示旧判定（曾导致「假 edge 显示晋级就绪」）。
        "promotion": evaluate_promotion(edge), "promotion_cached": _j(r[6]),
        "meta": _j(r[7]),
        "updated_at": r[8].isoformat() if r[8] else None,
    }


_SELECT = ("SELECT lane_id, mode, status, edge_json, risk_json, health_json,"
           " promotion_json, meta_json, updated_at FROM lane_registry")


def list_lanes() -> List[Dict[str, Any]]:
    ensure_table()
    try:
        from sqlalchemy import text

        ident, sess = _db()
        with ident():
            with sess() as db:
                rows = db.execute(text(_SELECT + " ORDER BY lane_id")).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception as e:
        logger.warning("[LaneRegistry] list_lanes 失败: %s", e)
        return []


def get_lane(lane_id: str) -> Optional[Dict[str, Any]]:
    ensure_table()
    try:
        from sqlalchemy import text

        ident, sess = _db()
        with ident():
            with sess() as db:
                r = db.execute(text(_SELECT + " WHERE lane_id = :l"),
                               {"l": lane_id}).fetchone()
        return _row_to_dict(r) if r else None
    except Exception as e:
        logger.warning("[LaneRegistry] get_lane 失败: %s", e)
        return None


def register_lane(
    lane_id: str,
    *,
    mode: str = "paper",
    status: str = "stopped",
    risk: Optional[Dict[str, Any]] = None,
    meta: Optional[Dict[str, Any]] = None,
    edge: Optional[Dict[str, Any]] = None,
) -> bool:
    """插入或更新车道（幂等）。"""
    ensure_table()
    if mode not in ("paper", "live", "disabled"):
        raise ValueError(f"非法 mode: {mode}")
    if status not in ("active", "paused", "stopped"):
        raise ValueError(f"非法 status: {status}")
    try:
        from sqlalchemy import text

        ident, sess = _db()
        with ident():
            with sess() as db:
                db.execute(text(
                    "INSERT INTO lane_registry (lane_id, mode, status, risk_json,"
                    " meta_json, edge_json) VALUES (:l, :m, :s, :r, :meta, :e)"
                    " ON CONFLICT (lane_id) DO UPDATE SET"
                    " mode = :m, status = :s,"
                    " risk_json = COALESCE(:r, lane_registry.risk_json),"
                    " meta_json = COALESCE(:meta, lane_registry.meta_json),"
                    " edge_json = COALESCE(:e, lane_registry.edge_json),"
                    " updated_at = now()"
                ), {
                    "l": lane_id, "m": mode, "s": status,
                    "r": json.dumps(risk) if risk is not None else None,
                    "meta": json.dumps(meta, ensure_ascii=False) if meta is not None else None,
                    "e": json.dumps(edge) if edge is not None else None,
                })
                db.commit()
        return True
    except Exception as e:
        logger.warning("[LaneRegistry] register_lane(%s) 失败: %s", lane_id, e)
        return False


def seed_defaults() -> int:
    """把设计文档里的初始车道写入（幂等）。返回写入条数。"""
    n = 0
    for lane in DEFAULT_LANES:
        if register_lane(lane["lane_id"], mode=lane["mode"], status=lane["status"],
                         risk=lane.get("risk"), meta=lane.get("meta")):
            n += 1
    return n


def set_mode(lane_id: str, mode: str) -> bool:
    if mode not in ("paper", "live", "disabled"):
        raise ValueError(f"非法 mode: {mode}")
    lane = get_lane(lane_id)
    if not lane:
        return False
    return register_lane(lane_id, mode=mode, status=lane["status"],
                         risk=lane.get("risk"), meta=lane.get("meta"), edge=lane.get("edge"))


def set_status(lane_id: str, status: str) -> bool:
    if status not in ("active", "paused", "stopped"):
        raise ValueError(f"非法 status: {status}")
    lane = get_lane(lane_id)
    if not lane:
        return False
    return register_lane(lane_id, mode=lane["mode"], status=status,
                         risk=lane.get("risk"), meta=lane.get("meta"), edge=lane.get("edge"))


def stats_since(lane_id: Optional[str] = None) -> Optional[str]:
    """[2026-09-14 统计时代隔离] 车道统计时代起点（`meta.stats_since`，ISO 串）。

    配置/账户重构后，旧时代的账本行不应继续进入「今日/7 天/30 天」展示口径。
    指定 lane_id → 该车道的起点；否则 → 各车道起点中的**最新值**（中心口径）。
    历史行保留在库中供审计。
    """
    try:
        if lane_id:
            return ((get_lane(lane_id) or {}).get("meta") or {}).get("stats_since")
        vals = [str(s) for s in (
            ((ln.get("meta") or {}) or {}).get("stats_since")
            for ln in (list_lanes() or [])) if s]
        return max(vals) if vals else None
    except Exception as e:  # pragma: no cover - 注册表不可用时 fail-open
        logger.debug("[LaneRegistry] stats_since 读取失败: %s", e)
        return None


def update_meta(lane_id: str, meta: Dict[str, Any]) -> bool:
    """更新车道元数据（含报价/风控参数，供配置页 PATCH 持久化）。"""
    lane = get_lane(lane_id)
    if not lane:
        return False
    return register_lane(lane_id, mode=lane["mode"], status=lane["status"],
                         risk=lane.get("risk"), meta=meta, edge=lane.get("edge"))


def update_edge(lane_id: str, edge: Dict[str, Any]) -> bool:
    """写入扣费后的边际指标，并同步重算晋升判定。"""
    lane = get_lane(lane_id)
    if not lane:
        return False
    ok = register_lane(lane_id, mode=lane["mode"], status=lane["status"],
                       risk=lane.get("risk"), meta=lane.get("meta"), edge=edge)
    if ok:
        _write_promotion(lane_id, evaluate_promotion(edge))
    return ok


def update_health(lane_id: str, health: Dict[str, Any]) -> bool:
    ensure_table()
    try:
        from sqlalchemy import text

        ident, sess = _db()
        with ident():
            with sess() as db:
                db.execute(text(
                    "UPDATE lane_registry SET health_json = :h, updated_at = now()"
                    " WHERE lane_id = :l"
                ), {"h": json.dumps(health, ensure_ascii=False), "l": lane_id})
                db.commit()
        return True
    except Exception as e:
        logger.warning("[LaneRegistry] update_health 失败: %s", e)
        return False


# ── 熔断历史（设计文档 §3.5「熔断历史 N 次（近 30 天）」） ──
# 没有这张表时，前端只能显示「当前熔断数」，「历史次数」无从取得。

def ensure_breaker_log() -> None:
    try:
        from sqlalchemy import text

        ident, sess = _db()
        with ident():
            with sess() as db:
                db.execute(text(
                    "CREATE TABLE IF NOT EXISTS lane_breaker_log ("
                    " id BIGSERIAL PRIMARY KEY,"
                    " lane_id VARCHAR(64) NOT NULL,"
                    " breaker VARCHAR(32) NOT NULL,"
                    " state VARCHAR(16) NOT NULL,"
                    " reason TEXT,"
                    " ts TIMESTAMPTZ NOT NULL DEFAULT now())"
                ))
                db.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_lane_breaker_log_lane_ts"
                    " ON lane_breaker_log (lane_id, breaker, ts DESC)"
                ))
                db.commit()
    except Exception as e:
        logger.warning("[LaneRegistry] ensure_breaker_log 失败: %s", e)


def log_breaker(rows: list, *, dedup_sec: float = 3600.0) -> int:
    """把「当前处于 tripped」的熔断写进历史（同一 (lane,breaker) 一小时内去重）。

    幂等：反复调用同一状态不会重复计数。
    """
    ensure_breaker_log()
    written = 0
    try:
        from sqlalchemy import text

        ident, sess = _db()
        with ident():
            with sess() as db:
                for r in rows:
                    if r.get("state") != "tripped":
                        continue
                    dup = db.execute(text(
                        "SELECT 1 FROM lane_breaker_log WHERE lane_id=:l AND breaker=:b"
                        " AND ts >= now() - make_interval(secs => :s) LIMIT 1"
                    ), {"l": r["lane_id"], "b": r["breaker"], "s": float(dedup_sec)}).first()
                    if dup:
                        continue
                    db.execute(text(
                        "INSERT INTO lane_breaker_log (lane_id, breaker, state, reason)"
                        " VALUES (:l, :b, :st, :r)"
                    ), {"l": r["lane_id"], "b": r["breaker"], "st": "tripped",
                        "r": (r.get("reason") or "")[:500]})
                    written += 1
                db.commit()
    except Exception as e:
        logger.warning("[LaneRegistry] log_breaker 失败: %s", e)
    return written


def breaker_history(days: float = 30.0) -> list:
    """近 N 天熔断历史（按车道/类型聚合）。"""
    ensure_breaker_log()
    try:
        from sqlalchemy import text

        ident, sess = _db()
        with ident():
            with sess() as db:
                rows = db.execute(text(
                    "SELECT lane_id, breaker, COUNT(*) AS trips,"
                    " MAX(ts) AS last_ts FROM lane_breaker_log"
                    " WHERE ts >= now() - make_interval(secs => :s)"
                    " GROUP BY lane_id, breaker ORDER BY trips DESC"
                ), {"s": float(days) * 86400.0}).mappings().all()
        return [{"lane_id": r["lane_id"], "breaker": r["breaker"],
                 "trips": int(r["trips"]),
                 "last_ts": r["last_ts"].isoformat() if r["last_ts"] else None}
                for r in rows]
    except Exception as e:
        logger.warning("[LaneRegistry] breaker_history 失败: %s", e)
        return []


def evaluate_promotion(edge: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """按 PROMOTION_CRITERIA 判定；返回 {passed, failed, progress_pct, ready}。

    缺失字段一律视为**未通过**（fail-closed）。
    """
    e = edge or {}
    folds = e.get("folds") or []
    checks: Dict[str, bool] = {}

    # 来源校验：没有可信来源的 edge 一律不参与判定（防止人工/测试写入的假数据晋级）
    checks["edge_verified"] = str(e.get("source") or "") in EDGE_SOURCES

    n = int(e.get("n") or 0)
    checks["min_trades"] = n >= PROMOTION_CRITERIA["min_trades"]["threshold"]

    pos_folds = sum(1 for f in folds if float(f.get("net_bp") or 0) > 0)
    checks["folds_positive"] = pos_folds >= PROMOTION_CRITERIA["folds_positive"]["threshold"]

    checks["fold_t"] = bool(folds) and all(
        float(f.get("t") or 0) > PROMOTION_CRITERIA["fold_t"]["threshold"] for f in folds
    ) and len(folds) >= PROMOTION_CRITERIA["folds_positive"]["threshold"]

    dd = e.get("max_dd_pct")
    checks["max_drawdown_pct"] = dd is not None and float(dd) < PROMOTION_CRITERIA["max_drawdown_pct"]["threshold"]

    fr = e.get("fill_rate_ratio")
    checks["fill_rate_ratio"] = fr is not None and float(fr) >= PROMOTION_CRITERIA["fill_rate_ratio"]["threshold"]

    # 净期望本身也必须为正（否则前面全过也没意义）
    checks["net_positive"] = float(e.get("net_bp") or 0) > 0

    passed = [k for k, v in checks.items() if v]
    failed = [k for k, v in checks.items() if not v]
    total = len(checks)
    return {
        "passed": passed,
        "failed": failed,
        "labels": {k: PROMOTION_CRITERIA.get(k, {}).get("label", k) for k in checks},
        "progress_pct": round(100.0 * len(passed) / max(1, total), 1),
        "ready": len(failed) == 0,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def _write_promotion(lane_id: str, promotion: Dict[str, Any]) -> None:
    try:
        from sqlalchemy import text

        ident, sess = _db()
        with ident():
            with sess() as db:
                db.execute(text(
                    "UPDATE lane_registry SET promotion_json = :p, updated_at = now()"
                    " WHERE lane_id = :l"
                ), {"p": json.dumps(promotion, ensure_ascii=False), "l": lane_id})
                db.commit()
    except Exception as e:
        logger.warning("[LaneRegistry] _write_promotion 失败: %s", e)
