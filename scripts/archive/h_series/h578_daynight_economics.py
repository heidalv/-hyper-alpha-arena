"""h578 — 现纪元的**昼/夜经济性对照**（只读，R81）。

为什么：R80 实测**夜间止损率是白天的 1.31×**（夜 4.84% vs 昼 3.69%）⇒ 纪元整体的
−0.897$/h 把昼夜混在一起，掩盖了"哪一半在亏"。这直接影响可操作决策：
夜间是否该收紧（趋势闸/规模/是否继续报价），以及 h520（趋势闸）的证据该怎么读。

口径：
  · 昼 = 本地 08:00–21:00，夜 = 其余（与 `h577` 一致）；
  · 腿速一律用**可交易口径**（`_covered_hours`，扣除外部停摆）；
  · 金额用 `net_bp×notional/1e4`（与钱图同源）。

用法：python scripts/h578_daynight_economics.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402

ERA_SINCE = "2026-09-28T05:00:00+00:00"
DAY_H0, DAY_H1 = 8, 21


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=ERA_SINCE,
                    help="统计起点（默认纪元起点；用 2026-09-28T18:33:00+00:00 = 本地 "
                         "09-29 02:33 = h472 修复落地时刻，可排除'修复前遗留 taker 腿'伪影）")
    _a = ap.parse_args()
    since_used = _a.since
    out: dict = {"era_since": ERA_SINCE, "since_used": since_used,
                 "day_hours": [DAY_H0, DAY_H1]}
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        cur.execute(
            "SELECT to_char(ts AT TIME ZONE 'Asia/Shanghai', 'HH24') AS hh, count(*),"
            " COALESCE(sum(net_bp*notional/1e4),0)::float8 AS net_usd,"
            " COALESCE(sum(fee_bp*notional/1e4),0)::float8 AS fee_usd,"
            " count(*) FILTER (WHERE meta_json->>'exit_path' LIKE 'stop_loss%%') AS stops,"
            " COALESCE(sum(net_bp*notional/1e4) FILTER (WHERE meta_json->>'exit_path'"
            "   LIKE 'stop_loss%%'),0)::float8 AS stop_usd,"
            " count(*) FILTER (WHERE meta_json->>'exit_path' LIKE '%%taker') AS taker"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND symbol = ANY(%s) GROUP BY 1 ORDER BY 1",
            (h.LANE, since_used, syms))
        per_hour = {int(r[0]): {"legs": int(r[1]), "net_usd": float(r[2]),
                                "fee_usd": float(r[3]), "stops": int(r[4]),
                                "stop_usd": float(r[5]), "taker": int(r[6])}
                    for r in cur.fetchall()}
        out["per_hour"] = per_hour

        # 可交易小时：按昼/夜分别累计（逐小时窗，避免跨小时误差）
        def _cov_daynight(day: bool):
            """可交易小时（**按 `since_used` 起算**——首版从纪元起点起算，
            导致速率被窗口外的时段稀释 ✗，R81 自查修掉）。"""
            tot = 0.0
            base = (dt.datetime.fromisoformat(since_used).astimezone()
                    .replace(minute=0, second=0, microsecond=0))
            end = dt.datetime.now().astimezone()
            t = base
            while t < end:
                is_day = DAY_H0 <= t.hour < DAY_H1
                if is_day == day:
                    cov, _ = h._covered_hours(
                        t.astimezone(dt.timezone.utc).isoformat(),
                        min(t + dt.timedelta(hours=1), end).astimezone(
                            dt.timezone.utc).isoformat(), syms)
                    tot += float(cov or 0.0)
                t += dt.timedelta(hours=1)
            return tot

        agg = {}
        for tag, is_day in (("昼", True), ("夜", False)):
            hs = [v for k, v in per_hour.items() if (DAY_H0 <= k < DAY_H1) == is_day]
            legs = sum(v["legs"] for v in hs)
            net = sum(v["net_usd"] for v in hs)
            fee = sum(v["fee_usd"] for v in hs)
            stops = sum(v["stops"] for v in hs)
            stop_usd = sum(v["stop_usd"] for v in hs)
            taker = sum(v["taker"] for v in hs)
            cov = _cov_daynight(is_day)
            agg[tag] = {
                "legs": legs, "covered_h": round(cov, 2),
                "legs_per_h": round(legs / cov, 1) if cov else None,
                "net_usd": round(net, 3), "usd_per_h": round(net / cov, 3) if cov else None,
                "fee_usd": round(fee, 3),
                "stops": stops, "stop_rate": (round(stops / legs, 4) if legs else None),
                "stop_usd": round(stop_usd, 3),
                "taker_share": (round(taker / legs, 4) if legs else None),
                "net_bp_per_leg": (round(net / (legs * 1.0), 5) if legs else None),
            }
        out["daynight"] = agg
        print("=" * 96)
        print("现纪元昼/夜经济性对照（腿速=可交易口径；金额=账本口径）")
        print("=" * 96)
        print(f"{'时段':<6}{'腿':>6}{'可交易h':>10}{'腿/h':>8}{'净$':>9}{'$/h':>8}"
              f"{'止损率':>8}{'止损$':>9}{'taker占比':>10}")
        for tag, d in agg.items():
            print(f"{tag:<6}{d['legs']:>6}{d['covered_h']:>10.2f}"
                  f"{(d['legs_per_h'] or 0):>8.1f}{d['net_usd']:>9.2f}"
                  f"{(d['usd_per_h'] or 0):>8.3f}"
                  f"{(d['stop_rate'] or 0):>8.2%}{d['stop_usd']:>9.2f}"
                  f"{(d['taker_share'] or 0):>10.2%}")
        print("-" * 96)
        _d, _n = agg["昼"], agg["夜"]
        if _d["usd_per_h"] is not None and _n["usd_per_h"] is not None:
            _gap = _n["usd_per_h"] - _d["usd_per_h"]
            print(f"⇒ 夜间比白天**每可交易小时多亏 {abs(_gap):.3f}$**"
                  f"（夜 {_n['usd_per_h']:+.3f} vs 昼 {_d['usd_per_h']:+.3f}）"
                  if _gap < 0 else
                  f"⇒ 夜间反而更好 {_gap:+.3f}$/h（与 R80 的止损率方向相反 ⇒ 需查构成）")
            print(f"⇒ 止损率：夜 {_n['stop_rate']:.2%} vs 昼 {_d['stop_rate']:.2%}"
                  f"（比值 {(_n['stop_rate'] / _d['stop_rate']) if _d['stop_rate'] else float('nan'):.2f}×）")
            print("⇒ 可操作含义：只有在**同一参数体制内**、且**修复落地之后的时段**比较昼夜，"
                  "结论才可用——本脚本首版就是把 h472 修复前的 105 条 `ofi_flatten_taker` 腿"
                  "（全部落在夜桶）当成了'夜间结构更差' ✗，已用 `--since` 修掉。"
                  "若在这两个前提下夜间仍更亏，再考虑降低夜间暴露；否则不要动。")
        # [R81] **按币分层**：昼夜的币种构成若不同，上面的差距就可能只是"夜间 NEAR 多"。
        cur.execute(
            "SELECT symbol,"
            " count(*) FILTER (WHERE extract(hour from ts AT TIME ZONE 'Asia/Shanghai')"
            "   >= %s AND extract(hour from ts AT TIME ZONE 'Asia/Shanghai') < %s) AS d_legs,"
            " COALESCE(sum(net_bp*notional/1e4) FILTER (WHERE extract(hour from ts"
            "   AT TIME ZONE 'Asia/Shanghai') >= %s AND extract(hour from ts"
            "   AT TIME ZONE 'Asia/Shanghai') < %s),0)::float8 AS d_net,"
            " count(*) FILTER (WHERE NOT (extract(hour from ts AT TIME ZONE 'Asia/Shanghai')"
            "   >= %s AND extract(hour from ts AT TIME ZONE 'Asia/Shanghai') < %s)) AS n_legs,"
            " COALESCE(sum(net_bp*notional/1e4) FILTER (WHERE NOT (extract(hour from ts"
            "   AT TIME ZONE 'Asia/Shanghai') >= %s AND extract(hour from ts"
            "   AT TIME ZONE 'Asia/Shanghai') < %s)),0)::float8 AS n_net,"
            " count(*) FILTER (WHERE meta_json->>'exit_path' LIKE '%%taker' AND NOT"
            "   (extract(hour from ts AT TIME ZONE 'Asia/Shanghai') >= %s AND extract(hour"
            "   from ts AT TIME ZONE 'Asia/Shanghai') < %s)) AS n_taker"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND symbol = ANY(%s) GROUP BY 1 ORDER BY 2 DESC",
            (DAY_H0, DAY_H1, DAY_H0, DAY_H1, DAY_H0, DAY_H1, DAY_H0, DAY_H1,
             DAY_H0, DAY_H1, h.LANE, since_used, syms))
        pc = {}
        print("\n[按币分层] 昼/夜 的腿数与净额（排除'夜间币种构成不同'这一混淆）：")
        print(f"  {'币':<6}{'昼腿':>7}{'夜腿':>7}{'夜腿占比':>10}{'昼净$':>9}{'夜净$':>9}"
              f"{'夜taker占比':>12}")
        for r in cur.fetchall():
            sym, dl, dn, nl, nn, nt = (str(r[0]), int(r[1]), float(r[2]), int(r[3]),
                                       float(r[4]), int(r[5]))
            pc[sym] = {"day_legs": dl, "night_legs": nl, "day_net_usd": round(dn, 3),
                       "night_net_usd": round(nn, 3),
                       "night_taker_share": round(nt / nl, 4) if nl else None}
            print(f"  {sym:<6}{dl:>7}{nl:>7}"
                  f"{(nl / (dl + nl) if (dl + nl) else 0):>10.0%}"
                  f"{dn:>9.2f}{nn:>9.2f}"
                  f"{(nt / nl if nl else 0):>12.1%}")
        out["per_coin_daynight"] = pc
        # [R81b] **夜间多出来的 taker 腿走哪条路径**（决定该修哪个旋钮）。
        cur.execute(
            "SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(入场腿)') AS p,"
            " count(*) FILTER (WHERE extract(hour from ts AT TIME ZONE 'Asia/Shanghai')"
            "   >= %s AND extract(hour from ts AT TIME ZONE 'Asia/Shanghai') < %s) AS d,"
            " count(*) FILTER (WHERE NOT (extract(hour from ts AT TIME ZONE 'Asia/Shanghai')"
            "   >= %s AND extract(hour from ts AT TIME ZONE 'Asia/Shanghai') < %s)) AS n,"
            " COALESCE(sum(net_bp*notional/1e4) FILTER (WHERE NOT (extract(hour from ts"
            "   AT TIME ZONE 'Asia/Shanghai') >= %s AND extract(hour from ts"
            "   AT TIME ZONE 'Asia/Shanghai') < %s)),0)::float8 AS n_net"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND symbol = ANY(%s) GROUP BY 1 ORDER BY 3 DESC",
            (DAY_H0, DAY_H1, DAY_H0, DAY_H1, DAY_H0, DAY_H1, h.LANE, since_used, syms))
        paths = {}
        print("\n[出场路径 × 昼夜]（按夜腿数排序）：")
        print(f"  {'exit_path':<34}{'昼腿':>7}{'夜腿':>7}{'夜净$':>9}")
        for r in cur.fetchall():
            p, d, n, nn = str(r[0]), int(r[1]), int(r[2]), float(r[3])
            paths[p] = {"day_legs": d, "night_legs": n, "night_net_usd": round(nn, 3)}
            print(f"  {p:<34}{d:>7}{n:>7}{nn:>9.2f}")
        out["exit_path_daynight"] = paths
    p = ROOT / "research_l1" / "out" / "h578_daynight_economics.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"写出 {p.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
