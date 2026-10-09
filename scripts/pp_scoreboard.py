# -*- coding: utf-8 -*-
"""重复来回做市（ping-pong）记分板。只读，不写账。

验收口径（用户 2026-10-09）：
  · 看 `lane_ledger.meta_json->>'rt_bp'`（开仓价→平仓价），不看旧的价差列。
  · 满约 100 笔后：成功率仍在 60% 附近 **且** 赚的幅度大于亏的幅度 ⇒ 继续重复；
    成功率跌破打平线（|avg_loss|/(avg_win+|avg_loss|)），或赚的没有亏的大 ⇒ 停。
  · 100 笔以前只看卫生：进场有没有插进价差（qpos='inside'）、有没有普通吃单
    （吃单只允许 taker_no_book / taker_stop，吃单进场必须为 0）。

用法：
  python scripts/pp_scoreboard.py            # 从 data/pp_era_start_ts.txt 起算
  python scripts/pp_scoreboard.py --begin    # 记录本时代起点（部署时执行一次）
  python scripts/pp_scoreboard.py --since 24 # 只看最近 N 小时（忽略标记）
  python scripts/pp_scoreboard.py --lane xxx # 车道（默认 mm_asterdex）
"""
import argparse
import pathlib
import sys
import time

import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

MARKER = ROOT / "data" / "pp_era_start_ts.txt"
LANE = "mm_asterdex"
MIN_N = 100  # 满约 100 笔再下结论
WIN_TARGET = 0.60


