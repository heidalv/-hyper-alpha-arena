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
import logging
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

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
    """K 线周期 → V7 记忆的周期档（S/M/L）。

    [轮51 2026-09-17 目标④ 学习进化层语义对齐] 此前这里是**第三套**独立词表：
        4h/8h/1d → "L"，15m/30m/1h/2h → "M"，其余 → "S"
    它与另外两层冲突 —— 同一个"中"字在三层指三个不同周期：
        因子发现：4h = "midlong"(中期)
        调度 V7 ：15m = "中周期"
        学习层  ：15m/30m/1h/2h = "M"(中期)
        策略层  ：mid 车道实际中位持仓 3.2h（= 日内）
    现在改为**从唯一真源 `backend/config/cycle_semantics.py` 派生**，字母含义重定为：
        S = 短线（分钟级，1m/3m/5m —— 已判死的车道）
        M = **日内**（15m/30m/1h/2h，前瞻 1.5–2h）  ← 原标签"中期"是错的
        L = **长期趋势**（4h 及以上，前瞻 24h–3d）
    行为差异仅一处：`1w`/`1M` 此前落进 "S"（明显错误，月线不是短线），现归 "L"。
    存量 `v7_lessons.cycle` 值不改写（检索按原值继续可用）。
    """
    p = (period or "").strip()
    if not p:
        return "S"
    try:
        from backend.config.cycle_semantics import INTRADAY, TREND, period_to_cycle
        cyc = period_to_cycle(p)
        if cyc == INTRADAY:
            # 分钟级（1m/3m/5m）仍算短线；15m 及以上算日内
            return "S" if p.lower() in ("1m", "3m", "5m") else "M"
        if cyc == TREND:
            return "L"
    except Exception:
        pass
    # 真源不可用时退回历史词表（fail-safe，绝不因语义模块把链路打断）
    low = p.lower()
    if low in ("4h", "8h", "1d"):
        return "L"
    if low in ("15m", "30m", "1h", "2h"):
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


def _extract_trajectory(report: Dict[str, Any], period: str) -> Optional[Dict[str, Any]]:
    """[2026-09-07 QuantaAlpha 轨迹级进化] 把整轮挖掘当一条「轨迹」记录漏斗，
    并产出针对**失败环节**的定向变异指令（而非泛泛的「下一轮更好」）。

    与 success_recipe/gate_lesson 的区别：那些是结果快照；轨迹记录的是
    「候选从哪来 → 在哪一关死 → 该怎么改」的因果链，供下轮直接改写挖掘行为。

    轨迹变异规则（确定性，不让 LLM 当裁判）：
      - 候选多但初筛死一片（ICIR/换手）→ 指令：GP/MCTS 提高 ICIR 下限、降换手；
      - 过初筛但池筛死（增量相关高）→ 指令：换字段/换窗口族，找低相关；
      - 过池筛但 DSR/PBO 死 → 指令：减少同族重复搜索，拉大多样性；
      - 幸存但晋升 0（测试集/净IC）→ 指令：优先 near-miss 修复与成本约束。
    """
    if report.get("error"):
        return None
    cand = int(report.get("candidates") or 0)
    evaluated = int(report.get("evaluated") or 0)
    survivors = int(report.get("survivors") or 0)
    promoted = report.get("promoted_factors") or []
    if cand <= 0:
        return None

    # 漏斗定位失败环节
    purge = report.get("purge") or {}
    stage_counts = report.get("stage_counts") or {}
    rej_eval = int(stage_counts.get("rejected_eval") or purge.get("rejected_eval") or 0)
    rej_pool = int(stage_counts.get("rejected_pool") or purge.get("rejected_pool") or 0)
    rej_dsr = int(stage_counts.get("rejected_dsr_pbo") or purge.get("rejected_dsr_pbo") or 0)

    if promoted:
        step, directive = "promote_ok", (
            "本轮有晋升。下轮以晋升因子为父本做结构变异（保骨架、换窗口/字段），"
            "并检索其低相关互补因子。"
        )
    elif rej_dsr > 0 and rej_dsr >= max(1, survivors):
        step, directive = "dsr_pbo", (
            "候选过池筛但死于 DSR/PBO（过拟合）。下轮：减少同族重复假设，"
            "拉大字段/窗口多样性，避免在同一信号族上反复微调。"
        )
    elif rej_pool > 0:
        step, directive = "pool_corr", (
            "候选过初筛但死于增量相关（与池内太像）。下轮：换字段族"
            "（funding/oi/liquidation/wick）与不同窗口，优先低相关新信号。"
        )
    elif rej_eval > 0 and rej_eval >= cand * 0.5:
        step, directive = "eval_gate", (
            "大量候选死于初筛（ICIR/换手/半衰期）。下轮：提高挖掘适应度的 ICIR "
            "权重、加强换手惩罚，优先低频高质信号而非高频噪声。"
        )
    elif survivors > 0 and not promoted:
        step, directive = "final_gate", (
            "有幸存者但晋升 0（测试集/净 IC 拦）。下轮：用 near-miss 修复"
            "（decay/ts_rank 包装）救接近达标者，并强化含成本净收益目标。"
        )
    else:
        step, directive = "explore", (
            "漏斗无单一瓶颈。下轮：扩大搜索广度（更多种子/迭代），保持多样性。"
        )

    trajectory = {
        "period": period,
        "candidates": cand,
        "evaluated": evaluated,
        "survivors": survivors,
        "promoted": len(promoted),
        "rej_eval": rej_eval,
        "rej_pool": rej_pool,
        "rej_dsr_pbo": rej_dsr,
        "failure_step": step,
        "mutation_directive": directive,
    }
    cycle = _period_to_cycle(period)
    return {
        "created_at": _now(),
        "kind": "trajectory",
        "cycle": cycle,
        "period": period,
        "title": f"{period} 挖掘轨迹：瓶颈={step}（候选{cand}→幸存{survivors}→晋升{len(promoted)}）",
        "summary": f"失败环节定位={step}。定向变异指令：{directive}",
        "report_json": {"trajectory": trajectory, "report": report},
        "quality": 0.85,
    }


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

    # [2026-09-07] 轨迹级进化：最先提取（失败环节定位 + 定向变异指令）
    traj = _extract_trajectory(report, period)
    if traj is not None:
        lessons.append(traj)

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


