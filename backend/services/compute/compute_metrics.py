"""
算力历史指标服务（v6 第十章 前端仪表台配套）。

- `compute_metrics` 表（PG analytics 库，原生 DDL 幂等建表）：
  kind ∈ {resource, task, speedup}，key 为指标名，value 为数值，extra 为 JSON 附加
- 后台采样线程（60s）：CPU/内存/GPU 温度等资源指标持续落库 → 前端趋势图数据源
- `record_task_event()`：任务耗时/成功率/加速比事件记录（由路由/任务回调调用）
- `query(window)`：按 1h/24h/7d/30d 窗口查询，供图表消费
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ENGINE = None
_DDL_DONE = False
_ddl_lock = threading.Lock()
_started = False
_start_lock = threading.Lock()

_WINDOWS = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from backend.database.connection import analytics_engine
        _ENGINE = analytics_engine
    return _ENGINE


def _ensure_table() -> None:
    """幂等建表（原生 DDL；PG 专用 BIGSERIAL/JSONB，SQLite 兜底走 TEXT）。"""
    global _DDL_DONE
    with _ddl_lock:
        if _DDL_DONE:
            return
        try:
            eng = _engine()
            url = str(eng.url).lower()
            if url.startswith("sqlite"):
                ddl = """
                CREATE TABLE IF NOT EXISTS compute_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts BIGINT NOT NULL,
                    kind VARCHAR(32) NOT NULL,
                    key VARCHAR(64) NOT NULL,
                    value DOUBLE PRECISION NOT NULL,
                    extra TEXT
                )
                """
            else:
                ddl = """
                CREATE TABLE IF NOT EXISTS compute_metrics (
                    id BIGSERIAL PRIMARY KEY,
                    ts BIGINT NOT NULL,
                    kind VARCHAR(32) NOT NULL,
                    key VARCHAR(64) NOT NULL,
                    value DOUBLE PRECISION NOT NULL,
                    extra JSONB
                )
                """
            with eng.begin() as conn:
                conn.execute(__import__("sqlalchemy").text(ddl))
                conn.execute(__import__("sqlalchemy").text(
                    "CREATE INDEX IF NOT EXISTS idx_compute_metrics_ts ON compute_metrics(ts)"
                ))
                conn.execute(__import__("sqlalchemy").text(
                    "CREATE INDEX IF NOT EXISTS idx_compute_metrics_key_ts ON compute_metrics(key, ts)"
                ))
            _DDL_DONE = True
        except Exception as e:  # noqa: BLE001
            logger.warning("[ComputeMetrics] 建表失败（可重试）: %s", e)


def _insert(ts: int, kind: str, key: str, value: float, extra: Optional[Dict[str, Any]] = None) -> None:
    _ensure_table()
    try:
        eng = _engine()
        url = str(eng.url).lower()
        if url.startswith("sqlite"):
            stmt = "INSERT INTO compute_metrics (ts, kind, key, value, extra) VALUES (:ts, :kind, :key, :value, :extra)"
            params = {"ts": ts, "kind": kind, "key": key, "value": value,
                      "extra": json.dumps(extra, ensure_ascii=False) if extra else None}
        else:
            stmt = "INSERT INTO compute_metrics (ts, kind, key, value, extra) VALUES (:ts, :kind, :key, :value, :extra)"
            params = {"ts": ts, "kind": kind, "key": key, "value": value,
                      "extra": json.dumps(extra, ensure_ascii=False) if extra else None}
        with eng.begin() as conn:
            conn.execute(__import__("sqlalchemy").text(stmt), params)
    except Exception as e:  # noqa: BLE001
        logger.debug("[ComputeMetrics] 写入失败 %s/%s: %s", kind, key, e)


def record_task_event(kind: str, key: str, value: float, extra: Optional[Dict[str, Any]] = None) -> None:
    """记录任务事件指标（kind ∈ task/speedup），失败静默不影响主流程。"""
    try:
        _insert(int(time.time()), kind, key, float(value), extra)
    except Exception:  # noqa: BLE001
        pass


def _sample_once() -> None:
    """单轮资源采样：CPU/内存/GPU 温度与显存 → resource 行。"""
    try:
        from backend.services.compute.hardware_monitor import snapshot as hw_snapshot
        snap = hw_snapshot()
        ts = int(time.time())
        cpu = snap.get("cpu", {})
        mem = snap.get("memory", {})
        gpu = snap.get("gpu", {})
        _insert(ts, "resource", "cpu_usage_pct", float(cpu.get("usage_pct", 0) or 0))
        _insert(ts, "resource", "mem_usage_pct", float(mem.get("usage_pct", 0) or 0))
        if gpu.get("available"):
            _insert(ts, "resource", "gpu_temp_c", float(gpu.get("temp_c", 0) or 0))
            _insert(ts, "resource", "gpu_util_pct", float(gpu.get("utilization_pct") or 0))
            _insert(ts, "resource", "gpu_mem_free_mb", float(gpu.get("mem_free_mb", 0) or 0))
    except Exception as e:  # noqa: BLE001
        logger.debug("[ComputeMetrics] 资源采样失败: %s", e)


def _sampler_loop(stop: threading.Event) -> None:
    _sample_once()
    while not stop.wait(60):
        _sample_once()


def start_sampler() -> None:
    """启动 60s 资源采样线程（幂等）。"""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
        stop = threading.Event()
        t = threading.Thread(target=_sampler_loop, args=(stop,), daemon=True,
                             name="compute-metrics-sampler")
        t.start()
        logger.info("[ComputeMetrics] 资源采样线程已启动（60s 周期）")


def query(window: str = "24h", kinds: Optional[List[str]] = None,
          keys: Optional[List[str]] = None, limit: int = 2000) -> Dict[str, Any]:
    """按时间窗口查询历史指标。

    返回按 key 分组的降采样序列 [{ts, value}]（超出 limit 时按步长抽稀）。
    """
    _ensure_table()
    win_sec = _WINDOWS.get(window, _WINDOWS["24h"])
    cutoff = int(time.time()) - win_sec
    try:
        from sqlalchemy import bindparam
        from sqlalchemy import text as _sa_text
        sql = ("SELECT ts, kind, key, value, extra FROM compute_metrics "
               "WHERE ts >= :cutoff ")
        params: Dict[str, Any] = {"cutoff": cutoff}
        _bp: List[Any] = []
        if keys:
            sql += "AND key IN :keys "
            params["keys"] = tuple(keys)
            _bp.append(bindparam("keys", expanding=True))
        elif kinds:
            sql += "AND kind IN :kinds "
            params["kinds"] = tuple(kinds)
            _bp.append(bindparam("kinds", expanding=True))
        sql += "ORDER BY ts ASC"
        eng = _engine()
        rows = []
        stmt = _sa_text(sql)
        if _bp:
            stmt = stmt.bindparams(*_bp)
        with eng.connect() as conn:
            for r in conn.execute(stmt, params):
                rows.append({
                    "ts": int(r[0]), "kind": r[1], "key": r[2],
                    "value": float(r[3]),
                    "extra": json.loads(r[4]) if r[4] else None,
                })
    except Exception as e:  # noqa: BLE001
        logger.warning("[ComputeMetrics] 查询失败: %s", e)
        return {"window": window, "series": {}, "error": str(e)}

    # 按 key 分组 + 抽稀
    series: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        series.setdefault(r["key"], []).append({"ts": r["ts"], "value": r["value"],
                                                "extra": r["extra"]})
    out: Dict[str, List[Dict[str, Any]]] = {}
    for key, pts in series.items():
        if len(pts) > limit:
            step = len(pts) / limit
            pts = [pts[int(i * step)] for i in range(limit)]
        out[key] = pts
    return {"window": window, "series": out}
