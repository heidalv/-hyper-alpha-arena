# -*- coding: utf-8 -*-
"""[F284 2026-09-16] 观测口径：外部 worker 心跳的读取与合并。

背景（F283）：车道 tick 已从 API 后端移到独立进程 `scripts/mm_lane_worker.py`
（后端被外部启动器每 ~5 分钟重启一次，进程内 tick 无法保证连续性）。
但 `/api/trading/lanes/{id}/shadow` 读的是**本进程内**那个 runner 实例的计数器
⇒ 外部 ticker 模式下它会一直显示 `ticks=0`（**观测失真**：车道其实在跑、
账本在写、报价在挂）。本模块把 worker 写出的心跳
（`logs/mm_lane_status.json`，字段与 `/shadow` 同名）合并进状态返回，
并显式标注 `ticker="external-worker" | "inprocess"` 与心跳新鲜度。

合并规则（宁可显示"进程内 + 心跳陈旧"，也不要伪造计数）：
  · 心跳文件不存在/不可解析 ⇒ 原样返回，`ticker="inprocess"`；
  · 心跳**不新鲜**（> `FRESH_MAX_AGE_SEC`）⇒ 不用它的计数，只附一行诊断；
  · 心跳新鲜 且（`MM_LANE_TICKER=external` 或 进程内 ticks==0）⇒ 用 worker 计数。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

REPO = Path(__file__).resolve().parents[3]
STATUS_PATH = REPO / "logs" / "mm_lane_status.json"
FRESH_MAX_AGE_SEC = 90.0
# 与 /shadow 同名、可由 worker 心跳覆盖的计数键
# [F285 2026-09-16] 加 `states`：持仓/挂单价也只认**真正在 tick 的那个进程**，
# 否则看板会显示 API 进程启动时读入、之后不再更新的陈旧挂单/持仓 ✗。
_WORKER_KEYS = ("ticks", "fills", "flattens", "last_tick_ts", "quoted_decisions",
                "skip_counts", "side_counts", "gap_repair", "fill_notional", "states",
                # [R201 2026-09-29] 闸门探针（不被 `dec.skip` 遮蔽、也不被前 12 截断）
                # ⇒ 新增闸门"到底有没有机会执行"变成读数，而不是靠推理 ✗✓
                "gate_probe_counts",
                # [F288 2026-09-16] 报价行为读数（digest/看板此前恒为 None）
                "avg_width_bp", "avg_sigma", "avg_sigma_all", "fills_per_hour",
                "quote_modes", "frozen_share", "avg_base_bp", "sigma_decisions",
                # [F339 2026-09-22] 车道级闸门的可见性：此前 `lane_pause` 只存在于
                # `plan_tick` 的返回值里，**从不进状态文件** ⇒ 车道闸（日亏 /
                # toxic_streak / 车道级 σ）触发时完全不可见。实测后果：
                # `daily_loss_stop_pct=80%` 在 12 天里 0 次触发这件事一直没被发现。
                "lane_pause_counts", "lane_pause_last",
                "day_pnl_usd", "day_pnl_limit_usd",
                # [h663 审计#8 修复] 三道白名单对齐(F339b 契约):方向卡/Q 速控/
                # h624 KPI/成交源——runner.status 有、mm_lane_worker 有,这里不能漏,
                # 否则 /shadow 改走 merge_worker_status 时静默消失。
                "direction_card", "q_speed", "h624_kpi", "seg_source", "dir_score",
                # [h702 2026-10-02] **补三处遗漏的白名单键**:API/前端此前看不到
                # ①`universe_radar`(自驱雷达状态⇒"上次评估"只能退化成上次换币时间)
                # ②`events`(临时阻止/突发警报流)③`venue_filters`(429/过滤器拒单)
                # ④`live_bridge`(实盘账户/挂单/持仓快照)。四者都在 runner.status()
                # 与 mm_lane_worker 白名单里,唯独漏在这一层 ⇒ 静默消失。
                "universe_radar", "events", "venue_filters", "live_bridge")
_EXTERNAL_VALUES = ("external", "worker", "out-of-process")


def ticker_is_external() -> bool:
    """后端侧开关：`MM_LANE_TICKER=external`（与 `register_shadow_task` 同判据 ✓）。"""
    return (os.getenv("MM_LANE_TICKER", "inprocess") or "inprocess").strip().lower() in _EXTERNAL_VALUES


def read_worker_status(path: Optional[str] = None,
                       now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """读 worker 心跳；返回带 `snapshot_age_sec` / `fresh` 的字典，读不到返回 None。"""
    p = Path(path) if path else STATUS_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    ts = float(raw.get("ts") or 0.0)
    age = ((now if now is not None else time.time()) - ts) if ts > 0 else -1.0
    raw["snapshot_age_sec"] = round(age, 1)
    raw["fresh"] = bool(0 <= age <= FRESH_MAX_AGE_SEC)
    return raw


def merge_worker_status(status: Optional[Dict[str, Any]],
                        snap: Optional[Dict[str, Any]],
                        prefer_external: Optional[bool] = None) -> Dict[str, Any]:
    """把 worker 心跳合并进 `/shadow` 状态（见模块 docstring 的合并规则）。"""
    out: Dict[str, Any] = dict(status or {})
    inproc_ticks = int((status or {}).get("ticks") or 0)
    if prefer_external is None:
        prefer_external = ticker_is_external()
    use_worker = bool(snap and snap.get("fresh") and (prefer_external or inproc_ticks == 0))
    if not use_worker:
        out["ticker"] = "inprocess"
        if snap is not None:
            out["worker_snapshot"] = {
                "fresh": bool(snap.get("fresh")),
                "age_sec": snap.get("snapshot_age_sec"),
                "note": "心跳不新鲜或进程内正在 tick ⇒ 计数仍取进程内",
            }
        return out
    for k in _WORKER_KEYS:
        if snap.get(k) is not None:
            out[k] = snap[k]
    out["ticker"] = "external-worker"
    out["worker_snapshot"] = {
        "fresh": True,
        "age_sec": snap.get("snapshot_age_sec"),
        "ts": snap.get("ts"),
        "ok": snap.get("ok"),
        "reason": snap.get("reason") or "",
    }
    return out
