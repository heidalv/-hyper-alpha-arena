"""period_daily_report — 双车道统一日报生成器（轮63 重构，2026-09-18）。

## 本轮改了什么（以及为什么）

旧版按硬编码的 `("scalp","midlong","long")` 三档聚合，而这三档与交易引擎的车道**不是同一套东西**：
- `_horizon_of()` 把 `trade_nature="intraday"` 兜底成 `"scalp"`（实测 3 笔被静默丢出中线报告）；
- 「中线」在报告里叫 `midlong`，在 UI 叫 `mid`/「日内波段」，在引擎叫 `swing` —— 三者指同一条车道却互不相认；
- 段内没有任何周期身份字段，前端只能靠外层 tab 猜，**这就是「哪个是哪个分不出来」的机制性原因**。

现在：车道定义只来自 `backend/config/lane_semantics.py`，每段自带 `lane_identity`（含 tier/nature/
主看周期/期望持仓/实测持仓），前端不再需要猜测。

## 落库口径

`period_daily_reports.lane` = `intraday`（日内，含中线槽位）或 `trend`（长线趋势）。
旧列名 `horizon` 保留为兼容别名，写入值与 lane 相同（见 `period_reports_routes`）。

## 时间基准

报告日期与窗口一律按**本地交易日**（服务器 Asia/Shanghai），与 `paper_positions`
的 naive 本地时间戳同源。旧实现用 `datetime.utcnow()` 做基线，差 8 小时 →
窗口会扫到「未来」时间戳，且按 UTC 日归档会把两个本地交易日混进同一天。

## LLM 分析

旧版把**同一段总结**写给全部三行（`for h in ("long","midlong","scalp")`），于是点「长线」看到的是
三周期混在一起的总结 —— 也是「分不清」的来源。现在**每条车道各自一段总结**，行与内容一一对应。

纯规则 + 非交易路径。
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Any, Dict, List, Optional

from backend.config import lane_semantics as lane_sem
from backend.services.pnl_authority import now_local

logger = logging.getLogger(__name__)

# 进程内动作流水缓冲（长线 V2 管理动作 + 入场闸拦截），日报生成时读取。
# 每条记录带 lane，避免把长线动作写进日内段（旧版只按时间过滤，不看车道）。
_ACTION_LOG: deque = deque(maxlen=500)


def log_long_action(symbol: str, action: str, reason: str, lane: str = lane_sem.LANE_TREND) -> None:
    """记录一条车道管理动作。

    lane 默认长线：现有全部调用点都在长线 V2 管理路径
    （full_auto_trading_service 的 V2 平仓/收紧/加仓/减仓、midlong_executor 的入场闸拦截）。
    """
    try:
        _ACTION_LOG.append({
            "ts": time.time(), "symbol": str(symbol).upper(),
            "action": str(action), "reason": str(reason)[:160],
            "lane": lane_sem.get_spec(lane).lane,
        })
    except Exception:
        pass


def _drain_actions(since_ts: float, lane: Optional[str] = None) -> List[Dict[str, Any]]:
    """取 since_ts 之后的动作流水；lane 非空时只看该车道。"""
    want = lane_sem.get_spec(lane).lane if lane else None
    out = []
    for a in list(_ACTION_LOG):
        if float(a.get("ts") or 0) < since_ts:
            continue
        if want is not None and lane_sem.get_spec(a.get("lane")).lane != want:
            continue
        out.append(a)
    return out


def _today_utc() -> str:
    """报告日期（本地日）。函数名保留兼容，语义已改为**本地交易日**。

    [轮63] 原实现用 `datetime.utcnow()`，而 DB 时间戳是本地 naive（Asia/Shanghai），
    两者差 8 小时 → 按 UTC 日归档会把两个本地交易日混进同一天。
    """
    from backend.services.pnl_authority import local_today
    return local_today()


def _is_today(report_date: str) -> bool:
    return str(report_date) == _today_utc()


# ── 车道解析（唯一入口：一切分档都走 lane_semantics）─────────────────────
def horizon_of(order) -> str:
    """持仓/订单 → 车道。保留旧函数名供既有调用点使用。"""
    return lane_sem.resolve_lane_for_position(order)


# 旧名别名（有外部调用点，不删除）
_horizon_of = horizon_of


def _lane_of(order) -> str:
    return lane_sem.resolve_lane_for_position(order)


# ── 区间统计 ────────────────────────────────────────────────────────────
def _window_from_now(hours: int):
    """(起, 止) —— 相对现在回溯 hours 小时（**本地基准**，与 DB 时间列同源）。

    [轮63] 原实现用 `datetime.utcnow()` 做基线，与本地 naive 的 `closed_at`
    差 8 小时：窗口会扫到尚未发生的「未来」时间戳，也会漏掉最近的样本。
    """
    from backend.services.pnl_authority import rolling_window
    return rolling_window(hours)


def day_window(date_str: str):
    """某个日历日的窗口 [date 00:00, date+1 00:00)（本地基准），供历史回填精确取数。

    旧实现只能按「现在往前 N 小时」取数，因此**无法为历史日期重建报告**；
    回填只能搬旧 payload，而旧 payload 是按三档（scalp/midlong 各一行）写的，
    并成双车道后必然重复。本函数让历史报告可以从源数据真正重建。
    """
    from backend.services.pnl_authority import local_day_window
    return local_day_window(date_str)


def _closed_rows(db, account_id: int, window) -> List[Any]:
    """已平仓行（未按车道过滤，避免每条车道各查一次库）。

    window: (起, 止) 二元组。`closed_at` 为 NULL 的行按「现在」兜底
    （与旧实现一致：旧版只按 `closed_at >= cutoff` 过滤，NULL 本就会被排除；
    这里显式兜底以免存量脏数据整行消失）。
    """
    from sqlalchemy import or_

    from backend.database.models import PaperPosition

    start, end = window
    return db.query(PaperPosition).filter(
        PaperPosition.account_id == int(account_id),
        PaperPosition.status.in_(["closed", "liquidated"]),
        or_(PaperPosition.closed_at.between(start, end), PaperPosition.closed_at.is_(None)),
    ).all()


def _hold_hours(row) -> Optional[float]:
    """持仓小时数；缺时间戳返回 None。"""
    try:
        o, c = getattr(row, "opened_at", None), getattr(row, "closed_at", None)
        if o is None or c is None:
            return None
        delta = (c - o).total_seconds() / 3600.0
        return round(delta, 3) if delta >= 0 else None
    except Exception:
        return None


def _stats_from_rows(rows: List[Any]) -> Dict[str, Any]:
    """一组已平仓行 → 统计块（含持仓时长分布：报告要能自证「这是哪个周期」）。"""
    from backend.services.pnl_authority import realized_pnl

    pnls = [realized_pnl(r) for r in rows]
    wins = [p for p in pnls if p > 0]
    holds = sorted(h for h in (_hold_hours(r) for r in rows) if h is not None)

    def _pct(p: float) -> Optional[float]:
        if not holds:
            return None
        idx = min(len(holds) - 1, max(0, int(round(p * (len(holds) - 1)))))
        return holds[idx]

    over_24h = sum(1 for h in holds if h > 24.0)
    return {
        "n_closed": len(rows),
        "total_pnl": round(sum(pnls), 4),
        "win_rate": round(len(wins) / len(pnls), 3) if pnls else 0.0,
        "hold_hours": {
            "n": len(holds),
            "median": _pct(0.5),
            "p90": _pct(0.9),
            "max": round(max(holds), 2) if holds else None,
            "over_24h": over_24h,
            "over_24h_ratio": round(over_24h / len(holds), 3) if holds else None,
        },
        "by_symbol": _group_pnl(rows),
    }


def _trades_since(db, account_id: int, horizon: str, hours: int = 24,
                  window=None) -> Dict[str, Any]:
    """该车道在窗口内的平仓统计。window 未给时按「现在回溯 hours 小时」。"""
    lane = lane_sem.get_spec(horizon).lane
    win = window or _window_from_now(hours)
    rows = [r for r in _closed_rows(db, account_id, win) if _lane_of(r) == lane]
    return _stats_from_rows(rows)


def _group_pnl(rows) -> List[Dict[str, Any]]:
    from backend.services.pnl_authority import realized_pnl

    d: Dict[str, float] = {}
    for r in rows:
        s = str(r.symbol or "?").upper()
        d[s] = d.get(s, 0.0) + realized_pnl(r)
    items = sorted(d.items(), key=lambda x: -x[1])[:5]
    return [{"symbol": k, "pnl": round(v, 2)} for k, v in items]


def _symbol_daily(db, account_id: int, horizon: str, hours: int = 24,
                  window=None) -> List[Dict[str, Any]]:
    """D2 feed: 该车道窗口内每币种已实现 PnL 与平仓笔数（驱动 symbol_penalty 状态机）。"""
    from backend.services.pnl_authority import realized_pnl

    lane = lane_sem.get_spec(horizon).lane
    win = window or _window_from_now(hours)
    agg: Dict[str, Dict[str, float]] = {}
    for r in _closed_rows(db, account_id, win):
        if _lane_of(r) != lane:
            continue
        s = str(r.symbol or "?").upper()
        d = agg.setdefault(s, {"pnl": 0.0, "n": 0})
        d["pnl"] += realized_pnl(r)
        d["n"] += 1
    return [{"symbol": k, "pnl": round(v["pnl"], 4), "n": int(v["n"])}
            for k, v in sorted(agg.items(), key=lambda x: x[1]["pnl"])]


def _exit_stats(db, account_id: int, horizon: str, hours: int = 24) -> Dict[str, Any]:
    """D1: 退出通道统计（max_hold_timeout 占比监控）。"""
    from collections import Counter
    from datetime import timedelta

    try:
        from backend.database.models import PositionExitEvent
        cutoff = _window_from_now(hours)[0]
        rows = db.query(PositionExitEvent).filter(
            PositionExitEvent.account_id == int(account_id),
            PositionExitEvent.created_at >= cutoff,
        ).all()
        dist = Counter(str(getattr(r, "exit_channel", None) or "unknown") for r in rows)
        return {"total_exits": len(rows), "max_hold_timeout": int(dist.get("max_hold_timeout", 0)),
                "by_channel": dict(dist.most_common(8))}
    except Exception as e:
        logger.debug("[PeriodDaily] exit_stats failed: %s", e)
        return {"total_exits": 0, "max_hold_timeout": 0, "by_channel": {}}


def _open_positions(db, account_id: int, horizon: str) -> List[Dict[str, Any]]:
    """该车道在手仓位。

    `nature`/`tier` 原样输出（不做美化）：报告要如实反映引擎写下的标签，
    否则又会出现「报告说中线、引擎写 scalp」的二次错位。
    """
    from backend.database.models import PaperPosition

    lane = lane_sem.get_spec(horizon).lane
    poss = db.query(PaperPosition).filter(
        PaperPosition.account_id == int(account_id),
        PaperPosition.status == "open",
    ).all()
    out = []
    for p in poss:
        if _lane_of(p) != lane:
            continue
        out.append({
            "symbol": p.symbol, "side": p.side,
            "lane": lane,
            "nature": getattr(p, "trade_nature", None),
            "tier": getattr(p, "timeframe_tier", None),
            "expected_hold_hours": float(getattr(p, "expected_hold_hours", 0) or 0) or None,
            "held_hours": _hold_hours_open(p),
            "entry_price": float(p.entry_price or 0),
            "mark_price": float(p.mark_price or 0),
            "unrealized_pnl": round(float(p.unrealized_pnl or 0), 4),
            "sl_price": float(p.sl_price or 0) or None,
            "peak_pnl_pct": float(getattr(p, "peak_pnl_pct", 0) or 0),
            "opened_at": str(getattr(p, "opened_at", None)),
        })
    return out


def _hold_hours_open(row) -> Optional[float]:
    """在手仓位的已持有小时数。"""
    try:
        o = getattr(row, "opened_at", None)
        if o is None:
            return None
        return round((now_local() - o).total_seconds() / 3600.0, 3)
    except Exception:
        return None


def _l1_panel(symbols=None) -> Dict[str, Any]:
    """各核心币 L1 状态面板（trend_layer.classify 快照）。

    计算较重（每币取 K 线 + BOCPD 变点 + Wyckoff 相位）。**只对当日报告生成**
    （见 build_daily_report 的 is_today 守卫）——回填历史日期时跑它既慢又无意义。
    """
    from backend.services import long_trend_v2 as lv2
    syms = symbols or ["BTC", "ETH", "SOL", "BNB", "XRP"]
    panel = {}
    for s in syms:
        try:
            df, c = lv2._get_l1_classification(s)
            if c is None:
                panel[s] = {"state": "n/a", "reason": "数据不足"}
                continue
            panel[s] = {
                "state": c.get("state"), "score": c.get("score"),
                "strength": c.get("strength"), "signals": c.get("signals", {}),
                "target": c.get("target"), "close": c.get("close"),
            }
            # [B1/B2] 趋势起始点（BOCPD 变点）与 Wyckoff 相位（报告观测字段）
            try:
                from backend.services.trend_inception import inception_check
                panel[s]["inception"] = inception_check(df)
            except Exception:
                pass
            try:
                from backend.services.wyckoff_phase import classify_phase as _wp
                panel[s]["wyckoff"] = _wp(df)
            except Exception:
                pass
        except Exception as e:
            panel[s] = {"state": "error", "reason": str(e)[:80]}
    return panel


def _quality_block(db, account_id: int, lane: str, hours: int = 24, window=None) -> Dict[str, Any]:
    """车道数据质量提示：标签矛盾 / 无标签的样本数。

    静默把标签矛盾的仓位塞进某条车道，正是「分不出哪个是哪个」的成因之一；
    这里把它显式暴露出来。
    """
    win = window or _window_from_now(hours)
    mismatched: List[Dict[str, Any]] = []
    untagged = 0
    try:
        for r in _closed_rows(db, account_id, win):
            if _lane_of(r) != lane:
                continue
            note = lane_sem.lane_mismatch(
                getattr(r, "trade_nature", None), getattr(r, "timeframe_tier", None))
            if note:
                mismatched.append({"symbol": r.symbol, "issue": note})
            elif not getattr(r, "trade_nature", None) and not getattr(r, "timeframe_tier", None):
                untagged += 1
    except Exception as e:
        logger.debug("[PeriodDaily] quality block failed: %s", e)
    return {
        "mismatched_labels": len(mismatched),
        "mismatched_samples": mismatched[:5],
        "untagged_trades": untagged,
    }


# ── 报告构造 ────────────────────────────────────────────────────────────
def _lane_section(db, account_id: int, lane: str, symbols=None,
                  report_date: Optional[str] = None, window=None) -> Dict[str, Any]:
    """构造单条车道的日报段。

    window 非空时按该窗口取数（历史回填走这条路），否则按「现在回溯 24h」。
    """
    from backend.services.loss_attribution import build_loss_attribution

    spec = lane_sem.get_spec(lane)
    win = window or _window_from_now(24)
    sec: Dict[str, Any] = {
        # 周期身份：前端直接展示，不必再猜
        "lane_identity": spec.identity(),
        "window": {"start": str(win[0]), "end": str(win[1])},
        "trades_24h": _trades_since(db, account_id, lane, window=win),
        "open_positions": _open_positions(db, account_id, lane),
        "loss_attribution": build_loss_attribution(db, account_id, lane, days=1, window=win),
        "symbol_daily": _symbol_daily(db, account_id, lane, window=win),
        "data_quality": _quality_block(db, account_id, lane, window=win),
    }
    if lane == lane_sem.LANE_INTRADAY:
        # D1 超时退出监控原本挂在 scalp 段；scalp 已并入日内车道，监控随之迁移，
        # 否则该指标会随「短线车道停用」一起从报告里消失。
        sec["exit_stats"] = _exit_stats(db, account_id, lane)
    if lane == lane_sem.LANE_TREND:
        sec["actions_24h"] = _drain_actions((window or _window_from_now(24))[0].timestamp(),
                                            lane=lane)
        # [B3/B4] 宏观顺逆风 + 减半周期相位（月频慢变量，写报告观测）
        try:
            from backend.services.macro_tailwind import compute_macro_tailwind
            sec["macro_tailwind"] = compute_macro_tailwind(db)
        except Exception:
            pass
        try:
            from backend.services.halving_phase import compute_halving_phase
            sec["halving_phase"] = compute_halving_phase()
        except Exception:
            pass
    # L1 面板是长线车道的盘面背景，但只在生成「当日」报告时算
    if lane == lane_sem.LANE_TREND and _is_today(report_date or _today_utc()):
        sec["l1_panel"] = _l1_panel(symbols)
    return sec


def build_daily_report(db, account_id: int, symbols=None,
                       date: Optional[str] = None) -> Dict[str, Any]:
    """生成双车道日报 dict（不落库）。

    `date` 为过去日期时按该日历日的 UTC 窗口取数（精确重建历史），
    为 None/今日时按「现在回溯 24h」（与 cron 08:05 生成的口径一致）。
    """
    report_date = date or _today_utc()
    window = None if _is_today(report_date) else day_window(report_date)
    sections = {}
    for lane in lane_sem.report_lanes():
        sections[lane] = _lane_section(db, account_id, lane, symbols=symbols,
                                       report_date=report_date, window=window)
    return {
        "report_date": report_date,
        "account_id": int(account_id),
        "lanes": list(lane_sem.report_lanes()),
        "window_mode": "live_24h" if window is None else "calendar_day",
        "sections": sections,
    }


def _llm_prompt_for(lane: str, sec: Dict[str, Any], report_date: str) -> str:
    spec = lane_sem.get_spec(lane)
    brief = json.dumps(
        {k: v for k, v in sec.items() if k != "l1_panel"},
        ensure_ascii=False, default=str,
    )[:3000]
    return (
        f"你是加密货币交易系统的「{spec.label_full}」车道日报分析师。\n"
        f"该车道的周期身份：主看 {spec.report_timeframe} K线"
        f"（确认周期 {'/'.join(spec.confirm_timeframes)}），"
        f"设计期望持仓 {spec.expected_hold_hours:g}h，期望区间 {lane_sem.hold_bracket_label(lane)}。\n"
        f"以下是 {report_date} 该车道的日报数据：\n{brief}\n\n"
        f"请用 3-5 句话只总结**这一条车道**：今日表现、亏损归因、持仓时长是否偏离该车道应有的周期"
        f"、明日应关注的风险点。不要提其它车道。只输出结论。"
    )


def _llm_analysis(db, account_id: int, lane: str, sec: Dict[str, Any],
                  report_date: str) -> Optional[str]:
    """LLM 对单条车道日报做定性分析（可选，失败静默，非交易路径）。"""
    try:
        import os
        if os.getenv("LLM_PERIOD_DAILY_REVIEW", "0").strip().lower() not in ("1", "true", "yes", "on"):
            return None
        from backend.services.llm_config_service import get_llm_config_for_account, call_llm_api_sync
        cfg = get_llm_config_for_account(account_id) if account_id else None
        if not cfg:
            return None
        return call_llm_api_sync(
            cfg,
            [{"role": "user", "content": _llm_prompt_for(lane, sec, report_date)}],
            caller="PeriodDailyReport",
        )
    except Exception as e:
        logger.debug("[PeriodDailyReport] LLM 分析跳过(%s): %s", lane, e)
        return None


def _upsert_lane_row(db, account_id: int, rdate: str, lane: str):
    """按 (account, date, lane) 幂等 upsert 定位或创建行；调用方负责赋值与 commit。

    两个必须处理的存量情况：

    1. **迁移落地前**：库里行的 `lane` 仍为 NULL，只有旧的 `horizon` 有值。
       故用 `lane == X OR horizon == X` 定位；`lane == 'intraday'` 对 NULL 求值为 NULL
       （非真），OR 分支不会误命中其它车道。
    2. **旧三档塌缩成双车道**：同一 (账户, 日期) 下 `scalp` 与 `midlong` 两行都归入
       intraday（实测每天都是这样）。只 update 一行会让另一行变成永不刷新的僵尸副本，
       报告里出现同名双行 —— 这里保留「horizon 恰好等于车道名」的那行，其余删除。
    """
    from backend.database.models import PeriodDailyReport

    existing = db.query(PeriodDailyReport).filter(
        PeriodDailyReport.account_id == int(account_id),
        PeriodDailyReport.report_date == rdate,
    ).all()
    same = [r for r in existing
            if _row_lane(r) == lane or str(getattr(r, "horizon", "") or "").lower() == lane]
    if same:
        keep = same[0]
        for extra in same[1:]:
            db.delete(extra)
            logger.info("[PeriodDaily] 合并重复车道行 id=%s → id=%s (lane=%s %s)",
                        extra.id, keep.id, lane, rdate)
        return keep

    row = PeriodDailyReport(account_id=int(account_id), report_date=rdate, horizon=lane)
    if _has_lane_column():
        row.lane = lane
    db.add(row)
    return row


def _row_lane(row) -> Optional[str]:
    """读一行已有的车道（lane 优先，回退 horizon；两者都是旧值时做等价换算）。"""
    return (lane_sem.lane_for_label(getattr(row, "lane", None))
            or lane_sem.lane_for_label(getattr(row, "horizon", None)))


_LANE_COLUMN_CACHE: Optional[bool] = None


def _has_lane_column() -> bool:
    """period_daily_reports.lane 是否存在（未跑迁移的库降级到 horizon 列）。"""
    global _LANE_COLUMN_CACHE
    if _LANE_COLUMN_CACHE is None:
        try:
            from backend.database.models import PeriodDailyReport
            _LANE_COLUMN_CACHE = "lane" in PeriodDailyReport.__table__.columns
        except Exception:
            _LANE_COLUMN_CACHE = False
    return bool(_LANE_COLUMN_CACHE)


def save_daily_report(db, account_id: int, symbols=None, date: Optional[str] = None) -> Optional[int]:
    """生成并落库双车道日报（按 date+account+lane 幂等 upsert）。返回行数。"""
    report = build_daily_report(db, account_id, symbols=symbols, date=date)
    rdate = report["report_date"]
    # report directives: D1 timeout retrain / D2 losing-symbol penalty
    _directives = None
    try:
        from backend.services.report_directives import analyze_directives
        _directives = analyze_directives(report)
    except Exception as _de:
        logger.debug("[PeriodDaily] directives failed: %s", _de)
    # 防御：build 阶段各段查询失败被吞时可能留 aborted 事务——rollback 后再落库
    try:
        db.rollback()
    except Exception:
        pass
    n = 0
    for lane, sec in report["sections"].items():
        if _directives:
            sec["directives"] = [d for d in _directives if d.get("lane") == lane]
        row = _upsert_lane_row(db, account_id, rdate, lane)
        row.payload_json = json.dumps(sec, ensure_ascii=False, default=str)
        row.horizon = lane  # 旧列同步为规范车道名，避免 lane/horizon 两列互相矛盾
        n += 1
    db.commit()

    # LLM 分析：**每条车道各自一段**（旧版把同一段写给全部行，导致点「长线」看到三周期混合总结）
    try:
        if _llm_enabled():
            for lane, sec in report["sections"].items():
                summary = _llm_analysis(db, account_id, lane, sec, rdate)
                if not summary:
                    continue
                row = _upsert_lane_row(db, account_id, rdate, lane)
                row.llm_summary = summary
            db.commit()
    except Exception:
        db.rollback()
    return n


def _llm_enabled() -> bool:
    import os
    return os.getenv("LLM_PERIOD_DAILY_REVIEW", "0").strip().lower() in ("1", "true", "yes", "on")


def rebuild_daily_report(db, account_id: int, date: str) -> int:
    """按**某个历史日历日**从源数据重建该日双车道日报（幂等覆盖）。返回落库行数。

    为什么需要它：旧表的行是按三档（scalp / midlong 各一行）写的，
    并成双车道后同一天必然出现两行归属同一条车道。搬旧 payload 不能解决——
    旧 payload 本身就是「scalp 段」和「midlong 段」两份互不相干的内容，
    合并只会把同一批仓位算两遍。唯一正确的做法是从 `paper_positions`
    按该日 UTC 窗口重新聚合。

    顺带把旧版本写进 payload 的内部枚举（`midlong`）一并替换为车道展示名。
    """
    report = build_daily_report(db, account_id, date=date)
    rdate = report["report_date"]
    try:
        db.rollback()  # 清理 build 阶段被吞掉的失败查询可能留下的事务
    except Exception:
        pass
    directives = None
    try:
        from backend.services.report_directives import analyze_directives
        directives = analyze_directives(report)
    except Exception:
        pass
    n = 0
    for lane, sec in report["sections"].items():
        if directives:
            sec["directives"] = [d for d in directives if d.get("lane") == lane]
        row = _upsert_lane_row(db, account_id, rdate, lane)
        row.payload_json = json.dumps(sec, ensure_ascii=False, default=str)
        row.horizon = lane  # 旧列同步为规范车道名
        row.llm_summary = None  # 旧总结是三周期混写的，重建后不再保留
        n += 1
    db.commit()
    return n


def rebuild_recent_reports(account_ids: Optional[List[int]] = None,
                           days: int = 30) -> Dict[str, Any]:
    """回填近 N 天日报（含把旧三档行合并为双车道）。返回统计。

    账户范围 = `paper_positions` 出现过的账户 **并上** `period_daily_reports` 已有行的账户：
    有些账户已经没有在手仓位（例如 188 已停），只查 positions 会漏掉它们，
    留下永远合并不掉的旧三档副本（实测正是 188 的几天）。

    日期范围含**今日**（`range(days+1)`）：当日那行若由旧代码写过（`horizon=scalp`
    与 `horizon=midlong` 两行），不重建就会一直以重复行形态留在报告里。
    """
    from datetime import timedelta

    out: Dict[str, Any] = {"accounts": 0, "days": 0, "rows": 0, "errors": []}
    try:
        from backend.core.tenant import set_system_identity
        set_system_identity()
        from backend.database.connection import SessionLocal
        from backend.database.models import PaperPosition, PeriodDailyReport
        db = SessionLocal()
        try:
            if not account_ids:
                ids = {int(r[0]) for r in db.query(PaperPosition.account_id).distinct().all()
                       if r[0] is not None}
                ids |= {int(r[0]) for r in db.query(PeriodDailyReport.account_id).distinct().all()
                        if r[0] is not None}
                account_ids = sorted(ids)
            out["accounts"] = len(account_ids)
            today = now_local().date()
            for acct in account_ids:
                for back in range(0, int(days) + 1):
                    d = (today - timedelta(days=back)).strftime("%Y-%m-%d")
                    try:
                        out["rows"] += int(rebuild_daily_report(db, int(acct), d) or 0)
                        out["days"] += 1
                    except Exception as e:
                        out["errors"].append({"account_id": acct, "date": d, "error": str(e)[:120]})
                        try:
                            db.rollback()
                        except Exception:
                            pass
        finally:
            db.close()
    except Exception as e:
        out["errors"].append({"error": str(e)[:200]})
        logger.warning("[PeriodDaily] 回填失败: %s", e)
    return out


def run_daily_reports() -> Dict[str, Any]:
    """cron 入口：遍历活跃会话生成日报。表缺失/异常静默降级。"""
    out = {"sessions": 0, "saved": 0, "error": None, "lanes": list(lane_sem.report_lanes())}
    try:
        # [2026-08-19] cron 后台线程无 HTTP 上下文，RLS fail-closed 会让日报全空
        # （trades/loss_attribution/symbol_daily 全部 0 行）→ 设管理员级身份穿透 RLS。
        from backend.core.tenant import set_system_identity
        set_system_identity()
        from backend.database.connection import SessionLocal
        from backend.database.models import FullAutoSession
        db = SessionLocal()
        try:
            sessions = db.query(FullAutoSession).filter(
                FullAutoSession.status.in_(["running", "defensive"])
            ).all()
            out["sessions"] = len(sessions)
            for s in sessions:
                acct = getattr(s, "paper_account_id", None) or getattr(s, "account_id", None)
                if not acct:
                    continue
                try:
                    out["saved"] += int(save_daily_report(db, int(acct)) or 0)
                except Exception as e:
                    logger.warning("[PeriodDailyReport] 会话 %s 日报失败: %s", s.session_id, e)
        finally:
            db.close()
    except Exception as e:
        out["error"] = str(e)[:200]
        logger.warning("[PeriodDailyReport] 日报任务失败: %s", e)
    return out
