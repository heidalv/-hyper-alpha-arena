# -*- coding: utf-8 -*-
"""[F279 2026-09-16] 停机断点 A/B：现行实盘（stale）vs 断点补齐（splice）。

问题：实盘进程今天（09-16）09:51~13:07 被拉起 **10 次**（logs/backend.pid*.log）。
每次重启，持久化的 `mid_hist` 停在停机前，而 `backfill_mid_hist`（F109）有
`len(mid_hist) >= keep 就跳过` 的短路 ⇒ **不补**，于是重启后第一条快照与停机前
那条直接相邻 ⇒ 一条跨越 5~20 分钟的**伪收益** ⇒ `realized_vol_bp` 抬高 ⇒
`vol_regime_blocked` 把该币站开整整一个窗口（15s × 20 期 ≈ 5 分钟）× 每次重启。
模型侧读的是连续快照、没有这条伪收益 ⇒ 实盘报价时间系统性少于模型。

做法：从 `logs/backend.pid*.log` 重建**真实停机窗口**，在回放里仿真两种口径：
  · stale  = 现行实盘（窗口内不 tick、不追加 mid_hist ⇒ 恢复时产生伪收益）；
  · splice = F279（窗口内不 tick，但 mid_hist 按快照补齐 ⇒ 连续、无伪收益）。
两者**唯一**差别就是这条伪收益 ⇒ 净额/成交/报价时间的差就是补齐的价值。

用法：
  python scripts/mm_restart_ab.py --day 2026-09-16 --start 08:00 --symbols BTC,ETH
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import sys
from datetime import datetime, timedelta, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from backend.services import lane_registry as reg  # noqa: E402
from backend.services.market_maker import evolution as evo  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.portfolio_replay import _load_all, replay_portfolio  # noqa: E402

LANE = "mm_asterdex"
CST = timezone(timedelta(hours=8))
MIN_DOWN_SEC = 30          # 停机短于该值不算断点（正常抖动）


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def restart_windows(day: str, repo: str = ROOT) -> list:
    """从 backend.pid*.log 重建停机窗口 [(down_ms, up_ms), ...]（升序）。

    每个 pid 日志 = 一次后端进程：CreationTime = 启动，LastWriteTime = 最后一条日志
    （= 被杀的时刻）。相邻两次启动之间即为停机窗口。
    """
    d0 = datetime.strptime(day, "%Y-%m-%d")
    d1 = d0 + timedelta(days=1)
    runs = []
    for p in glob.glob(os.path.join(repo, "logs", "backend.pid*.log")):
        st = datetime.fromtimestamp(os.path.getctime(p))
        en = datetime.fromtimestamp(os.path.getmtime(p))
        if st < d0 or st > d1:
            continue
        runs.append((int(st.timestamp() * 1000), int(en.timestamp() * 1000), os.path.basename(p)))
    runs.sort()
    wins = []
    for (s0, e0, n0), (s1, _e1, _n1) in zip(runs, runs[1:]):
        if (s1 - e0) / 1000.0 >= MIN_DOWN_SEC:
            wins.append((e0, s1, n0))
    return [(a, b) for a, b, _ in wins], runs


def _shift_windows(wins: list, day_src: str, day_dst: str, ref_min: float) -> list:
    """把 day_src 的停机时刻表**平移**到 day_dst（秒级），用于无停机日志的历史窗口。"""
    src = datetime.strptime(day_src, "%Y-%m-%d")
    dst = datetime.strptime(day_dst, "%Y-%m-%d")
    delta = (dst - src).total_seconds() * 1000
    out = []
    for a, b in wins:
        na, nb = a + int(delta), b + int(delta)
        if (nb - na) / 60000.0 > ref_min:      # 只保留 >= 阈值的（平移后不变，纯防御）
            out.append((na, nb))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=datetime.now(CST).strftime("%Y-%m-%d"))
    ap.add_argument("--start", default="08:00", help="窗口起点（CST，HH:MM）")
    ap.add_argument("--end", default=None, help="窗口终点（CST，HH:MM；默认=现在）")
    ap.add_argument("--symbols", default="BTC,ETH")
    ap.add_argument("--delay", type=float, default=31800.0, help="回放 tick 延迟 ms")
    ap.add_argument("--no-downtime", action="store_true", help="加跑一条不仿真停机的对照")
    ap.add_argument("--synth-from", default=None,
                    help="当日无停机日志时，用该日的时刻表平移合成（标注 synth）")
    args = ap.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    day = args.day
    s0 = f"{day}T{args.start}:00+08:00"
    end_hm = args.end or datetime.now(CST).strftime("%H:%M")
    s1 = f"{day}T{end_hm}:00+08:00"
    since_ms, until_ms = _ms(s0), _ms(s1)

    wins, runs = restart_windows(day)
    _synth = False
    if not wins and args.synth_from:
        src_wins, _ = restart_windows(args.synth_from)
        wins = _shift_windows(src_wins, args.synth_from, day, 0.0)
        _synth = True
    wins = [(a, b) for a, b in wins if a < until_ms and b > since_ms]
    print(f"[F279 A/B] day={day} 窗口={s0[11:16]}~{end_hm} (CST) symbols={syms} "
          f"delay={args.delay/1000:.1f}s{'  [停机时刻表=synth]' if _synth else ''}")
    print(f"  当日后端启动 {len(runs)} 次；窗口内停机断点 {len(wins)} 个，"
          f"累计 {(sum(b - a for a, b in wins) / 60000.0):.1f} 分钟")
    for a, b in wins:
        print(f"    down {datetime.fromtimestamp(a/1000, CST):%H:%M:%S} → "
              f"up {datetime.fromtimestamp(b/1000, CST):%H:%M:%S} "
              f"({(b - a)/60000.0:.1f} min)")

    lane = reg.get_lane(LANE) or {}
    meta = dict(lane.get("meta") or {})
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    qp = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
    print(f"  在位参数: w_base_bp={qp.w_base_bp} vol_pause_sigma={lim.vol_pause_sigma} "
          f"vol_window={lim.vol_window} judge_lag={meta.get('judge_lag_buckets')} "
          f"universe={meta.get('symbols')}")

    data = _load_all(syms, venue)
    sub, seed = {}, {}
    for s in syms:
        dd = data[s]
        a2 = int(np.searchsorted(dd["ots"], since_ms, "left"))
        a3 = int(np.searchsorted(dd["ots"], until_ms, "left"))
        t2 = int(np.searchsorted(dd["tts"], since_ms, "left"))
        t3 = int(np.searchsorted(dd["tts"], until_ms, "left"))
        sub[s] = {k: dd[k][a2:a3] for k in ("ots", "bb", "ba", "mps")}
        for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
            sub[s][k] = dd[k][t2:t3]
        lo_i = max(0, a2 - 240)
        seed[s] = [x for x in (float((dd["bb"][k] + dd["ba"][k]) / 2.0)
                               for k in range(lo_i, a2)) if x > 0]

    cases = [("stale(现行实盘)", "stale", wins), ("splice(F279修复)", "splice", wins)]
    if args.no_downtime:
        cases.append(("无停机(上界参照)", "stale", []))
    print(f"\n{'口径':>18s} {'fills':>6s} {'net$':>9s} {'net_bp':>8s} {'maker_bp':>9s} "
          f"{'flat_bp':>8s} {'dd%':>6s} {'quoted':>7s} {'both%':>6s} {'vol_pause':>10s}")
    rows = {}
    for name, mode, w in cases:
        r = replay_portfolio(
            syms, venue=venue, equity=300.0, params=qp, limits=lim, fill_notional=300.0,
            data=sub, vol_baseline=(vb or None), enforce_lane_limits=True,
            tick_delay_ms=float(args.delay), fill_notional_ratio=0.1,
            mid_hist_seed=(seed or None), restart_windows=(w or None), restart_mode=mode)
        sc = r.get("skip_counts") or {}
        sd = r.get("side_counts") or {}
        dec = max(1, sum(sd.values()))
        rows[name] = r
        print(f"{name:>18s} {r.get('fills'):>6} {r.get('net_usd'):>+9.3f} "
              f"{(r.get('net_bp') or 0):>+8.3f} {(r.get('maker_net_bp') or 0):>+9.3f} "
              f"{(r.get('flatten_net_bp') or 0):>+8.3f} {(r.get('max_dd_pct') or 0):>6.2f} "
              f"{(r.get('quoted_decisions') or 0):>7} "
              f"{(sd.get('both', 0) / dec * 100):>5.1f}% {sc.get('vol_pause', 0):>10}")
    if len(rows) >= 2:
        a, b = list(rows.values())[0], list(rows.values())[1]
        print(f"\n  差（splice − stale）：net {b['net_usd'] - a['net_usd']:+.3f}$  "
              f"fills {b['fills'] - a['fills']:+d}  "
              f"quoted {(b.get('quoted_decisions') or 0) - (a.get('quoted_decisions') or 0):+d}  "
              f"vol_pause {(b['skip_counts'].get('vol_pause', 0) - a['skip_counts'].get('vol_pause', 0)):+d}")
        print(f"  停机仿真：stale={a.get('restart_sim')} splice={b.get('restart_sim')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
