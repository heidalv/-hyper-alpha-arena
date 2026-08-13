"""
S3-3（04 综合方案 §3.5 质量门）：中长线策略周度绩效报表。

对应 04 综合方案要求的"周度绩效卡（胜率/盈亏比/同向再开率/分档 TP 触达率）"，
用于持续监控 S0-S2 止血修复上线后的真实效果，判断是否达到 §3.5 的"可用定义"：
  - 样本量 ≥ 40 笔
  - 胜率 ≥ 40% 或 盈亏比 ≥ 1.8（二者满足其一且期望值为正）
  - 亏损后 24h 同向再开率 ≤ 20%（S0 验收线）

数据来源说明（务实简化，非 S3-1/S3-2 的完整 regime_at_entry/close_reason 枚举
统一改造——那是更大的 schema 改动，本脚本先用现有字段跑起来产出可读报表，
避免"要等大改造完成才有可视化"的空窗期）：
  - 盈亏/胜率/盈亏比：来自 paper_orders（close_reason IS NOT NULL 的平仓单，
    其 pnl 字段是权威已实现盈亏）。
  - 持仓时长/分档 TP 触达率：来自 paper_positions（tp_level_reached 字段）。
  - 同向再开率：扫 paper_positions 按 (account_id, symbol, side) 时间排序，
    亏损全平后 24h 内是否有同方向新开仓。

用法：
    cd backend && python scripts/midlong_weekly_report.py [--days 14] [--out report.md]
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import bindparam, text  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

NATURES = ("swing", "trend_follow", "position")
_MIDISH = set(NATURES) | {"mid", "long"}


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _event_log_path() -> str:
    return os.getenv(
        "EVENT_LOG_PATH",
        os.path.join(_repo_root(), "data", "event_log.jsonl"),
    )


class _ERow:
    """轻量行对象，兼容 compute_order_stats / compute_open_stats。"""

    __slots__ = (
        "id", "account_id", "symbol", "side", "trade_nature", "close_reason",
        "pnl", "filled_at", "timeframe_tier", "status", "tp_level_reached",
        "opened_at", "closed_at", "partial_realized_pnl", "size",
        "entry_price", "margin", "leverage",
    )

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _parse_iso(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _fetch_event_log_midlong(days: int) -> tuple:
    """当 paper_* 空库时，从 event_log.jsonl 回填中长线开/平样本。

    返回 (order_like_rows, position_like_rows)。平仓按 aggregate_id 去重。
    """
    path = _event_log_path()
    if not os.path.isfile(path):
        return [], []
    cutoff = datetime.utcnow() - timedelta(days=int(days))
    # 用 naive UTC 比较；event 时间可能带 tz
    opens: dict = {}
    closes: dict = {}
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                et = str(ev.get("event_type") or "")
                if et not in ("PositionOpened", "PositionClosed"):
                    continue
                pl = ev.get("payload") or ev.get("data") or {}
                if not isinstance(pl, dict):
                    continue
                tn = str(
                    pl.get("trade_nature")
                    or pl.get("nature")
                    or pl.get("timeframe_tier")
                    or ""
                ).lower()
                if tn not in _MIDISH:
                    # close 可能缺 nature：若 open 已登记则补
                    pid = str(ev.get("aggregate_id") or pl.get("position_id") or "")
                    if et == "PositionClosed" and pid in opens:
                        tn = opens[pid].trade_nature
                    else:
                        continue
                if tn == "mid":
                    tn = "swing"
                if tn == "long":
                    tn = "trend_follow"
                ts = _parse_iso(ev.get("timestamp") or ev.get("occurred_at") or "")
                if ts is None:
                    continue
                ts_naive = ts.replace(tzinfo=None) if ts.tzinfo else ts
                if ts_naive < cutoff:
                    continue
                pid = str(ev.get("aggregate_id") or pl.get("position_id") or "")
                if not pid:
                    continue
                if et == "PositionOpened":
                    opens[pid] = _ERow(
                        id=int(pid) if str(pid).isdigit() else 0,
                        account_id=pl.get("account_id"),
                        symbol=str(pl.get("symbol") or "").upper(),
                        side=str(pl.get("side") or ""),
                        trade_nature=tn,
                        timeframe_tier="mid" if tn == "swing" else "long",
                        status="open",
                        tp_level_reached=0,
                        opened_at=ts_naive,
                        closed_at=None,
                        partial_realized_pnl=0.0,
                        size=pl.get("size"),
                        entry_price=pl.get("entry_price"),
                        margin=pl.get("margin"),
                        leverage=pl.get("leverage"),
                        close_reason=None,
                        pnl=None,
                        filled_at=ts_naive,
                    )
                else:
                    pnl = float(
                        pl.get("realized_pnl")
                        or pl.get("pnl")
                        or pl.get("partial_realized_pnl")
                        or 0
                    )
                    reason = pl.get("close_reason") or pl.get("reason") or "unknown"
                    o = opens.get(pid)
                    closes[pid] = _ERow(
                        id=int(pid) if str(pid).isdigit() else 0,
                        account_id=pl.get("account_id") or (o.account_id if o else None),
                        symbol=str(pl.get("symbol") or (o.symbol if o else "") or "").upper(),
                        side=str(pl.get("side") or (o.side if o else "") or ""),
                        trade_nature=tn or (o.trade_nature if o else "swing"),
                        close_reason=str(reason),
                        pnl=pnl,
                        filled_at=ts_naive,
                        timeframe_tier="mid" if (tn or "") == "swing" else "long",
                        status="closed",
                        tp_level_reached=int(pl.get("tp_level_reached") or 0),
                        opened_at=o.opened_at if o else None,
                        closed_at=ts_naive,
                        partial_realized_pnl=pnl,
                        size=pl.get("size") or (o.size if o else None),
                        entry_price=pl.get("entry_price") or (o.entry_price if o else None),
                        margin=pl.get("margin") or (o.margin if o else None),
                        leverage=pl.get("leverage") or (o.leverage if o else None),
                    )
    except Exception:
        return [], []

    # 已平仓的 open 标记 closed
    positions = []
    for pid, o in opens.items():
        c = closes.get(pid)
        if c:
            o.status = "closed"
            o.closed_at = c.closed_at
            o.close_reason = c.close_reason
            o.partial_realized_pnl = c.pnl
            o.tp_level_reached = c.tp_level_reached
        positions.append(o)
    # 仅有 close 没有 open 的也补一条 position
    for pid, c in closes.items():
        if pid not in opens:
            positions.append(c)
    orders = list(closes.values())
    return orders, positions


def _fetch_orders(db, days: int):
    stmt = text(
        f"""
        SELECT id, account_id, symbol, side, trade_nature, close_reason, pnl, filled_at
        FROM paper_orders
        WHERE close_reason IS NOT NULL
          AND trade_nature IN :natures
          AND filled_at > NOW() - INTERVAL '{int(days)} days'
        ORDER BY filled_at
        """
    ).bindparams(bindparam("natures", expanding=True))
    rows = db.execute(stmt, {"natures": list(NATURES)})
    return rows.fetchall()


def _fetch_positions(db, days: int):
    stmt = text(
        f"""
        SELECT id, account_id, symbol, side, trade_nature, timeframe_tier,
               close_reason, status, tp_level_reached, opened_at, closed_at,
               partial_realized_pnl, size, entry_price, margin, leverage
        FROM paper_positions
        WHERE trade_nature IN :natures
          AND opened_at > NOW() - INTERVAL '{int(days)} days'
        ORDER BY account_id, symbol, side, opened_at
        """
    ).bindparams(bindparam("natures", expanding=True))
    rows = db.execute(stmt, {"natures": list(NATURES)})
    return rows.fetchall()


def compute_open_stats(positions) -> dict:
    """窗口内开仓数（按 trade_nature）。"""
    stats = defaultdict(int)
    for p in positions:
        stats[p.trade_nature or "unknown"] += 1
    return dict(stats)


def compute_funding_adjusted_pnl(db, days: int, order_stats: dict) -> dict:
    """平仓净盈亏 + 同期中长线相关 funding 流水。"""
    gross = {}
    for nature, s in order_stats.items():
        gp = float(s.get("gross_profit") or 0)
        gl = float(s.get("gross_loss") or 0)
        gross[nature] = gp - gl
    total_trade_pnl = sum(gross.values())
    funding = 0.0
    try:
        row = db.execute(
            text(
                f"""
                SELECT COALESCE(SUM(f.payment), 0) AS funding_sum
                FROM paper_funding_ledger f
                WHERE f.settled_at > NOW() - INTERVAL '{int(days)} days'
                  AND (
                    f.position_id IN (
                      SELECT id FROM paper_positions
                      WHERE trade_nature IN :natures
                    )
                    OR EXISTS (
                      SELECT 1 FROM paper_positions p
                      WHERE p.trade_nature IN :natures
                        AND p.account_id = f.account_id
                        AND UPPER(p.symbol) = UPPER(f.symbol)
                        AND p.opened_at <= f.settled_at
                        AND (p.closed_at IS NULL OR p.closed_at >= f.settled_at)
                    )
                  )
                """
            ).bindparams(bindparam("natures", expanding=True)),
            {"natures": list(NATURES)},
        ).first()
        funding = float(row[0] or 0) if row else 0.0
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        funding = 0.0
    return {
        "trade_pnl": total_trade_pnl,
        "funding_payment": funding,
        "net_pnl_with_funding": total_trade_pnl + funding,
        "by_nature_trade_pnl": gross,
    }


def compute_max_same_dir_exposure(positions) -> dict:
    """按开平事件重建中长线同向名义峰值（无权益历史时仅报名义与并发数）。"""
    events = []
    for p in positions:
        try:
            size = float(p.size or 0)
            entry = float(p.entry_price or 0)
            notional = abs(size * entry) if size and entry else abs(float(p.margin or 0) * float(p.leverage or 1))
        except Exception:
            notional = 0.0
        side = str(p.side or "").lower()
        sign = 1 if side in ("long", "buy") else (-1 if side in ("short", "sell") else 0)
        if not p.opened_at or sign == 0 or notional <= 0:
            continue
        events.append((p.opened_at, "open", sign, notional, p.account_id))
        if p.closed_at:
            events.append((p.closed_at, "close", sign, notional, p.account_id))
    events.sort(key=lambda x: x[0])

    # per account open book: list of (sign, notional)
    books = defaultdict(list)
    max_long_n = 0.0
    max_short_n = 0.0
    max_net_abs = 0.0
    max_long_cnt = 0
    max_short_cnt = 0

    for _ts, kind, sign, notional, acct in events:
        book = books[acct]
        if kind == "open":
            book.append((sign, notional))
        else:
            for i, (s, n) in enumerate(book):
                if s == sign and abs(n - notional) < 1e-6:
                    book.pop(i)
                    break
            else:
                # 近似：减掉同向一笔
                for i, (s, n) in enumerate(book):
                    if s == sign:
                        book.pop(i)
                        break
        long_n = sum(n for s, n in book if s > 0)
        short_n = sum(n for s, n in book if s < 0)
        long_c = sum(1 for s, _ in book if s > 0)
        short_c = sum(1 for s, _ in book if s < 0)
        max_long_n = max(max_long_n, long_n)
        max_short_n = max(max_short_n, short_n)
        max_net_abs = max(max_net_abs, abs(long_n - short_n))
        max_long_cnt = max(max_long_cnt, long_c)
        max_short_cnt = max(max_short_cnt, short_c)

    return {
        "max_long_notional": round(max_long_n, 2),
        "max_short_notional": round(max_short_n, 2),
        "max_net_abs_notional": round(max_net_abs, 2),
        "max_long_count": max_long_cnt,
        "max_short_count": max_short_cnt,
    }


def compute_nibble_conversion(days: int) -> dict:
    """Hub NIBBLE/BUILD → 24h 内中长线开仓转化率（analytics DB）。"""
    import json as _json
    try:
        from backend.database.connection import AnalyticsSessionLocal
    except Exception:
        return {"nibble": 0, "build": 0, "converted": 0, "rate": 0.0, "error": "no_analytics"}

    adb = AnalyticsSessionLocal()
    core = SessionLocal()
    # [2026-08-14 F5 整改] 同上：核心库连接补租户上下文（RLS）
    try:
        core.connection().exec_driver_sql("SET app.is_admin = 'on'")
    except Exception:
        pass
    try:
        rows = adb.execute(
            text(
                f"""
                SELECT te.ts, t.symbol, te.payload_json
                FROM mlto_thesis_events te
                JOIN mlto_thesis t ON t.thesis_id = te.thesis_id
                WHERE te.event_type = 'hub_decision'
                  AND t.tier = 'long'
                  AND te.ts > NOW() - INTERVAL '{int(days)} days'
                ORDER BY te.ts
                """
            )
        ).fetchall()
        nibble_ts = []
        build_n = 0
        for r in rows:
            action = ""
            try:
                payload = r.payload_json
                if isinstance(payload, str):
                    payload = _json.loads(payload or "{}")
                if isinstance(payload, dict):
                    action = str(payload.get("action") or "").upper()
            except Exception:
                action = ""
            if action == "NIBBLE":
                nibble_ts.append((r.ts, str(r.symbol or "").upper()))
            elif action == "BUILD":
                build_n += 1
                nibble_ts.append((r.ts, str(r.symbol or "").upper()))

        # 开仓样本（中长线）
        opens = core.execute(
            text(
                f"""
                SELECT UPPER(symbol) AS symbol, opened_at
                FROM paper_positions
                WHERE trade_nature IN :natures
                  AND opened_at > NOW() - INTERVAL '{int(days) + 2} days'
                """
            ).bindparams(bindparam("natures", expanding=True)),
            {"natures": list(NATURES)},
        ).fetchall()
        open_list = [(o.opened_at, o.symbol) for o in opens if o.opened_at]
        # P0/P1：paper_positions 空时回退 event_log，避免转化率永久 0%
        if not open_list:
            try:
                _eo, _ep = _fetch_event_log_midlong(int(days) + 2)
                for p in _ep or []:
                    if getattr(p, "opened_at", None) and getattr(p, "symbol", None):
                        open_list.append((p.opened_at, str(p.symbol).upper()))
            except Exception:
                pass

        converted = 0
        for ts, sym in nibble_ts:
            for oa, osym in open_list:
                if osym != sym:
                    continue
                try:
                    # analytics ts 可能带 tz；event_log 为 naive
                    _oa = oa
                    _ts = ts
                    if hasattr(_oa, "tzinfo") and _oa.tzinfo and getattr(_ts, "tzinfo", None) is None:
                        _ts = _ts.replace(tzinfo=_oa.tzinfo) if hasattr(_ts, "replace") else _ts
                    if hasattr(_ts, "tzinfo") and _ts.tzinfo and getattr(_oa, "tzinfo", None) is None:
                        _oa = _oa.replace(tzinfo=_ts.tzinfo) if hasattr(_oa, "replace") else _oa
                    gap_h = (_oa - _ts).total_seconds() / 3600.0
                except Exception:
                    continue
                if 0 <= gap_h <= 24:
                    converted += 1
                    break

        total = len(nibble_ts)
        return {
            "nibble_or_build_events": total,
            "nibble_only": total - build_n,
            "build": build_n,
            "converted_opens_24h": converted,
            "conversion_rate": (converted / total) if total else 0.0,
            "open_samples": len(open_list),
        }
    except Exception as e:
        try:
            adb.rollback()
            core.rollback()
        except Exception:
            pass
        return {"nibble_or_build_events": 0, "converted_opens_24h": 0, "conversion_rate": 0.0, "error": str(e)[:120]}
    finally:
        try:
            adb.close()
            core.close()
        except Exception:
            pass


def compute_reopen_rate(positions) -> dict:
    """按 (account_id, symbol, side) 分组，统计亏损全平后 24h 内同向再开次数。"""
    groups = defaultdict(list)
    for p in positions:
        if p.status != "closed" or not p.closed_at:
            continue
        groups[(p.account_id, p.symbol, p.side)].append(p)

    per_nature_loss_closes = defaultdict(int)
    per_nature_reopens = defaultdict(int)

    for key, plist in groups.items():
        plist_sorted = sorted(plist, key=lambda x: x.closed_at)
        for i, p in enumerate(plist_sorted):
            pnl = float(p.partial_realized_pnl or 0)
            is_loss = pnl < 0 or (p.close_reason or "").lower() in (
                "sl", "stop_loss", "liquidation", "margin_call",
            )
            if not is_loss:
                continue
            nature = p.trade_nature or "unknown"
            per_nature_loss_closes[nature] += 1
            # 找同 symbol+side 的下一笔开仓（这里近似用同组下一条记录的 opened_at）
            for nxt in plist_sorted[i + 1:]:
                if nxt.opened_at and p.closed_at:
                    gap_h = (nxt.opened_at - p.closed_at).total_seconds() / 3600.0
                    if 0 <= gap_h <= 24:
                        per_nature_reopens[nature] += 1
                    break
    return {
        "loss_closes": dict(per_nature_loss_closes),
        "reopens_24h": dict(per_nature_reopens),
    }


def compute_order_stats(orders) -> dict:
    stats = defaultdict(lambda: {
        "count": 0, "wins": 0, "gross_profit": 0.0, "gross_loss": 0.0,
        "close_reason_dist": defaultdict(int),
    })
    for o in orders:
        nature = o.trade_nature or "unknown"
        s = stats[nature]
        s["count"] += 1
        pnl = float(o.pnl or 0)
        if pnl > 0:
            s["wins"] += 1
            s["gross_profit"] += pnl
        elif pnl < 0:
            s["gross_loss"] += abs(pnl)
        s["close_reason_dist"][(o.close_reason or "unknown")] += 1
    return stats


def compute_tp_stage_stats(positions) -> dict:
    stats = defaultdict(lambda: defaultdict(int))
    for p in positions:
        nature = p.trade_nature or "unknown"
        stats[nature][int(p.tp_level_reached or 0)] += 1
    return stats


def render_report(
    days: int,
    order_stats: dict,
    tp_stats: dict,
    reopen: dict,
    open_stats: Optional[dict] = None,
    funding: Optional[dict] = None,
    exposure: Optional[dict] = None,
    nibble: Optional[dict] = None,
    funnel: Optional[dict] = None,
    data_source: str = "paper_db",
) -> str:
    lines = []
    lines.append(f"# 中长线策略周度绩效报表（近 {days} 天）")
    lines.append("")
    lines.append(f"生成时间: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    _src_note = (
        "paper_orders + paper_positions + paper_funding_ledger + mlto_thesis_events"
        if data_source == "paper_db"
        else "event_log.jsonl（paper_* 空库回退）+ mlto_thesis_events + midlong_direction_audit.jsonl"
    )
    lines.append(
        f"> 数据来源: {_src_note}。对应 04 综合方案 §3.5 / P3 证明闭环。"
    )
    if data_source != "paper_db":
        lines.append("")
        lines.append(
            f"> **注意**: paper_* 表无样本，已回退 `{data_source}`，"
            "盈亏/开仓以事件日志为准。"
        )
    lines.append("")

    # ── P3 KPI 总览 ──
    lines.append("## P3 证明闭环 KPI")
    lines.append("")
    if open_stats is not None:
        _opens = sum(int(v) for v in open_stats.values())
        lines.append(f"- 开仓数（窗口内）: {_opens}  " + (
            " / ".join(f"{k}={v}" for k, v in sorted(open_stats.items())) if open_stats else ""
        ))
    if nibble is not None:
        if nibble.get("error"):
            lines.append(f"- NIBBLE/BUILD→成交转化: 查询失败 ({nibble.get('error')})")
        else:
            lines.append(
                f"- NIBBLE/BUILD 事件: {nibble.get('nibble_or_build_events', 0)} "
                f"(NIBBLE={nibble.get('nibble_only', 0)}, BUILD={nibble.get('build', 0)})"
            )
            lines.append(
                f"- NIBBLE/BUILD→24h 开仓转化率: {float(nibble.get('conversion_rate') or 0):.1%} "
                f"({nibble.get('converted_opens_24h', 0)}/{nibble.get('nibble_or_build_events', 0)})"
            )
    if funnel is not None and not funnel.get("error"):
        lines.append(
            f"- 决策漏斗(审计JSONL {funnel.get('lookback_hours', '?')}h): "
            f"skip={funnel.get('skips', 0)} / open_attempt={funnel.get('open_attempts', 0)} "
            f"/ opened={funnel.get('opened', 0)}"
        )
        _by_stg = funnel.get("by_stage_skip") or {}
        if _by_stg:
            lines.append(
                "- 拒仓按阶段: "
                + " · ".join(f"{k}={v}" for k, v in sorted(_by_stg.items(), key=lambda kv: -kv[1]))
            )
        _top = funnel.get("top_skip_reasons") or []
        if _top:
            lines.append(
                "- 拒仓原因 TOP: "
                + " · ".join(f"{r.get('reason')}×{r.get('count')}" for r in _top[:6])
            )
    if funding is not None:
        lines.append(
            f"- 交易净盈亏: {float(funding.get('trade_pnl') or 0):+.2f} | "
            f"Funding 流水: {float(funding.get('funding_payment') or 0):+.2f} | "
            f"**含费率净盈亏: {float(funding.get('net_pnl_with_funding') or 0):+.2f}**"
        )
    if exposure is not None:
        lines.append(
            f"- 最大同向名义: long={exposure.get('max_long_notional', 0):.0f} "
            f"/ short={exposure.get('max_short_notional', 0):.0f} | "
            f"最大净敞口名义={exposure.get('max_net_abs_notional', 0):.0f} | "
            f"最大同向笔数 long={exposure.get('max_long_count', 0)} "
            f"short={exposure.get('max_short_count', 0)}"
        )
    lines.append("")

    for nature in NATURES:
        s = order_stats.get(nature)
        lines.append(f"## {nature}")
        if not s or s["count"] == 0:
            lines.append("- 样本量: 0（暂无数据，可能是修复刚上线还未积累样本）")
            if open_stats and open_stats.get(nature):
                lines.append(f"- 窗口内开仓数: {open_stats.get(nature)}")
            lines.append("")
            continue
        count = s["count"]
        win_rate = s["wins"] / count if count else 0
        gp, gl = s["gross_profit"], s["gross_loss"]
        pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0)
        lines.append(f"- 样本量（平仓单数）: {count}")
        if open_stats is not None:
            lines.append(f"- 窗口内开仓数: {open_stats.get(nature, 0)}")
        lines.append(f"- 胜率: {win_rate:.1%}")
        lines.append(f"- 盈亏比 (profit factor): {pf:.2f}" if pf != float("inf") else "- 盈亏比: ∞（无亏损单）")
        lines.append(f"- 毛利/毛亏: +{gp:.2f} / -{gl:.2f} | 净盈亏: {gp - gl:+.2f}")

        _loss = reopen["loss_closes"].get(nature, 0)
        _reopen = reopen["reopens_24h"].get(nature, 0)
        _reopen_rate = (_reopen / _loss) if _loss else 0
        lines.append(f"- 亏损全平后 24h 同向再开率: {_reopen_rate:.1%}（{_reopen}/{_loss}）"
                      f" | 目标 ≤ 20%")

        lines.append("- close_reason 分布:")
        for reason, cnt in sorted(s["close_reason_dist"].items(), key=lambda kv: -kv[1]):
            lines.append(f"  - {reason}: {cnt} ({cnt / count:.1%})")

        _tp = tp_stats.get(nature, {})
        _tp_total = sum(_tp.values()) or 1
        lines.append("- 分档 TP 触达率 (tp_level_reached，基于开仓样本，非平仓单):")
        for lvl in (0, 1, 2, 3):
            _c = _tp.get(lvl, 0)
            lines.append(f"  - L{lvl}: {_c} ({_c / _tp_total:.1%})")

        lines.append("")
        lines.append("**可用定义达标情况**：")
        _pass_sample = count >= 40
        _pass_perf = win_rate >= 0.40 or pf >= 1.8
        _pass_reopen = _reopen_rate <= 0.20
        lines.append(f"- 样本量>=40: {'[PASS]' if _pass_sample else '[FAIL]'} ({count})")
        lines.append(f"- 胜率>=40%或盈亏比>=1.8: {'[PASS]' if _pass_perf else '[FAIL]'}")
        lines.append(f"- 同向再开率<=20%: {'[PASS]' if _pass_reopen else '[FAIL]'}")
        lines.append("")

    # 附件：最近一次 WFO
    try:
        _wfo_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "midlong_reports", "wfo_latest.json",
        )
        if os.path.isfile(_wfo_path):
            with open(_wfo_path, encoding="utf-8") as _wf:
                _wfo = json.load(_wf)
            lines.append("## 附件：Walk-Forward（代理趋势）")
            lines.append("")
            lines.append(f"- 生成: {_wfo.get('generated_at', '?')}")
            for r in _wfo.get("reports") or []:
                if not r.get("ok"):
                    lines.append(f"- {r.get('symbol')}: FAIL ({r.get('reason')})")
                    continue
                lines.append(
                    f"- {r.get('symbol')}: OOS ret={float(r.get('oos_return') or 0):.2%} "
                    f"Sharpe={float(r.get('oos_sharpe') or 0):.2f} "
                    f"MaxDD={float(r.get('oos_max_dd') or 0):.2%} "
                    f"DSR={r.get('dsr')}"
                )
            lines.append("")
    except Exception:
        pass

    return "\n".join(lines)


def generate_report(days: int = 14) -> str:
    """供程序化调用（如定时任务）的入口，返回 markdown 文本。"""
    db = SessionLocal()
    # [2026-08-14 F5 整改] 带租户上下文，修复「paper_* 空库回退 event_log」假象
    #（此前无上下文被 RLS 过滤，实际上 paper_orders 有数千条平仓记录）。
    try:
        db.connection().exec_driver_sql("SET app.is_admin = 'on'")
    except Exception:
        pass
    data_source = "paper_db"
    try:
        orders = list(_fetch_orders(db, days) or [])
        positions = list(_fetch_positions(db, days) or [])
        # P0：paper_* 空库时回退 event_log，避免 KPI 永久显示开仓 0
        if not orders and not positions:
            elog_orders, elog_positions = _fetch_event_log_midlong(days)
            if elog_orders or elog_positions:
                orders = elog_orders
                positions = elog_positions
                data_source = "event_log.jsonl"
        order_stats = compute_order_stats(orders)
        tp_stats = compute_tp_stage_stats(positions)
        reopen = compute_reopen_rate(positions)
        open_stats = compute_open_stats(positions)
        funding = compute_funding_adjusted_pnl(db, days, order_stats)
        # event_log 回退时 funding 账本可能仍空；交易盈亏以 order_stats 为准
        if data_source != "paper_db":
            try:
                _gross = 0.0
                for _n, _s in (order_stats or {}).items():
                    _gross += float(_s.get("gross_profit") or 0) - float(
                        _s.get("gross_loss") or 0
                    )
                funding = {
                    **(funding or {}),
                    "trade_pnl": round(_gross, 2),
                    "funding_payment": float((funding or {}).get("funding_payment") or 0),
                    "net_pnl_with_funding": round(
                        _gross + float((funding or {}).get("funding_payment") or 0), 2
                    ),
                }
            except Exception:
                pass
        exposure = compute_max_same_dir_exposure(positions)
        nibble = compute_nibble_conversion(days)
        # 若 analytics 开仓样本为空但 event_log 有开仓，补转化分母可见性
        if (
            data_source != "paper_db"
            and isinstance(nibble, dict)
            and int(nibble.get("converted_opens_24h") or 0) == 0
            and sum(int(v) for v in open_stats.values()) > 0
        ):
            nibble = {
                **nibble,
                "converted_opens_24h_note": "paper_positions 空；开仓数见 event_log open_stats",
            }
        funnel = {}
        try:
            from backend.services.mlto.midlong_direction_audit import (
                summarize_decision_funnel,
            )
            funnel = summarize_decision_funnel(float(days) * 24.0)
        except Exception as _fun_err:
            funnel = {"error": str(_fun_err)[:120]}
        return render_report(
            days, order_stats, tp_stats, reopen,
            open_stats=open_stats, funding=funding,
            exposure=exposure, nibble=nibble,
            funnel=funnel, data_source=data_source,
        )
    finally:
        db.close()


def run_and_save(days: int = 14, out_dir: str = "") -> str:
    """定时任务入口：生成报表并写入固定路径（覆盖式 latest + 带时间戳归档）。"""
    report = generate_report(days)
    _out_dir = out_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "midlong_reports",
    )
    os.makedirs(_out_dir, exist_ok=True)
    latest_path = os.path.join(_out_dir, "latest.md")
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(report)
    ts_path = os.path.join(_out_dir, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
    with open(ts_path, "w", encoding="utf-8") as f:
        f.write(report)
    return latest_path


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="中长线策略周度绩效报表")
    parser.add_argument("--days", type=int, default=14, help="回看天数（默认 14）")
    parser.add_argument("--out", type=str, default="", help="输出文件路径（默认仅打印到 stdout）")
    args = parser.parse_args()

    report = generate_report(args.days)

    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n[已写入] {args.out}")


if __name__ == "__main__":
    main()
