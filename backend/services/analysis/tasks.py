# -*- coding: utf-8 -*-
"""深度分析任务族（v3 方向 2，p1-deep-analysis，2026-09-03）。

全部经 ModelGateway.dual_call：盲评 → 比对 → 分歧仲裁 → consensus_score。
只有 accepted（score≥0.7 且 status=ok）的结论才入 signal_ledger / experiments。

任务：
  run_daily_brief()      日度 regime + 风险简报 + 桶权重建议 → 信号 + latest JSON
  run_weekly_review()    周度车道复盘 → 实验卡（proposed）
  run_timing()           周度引擎资本建议（反事实栏）→ latest JSON
  run_event_impact(...)  单事件冲击评估 → 信号（含避险窗口 meta）
  scan_and_eval_events() 扫描近窗高严重度未评估事件，按日预算批量评估

可信度：
  model_task_credibility()  模型×任务类型矩阵（来自 signal_ledger 命中/Brier + 共识分）
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from backend.services.analysis import ledgers, schemas
from backend.services.analysis.context_pack import build
from backend.services.analysis.model_gateway import ConsensusResult, get_model_gateway

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "analysis"
CONSENSUS_THRESHOLD = 0.7

# 事件扫描：这些类型进入 event_impact 评估队列
_EVENT_EVAL_TYPES = (
    "announcement.listing", "announcement.futures_listing", "announcement.delisting",
    "announcement.monitoring_tag", "liquidation.cascade", "liquidation.market_cascade",
    "funding.extreme", "position.oi_jump", "news.high_impact", "whale.large",
    "macro.scheduled", "macro.released",
)

_SYSTEM_BASE = (
    "你是 Hyper-Alpha-Arena 的量化投研分析员。"
    "只依据 context pack 中的事实作答，禁止编造未给出的价格、资金费、持仓或绩效数字；"
    "若某层缺失，在 summary / risks 中明确写出「数据不足」。"
    "输出必须是单个 JSON 对象，不要 Markdown 代码块。"
)

_TASK_HINTS = {
    "daily_brief": (
        "任务：日度 regime 与风险简报。"
        "给出当前市场 regime、三桶（trend/cashflow/research）权重建议、核心币 24h 方向视图与主要风险。"
        "bucket_weights 之和须 ≈1；symbol_views 只覆盖 context 里出现的币。"
    ),
    "weekly_review": (
        "任务：周度复盘。"
        "按车道/策略给出 keep|scale_up|scale_down|shadow|stop 评估，并产出可执行的实验卡列表"
        "（假设 / 改动 change{} / expected_metrics[] / window_hours / rollback_condition）。"
        "实验卡必须具体到可落地的 config 键或开关，禁止散文式建议。"
    ),
    "timing": (
        "任务：策略择时。"
        "给出 regime 与三桶权重，以及对 E1/E2/E5/E3 的资本建议；"
        "若有上周建议，在 counterfactual 中估算若照做的净 bp（没有则填 0 并说明）。"
    ),
    "event_impact": (
        "任务：单事件冲击评估。"
        "根据「待评估事件」与历史同类上下文，给出方向、强度、半衰期、受影响币、避险窗口小时数、"
        "以及持仓调节（none|no_new_long|no_new_short|reduce|tighten_stops|hedge）。"
        "不确定时降低 confidence，不要硬猜。"
    ),
    "trend_chart_review": (
        "任务：多模态趋势图审（K线视觉分析）。"
        "消息附有目标币种的多周期K线图（1w/1d/4h，蜡烛+EMA9/21+成交量）。"
        "从图中判断：趋势结构与所处阶段、三周期共振/背离、关键支撑阻力形态、量价配合。"
        "【硬约束】图只用于结构/形态/相对位置判断；精确价格、指标数值一律以 context pack 文本为准，"
        "禁止直接读图上的数字；key_levels 的 level 值从 context pack 的价格数据里取。"
        "结论必须同时给出 invalidation（作废条件）与 position_advice。"
    ),
}


def _env_true(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except Exception:
        return default


def tasks_enabled() -> bool:
    return _env_true("ANALYSIS_TASKS_ENABLED", True)


def _ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def _write_latest(name: str, payload: Dict[str, Any]) -> str:
    path = _ensure_data_dir() / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(path)


def _horizon_ms(hours: float, *, default: float = 24.0) -> int:
    try:
        h = float(hours)
    except Exception:
        h = default
    h = max(1.0, min(720.0, h))
    return int(h * 3600 * 1000)


def _norm_symbol(sym: Any) -> str:
    s = str(sym or "").upper().strip().replace("/", "").replace("-", "")
    if s.endswith("PERP"):
        s = s[:-4]
    if s.endswith("USDT"):
        s = s[:-4]
    return s[:16]


# --------------------------------------------------------------------------- dual runner
def _build_prompts(task: str, pack_text: str, *, event_block: str = "") -> tuple[str, str]:
    system = f"{_SYSTEM_BASE}\n{_TASK_HINTS.get(task, '')}\n{schemas.output_contract(task)}"
    parts = [f"【任务】{task}"]
    if event_block:
        parts.append(event_block)
    parts.append("【context pack】\n" + pack_text)
    return system, "\n\n".join(parts)


def run_dual_task(
    task: str,
    *,
    symbols: Optional[Sequence[str]] = None,
    layers: Optional[Sequence[str]] = None,
    events_hours: float = 24.0,
    event_block: str = "",
    event_meta: Optional[Dict[str, Any]] = None,
    max_output_tokens: Optional[int] = None,
    timeout_s: float = 240.0,
    dry_run: bool = False,
    ingest: bool = True,
    images: Optional[List[Dict[str, str]]] = None,
    images_for: Optional[List[str]] = None,
    ingest_symbol: Optional[str] = None,
) -> Dict[str, Any]:
    """通用双模型任务：构建 pack → dual_call →（可选）入账。

    images：多模态图包（chart_service 产出），透传给支持视觉的传输（P2 2026-09-05）。
    images_for：图只发给列表内传输（None=全发；图审任务只给 GLM，MiniMax 纯文本票）。
    ingest_symbol：信号/预测入账的币种覆盖（默认 BTC，保持旧行为）。
    """
    t0 = time.time()
    pack = build(task, symbols=symbols, layers=layers, events_hours=events_hours)
    if event_meta:
        # 把待评估事件钉进 layers，保证 hash 可复现且模型看得见
        pack.layers.setdefault("flows", {})
        pack.layers["flows"]["focus_event"] = event_meta
    system, user = _build_prompts(task, pack.to_prompt_text(40000), event_block=event_block)
    gw = get_model_gateway()
    cres = gw.dual_call(
        task, system, user,
        max_output_tokens=max_output_tokens,
        timeout_s=timeout_s,
        context_hash=pack.hash,
        data_cutoff_ms=pack.data_cutoff_ms,
        context_pack=pack.to_dict(),
        images=images,
        images_for=images_for,
    )
    out: Dict[str, Any] = {
        "task": task,
        "ok": cres.status != "skipped",
        "status": cres.status,
        "accepted": cres.accepted,
        "consensus_score": cres.consensus_score,
        "consensus_group": cres.consensus_group,
        "run_id": cres.run_id,
        "context_hash": cres.context_hash or pack.hash,
        "data_cutoff_ms": pack.data_cutoff_ms,
        "final": cres.final,
        "comparison": cres.comparison,
        "notes": list(cres.notes),
        "elapsed_sec": round(time.time() - t0, 2),
        "pack_errors": list(pack.errors),
        "signals": [],
        "experiments": [],
        "predictions": [],
        "latest_path": None,
        "dry_run": bool(dry_run),
    }
    if dry_run or not ingest:
        return out
    try:
        ingested = ingest_consensus(task, cres, pack, event_meta=event_meta, symbol=ingest_symbol)
        out.update(ingested)
    except Exception as exc:
        logger.warning("[analysis.tasks] ingest %s 失败: %s", task, exc)
        out["ingest_error"] = str(exc)[:240]
    return out


# --------------------------------------------------------------------------- ingest
def _record_primary_predictions(task: str, cres: ConsensusResult, *, horizon_ms: int, symbol: Optional[str] = None) -> List[str]:
    """每条有效主票落 agent_predictions（agent=model:{transport}），供模型×任务可信度矩阵。"""
    ids: List[str] = []
    for r in cres.primaries or []:
        if not r.ok or not r.json:
            continue
        direction = schemas.direction_to_int(r.json.get("direction"))
        sym = _norm_symbol(symbol) or "BTC"
        views = r.json.get("symbol_views") if isinstance(r.json.get("symbol_views"), list) else []
        affected = r.json.get("affected_symbols") if isinstance(r.json.get("affected_symbols"), list) else []
        if views and isinstance(views[0], dict) and views[0].get("symbol"):
            sym = _norm_symbol(views[0].get("symbol")) or "BTC"
        elif affected:
            sym = _norm_symbol(affected[0]) or "BTC"
        pid = ledgers.record_prediction(
            agent=f"model:{r.transport}",
            kind="direction",
            subject=sym,
            prediction={
                "symbol": sym,
                "direction": direction,
                "task": task,
                "strength": r.json.get("strength"),
                "summary": (r.json.get("summary") or "")[:200],
            },
            horizon_ms=horizon_ms,
            confidence=float(r.json.get("confidence") or 0.5),
            context_hash=cres.context_hash,
            analysis_run_id=r.run_id or cres.run_id,
        )
        if pid:
            ids.append(pid)
    return ids


def ingest_consensus(
    task: str,
    cres: ConsensusResult,
    pack,
    *,
    event_meta: Optional[Dict[str, Any]] = None,
    symbol: Optional[str] = None,
) -> Dict[str, Any]:
    """把共识结果写入信号账本 / 实验卡 / 最新建议文件。未 accepted 仍记录主票预测。

    symbol：信号/预测挂靠币种（trend_chart_review 等单币任务用；默认 BTC 保持旧行为）。
    """
    final = cres.final or {}
    signals: List[str] = []
    experiments: List[str] = []
    # 默认 horizon：日简报 24h；事件用 half_life；周复盘/择时 7d
    if task == "event_impact":
        horizon_ms = _horizon_ms(final.get("half_life_hours") or final.get("hedging_window_hours") or 24)
    elif task in ("weekly_review", "timing"):
        horizon_ms = _horizon_ms(168)
    else:
        horizon_ms = _horizon_ms(24)

    pred_ids = _record_primary_predictions(task, cres, horizon_ms=horizon_ms, symbol=symbol)

    latest_payload = {
        "task": task,
        "ts_ms": int(time.time() * 1000),
        "accepted": cres.accepted,
        "status": cres.status,
        "consensus_score": cres.consensus_score,
        "run_id": cres.run_id,
        "context_hash": cres.context_hash,
        "final": final if cres.accepted else None,
        "notes": list(cres.notes),
        "event": event_meta,
    }
    latest_name = {
        "daily_brief": "latest_daily_brief.json",
        "weekly_review": "latest_weekly_review.json",
        "timing": "latest_timing.json",
        "event_impact": "latest_event_impact.json",
    }.get(task)
    latest_path = _write_latest(latest_name, latest_payload) if latest_name else None

    if not cres.accepted or not final:
        return {
            "signals": signals,
            "experiments": experiments,
            "predictions": pred_ids,
            "latest_path": latest_path,
            "ingest_note": "未达共识阈值，仅记录主票预测与 latest（final=null）",
        }

    source = f"dual:{task}"
    regime = final.get("regime") if isinstance(final.get("regime"), str) else None
    overall_dir = schemas.direction_to_int(final.get("direction"))
    conf = float(final.get("confidence") or cres.consensus_score)
    strength = float(final.get("strength") or 0)

    # 总方向信号（默认以 BTC 为代表；单币任务（trend_chart_review）挂到目标币种上）
    sid = ledgers.record_signal(
        source=source,
        symbol=(_norm_symbol(symbol) or "BTC"),
        direction=overall_dir,
        horizon_ms=horizon_ms,
        strength=strength,
        confidence=conf,
        regime=regime,
        context_hash=cres.context_hash,
        analysis_run_id=cres.run_id,
        payload={
            "task": task,
            "summary": (final.get("summary") or "")[:400],
            "bucket_weights": final.get("bucket_weights"),
            "consensus_score": cres.consensus_score,
            "event_id": (event_meta or {}).get("id"),
            "event_type": (event_meta or {}).get("event_type"),
            "key_factors": final.get("key_factors"),
            # [2026-09-05] 图审任务的可执行字段（midlong_chart_gate 消费 position_advice；
            # invalidation/structure 供前端与人工复核）。其他任务没有这些键，保持原样。
            "position_advice": final.get("position_advice"),
            "invalidation": (final.get("invalidation") or "")[:300] or None,
            "structure": (final.get("structure") or "")[:300] or None,
        },
    )
    if sid:
        signals.append(sid)

    # 分币视图 / 受影响币
    if task == "daily_brief":
        for view in final.get("symbol_views") or []:
            if not isinstance(view, dict):
                continue
            sym = _norm_symbol(view.get("symbol"))
            if not sym or sym == "BTC":
                continue  # BTC 已记总方向
            h_ms = _horizon_ms(view.get("horizon_hours") or 24)
            sid2 = ledgers.record_signal(
                source=source,
                symbol=sym,
                direction=schemas.direction_to_int(view.get("direction")),
                horizon_ms=h_ms,
                strength=float(view.get("strength") or strength),
                confidence=conf,
                regime=regime,
                context_hash=cres.context_hash,
                analysis_run_id=cres.run_id,
                payload={"reason": view.get("reason"), "task": task, "consensus_score": cres.consensus_score},
            )
            if sid2:
                signals.append(sid2)
        # 桶权重建议单独落盘（E4 后续消费）
        bw = final.get("bucket_weights") if isinstance(final.get("bucket_weights"), dict) else {}
        _write_latest("latest_bucket_weights.json", {
            "ts_ms": latest_payload["ts_ms"], "source": source, "run_id": cres.run_id,
            "bucket_weights": bw, "regime": regime, "consensus_score": cres.consensus_score,
        })

    elif task == "event_impact":
        for sym in final.get("affected_symbols") or []:
            s = _norm_symbol(sym)
            if not s or s == "BTC":
                continue
            sid2 = ledgers.record_signal(
                source=source,
                symbol=s,
                direction=overall_dir,
                horizon_ms=horizon_ms,
                strength=strength,
                confidence=conf,
                regime=regime,
                context_hash=cres.context_hash,
                analysis_run_id=cres.run_id,
                payload={
                    "task": task,
                    "event_id": (event_meta or {}).get("id"),
                    "event_type": (event_meta or {}).get("event_type"),
                    "half_life_hours": final.get("half_life_hours"),
                    "hedging_window_hours": final.get("hedging_window_hours"),
                    "position_adjustments": final.get("position_adjustments"),
                    "consensus_score": cres.consensus_score,
                },
            )
            if sid2:
                signals.append(sid2)
        # 已评估事件登记（防重复）
        if event_meta and event_meta.get("id") is not None:
            _mark_event_evaluated(int(event_meta["id"]), cres.run_id, cres.consensus_score)

    elif task == "weekly_review":
        for exp in final.get("experiments") or []:
            if not isinstance(exp, dict):
                continue
            change = exp.get("change") if isinstance(exp.get("change"), dict) else None
            metrics = exp.get("expected_metrics") if isinstance(exp.get("expected_metrics"), list) else None
            title = str(exp.get("title") or "").strip()
            hyp = str(exp.get("hypothesis") or "").strip()
            wh = int(exp.get("window_hours") or 168)
            if not title or not hyp or not change or not metrics or wh <= 0:
                continue
            eid = ledgers.create_experiment(
                source=source,
                title=title,
                hypothesis=hyp,
                change=change,
                expected_metrics=metrics,
                window_hours=wh,
                rollback_condition=exp.get("rollback_condition"),
                analysis_run_id=cres.run_id,
                notes=f"weekly_review consensus={cres.consensus_score}",
            )
            if eid:
                experiments.append(eid)

    elif task == "timing":
        _write_latest("latest_engine_capital.json", {
            "ts_ms": latest_payload["ts_ms"], "source": source, "run_id": cres.run_id,
            "bucket_weights": final.get("bucket_weights"),
            "engine_capital": final.get("engine_capital"),
            "counterfactual": final.get("counterfactual"),
            "regime": regime, "consensus_score": cres.consensus_score,
        })

    return {
        "signals": signals,
        "experiments": experiments,
        "predictions": pred_ids,
        "latest_path": latest_path,
    }


# --------------------------------------------------------------------------- event eval memo
def _evaluated_path() -> Path:
    return _ensure_data_dir() / "evaluated_events.json"


def _load_evaluated() -> Dict[str, Any]:
    path = _evaluated_path()
    if not path.exists():
        return {"events": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"events": {}}


def _mark_event_evaluated(event_id: int, run_id: Optional[str], score: float) -> None:
    data = _load_evaluated()
    events = data.setdefault("events", {})
    events[str(event_id)] = {
        "run_id": run_id,
        "consensus_score": score,
        "ts_ms": int(time.time() * 1000),
    }
    # 只保留最近 2000 条
    if len(events) > 2000:
        ordered = sorted(events.items(), key=lambda kv: int((kv[1] or {}).get("ts_ms") or 0))
        data["events"] = dict(ordered[-2000:])
    _evaluated_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_event_evaluated(event_id: Any) -> bool:
    if event_id is None:
        return False
    return str(event_id) in (_load_evaluated().get("events") or {})


# --------------------------------------------------------------------------- public runners
def run_daily_brief(*, dry_run: bool = False) -> Dict[str, Any]:
    if not tasks_enabled() and not dry_run:
        return {"task": "daily_brief", "ok": False, "skipped": True, "reason": "ANALYSIS_TASKS_ENABLED=false"}
    return run_dual_task("daily_brief", events_hours=36.0, dry_run=dry_run)


def run_weekly_review(*, dry_run: bool = False) -> Dict[str, Any]:
    if not tasks_enabled() and not dry_run:
        return {"task": "weekly_review", "ok": False, "skipped": True, "reason": "ANALYSIS_TASKS_ENABLED=false"}
    return run_dual_task("weekly_review", events_hours=168.0, dry_run=dry_run)


def run_timing(*, dry_run: bool = False) -> Dict[str, Any]:
    if not tasks_enabled() and not dry_run:
        return {"task": "timing", "ok": False, "skipped": True, "reason": "ANALYSIS_TASKS_ENABLED=false"}
    return run_dual_task("timing", events_hours=168.0, dry_run=dry_run)


def run_event_impact(
    *,
    event: Optional[Dict[str, Any]] = None,
    event_id: Optional[int] = None,
    force: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """评估单个 market_events 行。event 缺省时按 event_id 查询。"""
    if not tasks_enabled() and not dry_run:
        return {"task": "event_impact", "ok": False, "skipped": True, "reason": "ANALYSIS_TASKS_ENABLED=false"}

    from backend.services.events import market_events_store as mes

    ev = event
    if ev is None and event_id is not None:
        ev = mes.get_by_id(int(event_id))
    if not ev:
        return {"task": "event_impact", "ok": False, "skipped": True, "reason": "event not found"}

    eid = ev.get("id")
    if eid is not None and is_event_evaluated(eid) and not force:
        return {
            "task": "event_impact", "ok": True, "skipped": True,
            "reason": "already_evaluated", "event_id": eid,
            "prior": (_load_evaluated().get("events") or {}).get(str(eid)),
        }

    sym = _norm_symbol(ev.get("symbol")) if ev.get("symbol") else None
    symbols = [sym] if sym else None
    event_meta = {
        "id": eid,
        "event_type": ev.get("event_type"),
        "symbol": ev.get("symbol"),
        "ts_ms": ev.get("ts_ms"),
        "severity": ev.get("severity"),
        "direction": ev.get("direction"),
        "title": (ev.get("title") or "")[:300],
        "source": ev.get("source"),
        "payload": ev.get("payload") if isinstance(ev.get("payload"), dict) else {},
    }
    event_block = "【待评估事件】\n" + json.dumps(event_meta, ensure_ascii=False, default=str)
    out = run_dual_task(
        "event_impact",
        symbols=symbols,
        layers=("market", "flows", "positions", "config"),
        events_hours=_env_float("ANALYSIS_EVENT_LOOKBACK_HOURS", 96.0),
        event_block=event_block,
        event_meta=event_meta,
        max_output_tokens=2000,
        dry_run=dry_run,
    )
    out["event_id"] = eid
    return out


def scan_and_eval_events(*, limit: Optional[int] = None, dry_run: bool = False) -> Dict[str, Any]:
    """扫描近窗高严重度事件，跳过已评估，按日预算评估最多 N 条。"""
    if not tasks_enabled() and not dry_run:
        return {"ok": False, "skipped": True, "reason": "ANALYSIS_TASKS_ENABLED=false"}

    from backend.services.events import market_events_store as mes

    lookback = _env_float("ANALYSIS_EVENT_LOOKBACK_HOURS", 96.0)
    min_sev = _env_int("ANALYSIS_EVENT_MIN_SEVERITY", 3)
    max_n = limit if limit is not None else _env_int("ANALYSIS_EVENT_BATCH_LIMIT", 3)
    rows = mes.recent(hours=lookback, min_severity=min_sev, limit=200)
    candidates = [
        r for r in rows
        if str(r.get("event_type") or "") in _EVENT_EVAL_TYPES and not is_event_evaluated(r.get("id"))
    ]
    candidates.sort(key=lambda r: (int(r.get("severity") or 0), int(r.get("ts_ms") or 0)), reverse=True)
    candidates = candidates[: max(0, int(max_n))]

    results: List[Dict[str, Any]] = []
    for ev in candidates:
        try:
            results.append(run_event_impact(event=ev, dry_run=dry_run))
        except Exception as exc:
            logger.warning("[analysis.tasks] event_impact 失败 id=%s: %s", ev.get("id"), exc)
            results.append({"event_id": ev.get("id"), "ok": False, "error": str(exc)[:200]})

    return {
        "ok": True,
        "scanned": len(rows),
        "candidates": len(candidates),
        "evaluated": len(results),
        "accepted": sum(1 for r in results if r.get("accepted")),
        "results": [
            {
                "event_id": r.get("event_id"),
                "status": r.get("status"),
                "accepted": r.get("accepted"),
                "consensus_score": r.get("consensus_score"),
                "run_id": r.get("run_id"),
                "signals": r.get("signals"),
                "skipped": r.get("skipped"),
                "reason": r.get("reason"),
                "error": r.get("error"),
            }
            for r in results
        ],
    }


# --------------------------------------------------------------------------- credibility
def model_task_credibility(days: int = 30) -> Dict[str, Any]:
    """模型 × 任务类型可信度矩阵。

    来源：
      1) agent_predictions：agent=model:{transport}，kind=direction → avg_score / avg_brier
      2) signal_ledger：source=dual:{task} → hit_rate / avg_excess_bp / avg_brier
      3) analysis_runs：近 N 天 consensus 行的 avg_consensus / 接受率
    """
    days = max(1, min(365, int(days)))
    agents = ledgers.agent_credibility(days)
    model_rows = [r for r in agents if str(r.get("agent") or "").startswith("model:")]
    signal_stats = ledgers.signal_source_stats(days)
    dual_rows = [r for r in signal_stats if str(r.get("source") or "").startswith("dual:")]

    consensus_by_task: Dict[str, Dict[str, Any]] = {}
    summary: Dict[str, Any] = {"daily_brief_days": 0}
    try:
        summary = ledgers.runs_summary(days)
        for row in summary.get("rows") or []:
            if str(row.get("role") or "") == "consensus" or str(row.get("transport") or "") == "consensus":
                task = str(row.get("task") or "")
                consensus_by_task[task] = {
                    "n": int(row.get("n") or 0),
                    "n_ok": int(row.get("n_ok") or 0),
                    "avg_consensus": row.get("avg_consensus"),
                    "avg_latency_ms": row.get("avg_latency_ms"),
                    "last_ms": row.get("last_ms"),
                }
    except Exception as exc:
        logger.debug("[analysis.tasks] runs_summary for credibility: %s", exc)

    return {
        "days": days,
        "threshold": CONSENSUS_THRESHOLD,
        "models": model_rows,
        "dual_signals": dual_rows,
        "consensus_by_task": consensus_by_task,
        "daily_brief_days": int(summary.get("daily_brief_days") or 0),
    }


# --------------------------------------------------------------------------- scheduled job wrappers
def scheduled_daily_brief() -> Dict[str, Any]:
    logger.info("[analysis.tasks] scheduled daily_brief start")
    out = run_daily_brief()
    logger.info(
        "[analysis.tasks] daily_brief done status=%s accepted=%s score=%.3f signals=%d",
        out.get("status"), out.get("accepted"), float(out.get("consensus_score") or 0),
        len(out.get("signals") or []),
    )
    return {k: out.get(k) for k in (
        "task", "ok", "status", "accepted", "consensus_score", "run_id", "signals", "notes", "elapsed_sec", "skipped", "reason"
    )}


# ------------------------------------------------------------------ trend chart review (P3 2026-09-05)
def run_trend_chart_review(
    symbol: str,
    *,
    tfs: Sequence[str] = ("1w", "1d", "4h"),
    dry_run: bool = False,
) -> Dict[str, Any]:
    """多模态趋势图审：深度K线数据包（主体）+ 图（GLM 专属）→ 双票 → 仲裁 → 入账。

    [2026-09-05 分工定型] K线**数据**深度分析是任务主体（全数值指标/结构表，两票都拿）；
    图只发给 GLM（Coding Plan 订阅内零边际成本、深度分析更强），MiniMax 按 token 计费
    吃纯文本第二票——异构双票交叉验证信息量更高，且省下 MiniMax 最贵的图片开销。
    """
    from backend.services.analysis.chart_service import chart_pack_for_symbol, deep_kline_text

    symbol = str(symbol or "").strip().upper()
    if not symbol:
        return {"task": "trend_chart_review", "ok": False, "skipped": True, "reason": "symbol 为空"}
    if not tasks_enabled():
        return {"task": "trend_chart_review", "ok": False, "skipped": True, "reason": "ANALYSIS_TASKS_ENABLED=false"}

    deep_text, deep_errs = deep_kline_text(symbol, tuple(tfs))
    images, chart_errors = chart_pack_for_symbol(symbol, tfs)
    if not images and not deep_text:
        return {"task": "trend_chart_review", "ok": False, "skipped": True,
                "reason": f"图与数据均渲染失败: {(chart_errors + deep_errs)[:3]}", "chart_errors": chart_errors}

    event_block = (
        f"【目标币种】{symbol}\n\n{deep_text}\n\n"
        f"【附图说明】本任务配有 {len(images)} 张 K 线图（{', '.join(tfs)}，蜡烛+EMA9/21+成交量）——"
        "若你的消息中确有图片，可结合图形结构交叉验证；若没有图片，仅依据上方数值数据深度分析作答，"
        "不要臆测图内容。精确数值一律以上方数据为准。"
    ) if images else (
        f"【目标币种】{symbol}\n\n{deep_text}\n\n【附图说明】本轮无图，仅依据数值数据深度分析。"
    )
    out = run_dual_task(
        "trend_chart_review",
        symbols=[symbol],
        images=images or None,
        images_for=["glm_opencode"] if images else None,
        event_block=event_block,
        event_meta={"symbol": symbol, "charts": [im["title"] for im in images]},
        max_output_tokens=2000,
        timeout_s=330.0,
        dry_run=dry_run,
        ingest_symbol=symbol,
    )
    out["symbol"] = symbol
    out["charts"] = [im["title"] for im in images]
    out["chart_errors"] = chart_errors + deep_errs
    try:
        _write_latest(f"latest_trend_chart_{symbol}.json", {
            "task": "trend_chart_review", "symbol": symbol, "ts_ms": int(time.time() * 1000),
            "accepted": out.get("accepted"), "status": out.get("status"),
            "consensus_score": out.get("consensus_score"), "final": out.get("final"),
            "charts": out.get("charts"), "notes": out.get("notes"),
        })
    except Exception:
        pass
    return out


def trend_chart_interval_sec() -> int:
    """图审扫描间隔。[2026-09-07] 28800→14400（4h）：对齐 QuantAgent 实证的
    4h 最佳分析节奏，让图证与主脑 mid=4h 决策 K 同频；下限 30 分钟。"""
    return max(1800, _env_int("ANALYSIS_TREND_CHART_SEC", 14400))


def chart_fresh_horizon_h() -> float:
    """已入账的图审短于这个小时数就跳过。[2026-09-07] 8→4，对齐图审间隔。"""
    return max(1.0, min(48.0, _env_float("ANALYSIS_TREND_CHART_FRESH_H", 4.0)))


def chart_max_per_cycle() -> int:
    """单轮最多审几个币。默认 4，避免一轮串行堵死。"""
    return max(1, min(20, _env_int("ANALYSIS_TREND_CHART_MAX_PER_CYCLE", 4)))


def _latest_chart_created_ms(symbol: str) -> Optional[int]:
    try:
        rows = ledgers.list_signals(source="dual:trend_chart_review", symbol=symbol, limit=3) or []
    except Exception:
        return None
    best: Optional[int] = None
    for r in rows:
        try:
            ms = int(r.get("created_ms") or 0)
        except (TypeError, ValueError):
            continue
        if ms <= 0:
            continue
        if best is None or ms > best:
            best = ms
    return best


def order_chart_review_symbols(
    universe: Sequence[str],
    *,
    priority: Optional[Sequence[str]] = None,
    now_ms: Optional[int] = None,
    created_ms: Optional[Dict[str, Optional[int]]] = None,
) -> List[str]:
    """缺图 / 过期优先，中长线白名单优先；已在新鲜窗内的不排进去。"""
    now = int(now_ms or time.time() * 1000)
    fresh_ms = int(chart_fresh_horizon_h() * 3600 * 1000)
    pri = {str(s).upper() for s in (priority or []) if s}
    ranked: List[tuple] = []
    seen = set()
    for raw in universe:
        sym = str(raw or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        if created_ms is not None:
            created = created_ms.get(sym)
        else:
            created = _latest_chart_created_ms(sym)
        if created is not None and (now - int(created)) < fresh_ms:
            continue
        missing = 1 if created is None else 0
        age = now - int(created) if created is not None else 10**18
        ranked.append((missing, 0 if sym in pri else 1, age, sym))
    ranked.sort(key=lambda t: (-t[0], t[1], -t[2], t[3]))
    return [t[3] for t in ranked[: chart_max_per_cycle()]]


def _midlong_chart_priority() -> List[str]:
    extras = ["BTC", "ETH", "SOL", "BNB", "XRP", "UNI", "ASTER", "VIRTUAL", "XPL", "LINK"]
    try:
        from backend.services.auto_coin_selector import (
            get_ai_mid_candidates_for_session,
            get_fixed_symbols_for_session,
        )
        from backend.services.full_auto.full_auto_trading_service import get_full_auto_trading_service
        svc = get_full_auto_trading_service()
        bag = getattr(svc, "sessions", None) or getattr(svc, "_sessions", None) or {}
        if isinstance(bag, dict):
            for sid, sess in bag.items():
                if str(getattr(sess, "status", "") or "").lower() not in ("running", "defensive"):
                    continue
                for tier in ("mid", "long"):
                    for sym in get_fixed_symbols_for_session(str(sid), tier=tier) or []:
                        extras.append(str(sym).upper())
                for sym in get_ai_mid_candidates_for_session(str(sid)) or []:
                    extras.append(str(sym).upper())
    except Exception:
        pass
    out: List[str] = []
    seen = set()
    for u in extras:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _trend_chart_universe() -> List[str]:
    """图审币池 = ANALYSIS_UNIVERSE ∪ 中长线白名单。"""
    raw = os.getenv("ANALYSIS_UNIVERSE", "BTC,ETH,SOL,BNB,XRP,DOGE,ADA,AVAX")
    seen: List[str] = []
    for s in raw.split(","):
        u = s.strip().upper()
        if u and u not in seen:
            seen.append(u)
    for u in _midlong_chart_priority():
        if u and u not in seen:
            seen.append(u)
    return seen


def scheduled_trend_chart_review() -> Dict[str, Any]:
    """按间隔扫图审：缺图/过期先打，新鲜的跳过。单币失败不影响其余。"""
    if not tasks_enabled():
        return {"ok": False, "skipped": True, "reason": "ANALYSIS_TASKS_ENABLED=false"}
    universe = _trend_chart_universe()
    todo = order_chart_review_symbols(universe, priority=_midlong_chart_priority())
    if not todo:
        logger.info("[analysis.tasks] trend_chart_review all fresh n=%d", len(universe))
        return {"task": "trend_chart_review", "ok": True, "skipped": True, "reason": "all_fresh",
                "ok_count": 0, "total": 0, "symbols": []}
    results: List[Dict[str, Any]] = []
    for sym in todo:
        try:
            r = run_trend_chart_review(sym)
            results.append({"symbol": sym, "ok": r.get("ok"), "status": r.get("status"),
                            "score": r.get("consensus_score"), "reason": r.get("reason")})
        except Exception as exc:
            logger.warning("[analysis.tasks] trend_chart_review %s 失败: %s", sym, exc)
            results.append({"symbol": sym, "ok": False, "reason": str(exc)[:200]})
    ok_n = sum(1 for r in results if r.get("ok") and r.get("status") != "degraded")
    acc_n = sum(1 for r in results if r.get("score") and float(r.get("score") or 0) >= 0.7)
    logger.info(
        "[analysis.tasks] trend_chart_review done todo=%s ok=%d/%d accepted~%d",
        todo, ok_n, len(results), acc_n,
    )
    return {"task": "trend_chart_review", "ok": ok_n > 0, "ok_count": ok_n, "total": len(results), "symbols": results}


def scheduled_weekly_review() -> Dict[str, Any]:
    logger.info("[analysis.tasks] scheduled weekly_review start")
    out = run_weekly_review()
    logger.info(
        "[analysis.tasks] weekly_review done status=%s accepted=%s experiments=%d",
        out.get("status"), out.get("accepted"), len(out.get("experiments") or []),
    )
    return {k: out.get(k) for k in (
        "task", "ok", "status", "accepted", "consensus_score", "run_id", "experiments", "signals", "notes", "elapsed_sec", "skipped", "reason"
    )}


def scheduled_timing() -> Dict[str, Any]:
    logger.info("[analysis.tasks] scheduled timing start")
    out = run_timing()
    return {k: out.get(k) for k in (
        "task", "ok", "status", "accepted", "consensus_score", "run_id", "notes", "elapsed_sec", "skipped", "reason"
    )}


def scheduled_event_scan() -> Dict[str, Any]:
    logger.info("[analysis.tasks] scheduled event_scan start")
    out = scan_and_eval_events()
    logger.info(
        "[analysis.tasks] event_scan done scanned=%s candidates=%s evaluated=%s accepted=%s",
        out.get("scanned"), out.get("candidates"), out.get("evaluated"), out.get("accepted"),
    )
    return out
