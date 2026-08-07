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

        converted = 0
        for ts, sym in nibble_ts:
            for oa, osym in open_list:
                if osym != sym:
                    continue
                try:
                    gap_h = (oa - ts).total_seconds() / 3600.0
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
) -> str:
    lines = []
    lines.append(f"# 中长线策略周度绩效报表（近 {days} 天）")
    lines.append("")
    lines.append(f"生成时间: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append(
        "> 数据来源: paper_orders(平仓单pnl) + paper_positions + paper_funding_ledger"
        " + mlto_thesis_events。对应 04 综合方案 §3.5 / P3 证明闭环。"
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
    try:
        orders = _fetch_orders(db, days)
        positions = _fetch_positions(db, days)
        order_stats = compute_order_stats(orders)
        tp_stats = compute_tp_stage_stats(positions)
        reopen = compute_reopen_rate(positions)
        open_stats = compute_open_stats(positions)
        funding = compute_funding_adjusted_pnl(db, days, order_stats)
        exposure = compute_max_same_dir_exposure(positions)
        nibble = compute_nibble_conversion(days)
        return render_report(
            days, order_stats, tp_stats, reopen,
            open_stats=open_stats, funding=funding,
            exposure=exposure, nibble=nibble,
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
