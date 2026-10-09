"""h571 — 现纪元的**逐小时腿速剖面**（只读）：哪些时段能承载"减腿类"试跑。

为什么需要（R69 的教训）：用户硬约束是 ≥60 腿/h，而现纪元吞吐只有 62–70/h
⇒ 任何**会让腿量下降**的试跑（③ 的 ×0.92、⑤ 的收紧陈旧报价）都可能在薄时段
被 `frequency_below_mandate` 自动回滚——**是排期问题，不是参数问题**。
故把"每小时的基线腿速"先算出来，排期前用「预期效应 × 该时段基线」对一遍地板。

口径：
  · 腿速 = 该**本地小时**的腿数 ÷ 该小时的可交易分钟数（`market_trades_aggregated`，
    与判定同源；见 `h425_repair_trial._covered_hours`）⇒ 扣除外部停摆；
  · 同时给出**墙钟**腿速与覆盖分钟数，便于识别薄时段是"市场冷"还是"我们没数据"。

用法：python scripts/h571_hourly_leg_profile.py [--hours 48]
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=8))
ERA_SINCE = "2026-09-28T05:00:00+00:00"   # 本地 09-28 13:00 = 现纪元起点


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=48, help="回看小时数（默认 48）")
    a = ap.parse_args()
    since = max(dt.datetime.fromisoformat(ERA_SINCE),
                dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=a.hours))
    since_iso = since.isoformat()
    rows = []
    with psycopg.connect(h.read_env_dsn()) as c:
        with c.cursor() as cur:
            meta = h._load_meta(cur)
            syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
            # 逐小时腿数（本地小时）
            cur.execute(
                "SELECT to_char(date_trunc('hour', ts AT TIME ZONE 'Asia/Shanghai'),"
                " 'MM-DD HH24') AS hr, count(*)"
                " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
                " AND symbol = ANY(%s) GROUP BY 1 ORDER BY 1",
                (h.LANE, since_iso, syms))
            legs_by_hour = {str(r[0]): int(r[1]) for r in cur.fetchall()}
            # 逐小时**可交易分钟**（与判定同源的表；bare symbol 名）
            mk = h.read_env_dsn().replace("/alpha_arena", "/alpha_market")
            bare = [s.split(":")[-1].upper() for s in syms]
            with psycopg.connect(mk) as mc, mc.cursor() as mcur:
                mcur.execute(
                    "SELECT to_char(to_timestamp(timestamp/1000.0)"
                    " AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24') AS hr,"
                    " count(DISTINCT date_trunc('minute',"
                    "   to_timestamp(timestamp/1000.0))) AS mins"
                    " FROM market_trades_aggregated WHERE timestamp > %s"
                    " AND symbol = ANY(%s) GROUP BY 1 ORDER BY 1",
                    (int(since.timestamp() * 1000), bare))
                mins_by_hour = {str(r[0]): int(r[1]) for r in mcur.fetchall()}
            for hr in sorted(set(legs_by_hour) | set(mins_by_hour)):
                mins = mins_by_hour.get(hr, 0)
                legs = legs_by_hour.get(hr, 0)
                cov_h = mins / 60.0
                rows.append({
                    "hour": hr, "legs": legs, "covered_min": mins,
                    "rate_covered": (round(legs / cov_h, 1) if cov_h else None),
                    "rate_wall": round(legs / 1.0, 1),
                    "thin_night": hr[-2:] in ("22", "23", "00", "01", "02", "03",
                                              "04", "05", "06"),
                })
            print(f"{'小时':<10}{'腿':>5}{'可交易min':>10}{'腿速/h':>9}{'墙钟/h':>9}  {'时段'}")
            for r in rows:
                _rc = r["rate_covered"]
                print(f"{r['hour']:<10}{r['legs']:>5}{r['covered_min']:>10}"
                      f"{(f'{_rc:.0f}' if _rc is not None else '-'):>9}"
                      f"{r['rate_wall']:>9.0f}  {'夜' if r['thin_night'] else '昼'}")
            day = [r for r in rows if not r["thin_night"] and r["covered_min"] >= 50]
            night = [r for r in rows if r["thin_night"] and r["covered_min"] >= 50]
            for name, grp in (("昼", day), ("夜", night)):
                if grp:
                    _l = sum(r["legs"] for r in grp)
                    _c = sum(r["covered_min"] for r in grp) / 60.0
                    print(f"  {name}间合计：{_l} 腿 / 可交易 {_c:.2f}h ⇒ "
                          f"{_l / max(_c, 0.01):.1f}/h（样本 {len(grp)} 小时）")
    p = ROOT / "research_l1" / "out" / "h571_hourly_leg_profile.json"
    p.write_text(json.dumps({"since": since_iso, "symbols": syms, "rows": rows},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写出 {p.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
