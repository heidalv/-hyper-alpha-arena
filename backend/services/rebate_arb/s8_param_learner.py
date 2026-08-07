"""
S8 参数学习回流（M8）

闭环：S8 每轮平仓 → rebate_trade_outcomes 落实际数据（实际 Rh/方向盈亏/返佣/时长）
     → 本模块统计 → data/s8_learned_params.json → S8 初始化时读取应用

校准三个拍脑袋参数：
  1. speculative_discount（积分估值折扣，原固定 0.5）：
     用「实际现金净值 / 积分」校准 — 方向盈亏 + 返佣是真钱，
     如果方向亏损持续侵蚀积分价值，折扣下调；反之上调。
  2. stage6_hold_default_seconds（默认持仓时长，原固定 4h）：
     按时长分桶比较「单位小时综合得分（现金净值 + 折后积分价值）」，
     选历史表现最好的桶。
  3. neutral_macro_position_scale（宏观兜底仓位系数，原固定 0.60）：
     按 macro_fallback 轮次的方向胜率动态调整。

所有学习值都有硬边界，样本不足时不覆盖默认值（写 None）。
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

LEARNED_PARAMS_FILE = os.path.join("data", "s8_learned_params.json")
_cache: dict = {"ts": 0.0, "data": {}}

MIN_SAMPLES = 5
MIN_SAMPLES_VETO = 10
LOOKBACK_DAYS = 14
RECOVERY_MAX_LEVERAGE = 3
PAPER_STOP_LOSS_NOTIONAL_PCT = 0.008
# 相对保证金止损（10x 下比名义%更贴近真实风险）
PAPER_STOP_LOSS_MARGIN_PCT = 0.04
# 定时平仓前：盈利未达此保证金比例则延长持仓，避免「小赚就跑」
PAPER_PROFIT_MIN_MARGIN_PCT_TO_CLOSE = 0.015
PAPER_HOLD_EXTEND_SECONDS = 600
PAPER_HOLD_MAX_EXTENSIONS = 3
RECOVERY_MIN_AI_CONFIDENCE = 70

# 安全边界
DISCOUNT_BOUNDS = (0.20, 0.80)
HOLD_BOUNDS_SECONDS = (2 * 3600, 8 * 3600)
NEUTRAL_SCALE_BOUNDS = (0.30, 0.80)

# 持仓时长分桶（小时上界，最后一桶为 +inf）
HOLD_BUCKETS = [(0.0, 1.0), (1.0, 3.0), (3.0, 5.0), (5.0, 24.0)]
BUCKET_DEFAULT_SECONDS = [1800, 2 * 3600, 4 * 3600, 6 * 3600]


def get_learning_gate(*, paper_mode: bool = False) -> Dict[str, Any]:
    """
    学习门禁状态。

    Paper 模拟盘：只记录 cash/pt、折扣等学习结果，**永不拦截开仓**（模拟就是用来试错）。
    Live 实盘：cash/pt 为负且样本充足时 recovery_mode，拦截 stage6_optimal 盲开。
    """
    params = load_learned_params()
    samples = int(params.get("samples") or 0)
    stats = params.get("_discount_stats") or {}
    cash_per_point = float(stats.get("cash_per_point") or 0)
    total_cash = float(stats.get("total_cash") or 0)
    total_points = float(stats.get("total_points") or 0)
    spec_discount = params.get("speculative_discount")
    data_negative = samples >= MIN_SAMPLES_VETO and cash_per_point < 0
    # 实盘才 veto；Paper 仅 advisory
    live_veto = data_negative and not paper_mode
    paper_advisory = data_negative and paper_mode
    return {
        "samples": samples,
        "cash_per_point": round(cash_per_point, 5),
        "total_cash": round(total_cash, 2),
        "total_points": round(total_points, 2),
        "speculative_discount": spec_discount,
        "veto_stage6": live_veto,
        "recovery_mode": live_veto,
        "paper_advisory": paper_advisory,
        "paper_blocks_open": False,
        "recommended_mode": "paper_experiment" if live_veto else None,
        "recovery_max_leverage": RECOVERY_MAX_LEVERAGE if live_veto else None,
        "recovery_min_ai_confidence": RECOVERY_MIN_AI_CONFIDENCE if live_veto else None,
        "paper_stop_loss_notional_pct": PAPER_STOP_LOSS_NOTIONAL_PCT,
        "paper_stop_loss_margin_pct": PAPER_STOP_LOSS_MARGIN_PCT,
        "paper_profit_min_margin_pct_to_close": PAPER_PROFIT_MIN_MARGIN_PCT_TO_CLOSE,
        "min_samples_veto": MIN_SAMPLES_VETO,
    }


def resolve_position_margin_usd(position) -> float:
    """从仓位 metadata 解析保证金（USD）。"""
    meta = position.metadata if isinstance(getattr(position, "metadata", None), dict) else {}
    margin = float(meta.get("margin_usd") or 0)
    if margin > 0:
        return margin
    notional = float(getattr(position, "side_a_size", 0) or 0)
    lev = float((meta.get("side_a") or {}).get("leverage") or meta.get("leverage") or 10)
    return notional / max(lev, 1.0) if notional > 0 else 0.0


def load_learned_params() -> Dict[str, Any]:
    """读取学习产出（60s 缓存）。无文件 / 样本不足时相应键为 None。"""
    now = time.time()
    if now - _cache["ts"] < 60:
        return _cache["data"]
    data: Dict[str, Any] = {}
    try:
        if os.path.exists(LEARNED_PARAMS_FILE):
            with open(LEARNED_PARAMS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
    except Exception as err:
        logger.warning(f"[S8Learner] 学习参数读取失败: {err}")
        data = {}
    _cache["ts"] = now
    _cache["data"] = data
    return data


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def recompute_learned_params(lookback_days: int = LOOKBACK_DAYS) -> Optional[Dict[str, Any]]:
    """
    重算 S8 学习参数并写文件。返回结果 dict（失败返回 None）。
    """
    try:
        from backend.database.connection import SessionLocal
        from backend.services.rebate_arb.schema import ensure_rebate_schema

        ensure_rebate_schema()
        db = SessionLocal()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
            rows, source = _fetch_s8_learning_rows(db, cutoff, limit=500)
            if source != "rebate_trade_outcomes" and rows:
                logger.info(
                    "[S8Learner] outcomes 表为空，回退 %s 样本 %d 条",
                    source, len(rows),
                )
        finally:
            db.close()

        n = len(rows)
        result: Dict[str, Any] = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": lookback_days,
            "samples": n,
            "data_source": source if n else "none",
            "speculative_discount": None,
            "stage6_hold_default_seconds": None,
            "neutral_macro_position_scale": None,
        }

        if n < MIN_SAMPLES:
            _write(result)
            logger.info(f"[S8Learner] 样本不足({n}<{MIN_SAMPLES})，保持默认参数")
            return result

        # 基准估值（来自规则注册表，失败用 Stage6 常见预估）
        usd_per_point = 0.1
        try:
            from backend.services.rebate_arb.rule_registry import STAGE6_POINT_MODEL
            usd_per_point = float(
                (STAGE6_POINT_MODEL.get("point_valuation") or {}).get("usd_per_point_estimate")
                or usd_per_point
            )
        except Exception:
            pass

        # ── 1. 估值折扣校准 ──
        total_points = sum(float(r.points or 0) for r in rows)
        total_cash = sum(float(r.pnl or 0) + float(r.rebate or 0) for r in rows)
        if total_points > 0 and usd_per_point > 0:
            # 每积分实际现金负担（负值 = 方向亏损在吃掉积分价值）
            cash_per_point = total_cash / total_points
            discount = _clamp(
                0.5 + cash_per_point / usd_per_point, *DISCOUNT_BOUNDS
            )
            result["speculative_discount"] = round(discount, 3)
            result["_discount_stats"] = {
                "total_points": round(total_points, 2),
                "total_cash": round(total_cash, 2),
                "cash_per_point": round(cash_per_point, 5),
            }

        # ── 2. 最优持仓时长 ──
        bucket_scores = []
        for i, (lo, hi) in enumerate(HOLD_BUCKETS):
            bucket = [
                r for r in rows
                if lo <= float(r.hold_hours or 0) < hi and float(r.hold_hours or 0) > 0
            ]
            if len(bucket) < 3:
                bucket_scores.append(None)
                continue
            disc = result["speculative_discount"] or 0.5
            score = sum(
                (float(r.pnl or 0) + float(r.rebate or 0)
                 + float(r.points or 0) * usd_per_point * disc)
                / max(float(r.hold_hours or 0), 1 / 60)
                for r in bucket
            ) / len(bucket)
            bucket_scores.append({"n": len(bucket), "score_per_hour": round(score, 4)})
        valid = [
            (i, b) for i, b in enumerate(bucket_scores) if b is not None
        ]
        if valid:
            best_i = max(valid, key=lambda x: x[1]["score_per_hour"])[0]
            hold_sec = int(_clamp(BUCKET_DEFAULT_SECONDS[best_i], *HOLD_BOUNDS_SECONDS))
            result["stage6_hold_default_seconds"] = hold_sec
            result["_hold_stats"] = bucket_scores

        # ── 3. 宏观兜底仓位系数 ──
        fallback_rows = []
        for r in rows:
            try:
                meta = json.loads(r.outcome_json or "{}").get("metadata") or {}
                ai_meta = meta.get("ai_metadata") or {}
                if str(ai_meta.get("direction_source") or "") == "macro_fallback":
                    fallback_rows.append(r)
            except Exception:
                continue
        if len(fallback_rows) >= MIN_SAMPLES:
            wins = sum(1 for r in fallback_rows if float(r.pnl or 0) > 0)
            win_rate = wins / len(fallback_rows)
            scale = _clamp(0.4 + 0.4 * win_rate, *NEUTRAL_SCALE_BOUNDS)
            result["neutral_macro_position_scale"] = round(scale, 3)
            result["_fallback_stats"] = {
                "n": len(fallback_rows), "win_rate": round(win_rate, 3),
            }

        _write(result)
        logger.info(
            f"[S8Learner] 学习参数更新: samples={n} "
            f"discount={result['speculative_discount']} "
            f"hold={result['stage6_hold_default_seconds']} "
            f"neutral_scale={result['neutral_macro_position_scale']}"
        )
        return result
    except Exception as exc:
        logger.warning(f"[S8Learner] 重算失败: {exc}")
        return None


def _write(payload: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(LEARNED_PARAMS_FILE), exist_ok=True)
        with open(LEARNED_PARAMS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        _cache["ts"] = 0.0
    except Exception as err:
        logger.warning(f"[S8Learner] 学习参数写入失败: {err}")


def recompute_async() -> None:
    """后台线程重算（平仓钩子用，不阻塞引擎）。"""
    threading.Thread(
        target=recompute_learned_params, daemon=True, name="s8-param-learner"
    ).start()


def _fetch_s8_learning_rows(
    db, cutoff, *, limit: int = 500
) -> tuple:
    """
    读取 S8 学习样本：优先 rebate_trade_outcomes，空表时回退 performance_logs。
    返回 (rows, source) — rows 元素需有 points/pnl/rebate/hold_hours/outcome_json/symbol。
    """
    from backend.database.models import RebatePerformanceLogDB, RebateTradeOutcomeDB
    from backend.services.rebate_arb.points_aggregation import is_trade_performance_log

    rows = (
        db.query(RebateTradeOutcomeDB)
        .filter(
            RebateTradeOutcomeDB.strategy_type == "S8",
            RebateTradeOutcomeDB.created_at >= cutoff.replace(tzinfo=None),
        )
        .order_by(RebateTradeOutcomeDB.created_at.desc())
        .limit(limit)
        .all()
    )
    if rows:
        return rows, "rebate_trade_outcomes"

    perf_rows = (
        db.query(RebatePerformanceLogDB)
        .filter(
            RebatePerformanceLogDB.strategy_type == "S8",
            RebatePerformanceLogDB.created_at >= cutoff.replace(tzinfo=None),
        )
        .order_by(RebatePerformanceLogDB.created_at.desc())
        .limit(limit)
        .all()
    )
    perf_rows = [r for r in perf_rows if is_trade_performance_log(r)]
    if not perf_rows:
        return [], "none"

    class _PerfAdapter:
        __slots__ = (
            "points", "pnl", "rebate", "hold_hours", "outcome_json",
            "symbol", "created_at", "position_id",
        )

        def __init__(self, row):
            self.points = float(row.total_points or 0)
            self.pnl = float(row.total_pnl or 0)
            self.rebate = float(row.total_rebate or 0)
            self.hold_hours = float(row.hold_hours or 0)
            self.position_id = row.position_id
            self.created_at = row.created_at
            self.symbol = ""
            self.outcome_json = json.dumps({
                "close_reason": row.close_reason,
                "metadata": {},
            }, ensure_ascii=False)

    try:
        from backend.database.models import RebatePositionDB

        pids = [r.position_id for r in perf_rows]
        pos_map = {
            p.position_id: p
            for p in db.query(RebatePositionDB).filter(
                RebatePositionDB.position_id.in_(pids)
            ).all()
        }
    except Exception:
        pos_map = {}

    adapted: List[Any] = []
    for r in perf_rows:
        item = _PerfAdapter(r)
        pos = pos_map.get(r.position_id)
        if pos:
            item.symbol = str(pos.symbol or "")
            try:
                meta = json.loads(pos.metadata_json or "{}") if hasattr(pos, "metadata_json") else {}
                item.outcome_json = json.dumps({
                    "close_reason": r.close_reason,
                    "metadata": meta if isinstance(meta, dict) else {},
                }, ensure_ascii=False, default=str)
            except Exception:
                pass
        adapted.append(item)
    return adapted, "rebate_performance_logs"


def build_learning_memory(limit_rounds: int = 10) -> Dict[str, Any]:
    """
    供 Dashboard / API 展示的学习记忆摘要：
    - 已学参数（折扣、持仓、样本）
    - 持仓时长分桶得分
    - 最近 N 轮真实成交（AI 选币 prompt 同源）
    """
    params = load_learned_params()
    gate = get_learning_gate(paper_mode=True)
    hold_labels = ["0-1h", "1-3h", "3-5h", "5-24h"]
    hold_stats_raw = params.get("_hold_stats") or []
    hold_buckets: List[Dict[str, Any]] = []
    for i, label in enumerate(hold_labels):
        stat = hold_stats_raw[i] if i < len(hold_stats_raw) else None
        if isinstance(stat, dict):
            hold_buckets.append({
                "label": label,
                "samples": stat.get("n"),
                "score_per_hour": stat.get("score_per_hour"),
                "is_best": params.get("stage6_hold_default_seconds") == BUCKET_DEFAULT_SECONDS[i],
            })
        else:
            hold_buckets.append({"label": label, "samples": 0, "score_per_hour": None, "is_best": False})

    recent_rounds: List[Dict[str, Any]] = []
    data_source = "none"
    try:
        from backend.database.connection import SessionLocal
        from backend.services.rebate_arb.schema import ensure_rebate_schema

        ensure_rebate_schema()
        cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
        db = SessionLocal()
        try:
            rows, data_source = _fetch_s8_learning_rows(db, cutoff, limit=max(limit_rounds, 50))
        finally:
            db.close()
        for r in rows[:limit_rounds]:
            entry: Dict[str, Any] = {
                "symbol": getattr(r, "symbol", None) or "?",
                "pnl_usd": round(float(getattr(r, "pnl", 0) or 0), 2),
                "points": round(float(getattr(r, "points", 0) or 0), 1),
                "rebate_usd": round(float(getattr(r, "rebate", 0) or 0), 2),
                "hold_hours": round(float(getattr(r, "hold_hours", 0) or 0), 2),
                "direction_correct": bool(float(getattr(r, "pnl", 0) or 0) > 0),
                "created_at": (
                    r.created_at.isoformat() if getattr(r, "created_at", None) else None
                ),
            }
            try:
                meta = json.loads(getattr(r, "outcome_json", None) or "{}").get("metadata") or {}
                ai_meta = meta.get("ai_metadata") or {}
                if ai_meta.get("ai_direction"):
                    entry["direction"] = ai_meta.get("ai_direction")
                entry["mode"] = meta.get("rh_optimization_mode")
            except Exception:
                pass
            recent_rounds.append(entry)
    except Exception as exc:
        logger.debug("[S8Learner] learning_memory recent_rounds skip: %s", exc)

    samples = int(params.get("samples") or gate.get("samples") or 0)
    recovery = bool(gate.get("recovery_mode"))
    paper_advisory = bool(gate.get("paper_advisory"))
    if paper_advisory:
        engine_status = "paper_learning"
        status_note = (
            f"Paper 模拟继续开仓收样本（{samples} 轮）；cash/pt={float(gate.get('cash_per_point') or 0):.4f} "
            "为负仅作告警，实盘才会拦截"
        )
    elif recovery:
        engine_status = "recovery_blocked"
        status_note = (
            f"学习门禁生效：近 {samples} 轮 cash/pt={float(gate.get('cash_per_point') or 0):.4f} 为负，"
            "暂停 stage6_optimal 开新仓，仅 recovery 实验模式"
        )
    elif samples < MIN_SAMPLES:
        engine_status = "collecting"
        status_note = f"样本收集中（{samples}/{MIN_SAMPLES}），Stage6 模型估算中，学习参数尚未覆盖默认值"
    else:
        engine_status = "learning_active"
        status_note = "Stage6 积分引擎 + 参数学习均已启用，开仓需悲观 EV>0"

    return {
        "engine_status": engine_status,
        "status_note": status_note,
        "updated_at": params.get("updated_at"),
        "samples": samples,
        "lookback_days": params.get("lookback_days") or LOOKBACK_DAYS,
        "learned": {
            "speculative_discount": params.get("speculative_discount"),
            "stage6_hold_default_seconds": params.get("stage6_hold_default_seconds"),
            "stage6_hold_default_hours": round(
                float(params.get("stage6_hold_default_seconds") or 0) / 3600, 1
            ) if params.get("stage6_hold_default_seconds") else None,
            "neutral_macro_position_scale": params.get("neutral_macro_position_scale"),
        },
        "gate": gate,
        "hold_buckets": hold_buckets,
        "recent_rounds": recent_rounds,
        "data_source": data_source,
        "memory_sources": [
            "rebate_trade_outcomes → s8_learned_params.json",
            "recent_rounds → AI 策略模型 prompt（M10）",
            "平仓 → unified_learning + learning_bus（方向胜率）",
        ],
    }


def get_recent_s8_rounds_for_ai(limit: int = 10) -> List[Dict[str, Any]]:
    """AI 策略模型 prompt 用的最近轮次摘要（与 Dashboard 学习记忆同源）。"""
    mem = build_learning_memory(limit_rounds=limit)
    out: List[Dict[str, Any]] = []
    for row in mem.get("recent_rounds") or []:
        out.append({
            "symbol": row.get("symbol"),
            "direction_pnl_usd": row.get("pnl_usd"),
            "direction_correct": row.get("direction_correct"),
            "points_earned": row.get("points"),
            "rebate_usd": row.get("rebate_usd"),
            "hold_hours": row.get("hold_hours"),
            "direction": row.get("direction"),
            "rh_optimization_mode": row.get("mode"),
        })
    return out


def apply_learned_params(strategy) -> None:
    """
    把学习参数应用到 S8 策略实例（参数加载入口调用）。

    只在学习值存在（样本充足）时覆盖，且全部经过硬边界 clamp。
    """
    params = load_learned_params()
    if not params:
        return
    try:
        disc = params.get("speculative_discount")
        if disc is not None:
            model = dict(strategy.stage6_model())
            valuation = dict(model.get("point_valuation") or {})
            valuation["speculative_discount"] = _clamp(float(disc), *DISCOUNT_BOUNDS)
            valuation["_learned"] = True
            model["point_valuation"] = valuation
            strategy.STAGE6_MODEL = model

        hold = params.get("stage6_hold_default_seconds")
        if hold is not None:
            strategy.STAGE6_HOLD_DEFAULT_SECONDS = int(
                _clamp(float(hold), *HOLD_BOUNDS_SECONDS)
            )

        scale = params.get("neutral_macro_position_scale")
        if scale is not None:
            strategy.NEUTRAL_MACRO_POSITION_SCALE = _clamp(
                float(scale), *NEUTRAL_SCALE_BOUNDS
            )
        logger.debug(
            f"[S8Learner] 已应用学习参数: discount={disc} hold={hold} scale={scale}"
        )
    except Exception as exc:
        logger.warning(f"[S8Learner] 学习参数应用失败: {exc}")
