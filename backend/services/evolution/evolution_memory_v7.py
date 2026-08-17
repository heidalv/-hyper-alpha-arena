"""
V7 因子进化长期记忆（SQLite 实现，当前即可运行）。

职责：
1. 每轮 factor_evolution_loop 结束后，把报告沉淀为可检索教训；
2. 下一轮 Codegen LLM prompt 注入最相关的历史教训/成功配方/失败案例；
3. 教训按使用次数与效果质量排序，支持衰减与淘汰（不靠时间倒序）。

这是增量模块：不修改主交易库 schema，不进入热路径。DB 文件：
    backend/data/factor_evolution_memory_v7.db
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_DB_PATH = Path(os.getenv("V7_MEMORY_DB_PATH", str(_BACKEND_DIR / "data" / "factor_evolution_memory_v7.db")))
_LOCK = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS v7_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,                -- success_recipe | failure_case | gate_lesson | decay_case | pipeline_issue
    cycle TEXT NOT NULL,               -- L | M | S
    period TEXT NOT NULL,              -- 4h / 1h / 15m / 5m / 1m
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    report_json TEXT NOT NULL DEFAULT '{}',
    quality REAL NOT NULL DEFAULT 0.5,
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',   -- active | retired
    UNIQUE(created_at, kind, cycle, period, title)
);

CREATE TABLE IF NOT EXISTS v7_generation_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    cycle TEXT NOT NULL,
    period TEXT NOT NULL,
    quick INTEGER NOT NULL DEFAULT 0,
    report_json TEXT NOT NULL,
    lesson_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS v7_retrieval_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    query TEXT NOT NULL,
    cycle TEXT,
    period TEXT,
    top_ids_json TEXT NOT NULL DEFAULT '[]'
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _LOCK:
        conn = _conn()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()


def _period_to_cycle(period: Optional[str]) -> str:
    p = (period or "").lower()
    if p in ("4h", "8h", "1d"):
        return "L"
    if p in ("15m", "30m", "1h", "2h"):
        return "M"
    return "S"


def _tokenize(text: str) -> List[str]:
    import re
    text = (text or "").lower()
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    words = re.findall(r"[a-z0-9_]{2,}", text)
    return cjk + words


def _vector(text: str, dim: int = 256) -> List[float]:
    vec = [0.0] * dim
    for tok in _tokenize(text):
        digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest, "little") % dim
        vec[idx] += 1.0 if (digest[-1] & 1) == 0 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))


def _extract_lessons(report: Dict[str, Any], period: str) -> List[Dict[str, Any]]:
    """Deterministic lesson extraction. LLM is not the alpha judge; the loop's
    hard metrics/report decide what enters memory."""
    cycle = _period_to_cycle(period)
    lessons: List[Dict[str, Any]] = []
    now = _now()
    base = {
        "created_at": now,
        "cycle": cycle,
        "period": period,
    }

    if report.get("error"):
        lessons.append({
            **base,
            "kind": "pipeline_issue",
            "title": f"{period} 进化链中断: {report.get('error')}",
            "summary": (report.get("message") or report.get("error") or "")[:500],
            "report_json": report,
            "quality": 0.9,
        })
        return lessons

    promoted = report.get("promoted_factors") or []
    for p in promoted:
        lessons.append({
            **base,
            "kind": "success_recipe",
            "title": f"{period} 晋升: {p.get('id')} ({p.get('source')})",
            "summary": (
                f"因子 {p.get('id')} 通过 WFO/DSR/PBO/测试集复评并进入影子期。"
                f"同轮候选={report.get('candidates')} 幸存={report.get('survivors')}。"
                f"保留其结构假设与参数尺度作为下一轮变异父本。"
            ),
            "report_json": {"factor": p, "report": report},
            "quality": 0.95,
        })

    if not promoted and report.get("candidates", 0) > 0:
        lessons.append({
            **base,
            "kind": "gate_lesson",
            "title": f"{period} 全量拒绝: {report.get('candidates')} 候选 0 晋升",
            "summary": (
                f"候选 {report.get('candidates')}，评估 {report.get('evaluated')}，"
                f"幸存 {report.get('survivors')}，晋升 0。下一轮应优先生成更低换手、"
                f"更高 ICIR、更简洁的互补假设，而不是重复本代结构。"
            ),
            "report_json": report,
            "quality": 0.7,
        })

    if report.get("degraded", 0) > 0:
        lessons.append({
            **base,
            "kind": "decay_case",
            "title": f"{period} 本轮衰退/淘汰 {report.get('degraded')} 个因子",
            "summary": (
                "已有因子发生 IC 衰减或治理淘汰；后续挖掘应避开同类窗口/字段组合，"
                "并优先寻找与衰退因子低相关的替代信号。"
            ),
            "report_json": report,
            "quality": 0.75,
        })

    if report.get("active_total", 0) == 0 and not report.get("error"):
        lessons.append({
            **base,
            "kind": "pipeline_issue",
            "title": f"{period} 活跃因子为 0（冷启动）",
            "summary": "当前池为空；优先短窗口反转/动量/波动率基础因子，把首个可交易池建立起来。",
            "report_json": report,
            "quality": 0.6,
        })

    return lessons


def record_report(period: str, report: Dict[str, Any]) -> int:
    """Write one evolution round + extracted lessons. Returns lesson count."""
    init_db()
    lessons = _extract_lessons(report, period)
    cycle = _period_to_cycle(period)
    with _LOCK:
        conn = _conn()
        try:
            cur = conn.execute(
                """INSERT INTO v7_generation_reports
                   (created_at, cycle, period, quick, report_json, lesson_count)
                   VALUES (?,?,?,?,?,?)""",
                (_now(), cycle, period, 1 if report.get("quick") else 0,
                 json.dumps(report, ensure_ascii=False, default=str), len(lessons)),
            )
            for lesson in lessons:
                conn.execute(
                    """INSERT OR IGNORE INTO v7_lessons
                       (created_at, kind, cycle, period, title, summary,
                        report_json, quality, status)
                       VALUES (?,?,?,?,?,?,?,?, 'active')""",
                    (
                        lesson["created_at"], lesson["kind"], lesson["cycle"],
                        lesson["period"], lesson["title"], lesson["summary"],
                        json.dumps(lesson.get("report_json", {}), ensure_ascii=False, default=str),
                        float(lesson.get("quality", 0.5)),
                    ),
                )
            conn.commit()
            return len(lessons)
        finally:
            conn.close()


def build_codegen_context(period: str, limit: int = 8) -> str:
    """Retrieve top memory for Codegen prompt injection.

    Ranking = vector similarity(query, title+summary)
            + 0.5 * quality
            + 0.2 * ln(1+use_count)
            - small recency decay.
    """
    init_db()
    query = f"{_period_to_cycle(period)} {period} 因子挖掘 晋升 拒绝 衰退 换手 ICIR"
    qvec = _vector(query)
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                """SELECT id, kind, cycle, period, title, summary, quality,
                          use_count, created_at
                   FROM v7_lessons
                   WHERE status='active'
                     AND (period=? OR cycle=? OR cycle='X')
                   ORDER BY id DESC LIMIT 200""",
                (period, _period_to_cycle(period)),
            ).fetchall()
        finally:
            conn.close()

    scored = []
    for r in rows:
        id_, kind, cycle, period_, title, summary, quality, use_count, created_at = r
        sim = _cosine(qvec, _vector(f"{title} {summary}"))
        try:
            created = datetime.fromisoformat(created_at)
            age_days = max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 86400)
        except Exception:
            age_days = 30.0
        recency = 0.5 ** (age_days / 14.0)
        score = sim + 0.5 * float(quality or 0.5) + 0.15 * math.log1p(int(use_count or 0)) + 0.2 * recency
        scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    picked = scored[: max(1, limit)]
    if not picked:
        return ""

    lines = ["## 因子进化长期记忆（V7，历史硬指标教训，仅供假设生成）"]
    for _, r in picked:
        id_, kind, cycle, period_, title, summary, quality, use_count, created_at = r
        kind_label = {
            "success_recipe": "成功配方",
            "failure_case": "失败案例",
            "gate_lesson": "门禁教训",
            "decay_case": "衰退案例",
            "pipeline_issue": "链路问题",
        }.get(kind, kind)
        lines.append(f"- [{kind_label}|{cycle}|{period_}] {title}: {summary}")
        # 记录本次被检索（下次检索质量上升；无效教训可通过 status=retired 淘汰）
        with _LOCK:
            conn = _conn()
            try:
                conn.execute(
                    "UPDATE v7_lessons SET use_count=use_count+1, last_used_at=? WHERE id=?",
                    (_now(), id_),
                )
                conn.execute(
                    """INSERT INTO v7_retrieval_log (created_at, query, cycle, period, top_ids_json)
                       VALUES (?,?,?,?,?)""",
                    (_now(), query, cycle, period_, json.dumps([id_])),
                )
                conn.commit()
            finally:
                conn.close()
    return "\n".join(lines)


def stats() -> Dict[str, Any]:
    init_db()
    with _LOCK:
        conn = _conn()
        try:
            lessons = conn.execute(
                """SELECT kind, count(*), avg(quality), sum(use_count)
                   FROM v7_lessons WHERE status='active' GROUP BY kind"""
            ).fetchall()
            reports = conn.execute("SELECT count(*) FROM v7_generation_reports").fetchone()[0]
            used = conn.execute("SELECT count(*) FROM v7_lessons WHERE use_count>0").fetchone()[0]
        finally:
            conn.close()
    return {
        "db": str(_DB_PATH),
        "reports": reports,
        "used_lessons": used,
        "by_kind": [
            {"kind": r[0], "count": r[1], "avg_quality": round(r[2] or 0, 3), "total_uses": r[3] or 0}
            for r in lessons
        ],
    }


def last_report_age_hours(period: str, quick: Optional[bool] = None) -> Optional[float]:
    """最近一次指定周期进化报告的年龄（小时）。无记录返回 None。"""
    init_db()
    with _LOCK:
        conn = _conn()
        try:
            sql = "SELECT MAX(created_at) FROM v7_generation_reports WHERE period=?"
            params: List[Any] = [period]
            if quick is not None:
                sql += " AND quick=?"
                params.append(1 if quick else 0)
            row = conn.execute(sql, params).fetchone()
        finally:
            conn.close()
    if not row or not row[0]:
        return None
    try:
        created = datetime.fromisoformat(row[0])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 3600.0)
    except Exception:
        return None


def maintenance(max_unused_age_days: int = 30) -> Dict[str, Any]:
    """记忆库维护：长期未被检索且无证据支撑的观察态教训退役。

    只淘汰“从未被使用”的旧教训；只要被 Codegen 检索过至少一次就保留，
    避免把仍可能有效的假设提前删除（不过度门禁，也不让噪声无限膨胀）。
    """
    init_db()
    cutoff = datetime.now(timezone.utc).timestamp() - max_unused_age_days * 86400
    with _LOCK:
        conn = _conn()
        try:
            cur = conn.execute(
                """UPDATE v7_lessons SET status='retired'
                   WHERE status='active' AND use_count=0
                     AND created_at < ?""",
                (datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat(),),
            )
            conn.commit()
            retired = cur.rowcount
            total = conn.execute("SELECT count(*) FROM v7_lessons WHERE status='active'").fetchone()[0]
        finally:
            conn.close()
    return {"retired": retired, "active_remaining": total}


def memory_report(limit: int = 30) -> List[Dict[str, Any]]:
    init_db()
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                """SELECT id, created_at, kind, cycle, period, title, summary,
                          quality, use_count, status
                   FROM v7_lessons ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    return [
        {
            "id": r[0], "created_at": r[1], "kind": r[2], "cycle": r[3],
            "period": r[4], "title": r[5], "summary": r[6],
            "quality": r[7], "use_count": r[8], "status": r[9],
        }
        for r in rows
    ]
