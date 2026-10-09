# -*- coding: utf-8 -*-
"""H457 双边往返 EV 模型（与线上机制一致）⇒ 解每个状态的最优挂单距离 δ*。

线上机制：双边 maker。挂买在 mid−δ、挂卖在 mid+δ（费≈0）。
  ① 成交：60s 内中价触及 mid−δ ⇒ 买腿成交价 = mid_t − δ（超冲 e = 触及时中价低于挂价的幅度）
  ② 出库：成交后 300s 内中价升到成交时中价 + δ 之上 ⇒ 卖腿成交
     成交+出库的盈亏 = δ − e（即捕获 δ，扣掉超冲）
  ③ 未出库：按窗口末中价标记 ⇒ 盈亏 = (mid_end − mid_t) + δ
  EV(δ, s) = P(成交|s) × [ P(出库|成交,s)·E(δ−e) + P(未出库|成交,s)·E(δ+Δmid_end) ]
状态桶：OFI×d 确认度（d=逆 r60）。数据：5s 网格（48h，h456 缓存）。
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CACHE = ROOT / "research_l1" / "out" / "h456_grid5_cache.json"
OUT = ROOT / "research_l1" / "out" / "h457_roundtrip_ev.json"
DELTAS = (1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0)
ENTRY_H = 12      # 60s（12 × 5s）
EXIT_H = 60       # 300s（60 × 5s）


def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    a = ap.parse_args()

    j = json.loads(CACHE.read_text(encoding="utf-8"))
    # ⚠️ JSON 会把 int 键写成字符串 ⇒ 必须转回 int（否则 t//15 报 TypeError）
    grids = {sym: {int(k): float(v) for k, v in g.items()}
             for sym, g in j["grids"].items()}
    ofi = {sym: {int(k): float(v) for k, v in d.items()}
           for sym, d in (j.get("ofi") or {}).items()}
    print(f"网格 {sum(len(v) for v in grids.values())} 点（{j.get('hours')}h 缓存）", flush=True)

    # 采样事件：每 5s 一点（避免重叠可用 stride 控制；此处 stride=1 并用 60s 入场窗）
    events = []   # (state_fo, delta_pl, fill_flag, exit_flag, pnl_fill_exit, pnl_hold)
    for sym, g in grids.items():
        ts = sorted(g)
        if len(ts) < ENTRY_H + EXIT_H + 20:
            continue
        for i in range(60, len(ts) - ENTRY_H - EXIT_H, 6):     # 每 30s 取一个样本
            t = ts[i]
            mid = g[t]
            m60 = g.get(ts[i - 12])
            if not m60 or m60 <= 0 or mid <= 0:
                continue
            r60 = (mid - m60) / m60 * 1e4
            if abs(r60) < 1e-9:
                continue
            d = -1.0 if r60 > 0 else 1.0          # 逆势（线上方向）
            f = ofi.get(sym, {}).get((t // 15) * 15, 0.0)
            fo = f * d                            # 确认度
            # 入场窗内的路径（相对 t 的不利偏移，单位 bp，正=不利）
            path_in = [d * (g[ts[i + k]] - mid) / mid * 1e4
                       for k in range(1, ENTRY_H + 1) if ts[i + k] in g]
            if len(path_in) < ENTRY_H:
                continue
            for delta in DELTAS:
                # ① 成交：不利偏移 ≥ δ
                tau = next((k for k, v in enumerate(path_in, start=1) if v >= delta), None)
                if tau is None:
                    events.append((fo, delta, 0, 0, 0.0, 0.0))
                    continue
                m_fill = g[ts[i + tau]]
                over = (d * (m_fill - mid) / mid * 1e4) - delta   # 超冲 e ≥ 0
                # ② 出库：从成交起，中价朝有利方向回到 m_fill 的有利侧 δ 之上
                exit_k = None
                for k in range(tau + 1, min(tau + EXIT_H, len(ts) - i)):
                    if ts[i + k] not in g:
                        continue
                    fav = -d * (g[ts[i + k]] - m_fill) / m_fill * 1e4   # 有利偏移（bp）
                    if fav >= delta:
                        exit_k = k
                        break
                if exit_k is not None:
                    pnl_fe = delta - over          # 捕获 δ 扣超冲
                    events.append((fo, delta, 1, 1, pnl_fe, 0.0))
                else:
                    # [h457b] **未出库分支必须带止损**：线上有 40bp 止损（宽限 0，即时 taker）
                    # 与 300s 硬上限。第一版漏了止损 ⇒ 该分支按末价标记 −30~−46bp，
                    # 使模型绝对值为负、与线上（往返 +0.9bp）矛盾 ✗。
                    k_end = min(tau + EXIT_H, len(ts) - 1)
                    stop_hit = None
                    for k in range(tau + 1, k_end + 1):
                        if ts[i + k] not in g:
                            continue
                        adv_k = d * (g[ts[i + k]] - m_fill) / m_fill * 1e4
                        if adv_k >= 40.0:
                            stop_hit = k
                            break
                    if stop_hit is not None:
                        pnl_hold = delta - 40.0 - 2.0     # 捕获 δ − 止损 − 费/滑点
                    else:
                        m_end = g[ts[i + k_end]]
                        pnl_hold = -d * (m_end - m_fill) / m_fill * 1e4 + delta
                    events.append((fo, delta, 1, 0, 0.0, pnl_hold))
    print(f"事件 {len(events)}", flush=True)

    def bucket_of(fo):
        return "confirm(≥0.5)" if fo >= 0.5 else ("弱确认(0~0.5)" if fo >= 0 else "逆流(<0)")

    res = {"hours": a.hours, "n_events": len(events), "buckets": {}}
    print(f"\n== 双边往返 EV(δ, state)（bp/次报价机会）==")
    for bname in ("confirm(≥0.5)", "弱确认(0~0.5)", "逆流(<0)"):
        sel = [e for e in events if bucket_of(e[0]) == bname]
        n = len(sel)
        if n < 100:
            continue
        print(f"\n-- {bname}（机会 {n}）--")
        evs = {}
        for delta in DELTAS:
            sub = [e for e in sel if abs(e[1] - delta) < 1e-9]
            p_fill = sum(e[2] for e in sub) / len(sub)
            filled = [e for e in sub if e[2]]
            if not filled:
                continue
            p_exit = sum(e[3] for e in filled) / len(filled)
            ex = [e[4] for e in filled if e[3]]
            hold = [e[5] for e in filled if not e[3]]
            e_ex = sum(ex) / len(ex) if ex else 0.0
            e_hold = sum(hold) / len(hold) if hold else 0.0
            ev = p_fill * (p_exit * e_ex + (1 - p_exit) * e_hold)
            evs[delta] = round(ev, 4)
            print(f"   δ={delta:>5.1f}  P(成交)={p_fill:5.2f}  P(出库|成交)={p_exit:5.2f}  "
                  f"E(已出库)={e_ex:+7.3f}  E(持有到末)={e_hold:+8.3f}  EV={ev:+7.4f}")
        best = max(evs, key=lambda k: evs[k]) if evs else None
        if best is not None:
            print(f"   ⇒ δ* = {best}bp（EV={evs[best]:+.4f}bp/次）")
        res["buckets"][bname] = {"n_opportunities": n, "ev": evs, "delta_star": best}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
