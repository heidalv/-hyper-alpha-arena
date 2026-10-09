# -*- coding: utf-8 -*-
"""[F345 2026-09-18] 学习 → 策略 **读回路**（唯一入口；默认按车道；读必留痕）。

## 为什么需要这个模块（报告 §15 的病根）

复查确认「学习系统与策略割裂」不是修辞，是三条可复现的代码事实：

1. **两套学习各读各的**：真正下单的中长线脑（`mlto/brain.py build_feed`）只读它自己那套
   （`reflexion_memory` / `similar_episodes` / `backtest_wisdom` / `consolidated_lessons`），
   对 **v7 教训池 / 因子运行时权重 / 衰减退役** 的读取点为 0。
2. **唯一的交易侧读点是残的**：`trading_analysts._build_v7_lessons_block` 的
   `kind IN ('gate_lesson','decay_case','failure_case','success_recipe')` 把
   `trajectory`/`pipeline_issue` 两类**结构性排除**——实测 810 条 active 里 459 条（56.7%）
   永远不可达；且打分 `quality + 0.5*recency` 对同期条目**饱和**（全部 = 1.45），
   排序退化为「id 倒序」，实测 top4 恒为 4 条最新 `success_recipe`（每次决策拿到同样的 4 行）。
3. **读了不算读**：上述读点用 `mode=ro` 打开、**不写 `use_count`**，而
   `evolution_memory_v7.maintenance()` 恰恰按 `use_count=0 AND 30 天` 退役教训
   ⇒ **被真读过的教训反而会被当垃圾清掉**（实测 active 中 751/810 = 92.7% 仍为 0 次）。
   "只写不读"因此在任何读数上都成立，即使读取发生过。

## 本模块做什么

把「读」做成一个**可开关、分层、留痕、可验收**的动作：

- **分层配额检索**（kind 配额 + 余量补齐）⇒ 杜绝单一 kind 垄断；
- **签名去重**（标题数字归一后去重）⇒ 不再让 2 条「15m 全量拒绝: N 候选 0 晋升」占位；
- **读取即留痕**：`use_count+1` / `last_used_at` / `v7_retrieval_log` 追加 `[lane]` 前缀行
  ⇒ 「写必有读」从口号变成可复跑的读数（`stats()`、`scripts/audit_learning_gap.py` ⑥）；
- **窗口不再截断历史**：全量 active 参与打分（810 行量级，带 120s 进程内缓存），
  不再 `ORDER BY id DESC LIMIT 200`；
- **失败绝不打断决策**：任何异常降级为空串；但**关闸与异常必须留日志**（纪律 A6 禁止静默退化）。

## 开关语义（关键：不制造静默退化）

`LEARNING_READBACK_ENABLED`：

| 取值 | 语义 |
|---|---|
| `1/true/yes/on` | **所有**车道启用 |
| `0/false/no/off` | **所有**车道关闭 |
| 未设 / `auto` | **保持各车道既有语义**：`master` 沿用 `V7_LESSONS_IN_MASTER`（默认 true，即现状不变）；`mlto` 等新车道默认**关**，待用户批准后置 1 |

即：本模块合入后**默认不改变任何实盘行为**；`mlto` 车道从"从未调用学习"变成
"已接线、一行 env 即可生效"，且关着的时候也留痕说明"为什么没有读"。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ROOT = _BACKEND_DIR.parent

# 允许测试/运维用 env 覆盖（测试用 tmp 库，绝不动真实库）
_DEFAULT_V7_DB = _BACKEND_DIR / "data" / "factor_evolution_memory_v7.db"
_DECAY_STATUS_PATH = _ROOT / "data" / "factor_decay_status.json"
_WEIGHTS_PATH = _ROOT / "data" / "factor_runtime_weights.json"
#: [F360 2026-09-18] 回测侧归因产物（B4 phase 2 覆盖度 + 增量消融）。本模块**只读**：
#: 目的是把「回测测出来的因子战绩」送进**决策上下文**，使 回测 ↔ 学习 ↔ 决策 形成回路，
#: 而不是只躺在周报里（设计 §6.4 要求回测归因与线上逐单归因**并列**，不可互相替代）。
_COUNTERFACTUAL_PATH = _ROOT / "data" / "backtest_counterfactual.jsonl"

ENV_ENABLED = "LEARNING_READBACK_ENABLED"
ENV_DEDUPE_S = "LEARNING_READBACK_DEDUPE_SECONDS"
ENV_CHAR_BUDGET = "LEARNING_READBACK_CHARS"

#: 生产可选的教训种类（含此前被结构性排除的两类）
ALL_KINDS: Sequence[str] = (
    "trajectory", "gate_lesson", "decay_case",
    "failure_case", "pipeline_issue", "success_recipe",
)

#: 每 kind 默认配额（合计 7；`limit` 小于合计时按分补齐）
DEFAULT_QUOTA: Dict[str, int] = {
    "trajectory": 1,
    "gate_lesson": 2,
    "decay_case": 1,
    "failure_case": 1,
    "pipeline_issue": 1,
    "success_recipe": 1,   # 降权：实测它垄断旧读点的 top4
}

KIND_LABEL: Dict[str, str] = {
    "success_recipe": "成功配方",
    "failure_case": "失败案例",
    "gate_lesson": "门禁教训",
    "decay_case": "衰退案例",
    "pipeline_issue": "链路问题",
    "trajectory": "挖掘轨迹",
}

_LOG_ONCE: set = set()
_LOG_ONCE_GUARD = threading.Lock()
_SEEN: Dict[Any, float] = {}
_SEEN_GUARD = threading.Lock()
_POOL_CACHE: Dict[Any, Any] = {}
_POOL_CACHE_GUARD = threading.Lock()


# ───────────────────────────── 基础工具 ─────────────────────────────

def _log_once(key: str, level: int, msg: str, *args: Any) -> None:
    with _LOG_ONCE_GUARD:
        if key in _LOG_ONCE:
            return
        _LOG_ONCE.add(key)
    logger.log(level, msg, *args)


def _truthy(v: Optional[str]) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _falsy(v: Optional[str]) -> bool:
    return str(v or "").strip().lower() in ("0", "false", "no", "off")


def v7_db_path() -> Path:
    """v7 记忆库路径（每次调用读 env，便于测试与运维切换）。"""
    return Path(os.getenv("V7_MEMORY_DB_PATH", str(_DEFAULT_V7_DB)))


def readback_mode() -> str:
    v = str(os.getenv(ENV_ENABLED, "auto") or "auto").strip().lower()
    if _truthy(v):
        return "on"
    if _falsy(v):
        return "off"
    return "auto"


def lane_enabled(lane: str) -> bool:
    """本车道是否允许读学习产物。默认 **不改变任何既有行为**。"""
    mode = readback_mode()
    if mode == "on":
        return True
    if mode == "off":
        return False
    key = str(lane or "").strip().lower()
    if key == "master":
        # 既有开关，默认 true ⇒ 合入本模块不改变 master 车道现状
        return _truthy(os.getenv("V7_LESSONS_IN_MASTER", "true"))
    # 新车道默认关：接线已就位，等用户批准后置 LEARNING_READBACK_ENABLED=1
    _log_once(
        f"disabled:{key}", logging.INFO,
        "[LearningReadback] lane=%s 读回路未启用（%s=auto 且该车道无既有开关）；"
        "置 %s=1 启用。", key, ENV_ENABLED, ENV_ENABLED,
    )
    return False


def _dedupe_seconds() -> float:
    try:
        return max(0.0, float(os.getenv(ENV_DEDUPE_S, "21600") or 21600))  # 默认 6h
    except Exception:
        return 21600.0


def _char_budget(default: int = 2400) -> int:
    try:
        return max(200, int(os.getenv(ENV_CHAR_BUDGET, str(default)) or default))
    except Exception:
        return default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sig(title: str) -> str:
    """标题签名：去掉数字与空白，用于去重同质教训。

    实测 v7 池里 153 条 gate_lesson 高度同质（`15m 全量拒绝: 137 候选 0 晋升`），
    只差数字；不去重时分层配额会被同一模板的 2 条占满。
    """
    import re
    return re.sub(r"[\d\s%．.]+", "", str(title or ""))[:80] or str(title or "")[:80]


def _score(row: Dict[str, Any]) -> float:
    """质量 + 少量使用次数增益 + 新鲜度。recency 权重下调（0.5→0.35）避免饱和垄断。"""
    q = float(row.get("quality") or 0.5)
    try:
        created = datetime.fromisoformat(str(row.get("created_at") or ""))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_d = max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 86400.0)
    except Exception:
        age_d = 30.0
    import math
    return q + 0.15 * math.log1p(int(row.get("use_count") or 0)) + 0.35 * (0.5 ** (age_d / 14.0))


# ───────────────────────────── 取候选池 ─────────────────────────────

def load_active_pool(*, cycle: Optional[str] = None, period: Optional[str] = None,
                     cache_ttl: float = 120.0) -> List[Dict[str, Any]]:
    """读取全部 active 教训（不再 `ORDER BY id DESC LIMIT 200` 截断历史）。

    只读打开；库缺失/表缺失/锁定 ⇒ 返回 []（并留一次日志）。
    """
    path = v7_db_path()
    if not path.exists():
        _log_once("nodbfile", logging.WARNING,
                  "[LearningReadback] v7 记忆库不存在，读回路返回空: %s", path)
        return []
    ck = (str(path), cycle, period)
    now = time.time()
    with _POOL_CACHE_GUARD:
        hit = _POOL_CACHE.get(ck)
        if hit and (now - hit[0]) <= cache_ttl:
            return hit[1]

    sql = ("SELECT id, kind, cycle, period, title, summary, quality, use_count, created_at "
           "FROM v7_lessons WHERE status='active'")
    params: List[Any] = []
    if cycle:
        sql += " AND cycle=?"
        params.append(str(cycle))
    if period:
        sql += " AND period=?"
        params.append(str(period))
    rows: List[Dict[str, Any]] = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            for r in con.execute(sql, params).fetchall():
                rows.append({
                    "id": r[0], "kind": r[1], "cycle": r[2], "period": r[3],
                    "title": r[4], "summary": r[5], "quality": r[6],
                    "use_count": r[7], "created_at": r[8],
                })
        finally:
            con.close()
    except Exception as exc:
        _log_once("poolerr", logging.WARNING,
                  "[LearningReadback] 读取 v7 教训池失败（读回路空转）: %s", str(exc)[:160])
        return []
    with _POOL_CACHE_GUARD:
        _POOL_CACHE[ck] = (now, rows)
    return rows


def select_lessons(pool: Sequence[Dict[str, Any]], *, limit: int = 6,
                   quota: Optional[Dict[str, int]] = None,
                   dedupe_signature: bool = True) -> List[Dict[str, Any]]:
    """分层配额选择：每 kind 至多 quota[kind] 条，余量按分补齐；纯函数，可单测。"""
    q = dict(DEFAULT_QUOTA if quota is None else quota)
    lim = max(1, int(limit))
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for row in pool:
        by_kind.setdefault(str(row.get("kind") or ""), []).append(row)
    for k in by_kind:
        by_kind[k].sort(key=_score, reverse=True)

    picked: List[Dict[str, Any]] = []
    seen_ids: set = set()
    seen_sigs: set = set()

    def _take(row: Dict[str, Any]) -> bool:
        rid = row.get("id")
        if rid in seen_ids:
            return False
        sg = _sig(row.get("title") or "")
        if dedupe_signature and sg in seen_sigs:
            return False
        seen_ids.add(rid)
        seen_sigs.add(sg)
        picked.append(row)
        return True

    for kind in ALL_KINDS:
        cap = int(q.get(kind, 1))
        if cap <= 0:
            continue
        taken = 0
        for row in by_kind.get(kind, []):
            if taken >= cap or len(picked) >= lim:
                break
            if _take(row):
                taken += 1

    if len(picked) < lim:
        # [F345 修正] 余量补齐必须**偏向被选得最少的 kind**（round-robin），
        # 否则池子被单一 kind 主导时，配额只是"前 N 条"的装饰，垄断照旧。
        leftovers = [r for r in pool if r.get("id") not in seen_ids]
        kind_count: Dict[str, int] = {}
        for row in picked:
            k = str(row.get("kind") or "")
            kind_count[k] = kind_count.get(k, 0) + 1
        leftovers.sort(key=lambda r: (kind_count.get(str(r.get("kind") or ""), 0), -_score(r)))
        for row in leftovers:
            if len(picked) >= lim:
                break
            if _take(row):
                k = str(row.get("kind") or "")
                kind_count[k] = kind_count.get(k, 0) + 1
    return picked[:lim]


# ───────────────────────────── 读取留痕（写必有读） ─────────────────────────────

def record_uses(ids: Sequence[int], *, lane: str, cycle: str = "", period: str = "",
                query: Optional[str] = None) -> int:
    """把"本次被读取"写进 v7：`use_count+1` / `last_used_at` / `v7_retrieval_log`。

    进程内去重（同 lane 同 id，默认 6h 一次）——决策循环 45s 一轮，不去重会把
    次数刷成噪声。写入是**尽力而为**：失败只留一次日志，绝不打断决策。
    """
    path = v7_db_path()
    if not path.exists() or not ids:
        return 0
    ttl = _dedupe_seconds()
    now = time.time()
    fresh: List[int] = []
    with _SEEN_GUARD:
        for i in ids:
            key = (str(lane), int(i))
            last = _SEEN.get(key, 0.0)
            if now - last < ttl:
                continue
            _SEEN[key] = now
            fresh.append(int(i))
    if not fresh:
        return 0
    q = query or f"[{lane}] 决策读回"
    ts = _now_iso()
    done = 0
    try:
        con = sqlite3.connect(str(path), timeout=10)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            for i in fresh:
                con.execute("UPDATE v7_lessons SET use_count=use_count+1, last_used_at=? WHERE id=?",
                            (ts, i))
                con.execute(
                    "INSERT INTO v7_retrieval_log (created_at, query, cycle, period, top_ids_json) "
                    "VALUES (?,?,?,?,?)",
                    (ts, q, cycle or None, period or None, json.dumps([i])),
                )
                done += 1
            con.commit()
        finally:
            con.close()
    except Exception as exc:
        _log_once("recorderr", logging.WARNING,
                  "[LearningReadback] 读取留痕失败（use_count 未累加）: %s", str(exc)[:160])
        return 0
    return done


def retrieve_lessons(*, lane: str, cycle: Optional[str] = None, period: Optional[str] = None,
                     limit: int = 6, quota: Optional[Dict[str, int]] = None,
                     mark_used: bool = True) -> List[Dict[str, Any]]:
    """分层检索 + 留痕。返回选中的教训（空 = 池空/库不可用）。"""
    pool = load_active_pool(cycle=cycle, period=period)
    if not pool:
        return []
    picked = select_lessons(pool, limit=limit, quota=quota)
    if mark_used and picked:
        record_uses([r["id"] for r in picked], lane=lane,
                    cycle=str(cycle or (picked[0].get("cycle") or "")),
                    period=str(period or (picked[0].get("period") or "")))
    return picked


# ───────────────────────────── 因子侧学习产物快照 ─────────────────────────────

def _inc_group_key(name: str) -> str:
    """增量归因里的"同一底层因子"归一键：去掉 `group:` 与 `evo_`/`evo_s5m_` 前缀。"""
    s = str(name or "")
    if s.startswith("group:"):
        s = s[len("group:"):]
    for pre in ("evo_s5m_", "evo_"):
        if s.startswith(pre):
            return s[len(pre):]
    return s


def backtest_attr_snapshot(*, top_n: int = 4) -> Dict[str, Any]:
    """[F360] 回测侧因子战绩快照（**只读**；两类口径分别标注，绝不混为一谈）。

    1. **覆盖度归因**（`data/backtest_factor_attr.jsonl`，B4 phase 2）：
       某因子在开仓 bar 活跃的那批交易的盈亏。**不是增量 alpha**（无对照/无反事实）。
    2. **增量（反事实）归因**（`data/backtest_counterfactual.jsonl`，OFAT 消融）：
       "去掉该因子后结果变化多少"。**各因子增量不相加等于总收益**（有交互），
       且**单窗口单参数**，只能用于排序/方向判断。

    关键的安全设计：增量记录若**缺少 `funding_fgi.valid` 标记**（或标记为 False），
    说明那次消融是在"因子维度结构性失效"（F355 假零）的配置下跑的 ——
    此时**全部增量恒为 0**，把它当真数据会得出"所有因子无贡献"的错误结论。
    本函数遇到这种记录会置 `incremental.trusted=False` 并给出原因，由渲染层显式提示。
    """
    out: Dict[str, Any] = {"role": "证据，非指令"}

    # ① 覆盖度归因（最近一次回测）
    try:
        from backend.services.live_pipeline_backtest_engine import load_factor_attr
        recs = load_factor_attr(limit=3)
        if recs:
            last = recs[-1]
            by = last.get("by_name") or {}
            rows = sorted(
                ((str(k), v) for k, v in by.items() if isinstance(v, dict)),
                key=lambda kv: float(kv[1].get("coverage") or 0.0), reverse=True,
            )
            out["coverage"] = {
                "available": True,
                "runs": len(recs),
                "run_id": last.get("run_id"), "symbol": last.get("symbol"),
                "tier": last.get("tier"),
                "n_factors": len(rows),
                "top": [{"factor": k, "coverage": v.get("coverage"),
                         "n_long": v.get("n_long"), "n_short": v.get("n_short"),
                         "avg_bp_long": v.get("avg_bp_long"),
                         "avg_bp_short": v.get("avg_bp_short")} for k, v in rows[:top_n]],
                "caveat": "覆盖度归因（该因子活跃时的交易盈亏），非增量 alpha",
            }
        else:
            out["coverage"] = {"available": False,
                               "note": "无回测归因记录（需 BACKTEST_FACTOR_ATTR=1 跑一次回测）"}
    except Exception as exc:
        out["coverage"] = {"available": False, "error": str(exc)[:120]}

    # ② 增量（反事实）归因（最近一次消融）
    try:
        path = Path(_COUNTERFACTUAL_PATH)
        if not path.exists():
            out["incremental"] = {"available": False, "note": "无增量消融记录"}
        else:
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if not lines:
                out["incremental"] = {"available": False, "note": "增量消融文件为空"}
            else:
                rec = json.loads(lines[-1])
                rows = [r for r in (rec.get("incremental") or []) if r.get("available")]
                rows.sort(key=lambda r: float(r.get("d_sum_pnl") or 0.0), reverse=True)
                # 去重：同一底层因子会以 `evo_<hash>` / `evo_s5m_<hash>` 两个名字各出现一次，
                # 消融脚本还会补一条 `group:<hash>` 聚合行 ⇒ 不去重时前三行是同一个因子（噪声）。
                # 优先保留 `group:` 行（它就是"整组剔除"的结论），其余同名只留第一条。
                _seen: Dict[str, Dict[str, Any]] = {}
                for r in rows:
                    _g = _inc_group_key(str(r.get("factor") or ""))
                    if _g not in _seen or str(r.get("factor") or "").startswith("group:"):
                        _seen[_g] = r
                deduped = sorted(_seen.values(),
                                 key=lambda r: float(r.get("d_sum_pnl") or 0.0), reverse=True)
                ff = rec.get("funding_fgi") or {}
                trusted = bool(ff.get("valid"))
                out["incremental"] = {
                    "available": bool(deduped),
                    "trusted": trusted,
                    "trust_note": (None if trusted else
                                   "该记录缺少 funding_fgi.valid 标记 ⇒ 疑似因子维度结构性失效"
                                   "（F355 假零），全部增量恒 0，**不可据此判断因子无贡献**"),
                    "symbol": rec.get("symbol"), "timeframe": rec.get("timeframe"),
                    "days": rec.get("days"), "tier": rec.get("tier"),
                    "baseline_sum_pnl": (rec.get("baseline") or {}).get("sum_pnl"),
                    "top": [{"factor": r.get("factor"), "d_sum_pnl": r.get("d_sum_pnl"),
                             "d_trades": r.get("d_trades")} for r in deduped[:top_n]],
                    "bottom": [{"factor": r.get("factor"), "d_sum_pnl": r.get("d_sum_pnl"),
                                "d_trades": r.get("d_trades")} for r in deduped[-top_n:]],
                    "caveat": "OFAT 消融：增量不相加等于总收益；单窗口/单参数，仅用于排序与方向判断",
                }
    except Exception as exc:
        out["incremental"] = {"available": False, "error": str(exc)[:120]}

    return out


def factor_system_snapshot(*, top_n: int = 6) -> Dict[str, Any]:
    """因子运行时权重 + 衰减/退役状态的**只读**快照（供决策 prompt 参考）。

    这两样都是"学习产物"，却从未被任何决策车道读取（报告 §15.1）。
    """
    snap: Dict[str, Any] = {"role": "证据，非指令"}
    try:
        from backend.services.factor_ic_evaluator import load_runtime_factor_weights
        w = load_runtime_factor_weights() or {}
        vals = {str(k): float(v) for k, v in w.items() if isinstance(v, (int, float))}
        zero = sorted([k for k, v in vals.items() if v == 0.0])
        neg = sorted([k for k, v in vals.items() if v < 0.0])
        snap["runtime_weights"] = {
            "n": len(vals),
            "n_zero": len(zero),
            "n_negative": len(neg),
            "zero_sample": zero[:top_n],
        }
        # [F346] **两个口径必须都报**：读回口径被 `max(0.1, ...)` 夹逼，
        # 文件里写着 0（= 学习层判定「IC<=0 不参与合成」）的因子读回来是 0.1+。
        # 只报读回口径会得出"零权重 0 个"的假读数，与同一段里的 retire=62 自相矛盾。
        try:
            raw = json.loads(_WEIGHTS_PATH.read_text(encoding="utf-8"))
            fw = {str(k): float(v or 0) for k, v in (raw.get("weights") or {}).items()}
            fzero = sorted([k for k, v in fw.items() if v == 0.0])
            snap["runtime_weights"].update({
                "n_file": len(fw),
                "n_file_zero": len(fzero),
                "file_zero_sample": fzero[:top_n],
            })
        except Exception:
            pass
    except Exception as exc:
        snap["runtime_weights"] = {"error": str(exc)[:120]}
    try:
        if _DECAY_STATUS_PATH.exists():
            raw = json.loads(_DECAY_STATUS_PATH.read_text(encoding="utf-8"))
            # [F345 修正] 真实结构是 {"_meta","status","ic_history"}（128 条在 `status` 里）。
            # 冒烟时按 `factors` 解析 ⇒ 只数出 3 个 unknown，读数完全是假的。
            factors: Any = None
            if isinstance(raw, dict):
                for _key in ("status", "factors"):
                    _v = raw.get(_key)
                    if isinstance(_v, dict) and _v:
                        factors = _v
                        break
                if factors is None:
                    factors = raw
            else:
                factors = raw
            recs: Dict[str, int] = {}
            names: Dict[str, List[str]] = {}
            if isinstance(factors, dict):
                for fid, v in factors.items():
                    r = str((v or {}).get("recommendation") or "unknown") if isinstance(v, dict) else "unknown"
                    recs[r] = recs.get(r, 0) + 1
                    if r in ("retire", "reduce"):
                        names.setdefault(r, []).append(str(fid))
            snap["decay"] = {
                "n": sum(recs.values()),
                "by_recommendation": recs,
                "retire_sample": sorted(names.get("retire", []))[:top_n],
                "reduce_sample": sorted(names.get("reduce", []))[:top_n],
            }
        else:
            snap["decay"] = {"n": 0, "note": "无衰减状态文件"}
    except Exception as exc:
        snap["decay"] = {"error": str(exc)[:120]}
    # [F360] 回测侧因子战绩（与上面的"线上/权治"产物并列；口径不同，不得合并）
    try:
        snap["backtest_attr"] = backtest_attr_snapshot(top_n=min(4, max(2, top_n // 2)))
    except Exception as exc:
        snap["backtest_attr"] = {"error": str(exc)[:120]}
    return snap


# ───────────────────────────── 组装给 LLM 的文本块 ─────────────────────────────

def format_for_prompt(lessons: Sequence[Dict[str, Any]], snapshot: Optional[Dict[str, Any]] = None,
                      *, char_budget: Optional[int] = None) -> str:
    """把教训 + 因子快照压成紧凑文本，**硬上限**并在截断处显式标注。"""
    budget = int(char_budget or _char_budget())
    lines: List[str] = []
    if lessons:
        lines.append("### 🧠 因子体系硬指标教训（V7 记忆池，分层抽样；供决策参考）")
        for r in lessons:
            label = KIND_LABEL.get(str(r.get("kind")), str(r.get("kind")))
            lines.append(
                f"- [{label}|{r.get('cycle')}|{r.get('period')}] "
                f"{str(r.get('title') or '')[:64]}: {str(r.get('summary') or '')[:110]}"
            )
    snap = snapshot or {}
    rw = snap.get("runtime_weights") or {}
    if rw and not rw.get("error"):
        lines.append("")
        lines.append("### ⚖️ 因子运行时权重/治理现状（学习侧产物，供参考）")
        lines.append(
            f"- 运行时权重条目 {rw.get('n', 0)}（读回口径；其中 0 值 {rw.get('n_zero', 0)}、"
            f"负值 {rw.get('n_negative', 0)}）"
        )
        _nfz = int(rw.get("n_file_zero") or 0)
        if _nfz:
            lines.append(
                f"- ⚠️ 真源不一致：权重文件里有 **{_nfz}** 个因子被学习层写成 0"
                f"（语义= IC≤0，**不参与合成**），但读回时被夹逼到 ≥0.1"
                f"⇒ 它们仍以低权重参与。样例：{', '.join(rw.get('file_zero_sample') or [])}"
            )
    dec = snap.get("decay") or {}
    if dec and not dec.get("error") and dec.get("n"):
        by = dec.get("by_recommendation") or {}
        pretty = ", ".join(f"{k}={v}" for k, v in sorted(by.items()))
        lines.append(f"- 衰减/退役裁定：{pretty}")
        if dec.get("retire_sample"):
            lines.append(f"  已判退役样例（其信号不应再被当有效证据）：{', '.join(dec['retire_sample'])}")

    # [F360] 回测侧因子战绩（与线上归因并列；两类口径分别标注；假零必须显式提示）
    ba = snap.get("backtest_attr") or {}
    cov = ba.get("coverage") or {}
    inc = ba.get("incremental") or {}
    if cov.get("available") or inc.get("available") or inc.get("trust_note"):
        lines.append("")
        lines.append("### 🔬 回测侧因子战绩（与线上逐单归因**并列**，口径不同、不可互相替代）")
    if cov.get("available"):
        _c = cov.get("top") or []
        _txt = "；".join(
            f"{x['factor']}(覆盖{x.get('coverage')}, 多{x.get('n_long')}/空{x.get('n_short')}, "
            f"多头均价{x.get('avg_bp_long')}bp)" for x in _c[:3])
        lines.append(f"- 覆盖度归因（{cov.get('symbol')}/{cov.get('tier')}，"
                     f"{cov.get('n_factors')} 个因子）：{_txt}")
        lines.append(f"  口径：{cov.get('caveat')}")
    if inc.get("available"):
        _i = inc.get("top") or []
        _txt = "；".join(f"{x['factor']}(ΔPnL={x.get('d_sum_pnl')}, Δ笔数={x.get('d_trades')})"
                         for x in _i[:3])
        lines.append(f"- 增量（反事实）归因（{inc.get('symbol')}/{inc.get('timeframe')}"
                     f"/{inc.get('days')}d，基线 PnL={inc.get('baseline_sum_pnl')}）：{_txt}")
        lines.append(f"  口径：{inc.get('caveat')}")
    if inc.get("trust_note"):
        # 假零必须显式提示：否则"所有因子增量为 0"会被读成"因子都没用"（F355）
        lines.append(f"- ⚠️ 增量记录**不可信**：{inc.get('trust_note')}")

    text = "\n".join(lines).strip()
    if len(text) > budget:
        text = text[:budget] + "…（截断）"
    return text


def decision_block(*, lane: str, symbol: Optional[str] = None, period: Optional[str] = None,
                   limit: int = 6, quota: Optional[Dict[str, int]] = None,
                   with_snapshot: bool = True) -> str:
    """决策车道的一次调用入口。关闸 ⇒ ""（并留一次可见日志，不静默）。"""
    if not lane_enabled(lane):
        return ""
    try:
        lessons = retrieve_lessons(lane=lane, period=period, limit=limit, quota=quota)
        snap = factor_system_snapshot() if with_snapshot else None
        block = format_for_prompt(lessons, snap)
        if not block:
            _log_once("emptyblock", logging.WARNING,
                      "[LearningReadback] lane=%s 读回路为空（池空或库不可用）", lane)
        return block
    except Exception as exc:
        _log_once("blockerr", logging.WARNING,
                  "[LearningReadback] lane=%s 读回路构建失败（跳过）: %s", lane, str(exc)[:160])
        return ""


# ───────────────────────────── 可验收读数 ─────────────────────────────

def stats() -> Dict[str, Any]:
    """读回路体检读数（供 `scripts/audit_learning_gap.py` ⑥ 与运维）。

    关键指标：`used_active`（active 中被读过至少一次的条数）与其占比——
    合入前实测 810 条里仅 59 条 >0（7.3%），且那 59 条来自 **codegen** 路径；
    交易侧读取此前完全不计次。
    """
    out: Dict[str, Any] = {"mode": readback_mode(), "db": str(v7_db_path())}
    path = v7_db_path()
    if not path.exists():
        out["error"] = "v7 库不存在"
        return out
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            out["active"] = con.execute(
                "SELECT COUNT(*) FROM v7_lessons WHERE status='active'").fetchone()[0]
            out["used_active"] = con.execute(
                "SELECT COUNT(*) FROM v7_lessons WHERE status='active' AND COALESCE(use_count,0)>0"
            ).fetchone()[0]
            out["by_kind"] = [
                {"kind": r[0], "n": r[1], "used": r[2]}
                for r in con.execute(
                    "SELECT kind, COUNT(*), SUM(CASE WHEN COALESCE(use_count,0)>0 THEN 1 ELSE 0 END) "
                    "FROM v7_lessons WHERE status='active' GROUP BY kind ORDER BY 2 DESC").fetchall()
            ]
            lanes: Dict[str, int] = {}
            for q, n in con.execute(
                    "SELECT query, COUNT(*) FROM v7_retrieval_log "
                    "WHERE query LIKE '[%' GROUP BY query ORDER BY 2 DESC LIMIT 20").fetchall():
                lanes[str(q)] = n
            out["reads_by_lane"] = lanes
            row = con.execute("SELECT MAX(created_at) FROM v7_retrieval_log").fetchone()
            out["last_read_at"] = row[0] if row else None
        finally:
            con.close()
    except Exception as exc:
        out["error"] = str(exc)[:160]
    return out


def main() -> int:  # pragma: no cover - 手工体检入口
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    print(json.dumps(stats(), ensure_ascii=False, indent=2, default=str))
    print("\n--- 样例决策块（lane=master, 不写库） ---")
    pool = load_active_pool()
    picked = select_lessons(pool, limit=6)
    print(format_for_prompt(picked, factor_system_snapshot()))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