_CODEGEN_SATURATED_WARNED: set = set()
_CODEGEN_SATURATED_GUARD = threading.Lock()


def _warn_codegen_window_saturated(period: str, cycle: str, total_matching: int) -> None:
    """[F362] 候选被 `LIMIT 200` 截断时告警一次（同一 period/cycle 只报一次）。

    不改变任何召回行为，只把"还有 N 条永远读不到"这件事**变可见**——
    否则池子持续增长、检索覆盖率持续下降，而外部完全看不出来。
    """
    key = (str(period), str(cycle))
    with _CODEGEN_SATURATED_GUARD:
        if key in _CODEGEN_SATURATED_WARNED:
            return
        _CODEGEN_SATURATED_WARNED.add(key)
    unreachable = max(0, int(total_matching) - 200)
    logger.warning(
        "[V7Memory] Codegen 候选窗口被 LIMIT 200 截断：period=%s cycle=%s 匹配 %d 条，"
        "其中 **%d 条结构上不可达**（无论质量多高都进不了 prompt）。"
        "置 V7_CODEGEN_FULL_POOL=1 可取消该窗口（全量参与打分）。",
        period, cycle, total_matching, unreachable,
    )


def latest_trajectory_directive(period: str) -> Optional[Dict[str, Any]]:
    """[2026-09-07] 取最近一次挖掘轨迹的定向变异指令（供下轮挖掘实际改行为）。

    返回 {failure_step, mutation_directive, candidates, survivors, promoted}，
    无轨迹记录时返回 None。进化循环可用它调整下轮 GP/MCTS 的适应度权重、
    字段族偏好等——这是 QuantaAlpha「轨迹级变异」的落地：改的是挖掘行为本身。
    """
    init_db()
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                """SELECT report_json FROM v7_lessons
                   WHERE kind='trajectory' AND status='active' AND period=?
                   ORDER BY id DESC LIMIT 1""",
                (period,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    try:
        payload = json.loads(row[0] or "{}")
        return payload.get("trajectory")
    except Exception:
        return None


def build_codegen_context(period: str, limit: int = 8) -> str:
    """Retrieve top memory for Codegen prompt injection.

    Ranking = vector similarity(query, title+summary)
            + 0.5 * quality
            + 0.2 * ln(1+use_count)
            - small recency decay.

    [F362 2026-09-18 可达性] 原实现先 `ORDER BY id DESC LIMIT 200` **再**按上式打分，
    等于在这套明示的排序之外**偷偷叠加了一层未记录的"最新 200 条"过滤**。
    实测（`scripts/probe_v7_codegen_reachability.py`，2026-09-18）：
    active 810 条里 **528 条（65.2%）** 结构上永远进不了 Codegen prompt
    （`4h/L`、`1d/L` 两个窗口都恰好被 200 撑满），其中 `success_recipe` 129、
    `gate_lesson` 92、`trajectory` 60、`decay_case` 6（其余 241 条是高度重复的
    `pipeline_issue`，价值较低）。这就是「学习只写不读」在**挖掘侧**的残留。

    处置（**不改变今日行为**）：
      - `V7_CODEGEN_FULL_POOL=1` ⇒ 取消 id 窗口，**全量 active 参与打分**（与交易侧读回路
        `learning_readback` 同口径），语义更符合 docstring 声明的排序；
      - 默认关 ⇒ 逐字节保持旧行为；
      - 无论开关如何，**窗口饱和时告警一次**（禁止静默退化）：告诉运维"还有 N 条读不到"。
    """
    init_db()
    query = f"{_period_to_cycle(period)} {period} 因子挖掘 晋升 拒绝 衰退 换手 ICIR"
    qvec = _vector(query)
    _full = str(os.getenv("V7_CODEGEN_FULL_POOL", "0")).strip().lower() in (
        "1", "true", "yes", "on")
    _cycle = _period_to_cycle(period)
    with _LOCK:
        conn = _conn()
        try:
            if _full:
                rows = conn.execute(
                    """SELECT id, kind, cycle, period, title, summary, quality,
                              use_count, created_at
                       FROM v7_lessons
                       WHERE status='active'
                         AND (period=? OR cycle=? OR cycle='X')
                       ORDER BY id DESC""",
                    (period, _cycle),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, kind, cycle, period, title, summary, quality,
                              use_count, created_at
                       FROM v7_lessons
                       WHERE status='active'
                         AND (period=? OR cycle=? OR cycle='X')
                       ORDER BY id DESC LIMIT 200""",
                    (period, _cycle),
                ).fetchall()
                if len(rows) >= 200:
                    try:
                        total = conn.execute(
                            """SELECT COUNT(*) FROM v7_lessons
                               WHERE status='active'
                                 AND (period=? OR cycle=? OR cycle='X')""",
                            (period, _cycle)).fetchone()[0]
                    except Exception:
                        total = len(rows)
                    _warn_codegen_window_saturated(period, _cycle, total)
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
            "trajectory": "挖掘轨迹",
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
