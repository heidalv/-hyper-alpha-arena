"""
事件溯源 Phase 2 — 双写 + 投影读路径 + C7 持续对拍。

在 Phase 1 shadow 记录基础上：
  - 写路径：record_and_apply 同步更新内存投影（原 DB 写逻辑不变）
  - 读路径（可选）：EVENT_SOURCING_PHASE2_READ=true 时 get_positions 优先走投影
  - 对拍（可选）：EVENT_SOURCING_PHASE2_RECONCILE=true 时写后比对 DB vs 投影

零风险：对拍不一致或投影缺失时 **fail-open 回退 DB**，不阻断交易。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from backend.services.event_sourcing.event_store import (
    DomainEvent,
    EventSourcedPositionRepository,
    EVT_POSITION_CHANGED,
    EVT_POSITION_CLOSED,
    EVT_POSITION_OPENED,
    get_event_store,
    is_enabled,
)

logger = logging.getLogger(__name__)

_live_repo: Optional[EventSourcedPositionRepository] = None
_repo_lock = __import__("threading").Lock()

# 对拍统计（供 /api/health 可观测）
_reconcile_stats: Dict[str, int] = {
    "checks": 0,
    "mismatches": 0,
    "last_ok": 1,
}

# [2026-08-11] 自愈节流：同一 (account, pid) 60s 内最多补写一次事件，防止反复刷事件日志。
_heal_ts: Dict[str, float] = {}


def is_phase2_read_enabled() -> bool:
    if not is_enabled():
        return False
    return os.environ.get("EVENT_SOURCING_PHASE2_READ", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


def is_phase2_reconcile_enabled() -> bool:
    if not is_enabled():
        return False
    return os.environ.get("EVENT_SOURCING_PHASE2_RECONCILE", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


def get_reconcile_stats() -> Dict[str, int]:
    return dict(_reconcile_stats)


def get_live_repository() -> EventSourcedPositionRepository:
    """进程级单例：写路径 record_and_apply，读路径读 projection。"""
    global _live_repo
    if _live_repo is not None:
        return _live_repo
    with _repo_lock:
        if _live_repo is None:
            _live_repo = EventSourcedPositionRepository(get_event_store())
            if is_enabled():
                try:
                    _live_repo.rebuild_from_events()
                    logger.info(
                        "[EventSourcing#9 Phase2] 投影已从事件日志预热: open=%d",
                        len(_live_repo.projection.open_positions()),
                    )
                except Exception as exc:
                    logger.debug("[EventSourcing#9 Phase2] 投影预热跳过: %s", exc)
        return _live_repo


def reset_live_repository_for_tests() -> None:
    """测试隔离用。"""
    global _live_repo
    with _repo_lock:
        _live_repo = None


def record_position_event(event_type: str, aggregate_id: str, payload: dict) -> bool:
    """双写：追加事件 + 同步内存投影。"""
    if not is_enabled() or not aggregate_id:
        return False
    try:
        repo = get_live_repository()
        return repo.record_and_apply(
            DomainEvent(event_type=event_type, aggregate_id=str(aggregate_id), payload=payload or {}),
        )
    except Exception as exc:
        logger.debug("[EventSourcing#9 Phase2] record_and_apply 失败（忽略）: %s", exc)
        return False


def _normalize_side(side: str) -> str:
    s = (side or "").lower()
    if s in ("buy", "long"):
        return "long"
    if s in ("sell", "short"):
        return "short"
    return s


@dataclass
class ReconcileResult:
    ok: bool
    db_open_ids: Set[str] = field(default_factory=set)
    proj_open_ids: Set[str] = field(default_factory=set)
    missing_in_proj: Set[str] = field(default_factory=set)
    extra_in_proj: Set[str] = field(default_factory=set)
    field_mismatches: List[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.ok:
            return "ok"
        parts = []
        if self.missing_in_proj:
            parts.append(f"missing_in_proj={sorted(self.missing_in_proj)}")
        if self.extra_in_proj:
            parts.append(f"extra_in_proj={sorted(self.extra_in_proj)}")
        if self.field_mismatches:
            parts.append(f"fields={self.field_mismatches[:5]}")
        return "; ".join(parts) or "mismatch"


def reconcile_db_vs_projection(
    db_positions: List[dict],
    *,
    account_id: int,
    status: str = "open",
) -> ReconcileResult:
    """C7 持续对拍：DB 仓位列表 vs 内存投影（仅 open）。"""
    global _reconcile_stats
    _reconcile_stats["checks"] = _reconcile_stats.get("checks", 0) + 1

    if status != "open":
        return ReconcileResult(ok=True)

    # [2026-08-11 修复] db_positions 为空可能是 RLS 隐藏（非管理员上下文），
    # 不是“仓位真的没了”。此时不能把投影里的 open 仓位当 extra 关掉，
    # 否则会误平真实开仓并和下一轮对拍形成 关→开 震荡。
    if not db_positions:
        return ReconcileResult(ok=True)

    db_open = {
        str(p.get("id") or p.get("position_id") or ""):
        p for p in (db_positions or []) if p.get("status", "open") == "open"
    }
    db_open.pop("", None)
    db_ids = set(db_open.keys())

    try:
        repo = get_live_repository()
        proj_all = repo.projection.current_state
        proj_open = {
            pid: pos for pid, pos in proj_all.items()
            if pos.get("status") == "open"
            and int(pos.get("account_id") or 0) in (0, int(account_id))
        }
        proj_ids = set(proj_open.keys())
    except Exception as exc:
        logger.debug("[EventSourcing#9 Phase2] 对拍读投影失败: %s", exc)
        return ReconcileResult(ok=True)

    missing = db_ids - proj_ids
    extra = proj_ids - db_ids
    field_mm: List[str] = []

    for pid in db_ids & proj_ids:
        dbp = db_open[pid]
        prj = proj_open[pid]
        for key in ("symbol", "side", "status"):
            dv = str(dbp.get(key) or "").upper() if key == "symbol" else str(dbp.get(key) or "").lower()
            pv = str(prj.get(key) or "").upper() if key == "symbol" else str(prj.get(key) or "").lower()
            if key == "side":
                dv = _normalize_side(dv)
                pv = _normalize_side(pv)
            if dv != pv:
                field_mm.append(f"{pid}.{key}:{pv}!={dv}")
        try:
            ds = float(dbp.get("size") or 0)
            ps = float(prj.get("size") or 0)
            if abs(ds - ps) > max(1e-8, ds * 1e-6):
                field_mm.append(f"{pid}.size:{ps}!={ds}")
        except (TypeError, ValueError):
            pass

    ok = not missing and not extra and not field_mm
    if not ok:
        _reconcile_stats["mismatches"] = _reconcile_stats.get("mismatches", 0) + 1
        _reconcile_stats["last_ok"] = 0
        logger.warning(
            "[EventSourcing#9 Phase2] C7 对拍不一致 account=%s %s",
            account_id, ReconcileResult(
                ok=False, db_open_ids=db_ids, proj_open_ids=proj_ids,
                missing_in_proj=missing, extra_in_proj=extra, field_mismatches=field_mm,
            ).summary,
        )
        # [2026-08-11 修复] C7 自愈：DB 有而投影缺的 open 仓位补写 PositionOpened；
        # 投影 open 但 DB 已不存在的补写 PositionClosed；size 差异补 PositionChanged。
        # 收敛后下轮对拍即 ok，读路径才允许走投影。
        healed = 0
        now = time.time()
        for pid in missing:
            key = f"open:{account_id}:{pid}"
            if now - _heal_ts.get(key, 0.0) < 60:
                continue
            dbp = db_open[pid]
            record_position_event(EVT_POSITION_OPENED, pid, {
                "account_id": account_id,
                "symbol": dbp.get("symbol"),
                "side": dbp.get("side"),
                "size": float(dbp.get("size") or 0),
                "entry_price": float(dbp.get("entry_price") or 0),
                "trade_nature": dbp.get("trade_nature"),
                "strategy_id": dbp.get("strategy_id"),
                "leverage": float(dbp.get("leverage") or 1),
                "_source": "reconcile_sync",
            })
            _heal_ts[key] = now
            healed += 1
        for pid in extra:
            key = f"close:{account_id}:{pid}"
            if now - _heal_ts.get(key, 0.0) < 60:
                continue
            prj = proj_open[pid]
            record_position_event(EVT_POSITION_CLOSED, pid, {
                "exit_price": float(prj.get("entry_price") or 0),
                "realized_pnl": 0.0,
                "_source": "reconcile_sync",
            })
            _heal_ts[key] = now
            healed += 1
        for pid in db_ids & proj_ids:
            if not any(pid in fm for fm in field_mm):
                continue
            key = f"change:{account_id}:{pid}"
            if now - _heal_ts.get(key, 0.0) < 60:
                continue
            dbp = db_open[pid]
            record_position_event(EVT_POSITION_CHANGED, pid, {
                "size": float(dbp.get("size") or 0),
                "_source": "reconcile_sync",
            })
            _heal_ts[key] = now
            healed += 1
        if healed:
            logger.info(
                "[EventSourcing#9 Phase2] C7 自愈写入 %d 条事件 account=%s "
                "(missing=%d extra=%d field=%d)",
                healed, account_id, len(missing), len(extra), len(field_mm),
            )
    else:
        _reconcile_stats["last_ok"] = 1

    return ReconcileResult(
        ok=ok,
        db_open_ids=db_ids,
        proj_open_ids=proj_ids,
        missing_in_proj=missing,
        extra_in_proj=extra,
        field_mismatches=field_mm,
    )


def projection_positions_for_account(
    account_id: int,
    *,
    status: str = "open",
) -> List[dict]:
    """从内存投影构建 get_positions 兼容的 dict 列表（无 mark_price 刷新）。"""
    repo = get_live_repository()
    out: List[dict] = []
    for pid, pos in repo.projection.current_state.items():
        if status and pos.get("status") != status:
            continue
        if int(pos.get("account_id") or 0) not in (0, int(account_id)):
            continue
        out.append({
            "id": int(pid) if str(pid).isdigit() else pid,
            "position_id": pid,
            "account_id": account_id,
            "symbol": pos.get("symbol"),
            "side": _normalize_side(str(pos.get("side") or "")),
            "size": float(pos.get("size") or 0),
            "entry_price": float(pos.get("entry_price") or 0),
            "status": pos.get("status", "open"),
            "trade_nature": pos.get("trade_nature") or "swing",
            "strategy_id": pos.get("strategy_id"),
            "leverage": float(pos.get("leverage") or 1),
            "unrealized_pnl": float(pos.get("unrealized_pnl") or 0),
            "mark_price": float(pos.get("mark_price") or pos.get("entry_price") or 0),
            # [2026-07-12 修复] 之前漏了 margin 字段，前端 PaperPosition 接口把它当必填数字
            # 直接 .toLocaleString()，projection 行没有这个 key 时前端整页崩溃(白屏/组件渲染出错)。
            # 这里先给个兜底 0，真实值会在 merge_projection_with_db_prices 里用 DB 行覆盖。
            "margin": float(pos.get("margin") or 0),
            "_source": "event_projection",
        })
    return out


def merge_projection_with_db_prices(
    projection_rows: List[dict],
    db_rows: List[dict],
) -> List[dict]:
    """投影结构 + DB 字段合并。

    [2026-07-12 修复] 之前只用白名单拷贝少数字段(mark_price/unrealized_pnl等)，
    projection_positions_for_account() 本身没生成的字段(pnl_pct/liquidation_price/
    tp_price/sl_price/close_reason/opened_at等)合并后仍然是 undefined——前端把这些
    当必填数字直接调用 .toLocaleString()/.toFixed()，导致模拟交易整页白屏
    ("组件渲染出错: Cannot read properties of undefined")。
    改为通用补齐：dbp 里任何 projection 行没有的 key 全部拷过来填补空缺；
    同时对一小组"要求实时性"的动态字段强制用 dbp 覆盖(即使 projection 行已有旧值)。
    """
    db_by_id = {str(p.get("id") or p.get("position_id")): p for p in db_rows}
    _live_priority_keys = (
        "mark_price", "unrealized_pnl", "margin",
        "net_group_side", "net_group_size", "net_group_signed_size",
        "net_group_margin", "net_group_leverage", "net_group_liq_price",
    )
    merged: List[dict] = []
    for row in projection_rows:
        pid = str(row.get("id") or row.get("position_id"))
        dbp = db_by_id.get(pid)
        if dbp:
            row = dict(row)
            for k, v in dbp.items():
                if k not in row or k in _live_priority_keys:
                    row[k] = v
        merged.append(row)
    return merged
