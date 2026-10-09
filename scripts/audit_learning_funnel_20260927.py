# -*- coding: utf-8 -*-
"""[新目标 R4] 学习覆盖率漏斗审计（逐笔 join 版）：把「平仓 → postmortem 事件」的损耗算到每一笔。

背景纠偏（必须留档）：
  §98/§99 断言"活路径信封永远没有 thesis_id ⇒ record_outcome 直接 skip=no_thesis"。
  **生产数据否定该断言**：09-15→09-27 共 158 笔平仓，mid 车道 138 笔中 138 笔带
  session_id、137 笔带 thesis_id（写在 exit_state_json.open_metadata）。真正缺 thesis_id 的是
  **long 车道**（20 笔中仅 1 笔）。
  同时 `mlto_block_skip=no_thesis` 这行埋点在**全部日志里 0 次命中**（所有 `no_thesis` 命中都是
  brain 扫描 / F40 补仓的 channel 标签，与学习无关）——即 H1 判据从未被触发过。

为什么闸门计数必须"逐笔 + 时间感知"：
  `ai_strategies.learning_enabled` 只有当前值；`genome.graduated_at` 只保留**最近一次**毕业时间
  （tpl_mid_range_* 家族当前值是 2026-09-27T12:10Z = 本地 20:10，即今晚启动时，而日志里
  09-20→09-23 就已经出现这些 id 的闸门命中 ⇒ 说明它们被解冻过又再冻结，字段已被覆盖）。
  所以"当时是否被挡"只能靠日志行（平仓当刻写下）逐笔对齐 closed_at。

用法：.venv\\Scripts\\python.exe scripts\\audit_learning_funnel_20260927.py [起始日]
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from backend.database.connection import (  # noqa: E402
    AnalyticsSessionLocal,
    SessionLocal,
)
from backend.services.learning_bus import get_thesis_postmortem_cooldown_sec  # noqa: E402

ACCOUNT_ID = 14
DEFAULT_SINCE = "2026-09-15"
PM_MATCH_LATE_SEC = 300      # 异步 worker 落库延迟上限
PM_MATCH_EARLY_SEC = 30      # 允许的时钟抖动
GATE_MATCH_SEC = 120         # 闸门日志与 closed_at 的对齐窗口
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
GATE_RE = re.compile(r"策略 (\S+) learning_enabled=false")


def _meta_of(exit_state_json) -> dict:
    try:
        obj = json.loads(exit_state_json) if isinstance(exit_state_json, str) else (exit_state_json or {})
    except Exception:
        obj = {}
    if not isinstance(obj, dict):
        obj = {}
    om = obj.get("open_metadata")
    om = dict(om) if isinstance(om, dict) else {}
    for k in ("thesis_id", "session_id", "timeframe_tier", "entry_source"):
        if k not in om and obj.get(k) is not None:
            om[k] = obj.get(k)
    return om


def _since_sql(since: str) -> str:
    """允许两种参数：日期（YYYY-MM-DD）或完整时间戳（含 T 或空格，原样用）。"""
    s = since.strip()
    if "T" in s or " " in s:
        return s
    return f"{s} 00:00:00"


def load_closes(since: str) -> list:
    db = SessionLocal()
    try:
        db.execute(text("SET app.is_admin='on'"))
        rows = db.execute(text(f"""
            SELECT id, symbol, timeframe_tier, strategy_id, close_reason, closed_at, exit_state_json
            FROM paper_positions
            WHERE account_id={ACCOUNT_ID} AND status='closed' AND closed_at >= timestamp '{_since_sql(since)}'
            ORDER BY closed_at
        """)).fetchall()
    finally:
        db.close()
    out = []
    for r in rows:
        om = _meta_of(r[6])
        out.append({
            "id": r[0], "symbol": (r[1] or "").upper(), "tier": str(r[2] or ""),
            "strategy_id": str(r[3] or ""), "reason": str(r[4] or ""), "closed_at": r[5],
            "thesis_id": str(om.get("thesis_id") or ""), "session_id": str(om.get("session_id") or ""),
        })
    return out


def load_pm_events(since: str) -> list:
    adb = AnalyticsSessionLocal()
    try:
        rows = adb.execute(text("""
            SELECT thesis_id, ts, payload_json FROM mlto_thesis_events
            WHERE event_type='postmortem' ORDER BY ts
        """)).fetchall()
    finally:
        adb.close()
    out = []
    for tid, ts, payload in rows:
        try:
            pl = json.loads(payload) if isinstance(payload, str) else (payload or {})
        except Exception:
            pl = {}
        pl = pl if isinstance(pl, dict) else {}
        out.append({"thesis_id": str(tid), "ts": ts, "pnl": pl.get("pnl"),
                    "reason": pl.get("close_reason")})
    return out


def load_gate_hits(since: str) -> list:
    """从日志取"平仓当刻被闸门挡住"的证据：(strategy_id, datetime)。"""
    hits = []
    for p in sorted((ROOT / "logs").glob("backend*.log")):
        try:
            if datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d") < since:
                continue
        except Exception:
            continue
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if "learning_enabled=false" not in line:
                        continue
                    m = TS_RE.match(line)
                    g = GATE_RE.search(line)
                    if m and g:
                        hits.append((g.group(1), datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")))
        except Exception:
            continue
    hits.sort(key=lambda x: x[1])
    return hits


def gross_pnl(pos, close, entry, size) -> float:
    """引擎口径毛盈亏：多 = (close-entry)*size，空 = (entry-close)*size，加分批已实现。"""
    side = str(pos.get("side") or "long").lower()
    d = 1.0 if side in ("long", "buy") else -1.0
    return (close - entry) * size * d + float(pos.get("partial") or 0.0)


def load_closes_raw(since: str) -> list:
    """带价格/数量/方向的全字段平仓表（用于 pnl 精确匹配）。"""
    db = SessionLocal()
    try:
        db.execute(text("SET app.is_admin='on'"))
        rows = db.execute(text(f"""
            SELECT id, symbol, timeframe_tier, close_reason, closed_at, entry_price,
                   close_price, mark_price, size, original_size, side, partial_realized_pnl,
                   exit_state_json
            FROM paper_positions
            WHERE account_id={ACCOUNT_ID} AND status='closed' AND closed_at >= timestamp '{_since_sql(since)}'
            ORDER BY closed_at
        """)).fetchall()
    finally:
        db.close()
    out = []
    for r in rows:
        om = _meta_of(r[12])
        out.append({
            "id": r[0], "symbol": (r[1] or "").upper(), "tier": str(r[2] or ""),
            "reason": str(r[3] or ""), "closed_at": r[4],
            "entry": float(r[5] or 0), "close": float(r[6] or r[7] or 0),
            "size": float(r[9] or r[8] or 0), "side": str(r[10] or "long"),
            "partial": float(r[11] or 0), "thesis_id": str(om.get("thesis_id") or ""),
        })
    return out


def match_by_pnl(raw_closes: list, pm_events: list, tol: float = 0.02) -> dict:
    """用 (thesis_id, close_reason, pnl 数值) 精确匹配：同一 thesis 会被多笔复用，
    只靠 thesis_id+时间窗会把"几天前的旧事件"误判成"本笔学过"，故必须带 pnl/reason。"""
    by_th = defaultdict(list)
    for i, e in enumerate(pm_events):
        try:
            pnl = float(e["pnl"])
        except Exception:
            continue
        by_th[e["thesis_id"]].append((pnl, str(e["reason"] or ""), e["ts"], i))
    result = {}
    used: dict = {}
    for c in raw_closes:
        pnl = gross_pnl(c, c["close"], c["entry"], c["size"])
        best = None
        for cand_pnl, cand_reason, ts, idx in by_th.get(c["thesis_id"], []):
            if abs(cand_pnl - pnl) > tol:
                continue
            d = (ts - c["closed_at"]).total_seconds()
            if not (-90 <= d <= 4 * 86400):
                continue
            # reason 只当**平手时的优先级**，不当硬条件：DB close_reason 与引擎传入的
            # `reason` 并不总是同一串（长线变体带动态百分比），硬比会漏配。
            same_reason = 0 if cand_reason == c["reason"] else 1
            key = (same_reason, abs(d))
            if best is None or key < best[0]:
                best = (key, d, ts, idx)
        if best is None:
            result[c["id"]] = ("never", None, None)
            continue
        _, d, ts, idx = best
        used.setdefault(idx, []).append(c["id"])
        if d <= PM_MATCH_LATE_SEC:
            result[c["id"]] = ("learned_fast", round(d), ts)
        else:
            result[c["id"]] = ("learned_backfill", round(d), ts)
        c["_pnl"] = pnl
    return result, used


def find_double_writes(pm_events: list, window_sec: int = 3600) -> list:
    """同一 thesis + 完全相同 payload(pnl/reason) 在窗口内出现多次 = 同一笔平仓被学了两次。"""
    dups = []
    buckets = defaultdict(list)
    for e in pm_events:
        try:
            key = (e["thesis_id"], round(float(e["pnl"]), 6), str(e["reason"] or ""))
        except Exception:
            continue
        buckets[key].append(e["ts"])
    for key, ts_list in buckets.items():
        ts_list.sort()
        for a, b in zip(ts_list, ts_list[1:]):
            if (b - a).total_seconds() <= window_sec:
                dups.append((key, a, b, (b - a).total_seconds()))
    return dups


def log_coverage_buckets(bucket_sec: int = 300) -> set:
    """真实日志覆盖：按 5 分钟桶统计"这些文件在那个时刻真的有输出"。

    不能用 [首行,末行] 区间代替：`backend.pid20676.log` 首末跨越 4 天但只有 8437 行，
    用区间会把大量空洞误判成"覆盖"（本脚本第一版就踩了这个坑）。
    """
    names = ("backend.log", "backend-console.log")
    buckets = set()
    for p in sorted((ROOT / "logs").glob("backend*.log")):
        if not (p.name in names or p.name.startswith("backend.pid")):
            continue
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if len(line) > 19 and line[4] == "-" and line[:4].isdigit():
                        try:
                            ts = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            continue
                        buckets.add(int(ts.timestamp()) // bucket_sec)
        except Exception:
            continue
    return buckets


def covered(buckets: set, ts: datetime, bucket_sec: int = 300, tol: int = 1) -> bool:
    b = int(ts.timestamp()) // bucket_sec
    return any((b + d) in buckets for d in range(-tol, tol + 1))


def main() -> int:
    since = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SINCE
    closes = load_closes(since)
    pm_events = load_pm_events(since)
    gate_hits = load_gate_hits(since)
    gate_by_strategy = defaultdict(list)
    for sid, ts in gate_hits:
        gate_by_strategy[sid].append(ts)
    cooldown = float(get_thesis_postmortem_cooldown_sec() or 3600)

    used = set()
    for c in closes:
        c["pm"] = None
        if not c["thesis_id"]:
            continue
        lo = c["closed_at"] - timedelta(seconds=PM_MATCH_EARLY_SEC)
        hi = c["closed_at"] + timedelta(seconds=PM_MATCH_LATE_SEC)
        for i, e in enumerate(pm_events):
            if i in used or e["thesis_id"] != c["thesis_id"]:
                continue
            if not (lo <= e["ts"] <= hi):
                continue
            used.add(i)
            c["pm"] = e
            break

    # 逐笔归因
    for c in closes:
        if c["pm"]:
            c["cause"] = "learned"
            continue
        if not c["thesis_id"]:
            c["cause"] = "no_thesis_id"
            continue
        gt = gate_by_strategy.get(c["strategy_id"], [])
        near = any(abs((g - c["closed_at"]).total_seconds()) <= GATE_MATCH_SEC for g in gt)
        c["cause"] = "gate_off_measured" if near else "unexplained"

    n = len(closes)
    learned = sum(1 for c in closes if c["cause"] == "learned")
    print("=" * 100)
    print(f"学习覆盖率漏斗（逐笔 join）｜账户 {ACCOUNT_ID}｜{since} 起｜平仓 {n} 笔｜"
          f"postmortem 事件 {len(pm_events)} 条｜节流阈值 {cooldown:.0f}s/symbol+tier")
    print("=" * 100)
    print(f"① 总覆盖：learned={learned}/{n} = {learned/max(1,n):.1%}")
    print("② 按车道 × 归因：")
    grid = defaultdict(Counter)
    for c in closes:
        grid[c["tier"] or "?"][c["cause"]] += 1
    causes = ["learned", "gate_off_measured", "no_thesis_id", "unexplained"]
    print(f"   {'车道':<8}" + "".join(f"{k:>20}" for k in causes) + f"{'合计':>10}")
    for tier in sorted(grid):
        row = grid[tier]
        print(f"   {tier:<8}" + "".join(f"{row[k]:>20}" for k in causes) + f"{sum(row.values()):>10}")
    tot = Counter()
    for tier in grid:
        tot.update(grid[tier])
    print(f"   {'合计':<8}" + "".join(f"{tot[k]:>20}" for k in causes) + f"{n:>10}")
    print("③ 未覆盖的 mid 笔（有 thesis_id 却无事件）明细，最多 15 条：")
    shown = 0
    for c in closes:
        if c["tier"] == "mid" and c["cause"] != "learned":
            print(f"   id={c['id']} {c['symbol']:<9} {c['closed_at']} {c['reason']:<26} "
                  f"strat={c['strategy_id']:<24} cause={c['cause']}")
            shown += 1
            if shown >= 15:
                break
    print("④ 未覆盖的 long 笔明细：")
    for c in closes:
        if c["tier"] == "long" and c["cause"] != "learned":
            print(f"   id={c['id']} {c['symbol']:<9} {c['closed_at']} {c['reason']:<26} "
                  f"strat={c['strategy_id']:<24} cause={c['cause']}")
    print("⑤ 闸门命中日志（逐策略）：")
    cnt = Counter(sid for sid, _ in gate_hits)
    for sid, k in cnt.most_common(12):
        ts_list = gate_by_strategy[sid]
        print(f"   {sid:<26} n={k:<4} 首={ts_list[0]} 末={ts_list[-1]}")
    print(f"   闸门日志总命中 = {len(gate_hits)}；覆盖日期 = "
          f"{sorted({ts.strftime('%Y-%m-%d') for _, ts in gate_hits})}")

    # ── ⑥ pnl 精确匹配：区分"实时学过"/"靠 backfill 迟到学过"/"从未学过" ──
    raw = load_closes_raw(since)
    matched, used_event = match_by_pnl(raw, pm_events)
    cnt = Counter(v[0] for v in matched.values())
    print()
    print("=" * 100)
    print("⑥ pnl 精确匹配（thesis_id + close_reason + pnl±0.02）")
    print(f"   实时学过(≤{PM_MATCH_LATE_SEC}s)={cnt['learned_fast']}  "
          f"backfill 迟到学过={cnt['learned_backfill']}  从未学过={cnt['never']}")
    by_tier2 = defaultdict(Counter)
    tier_of = {c["id"]: c["tier"] for c in raw}
    for pid, v in matched.items():
        by_tier2[tier_of.get(pid, "?")][v[0]] += 1
    for tier in sorted(by_tier2):
        row = by_tier2[tier]
        print(f"   {tier:<6} fast={row['learned_fast']:<4} backfill={row['learned_backfill']:<4} "
              f"never={row['never']}")
    delayed = sorted((v[1], pid) for pid, v in matched.items() if v[0] == "learned_backfill")
    if delayed:
        print(f"   迟到延迟（秒）中位={delayed[len(delayed)//2][0]} 最小={delayed[0][0]} 最大={delayed[-1][0]}")
    nevers = [pid for pid, v in matched.items() if v[0] == "never"]
    print(f"   从未学过的仓位 id（{len(nevers)} 笔）: {nevers}")
    strat_of = {c["id"]: c.get("strategy_id", "") for c in closes}
    reasons = Counter(c["reason"][:24] for c in raw if c["id"] in set(nevers))
    print(f"   从未学过者 close_reason 分布: {reasons.most_common(8)}")

    # ── ⑦ 双写检测 ──
    dups = find_double_writes(pm_events)
    print()
    print("⑦ 双写检测（同 thesis + 同 pnl + 同 reason，1h 内重复落库）")
    print(f"   重复组数 = {len(dups)}（涉及 {sum(2 for _ in dups)} 条事件）")
    for (th, pnl, reason), a, b, gap in dups[:10]:
        print(f"   th={th[:8]} pnl={pnl} reason={reason[:26]:<26} {a} → {b} (+{gap:.0f}s)")
    print(f"   事件总条数 = {len(pm_events)}；被『学过』消耗的条数 = {len(used_event)}；"
          f"同一 (thesis,pnl,reason) 被多笔平仓命中的条数 = "
          f"{sum(len(v) - 1 for v in used_event.values() if len(v) > 1)}")

    # ── ⑧ 覆盖感知归因：把"没学到"拆成"当时被闸门挡住(有日志证据)"与"日志已丢/不可观测" ──
    buckets = log_coverage_buckets()
    print()
    print("=" * 100)
    print("⑧ 日志覆盖（5 分钟桶）：" + " / ".join(
        f"{datetime.fromtimestamp(b*300).strftime('%m-%d %H:%M')}"
        for b in sorted({b // 12 for b in buckets})[:40]) + " …" if buckets else "⑧ 无覆盖")
    print(f"   覆盖桶数 = {len(buckets)}（≈{len(buckets)*5/60:.0f} 小时有日志）")
    gate_by_id = {}
    for c in closes:
        gt = gate_by_strategy.get(c["strategy_id"], [])
        gate_by_id[c["id"]] = any(abs((g - c["closed_at"]).total_seconds()) <= GATE_MATCH_SEC for g in gt)
    never = [c for c in raw if matched.get(c["id"], ("never",))[0] == "never"]
    buckets = Counter()
    detail = defaultdict(list)
    for c in never:
        cov_here = covered(buckets, c["closed_at"])
        if not c["thesis_id"]:
            k = "无 thesis_id（结构缺口）"
        elif gate_by_id.get(c["id"]):
            k = "被 learning_enabled=false 挡住（有日志证据）"
        elif cov_here:
            k = "覆盖区间内无闸门证据 ⇒ 真·未知"
        else:
            k = "日志空洞期内 ⇒ 不可观测"
        buckets[k] += 1
        detail[k].append(c["id"])
    print(f"未学到 postmortem 的 {len(never)} 笔，按证据强度分类：")
    for k, v in buckets.most_common():
        print(f"   {k:<46} {v:>4} 笔  ids={detail[k][:14]}{' …' if len(detail[k]) > 14 else ''}")
    unknown = detail["覆盖区间内无闸门证据 ⇒ 真·未知"]
    print(f"   真·未知 {len(unknown)} 笔（覆盖区间内、有 thesis_id、无闸门日志）→ 需继续深挖")
    for c in raw:
        if c["id"] in unknown:
            print(f"      id={c['id']} {c['symbol']:<9} {c['closed_at']} {c['reason'][:30]:<30} "
                  f"tier={c['tier']} entry={c['entry']} close={c['close']} size={c['size']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