def _f(x, default=0.0):
    try:
        v = float(x)
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def main() -> int:
    ap = argparse.ArgumentParser(description="ping-pong rt_bp 记分板")
    ap.add_argument("--begin", action="store_true",
                    help="记录本时代起点（写入 data/pp_era_start_ts.txt）")
    ap.add_argument("--since", type=float, default=0.0,
                    help="只看最近 N 小时（覆盖标记起点）")
    ap.add_argument("--lane", default=LANE, help=f"车道 id（默认 {LANE}）")
    args = ap.parse_args()

    if args.begin:
        MARKER.parent.mkdir(parents=True, exist_ok=True)
        MARKER.write_text(str(time.time()), encoding="utf-8")
        print(f"已记录 ping-pong 时代起点: {MARKER} = {time.time():.0f}")

    start_ts = 0.0
    if args.since > 0:
        start_ts = time.time() - args.since * 3600.0
    elif MARKER.exists():
        try:
            start_ts = float(MARKER.read_text(encoding="utf-8").strip() or 0.0)
        except ValueError:
            start_ts = 0.0
    if start_ts <= 0:
        print("没有时代起点：先执行 `python scripts/pp_scoreboard.py --begin`，"
              "或用 --since N 指定小时数。")
        return 2

    c = psycopg.connect(read_env_dsn(), autocommit=True)
    cur = c.cursor()
    cur.execute("""
        SELECT symbol, ts,
               meta_json->>'rt_bp'      AS rt,
               meta_json->>'exit_path'  AS ep,
               meta_json->>'qpos'       AS qpos,
               meta_json->>'side'       AS side,
               meta_json->>'pp_arm_rel_bp' AS arm_rel,
               COALESCE((meta_json->>'flatten')::boolean, false) AS flatten,
               fee_bp
        FROM lane_ledger
        WHERE lane_id=%s AND event='fill' AND ts >= to_timestamp(%s)
        ORDER BY ts
    """, (args.lane, start_ts))
    rows = cur.fetchall()

    rt = []
    per_symbol: dict = {}
    inside_entries = []
    taker_bad = []
    taker_entries = []
    ep_counts: dict = {}
    for sym, ts, rtv, ep, qpos, side, arm_rel, flatten, fee in rows:
        ep = str(ep or "")
        ep_counts[ep] = ep_counts.get(ep, 0) + 1
        sym = str(sym or "?")
        if rtv is not None:
            v = _f(rtv)
            rt.append(v)
            per_symbol.setdefault(sym, []).append(v)
        # [2026-10-09] 插进价差的**真口径**：挂单时刻相对买一/卖一的偏移。
        # 买侧 >0 / 卖侧 <0（容差 0.5bp）才算真插价差；老行没这字段时
        # 退回 qpos 口径（qpos 会把"成交后盘口穿过我们"误报成插价差）。
        side_s = str(side or "").lower()
        if not flatten:
            violated = False
            if arm_rel is not None:
                rel = _f(arm_rel)
                violated = (side_s == "buy" and rel > 0.5) or (
                    side_s == "sell" and rel < -0.5)
            elif str(qpos or "") == "inside":
                violated = True
            if violated:
                inside_entries.append((ts, sym, f"rel={arm_rel}" if arm_rel else qpos))
        paid = _f(fee) < -1e-9
        if paid and ep not in ("taker_no_book", "taker_stop"):
            taker_bad.append((ts, sym, ep))
        if paid and not flatten:
            taker_entries.append((ts, sym, ep))

    print(f"\n车道 {args.lane} · 起点 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_ts))}")
    print(f"总成交腿数: {len(rows)}")

    print("\n── 卫生（100 笔以前只看这个）──────────────────────────")
    print(f"  插进价差的进场腿(qpos='inside'): {len(inside_entries)}")
    for ts, sym, _q in inside_entries[:10]:
        print(f"    {ts:%m-%d %H:%M:%S} {sym}")
    print(f"  普通吃单(fee<0 且非 taker_no_book/taker_stop): {len(taker_bad)}")
    for ts, sym, ep in taker_bad[:10]:
        print(f"    {ts:%m-%d %H:%M:%S} {sym} {ep}")
    print(f"  吃单进场: {len(taker_entries)}")
    for ts, sym, ep in taker_entries[:10]:
        print(f"    {ts:%m-%d %H:%M:%S} {sym} {ep}")

    print("\n── 来回账（rt_bp = 开仓价→平仓价）─────────────────────")
    n = len(rt)
    print(f"  已完成来回: {n} / 目标 {MIN_N}")
    if not rt:
        print("  还没有带 rt_bp 的平仓腿。继续攒。")
        return 0
    wins = [v for v in rt if v > 0]
    losses = [v for v in rt if v < 0]
    wr = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    avg = sum(rt) / n
    print(f"  成功率: {wr:.1%}（目标 ≥{WIN_TARGET:.0%}）")
    print(f"  赚的幅度: {avg_win:+.2f}bp / 笔 · 亏的幅度: {avg_loss:+.2f}bp / 笔")
    print(f"  每笔均值: {avg:+.2f}bp")
    print(f"  出口构成: {dict(sorted(ep_counts.items(), key=lambda kv: -kv[1]))}")
    if len(per_symbol) > 1:
        print("  逐币:")
        for sym, vs in sorted(per_symbol.items(),
                              key=lambda kv: -len(kv[1])):
            w = [v for v in vs if v > 0]
            print(f"    {sym:8s} n={len(vs):3d} 胜率={len(w)/len(vs):5.1%} "
                  f"均={sum(vs)/len(vs):+7.2f}bp")

    print("\n── 裁决 ────────────────────────────────────────────────")
    if n < MIN_N:
        print(f"  未满 {MIN_N} 笔：只看卫生项，不宣布赚钱。"
              "插进价差=0 且 普通吃单=0 即继续。")
        return 0
    bigger = avg_win > abs(avg_loss)
    breakeven = abs(avg_loss) / (avg_win + abs(avg_loss)) if avg_win > 0 else 1.0
    if wr < breakeven or not bigger:
        print(f"  ✋ STOP：成功率 {wr:.1%} 跌破打平线 {breakeven:.1%}，"
              f"或赚的幅度({avg_win:+.2f}bp)不大于亏的幅度({avg_loss:+.2f}bp)。"
              "回滚：MM_PINGPONG=0。")
        return 1
    if wr >= WIN_TARGET and bigger:
        print(f"  ✓ GO：成功率 {wr:.1%} 在 {WIN_TARGET:.0%} 附近，"
              f"赚({avg_win:+.2f}bp) > 亏({abs(avg_loss):.2f}bp)。继续重复。")
        return 0
    print(f"  观察：成功率 {wr:.1%} 未到 {WIN_TARGET:.0%} 但仍在打平线之上，"
          "继续攒样本再判。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
