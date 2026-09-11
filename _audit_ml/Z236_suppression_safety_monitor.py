# -*- coding: utf-8 -*-
"""[§83 监控 2026-09-11 / 目标③] P19-B 出场抑制的**线上安全监控**（只读、可日跑）。

要盯住三件事（目标③ 的原文）：
  ① **保护性通道永不被抑制** —— 任何 `exit_channel_broken` 事件命中的通道都必须**非保护**；
  ② **抑制事件可追溯** —— 事件要能在 `position_exit_events`（结构化）或
     `full_auto_sessions.event_log`（会话事件）里查到，并带 `tier|channel`；
  ③ **不产生悬挂仓位** —— 被抑制的仓位必须在 `HANG_LIMIT_H` 小时内由**保护性通道**
     （sl/tp/超时/尘仓/浮盈保护…）或任何通道离场；超时仍开着 ⇒ 悬挂告警。

判定是**可红**的（`classify_suppression_events` 为纯函数，由
`backend/tests/unit/test_suppression_safety_monitor_20260911.py` 用合成数据双向验证）。
当窗口里没有抑制事件时输出 `NO_DATA`（不假装通过）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)

from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.exit.channel_breaker_gate import channel_of, is_protected  # noqa: E402
from sqlalchemy import text  # noqa: E402

HANG_LIMIT_H = float(os.environ.get("SUPPRESSION_HANG_LIMIT_H", "48") or 48)
EVENT = "exit_channel_broken"


def classify_suppression_events(
    events: List[Dict[str, Any]], *, hang_limit_h: float = HANG_LIMIT_H
) -> Dict[str, Any]:
    """纯函数：把抑制事件分成 保护性命中(缺陷) / 悬挂(缺陷) / 正常。

    每个事件 dict 需含：`symbol`, `channel`, `tier`, `suppressed_at`(datetime),
    以及可选 `resolved_at`(datetime) 与 `resolved_channel`(str)。
    """
    violations: List[Dict[str, Any]] = []
    dangling: List[Dict[str, Any]] = []
    ok: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for e in events:
        ch = str(e.get("channel") or "")
        if is_protected(ch):
            violations.append({**e, "why": f"保护性通道被抑制: {ch}"})
            continue
        res = e.get("resolved_at")
        if res is None:
            held = (now - e["suppressed_at"]).total_seconds() / 3600.0
            if held > hang_limit_h:
                dangling.append({**e, "why": f"抑制后 {held:.1f}h 仍未离场（> {hang_limit_h}h）"})
            else:
                ok.append({**e, "why": f"抑制后 {held:.1f}h 内仍在场内（未超 {hang_limit_h}h）"})
        else:
            ok.append({**e, "why": "已被后续离场解决"})
    return {
        "n": len(events),
        "violations": violations,
        "dangling": dangling,
        "ok": ok,
        "verdict": ("NO_DATA" if not events else
                    "FAIL" if (violations or dangling) else "PASS"),
    }


def count_stale_advisories(lines: List[str]) -> Dict[str, int]:
    """纯函数：统计"证据过期 ⇒ 本次不抑制"（P27-A 咨询式降级）按通道的次数。

    为什么需要：P27-A 之后这类降级**不会**产生 `exit_channel_broken` 事件，
    只打 WARNING ⇒ 如果没人统计，就无从知道"过期规则到底拦下了多少次抑制"。
    """
    import re
    pat = re.compile(r"证据过期 ⇒ 本次不抑制（P27-A）：([\w|]+)")
    out: Dict[str, int] = {}
    for l in lines:
        m = pat.search(l)
        if m:
            out[m.group(1)] = out.get(m.group(1), 0) + 1
    return out


def _load_events(days: int) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """从两张表里取抑制事件并解析出 symbol/channel/时间/后续结局。"""
    db = SessionLocal()
    events: List[Dict[str, Any]] = []
    counts = {"exit_events": 0, "session_log": 0}
    try:
        db.execute(text("set app.is_admin='on'"))
        rows = db.execute(text(f"""
            select position_id, symbol, lower(coalesce(exit_channel,'')), event_type,
                   to_char(created_at,'YYYY-MM-DD HH24:MI:SS'), coalesce(metadata_json,'')
            from position_exit_events
            where event_type = '{EVENT}' and created_at >= now() - interval '{int(days)} day'
            order by created_at
        """)).fetchall()
        counts["exit_events"] = len(rows)
        for pid, sym, ch, _et, ts, detail in rows:
            events.append({
                "position_id": pid, "symbol": sym,
                "channel": channel_of(ch or detail), "tier": "",
                "suppressed_at": datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc),
                "raw": (ch or detail)[:120],
            })
        # 会话事件（append_event 落 event_log JSON）
        sess = db.execute(text(f"""
            select e->>'detail', e->>'time'
            from full_auto_sessions s, json_array_elements(s.event_log) e
            where e->>'event' = '{EVENT}'
            order by e->>'time'
        """)).fetchall()
        counts["session_log"] = len(sess)
        for detail, ts in sess:
            try:
                t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except Exception:
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            events.append({
                "position_id": None, "symbol": "", "channel": channel_of(str(detail or "")),
                "tier": "", "suppressed_at": t, "raw": str(detail)[:120],
            })
        # 后续结局：同 symbol 在该时间之后的平仓（若 symbol 为空则跳过）
        for e in events:
            if not e["symbol"]:
                continue
            row = db.execute(text(f"""
                select to_char(closed_at,'YYYY-MM-DD HH24:MI:SS'), coalesce(close_reason,'')
                from paper_positions
                where upper(symbol) = upper(:sym) and status='closed' and closed_at > :ts
                order by closed_at limit 1
            """), {"sym": e["symbol"], "ts": e["suppressed_at"]}).fetchone()
            if row:
                e["resolved_at"] = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                e["resolved_channel"] = channel_of(row[1])
    finally:
        db.close()
    return events, counts


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    events, counts = _load_events(days)
    res = classify_suppression_events(events)
    print("=" * 100)
    print(f"P19-B 出场抑制安全监控（近 {days} 天）｜悬挂上限 {HANG_LIMIT_H}h")
    print("=" * 100)
    print(f"抑制事件来源：position_exit_events={counts['exit_events']}｜"
          f"session.event_log={counts['session_log']}｜合计 {res['n']}")
    print(f"判定：**{res['verdict']}**"
          + ("（窗口内没有被抑制的离场 ⇒ 无法证伪，也不假装通过）" if res["verdict"] == "NO_DATA" else ""))
    for e in res["violations"]:
        print(f"  ❌ 保护性通道被抑制: {e.get('symbol')} {e.get('channel')} @ {e['suppressed_at']} — {e['why']}")
    for e in res["dangling"]:
        print(f"  ❌ 悬挂仓位: {e.get('symbol')} channel={e.get('channel')} @ {e['suppressed_at']} — {e['why']}")
    for e in res["ok"][:10]:
        print(f"  ✅ {e.get('symbol') or '(会话事件)'} channel={e.get('channel')} — {e['why']}")
    if len(res["ok"]) > 10:
        print(f"  …… 另有 {len(res['ok']) - 10} 条正常")

    # [§84/P27-A] 咨询式降级计数（不会进事件流，只打 WARNING ⇒ 只能扫日志）
    try:
        log = ROOT / "logs" / "backend.log"
        adv = count_stale_advisories(log.read_text(encoding="utf-8", errors="replace").splitlines()) \
            if log.exists() else {}
    except Exception:
        adv = {}
    print(f"\n证据过期 ⇒ 只记录不抑制（P27-A）计数：{adv or '（本日志窗口内无）'}")
    if adv:
        tot = sum(adv.values())
        print(f"  ⇒ 共 {tot} 次本会被抑制、因证据过期而放行；按通道：{adv}")
    out = ROOT / "data" / "audit_reports" / f"suppression_safety_{datetime.now():%Y%m%d_%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "days": days, "hang_limit_h": HANG_LIMIT_H, "counts": counts,
        "verdict": res["verdict"], "violations": [
            {k: (str(v) if isinstance(v, datetime) else v) for k, v in e.items()}
            for e in res["violations"]],
        "dangling": [
            {k: (str(v) if isinstance(v, datetime) else v) for k, v in e.items()}
            for e in res["dangling"]],
        "ok_n": len(res["ok"]),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"快照：{out}")
    return 0 if res["verdict"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
