# -*- coding: utf-8 -*-
"""job_registry —— 统一定时任务登记 / 心跳 / 失败告警（v3 方向 6）。

表（幂等自建）：
  job_registry  每个任务一行：name, cadence, description, owner, enabled, expected_interval_sec,
                last_start, last_end, last_status(ok/error/running/skipped), last_duration_ms,
                last_error, consecutive_failures, run_count, fail_count, heartbeat_at, last_result(JSON)
  job_runs      每次运行一行（14 天滚动清理）：name, started_at, ended_at, status, duration_ms, error, result

用法：
  register_job(name, cadence, description, owner, expected_interval_sec)   登记元数据（幂等 upsert）
  with job_run(name) as rec: ...; rec.set_result({...})                    包裹一次运行：自动记 start/end/耗时/失败
  heartbeat(name)                                                          长循环内定期心跳
  list_jobs()                                                              合并 APScheduler 下次运行时间 + 陈旧判定
  watchdog_job()                                                           每 5 分钟：滞后/中断告警（P2/P1）、清理 job_runs

失败告警：单次失败 P2（去重 10 分钟）；连续 ≥ 3 次 P1；owner=risk 的任务失败直接 P1。
DB 不可用时全部静默降级为日志（不影响任务本身执行）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_schema_ready = False
_schema_lock = threading.Lock()
# 进程内可手动触发的任务函数表（name → callable），由 v3_jobs 注册
_RUNNERS: Dict[str, Callable[..., Any]] = {}
# 进程内元数据缓存（DB 不可用时 /api/ops/jobs 仍能显示）
_META: Dict[str, Dict[str, Any]] = {}
_meta_lock = threading.Lock()


def _db():
    from backend.database.connection import SessionLocal
    from backend.core.tenant import set_system_identity
    set_system_identity()
    return SessionLocal()


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        from sqlalchemy import text
        ddl = [
            """
            CREATE TABLE IF NOT EXISTS job_registry (
                name VARCHAR(80) PRIMARY KEY,
                cadence VARCHAR(80),
                description TEXT,
                owner VARCHAR(40) DEFAULT 'system',
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                expected_interval_sec INTEGER,
                last_start TIMESTAMPTZ,
                last_end TIMESTAMPTZ,
                last_status VARCHAR(16),
                last_duration_ms INTEGER,
                last_error TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                run_count INTEGER NOT NULL DEFAULT 0,
                fail_count INTEGER NOT NULL DEFAULT 0,
                heartbeat_at TIMESTAMPTZ,
                last_result TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS job_runs (
                id BIGSERIAL PRIMARY KEY,
                name VARCHAR(80) NOT NULL,
                started_at TIMESTAMPTZ NOT NULL,
                ended_at TIMESTAMPTZ,
                status VARCHAR(16),
                duration_ms INTEGER,
                error TEXT,
                result TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_job_runs_name_started ON job_runs (name, started_at DESC)",
        ]
        db = _db()
        try:
            for stmt in ddl:
                db.execute(text(stmt))
            db.commit()
            _schema_ready = True
        except Exception as exc:
            db.rollback()
            logger.warning("[job_registry] 建表失败（降级为仅日志）: %s", exc)
        finally:
            db.close()


def register_job(name: str, cadence: str, description: str = "", owner: str = "system",
                 expected_interval_sec: Optional[int] = None, runner: Optional[Callable[..., Any]] = None) -> None:
    """登记任务元数据（幂等）。expected_interval_sec 用于陈旧判定；缺省按 cadence 粗略推断。"""
    if expected_interval_sec is None:
        expected_interval_sec = _infer_interval(cadence)
    with _meta_lock:
        _META[name] = {"name": name, "cadence": cadence, "description": description, "owner": owner,
                       "expected_interval_sec": expected_interval_sec}
        if runner is not None:
            _RUNNERS[name] = runner
    try:
        ensure_schema()
        from sqlalchemy import text
        db = _db()
        try:
            db.execute(text(
                "INSERT INTO job_registry (name, cadence, description, owner, expected_interval_sec) "
                "VALUES (:n, :c, :d, :o, :e) "
                "ON CONFLICT (name) DO UPDATE SET cadence = EXCLUDED.cadence, description = EXCLUDED.description, "
                "owner = EXCLUDED.owner, expected_interval_sec = EXCLUDED.expected_interval_sec, updated_at = now()"
            ), {"n": name, "c": cadence, "d": description, "o": owner, "e": expected_interval_sec})
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.debug("[job_registry] register %s fail: %s", name, exc)
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[job_registry] register %s skipped: %s", name, exc)


def _infer_interval(cadence: str) -> Optional[int]:
    c = str(cadence or "").lower()
    try:
        if c.startswith("interval"):
            num = "".join(ch for ch in c if ch.isdigit())
            n = int(num) if num else 60
            if "h" in c.replace("interval", ""):
                return n * 3600
            if "m" in c.replace("interval", "") and "ms" not in c:
                return n * 60
            return n
        if "daily" in c:
            return 24 * 3600
        if "weekly" in c:
            return 7 * 24 * 3600
        if "hourly" in c:
            return 3600
    except Exception:
        pass
    return None


def is_enabled(name: str) -> bool:
    try:
        ensure_schema()
        from sqlalchemy import text
        db = _db()
        try:
            row = db.execute(text("SELECT enabled FROM job_registry WHERE name = :n"), {"n": name}).fetchone()
            return bool(row[0]) if row and row[0] is not None else True
        finally:
            db.close()
    except Exception:
        return True


def set_enabled(name: str, enabled: bool) -> bool:
    try:
        ensure_schema()
        from sqlalchemy import text
        db = _db()
        try:
            n = db.execute(text("UPDATE job_registry SET enabled = :e, updated_at = now() WHERE name = :n"),
                           {"e": bool(enabled), "n": name}).rowcount
            db.commit()
            return bool(n)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[job_registry] set_enabled %s fail: %s", name, exc)
        return False


class RunRecord:
    def __init__(self, name: str):
        self.name = name
        self.started = time.time()
        self.result: Any = None
        self.skipped = False

    def set_result(self, result: Any) -> None:
        self.result = result


def _json_dumps_safe(obj: Any, limit: int = 4000) -> Optional[str]:
    if obj is None:
        return None
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = str(obj)
    return s[:limit]


def _write_start(name: str, started_at: datetime) -> None:
    from sqlalchemy import text
    db = _db()
    try:
        db.execute(text(
            "INSERT INTO job_registry (name, last_start, last_status, run_count) VALUES (:n, :s, 'running', 1) "
            "ON CONFLICT (name) DO UPDATE SET last_start = :s, last_status = 'running', heartbeat_at = :s, "
            "run_count = job_registry.run_count + 1, updated_at = now()"
        ), {"n": name, "s": started_at})
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.debug("[job_registry] start %s fail: %s", name, exc)
    finally:
        db.close()


def _write_end(name: str, started_at: datetime, ended_at: datetime, status: str, duration_ms: int,
               error: Optional[str], result: Any) -> int:
    """返回 consecutive_failures（供告警判级）。"""
    from sqlalchemy import text
    db = _db()
    consec = 0
    try:
        if status == "ok":
            row = db.execute(text(
                "UPDATE job_registry SET last_end = :e, last_status = 'ok', last_duration_ms = :d, last_error = NULL, "
                "consecutive_failures = 0, heartbeat_at = :e, last_result = :r, updated_at = now() WHERE name = :n "
                "RETURNING consecutive_failures"
            ), {"n": name, "e": ended_at, "d": duration_ms, "r": _json_dumps_safe(result)}).fetchone()
        else:
            row = db.execute(text(
                "UPDATE job_registry SET last_end = :e, last_status = :st, last_duration_ms = :d, last_error = :err, "
                "consecutive_failures = CASE WHEN :st = 'error' THEN consecutive_failures + 1 ELSE consecutive_failures END, "
                "fail_count = CASE WHEN :st = 'error' THEN fail_count + 1 ELSE fail_count END, "
                "heartbeat_at = :e, updated_at = now() WHERE name = :n RETURNING consecutive_failures"
            ), {"n": name, "e": ended_at, "st": status, "d": duration_ms, "err": (error or "")[:2000]}).fetchone()
        consec = int(row[0]) if row and row[0] is not None else 0
        db.execute(text(
            "INSERT INTO job_runs (name, started_at, ended_at, status, duration_ms, error, result) "
            "VALUES (:n, :s, :e, :st, :d, :err, :r)"
        ), {"n": name, "s": started_at, "e": ended_at, "st": status, "d": duration_ms,
            "err": (error or "")[:2000] or None, "r": _json_dumps_safe(result, 2000)})
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.debug("[job_registry] end %s fail: %s", name, exc)
    finally:
        db.close()
    return consec


@contextmanager
def job_run(name: str, *, respect_enabled: bool = True):
    """包裹一次任务运行。任务异常会被记录并**重新抛出**（调度器仍能看到失败）。
    若任务被 disable（job_registry.enabled=false）→ 跳过执行（rec.skipped=True）。"""
    rec = RunRecord(name)
    started_at = datetime.now(timezone.utc)
    if respect_enabled and not is_enabled(name):
        rec.skipped = True
        logger.info("[job_registry] %s 已被禁用，跳过", name)
        try:
            ensure_schema()
            _write_end(name, started_at, started_at, "skipped", 0, None, None)
        except Exception:
            pass
        yield rec
        return
    try:
        ensure_schema()
        _write_start(name, started_at)
    except Exception as exc:
        logger.debug("[job_registry] start record fail: %s", exc)
    try:
        yield rec
    except Exception as exc:
        ended = datetime.now(timezone.utc)
        dur = int((ended - started_at).total_seconds() * 1000)
        consec = 0
        try:
            consec = _write_end(name, started_at, ended, "error", dur, f"{type(exc).__name__}: {exc}", None)
        except Exception:
            pass
        logger.exception("[job_registry] %s 失败 (%d ms, 连续 %d 次): %s", name, dur, consec, exc)
        _alert_failure(name, exc, consec)
        raise
    ended = datetime.now(timezone.utc)
    dur = int((ended - started_at).total_seconds() * 1000)
    try:
        _write_end(name, started_at, ended, "ok", dur, None, rec.result)
    except Exception:
        pass


def heartbeat(name: str) -> None:
    try:
        ensure_schema()
        from sqlalchemy import text
        db = _db()
        try:
            db.execute(text(
                "INSERT INTO job_registry (name, heartbeat_at) VALUES (:n, now()) "
                "ON CONFLICT (name) DO UPDATE SET heartbeat_at = now(), updated_at = now()"
            ), {"n": name})
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[job_registry] heartbeat %s fail: %s", name, exc)


def _alert_failure(name: str, exc: Exception, consec: int) -> None:
    try:
        from backend.services.ops.alerts import send_alert
        owner = (_META.get(name) or {}).get("owner", "system")
        level = "P1" if (consec >= 3 or owner == "risk") else "P2"
        send_alert(level, f"定时任务失败: {name}",
                   f"任务 {name}（owner={owner}）失败，连续 {consec} 次\n{type(exc).__name__}: {str(exc)[:500]}",
                   dedupe_key=f"job_fail:{name}", source="job_registry")
    except Exception as e:  # pragma: no cover
        logger.debug("[job_registry] alert fail: %s", e)


# ───────────────────────────── 查询 ─────────────────────────────
def list_jobs() -> List[Dict[str, Any]]:
    """合并：job_registry 行 + APScheduler 任务（下次运行时间）+ 进程内元数据。"""
    rows: Dict[str, Dict[str, Any]] = {}
    try:
        ensure_schema()
        from sqlalchemy import text
        db = _db()
        try:
            for r in db.execute(text("SELECT * FROM job_registry ORDER BY name")).mappings().all():
                d = dict(r)
                for k in ("last_start", "last_end", "heartbeat_at", "created_at", "updated_at"):
                    if d.get(k) is not None and hasattr(d[k], "isoformat"):
                        d[k] = d[k].isoformat()
                rows[d["name"]] = d
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[job_registry] list fail: %s", exc)
    with _meta_lock:
        for name, meta in _META.items():
            rows.setdefault(name, {"name": name}).update({k: v for k, v in meta.items() if rows.get(name, {}).get(k) in (None, "")})
            rows[name]["can_trigger"] = name in _RUNNERS
    # APScheduler 侧
    try:
        from backend.services.scheduler import task_scheduler
        for j in task_scheduler.get_job_info() or []:
            jid = str(j.get("id") or "")
            name = jid[3:] if jid.startswith("v3_") else jid
            target = rows.get(name) or rows.get(jid)
            nrt = j.get("next_run_time")
            nrt_s = nrt.isoformat() if hasattr(nrt, "isoformat") else (str(nrt) if nrt else None)
            if target is None:
                rows[jid] = {"name": jid, "owner": "legacy", "cadence": "apscheduler", "scheduler_only": True,
                             "next_run_time": nrt_s, "func_name": j.get("func_name")}
            else:
                target["next_run_time"] = nrt_s
                target["scheduler_id"] = jid
                target["func_name"] = j.get("func_name")
    except Exception as exc:
        logger.debug("[job_registry] scheduler info fail: %s", exc)
    now = datetime.now(timezone.utc)
    out = []
    for name, d in rows.items():
        d.setdefault("name", name)
        d["stale"] = _is_stale(d, now)
        out.append(d)
    out.sort(key=lambda x: (0 if not x.get("scheduler_only") else 1, str(x.get("owner") or ""), x["name"]))
    return out


def _is_stale(d: Dict[str, Any], now: datetime) -> Optional[str]:
    """陈旧判定：expected_interval_sec 存在且 last_end/heartbeat 距今 > 2× 间隔（cron 日任务 > 26h）。"""
    exp = d.get("expected_interval_sec")
    if not exp:
        return None
    ref = d.get("heartbeat_at") or d.get("last_end") or d.get("last_start")
    if not ref:
        return "never_ran"
    try:
        ts = datetime.fromisoformat(str(ref).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (now - ts).total_seconds()
        limit = max(float(exp) * 2.0, float(exp) + 1800.0)
        if age > limit * 2:
            return "critical"
        if age > limit:
            return "warn"
    except Exception:
        return None
    return None


def job_runs(name: str, limit: int = 50) -> List[Dict[str, Any]]:
    try:
        ensure_schema()
        from sqlalchemy import text
        db = _db()
        try:
            rows = db.execute(text(
                "SELECT id, name, started_at, ended_at, status, duration_ms, error, result FROM job_runs "
                "WHERE name = :n ORDER BY started_at DESC LIMIT :l"
            ), {"n": name, "l": int(limit)}).mappings().all()
            out = []
            for r in rows:
                d = dict(r)
                for k in ("started_at", "ended_at"):
                    if d.get(k) is not None and hasattr(d[k], "isoformat"):
                        d[k] = d[k].isoformat()
                out.append(d)
            return out
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[job_registry] runs %s fail: %s", name, exc)
        return []


def trigger(name: str) -> Dict[str, Any]:
    """立即在当前线程运行一次（仅进程内登记了 runner 的任务）。"""
    fn = _RUNNERS.get(name)
    if fn is None:
        return {"ok": False, "error": "no_runner", "name": name}
    with job_run(name, respect_enabled=False) as rec:
        out = fn()
        rec.set_result(out)
    return {"ok": True, "name": name, "result": out}


# ───────────────────────────── 看门狗 ─────────────────────────────
_stale_alerted: Dict[str, float] = {}


def watchdog_job() -> Dict[str, Any]:
    """每 5 分钟：滞后 P2 / 中断 P1 告警（每任务每小时最多一条）；清理 14 天前的 job_runs。"""
    out: Dict[str, Any] = {"warn": [], "critical": [], "cleaned": 0}
    now = time.time()
    for j in list_jobs():
        if j.get("scheduler_only") or not j.get("enabled", True):
            continue
        st = j.get("stale")
        if st in ("warn", "critical"):
            out[st].append(j["name"])
            if now - _stale_alerted.get(j["name"], 0.0) > 3600:
                _stale_alerted[j["name"]] = now
                try:
                    from backend.services.ops.alerts import send_alert
                    send_alert("P1" if st == "critical" else "P2", f"定时任务{'中断' if st == 'critical' else '滞后'}: {j['name']}",
                               f"cadence={j.get('cadence')} 上次结束={j.get('last_end')} 心跳={j.get('heartbeat_at')} "
                               f"状态={j.get('last_status')} 连续失败={j.get('consecutive_failures')}",
                               dedupe_key=f"job_stale:{j['name']}", source="job_watchdog")
                except Exception:
                    pass
    try:
        ensure_schema()
        from sqlalchemy import text
        db = _db()
        try:
            n = db.execute(text("DELETE FROM job_runs WHERE started_at < :cut"),
                           {"cut": datetime.now(timezone.utc) - timedelta(days=14)}).rowcount
            db.commit()
            out["cleaned"] = int(n or 0)
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[job_registry] cleanup fail: %s", exc)
    return out
