# -*- coding: utf-8 -*-
"""事件采集任务统一注册（v3 p0-event-data）。

两处挂载：
  - 主进程  backend/services/ops/v3_jobs_ext.register_extended_jobs → register_event_jobs(..., host="main")
  - 数据中心 backend/workers/market_data_center._run_collectors      → register_event_jobs(..., host="dc")

谁真正执行由 EVENT_COLLECTORS_HOST 决定（dc | main | both，默认 dc：与 K 线/资金费/鲸鱼采集同进程，
不随主 API 重启而断流）。未执行的一方仍登记任务元数据 + runner，使 /api/ops/jobs 可见并可手动触发。

任务表：
  exchange_announcements   10 分钟   交易所公告（Binance/OKX/Bybit）
  liquidation_stream       常驻      多所清算 WebSocket（Binance/Aster forceOrder + OKX + Bybit，心跳 60s）
  liquidation_rollup       1 小时    逐笔 → 小时聚合 + 30 天清理
  position_structure       1 小时    OI/多空比/大户/主动买卖比（Top-60 ∪ 核心币）
  funding_backfill         24 小时   全币池结算费率回填（首轮启动即跑，可断点续跑）
  funding_universe_scan    10 分钟   极端资金费 → market_events
  market_events_bridge     5 分钟    news(强度≥4)/whale(≥$50M)/macro(重要≥4) → market_events
  smart_money_track        5 分钟    OKX带单员+HL鲸鱼持仓监控 → smart_money_*，大动作 → market_events
  smart_money_scorecard    1 小时    大佬成绩单结算（动作 vs 4h/24h 价格）
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_REGISTERED_ONCE = threading.Event()

JOB_SPECS: List[Dict[str, Any]] = [
    {"name": "exchange_announcements", "interval": 600, "owner": "data",
     "desc": "交易所公告：Binance CMS(上新/下架) / OKX / Bybit → exchange_announcements + market_events"},
    {"name": "liquidation_rollup", "interval": 3600, "owner": "data",
     "desc": "多所逐笔清算 → liquidation_events 小时聚合（按 exchange 分行，source=ws）+ 30 天清理"},
    {"name": "position_structure", "interval": 3600, "owner": "data",
     "desc": "Binance /futures/data 持仓结构（OI、多空比、大户持仓比、主动买卖比）Top-60 ∪ 核心币 → position_structure"},
    {"name": "funding_backfill", "interval": 86400, "owner": "data",
     "desc": "全币池结算资金费一年回填（/fapi/v1/fundingRate，断点续跑，每日增量）→ perp_funding"},
    {"name": "funding_universe_scan", "interval": 600, "owner": "data",
     "desc": "全场所最新资金费折 8h 口径，|rate| ≥ FUNDING_EXTREME_8H → market_events funding.extreme"},
    {"name": "market_events_bridge", "interval": 300, "owner": "data",
     "desc": "news_events(强度≥NEWS_HIGH_IMPACT_MIN，默认4) / whale_activities(≥$50M) / macro_events(重要≥4) → market_events"},
    {"name": "smart_money_track", "interval": 300, "owner": "data",
     "desc": "聪明钱监控：OKX带单员Top-N + Hyperliquid注册鲸鱼持仓 → smart_money_snapshots/moves，大动作 → market_events(smart_money.move)"},
    {"name": "smart_money_scorecard", "interval": 3600, "owner": "data",
     "desc": "大佬成绩单：动作 vs 4h/24h 后价格结算命中率/均收益 → smart_money_scorecard"},
]


def host_mode() -> str:
    m = (os.getenv("EVENT_COLLECTORS_HOST", "dc") or "dc").strip().lower()
    return m if m in ("dc", "main", "both") else "dc"


def should_run_here(host: str) -> bool:
    mode = host_mode()
    return mode == "both" or mode == host


def _runners() -> Dict[str, Callable[..., Any]]:
    from backend.services.events import exchange_announcements, liquidation_stream, position_structure, funding_universe, bridge, smart_money
    return {
        "exchange_announcements": exchange_announcements.collect_once,
        "liquidation_rollup": liquidation_stream.rollup_hourly,
        "position_structure": position_structure.collect_once,
        "funding_backfill": lambda: funding_universe.backfill_settled_funding(days=365, max_seconds=1500.0),
        "funding_universe_scan": funding_universe.scan_extremes,
        "market_events_bridge": bridge.bridge_once,
        "smart_money_track": smart_money.collect_once,
        "smart_money_scorecard": smart_money.scorecard_update,
    }


def _default_wrap(job_name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    """DC 进程用的包装（与 ops.v3_jobs._wrap 同语义：job_run 登记心跳/失败；disabled 跳过）。"""

    def _runner(*args, **kwargs):
        try:
            from backend.services.ops.job_registry import job_run
        except Exception:
            job_run = None
        if job_run is None:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                logger.exception("[events.collectors] %s 失败: %s", job_name, exc)
                return None
        with job_run(job_name) as rec:
            if getattr(rec, "skipped", False):
                return None
            out = fn(*args, **kwargs)
            rec.set_result(out)
            return out

    _runner.__name__ = f"evt_{job_name}"
    return _runner


def _default_job(name: str, cadence: str, description: str, owner: str = "data",
                 runner: Optional[Callable[..., Any]] = None, expected_interval_sec: Optional[int] = None) -> None:
    try:
        from backend.services.ops.job_registry import register_job
        register_job(name, cadence, description, owner=owner, expected_interval_sec=expected_interval_sec, runner=runner)
    except Exception as exc:
        logger.debug("[events.collectors] register_job %s 失败: %s", name, exc)


def register_event_jobs(task_scheduler, wrap: Optional[Callable[..., Any]] = None,
                        job: Optional[Callable[..., Any]] = None, *, host: str = "main") -> List[str]:
    """登记（并按 host 决定是否调度）全部事件采集任务。返回**本进程实际调度**的任务名。"""
    wrap = wrap or _default_wrap
    job = job or _default_job
    run_here = should_run_here(host)
    runners = _runners()
    scheduled: List[str] = []

    # 元数据 + 手动 runner（两边都登记；DB upsert 幂等）
    for spec in JOB_SPECS:
        try:
            job(spec["name"], f"interval {spec['interval']}s", spec["desc"], owner=spec["owner"],
                runner=runners[spec["name"]], expected_interval_sec=int(spec["interval"]))
        except Exception as exc:
            logger.debug("[events.collectors] 登记 %s 失败: %s", spec["name"], exc)
    try:
        from backend.services.events.liquidation_stream import stream_status
        job("liquidation_stream", "stream", "多所全市场逐笔清算 WebSocket（Binance/Aster forceOrder + OKX liquidation-orders + Bybit allLiquidation）→ liquidation_ticks + market_events（心跳 60s）",
            owner="data", runner=stream_status, expected_interval_sec=180)
    except Exception as exc:
        logger.debug("[events.collectors] 登记 liquidation_stream 失败: %s", exc)

    if not run_here:
        logger.info("[events.collectors] EVENT_COLLECTORS_HOST=%s，本进程(%s)只登记元数据不调度", host_mode(), host)
        return scheduled

    # 调度（replace_existing 由 task_scheduler.add_interval_task 内部保证）
    for spec in JOB_SPECS:
        name = spec["name"]
        try:
            task_scheduler.add_interval_task(
                task_func=wrap(name, runners[name]), interval_seconds=int(spec["interval"]),
                task_id=f"v3_evt_{name}", max_instances=1,
            )
            scheduled.append(name)
        except Exception as exc:
            logger.warning("[events.collectors] %s 调度失败: %s", name, exc)

    # 常驻 WebSocket
    try:
        from backend.services.events.liquidation_stream import start_liquidation_stream
        if start_liquidation_stream():
            scheduled.append("liquidation_stream")
    except Exception as exc:
        logger.warning("[events.collectors] liquidation_stream 启动失败: %s", exc)

    # 启动即跑一轮：公告 / 桥接 / 极端费率（轻量）+ 回填后台线程（重、带时间预算）
    if not _REGISTERED_ONCE.is_set():
        _REGISTERED_ONCE.set()

        def _kickoff() -> None:
            import time as _t
            _t.sleep(20)  # 等 DB/网络就绪
            for name in ("exchange_announcements", "market_events_bridge", "funding_universe_scan", "position_structure"):
                try:
                    wrap(name, runners[name])()
                except Exception as exc:
                    logger.warning("[events.collectors] 首轮 %s 失败: %s", name, exc)
            try:
                wrap("funding_backfill", runners["funding_backfill"])()
            except Exception as exc:
                logger.warning("[events.collectors] 首轮 funding_backfill 失败: %s", exc)

        threading.Thread(target=_kickoff, name="event-collectors-kickoff", daemon=True).start()
        _start_status_publisher(host)

    logger.info("[events.collectors] 本进程(%s)已调度 %d 个事件采集任务: %s", host, len(scheduled), ", ".join(scheduled))
    return scheduled


# ─────────────────────────────────────────────────────────────────────────────
# 跨进程状态：执行进程每 30s 把本地状态写到 JSON，主 API 进程读取给 /api/ops 用
# ─────────────────────────────────────────────────────────────────────────────
STATUS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "data", "events_collectors_status.json")
_PUBLISHER_STARTED = threading.Event()
_RUNNING_HOST: Optional[str] = None


def _local_status() -> Dict[str, Any]:
    from backend.services.events import exchange_announcements, liquidation_stream, position_structure, funding_universe, bridge, smart_money
    from backend.services.events._http import failure_report
    return {
        "host_mode": host_mode(),
        "announcements": exchange_announcements.last_summary(),
        "liquidation_stream": liquidation_stream.stream_status(),
        "position_structure": position_structure.last_summary(),
        "funding": funding_universe.last_summary(),
        "funding_backfill_state": funding_universe.backfill_state(),
        "bridge": bridge.last_summary(),
        "smart_money": smart_money.last_summary(),
        "http_failures": failure_report(),
    }


def _write_status_file(host: str) -> None:
    import json
    import time as _t
    data = _local_status()
    data.update({"published_by": host, "pid": os.getpid(), "written_at": _t.time()})
    tmp = STATUS_FILE + ".tmp"
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, default=str)
    os.replace(tmp, STATUS_FILE)


def _start_status_publisher(host: str) -> None:
    global _RUNNING_HOST
    _RUNNING_HOST = host
    if _PUBLISHER_STARTED.is_set():
        return
    _PUBLISHER_STARTED.set()

    def _loop() -> None:
        import time as _t
        while True:
            try:
                _write_status_file(host)
            except Exception as exc:
                logger.debug("[events.collectors] 状态文件写入失败: %s", exc)
            _t.sleep(30)

    threading.Thread(target=_loop, name="event-collectors-status", daemon=True).start()


def collectors_status(local_only: bool = False) -> Dict[str, Any]:
    """各采集器最近一轮摘要 + 流状态（/api/ops/market-events/status）。

    采集在别的进程执行时（默认 dc），读取该进程发布的状态文件（≤ 5 分钟视为新鲜），
    否则返回本进程状态。返回值含 `status_source`（local | file）与 `status_age_sec`。"""
    import json
    import time as _t
    local = _local_status()
    if local_only or _RUNNING_HOST is not None:
        local.update({"status_source": "local", "status_age_sec": 0.0})
        return local
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r", encoding="utf-8") as fh:
                remote = json.load(fh)
            age = _t.time() - float(remote.get("written_at") or 0)
            if age <= 300:
                remote.update({"status_source": "file", "status_age_sec": round(age, 1)})
                return remote
            local["status_note"] = f"采集进程状态文件已过期 {age:.0f}s（{remote.get('published_by')} pid={remote.get('pid')}）"
        else:
            local["status_note"] = "采集进程尚未发布状态文件（EVENT_COLLECTORS_HOST=%s）" % host_mode()
    except Exception as exc:
        local["status_note"] = f"读取采集进程状态失败: {exc}"
    local.update({"status_source": "local", "status_age_sec": 0.0})
    return local
