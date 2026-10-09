"""H84：从持仓曲线推导**真实仓位周期** —— 修 H75 的阻断（第 40 条教训的修法）。

# 为什么必须自己推导

`logs/mm_fill_basis.jsonl` 里 `position_id` 形如 `mm:ASTER:1`，但实测：
  · 不同 id 只有 **41 个**，而 `mm:ASTER:1` 一个 id 就占 **440 行**
  · `mm:XRP:1` 152 行 / `mm:XRP:2` 234 行 —— **每币只有 1~2 个序号**

⇒ 它是**每币单调递增的库存周期计数器**，**不是**"一次往返"。
**计数器 ≠ 周期标识**（第 40 条教训）。同一个序号下跨越了几百笔成交、很多次开平。

# 正确做法：从成交重建持仓曲线

每条 fill_basis 记录有 `side` 与 `qty`，于是

    position_after[k] = Σ_{i≤k} signed_qty(i),   signed = +qty(买) / −qty(卖)

**一个仓位周期** = 连续的、持仓不跨越 0 的一段：
  · 开始：`position_after` 由 0（或换向）变为非 0
  · 结束：`position_after` 回到 0，**或符号翻转**（穿仓/反手）

每个周期内：
  · `n_fills` = 该段成交笔数
  · `flattened` = 该段是否**以 flatten 腿结束**（`flatten=true` 的行落在段内）
  · `起始名义` = 段内最大 |持仓| × 价格（估风险规模）
  · `时长` = 段末 − 段首

# 判据（事先定死）

  · 若推导出的周期数 **≫ 41** ⇒ 证实"计数器不是周期标识"，且本方法可用
  · 若周期数 ≈ 41 ⇒ 说明 position_id 其实**就是**周期标识，我上一轮的判断错了
    （如实撤回）
  · 用推导出的周期标签，重跑 H75 的强平预测器

用法：
    .venv\\Scripts\\python.exe scripts\\h84_derive_episodes.py
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASIS = ROOT / "logs" / "mm_fill_basis.jsonl"
OUT = ROOT / "research_l1" / "out" / "h84_episodes.json"


def load():
    rows = []
    for line in BASIS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    rows.sort(key=lambda r: r.get("ts") or 0)
    return rows


def derive(rows, eps_abs=1e-9):
    """按 (symbol) 分别重建持仓曲线并切分周期。"""
    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r.get("symbol")].append(r)
    episodes = []
    for sym, rs in by_sym.items():
        pos = 0.0
        cur = None
        for r in rs:
            side = str(r.get("side") or "").lower()
            qty = float(r.get("qty") or 0.0)
            px = float(r.get("fill_px") or 0.0)
            signed = qty if side == "buy" else -qty
            prev = pos
            pos = prev + signed
            # 换向或从 0 起：开一个新周期
            start_new = (cur is None) or (
                abs(prev) <= eps_abs and abs(pos) > eps_abs) or (
                prev * pos < 0)
            if start_new:
                if cur is not None:
                    episodes.append(cur)
                cur = {"sym": sym, "t0": r.get("iso"), "ts0": r.get("ts"),
                       "n": 0, "flat": False, "max_abs_pos": 0.0,
                       "signed": 0.0, "last_px": px, "sign": (1 if signed > 0 else -1),
                       "fillts": [], "pos": []}
            if cur is None:
                continue
            cur["n"] += 1
            cur["signed"] += signed
            cur["max_abs_pos"] = max(cur["max_abs_pos"], abs(pos))
            cur["last_px"] = px or cur["last_px"]
            cur["fillts"].append(r.get("ts"))
            cur["pos"].append(pos)
            if r.get("flatten"):
                cur["flat"] = True
            # 回到 0 ⇒ 收尾
            if abs(pos) <= eps_abs:
                cur["closed"] = True
                episodes.append(cur)
                cur = None
        if cur is not None:
            cur["closed"] = False
            episodes.append(cur)
    for i, e in enumerate(episodes):
        e["eid"] = f"{e['sym']}#{i}"
        ts = [t for t in e["fillts"] if t]
        e["dur_s"] = (max(ts) - min(ts)) if len(ts) > 1 else 0.0
        e["notional"] = e["max_abs_pos"] * (e["last_px"] or 0.0)
    return episodes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true", help="打印全部周期明细")
    a = ap.parse_args()

    import numpy as np

    rows = load()
    if not rows:
        print("fill_basis 为空")
        return 1
    print("=" * 100)
    print("H84  从持仓曲线推导真实仓位周期")
    print("=" * 100)
    print(f"  fill_basis 行数 {len(rows):,}")
    pids = {r.get("position_id") for r in rows}
    print(f"  其中 position_id 不同值：**{len(pids)}**（计数器，非周期标识）")

    eps = derive(rows)
    n_flat = sum(1 for e in eps if e["flat"])
    n_closed = sum(1 for e in eps if e.get("closed"))
    print(f"\n  ⇒ 推导出周期数：**{len(eps)}**")
    print(f"     其中回到 0 平掉的：{n_closed}   未回到 0 的（跨越采集边界）：{len(eps)-n_closed}")
    print(f"     含 flatten 腿的：{n_flat}（{n_flat/max(len(eps),1)*100:.1f}%）")

    print("\n" + "=" * 100)
    print("判据")
    print("=" * 100)
    if len(eps) > len(pids) * 2:
        print(f"  ⇒ 周期数 {len(eps)} ≫ position_id 数 {len(pids)}")
        print("     ⇒ **证实「计数器 ≠ 周期标识」**，且本方法可用 ✓")
    elif abs(len(eps) - len(pids)) <= max(2, len(pids) * 0.2):
        print(f"  ⇒ 周期数 {len(eps)} ≈ position_id 数 {len(pids)}")
        print("     ⇒ position_id 其实就是周期标识 ⇒ **我上一轮的判断错了，撤回**")
    else:
        print(f"  ⇒ 周期数 {len(eps)} vs position_id 数 {len(pids)}：介于两者之间，需细查")

    # 分布
    if eps:
        durs = np.array([e["dur_s"] for e in eps])
        ns = np.array([e["n"] for e in eps])
        notl = np.array([e["notional"] for e in eps])
        print(f"\n  ── 周期分布 ──")
        print(f"     每周期成交笔数：中位 {np.median(ns):.0f}  最大 {ns.max():.0f}  合计 {ns.sum():.0f}")
        print(f"     时长 s：中位 {np.median(durs):.0f}  最大 {durs.max():.0f}")
        print(f"     峰值名义 USD：中位 {np.median(notl):.2f}  最大 {notl.max():.2f}")

        f_ = [e for e in eps if e["flat"]]
        nf = [e for e in eps if not e["flat"]]
        for lab, sub in (("含 flatten", f_), ("无 flatten", nf)):
            if not sub:
                print(f"\n     {lab}: 无")
                continue
            d = np.array([e["dur_s"] for e in sub])
            n = np.array([e["n"] for e in sub])
            print(f"\n     {lab}: n={len(sub)}")
            print(f"        笔数/周期 中位 {np.median(n):.0f}   时长 中位 {np.median(d):.0f}s "
                  f" 最大 {d.max():.0f}s")

    if a.dump or len(eps) <= 60:
        print("\n" + "=" * 100)
        print("周期明细")
        print("=" * 100)
        print(f"  {'eid':<16} {'笔':>4} {'flat':>5} {'closed':>7} {'时长s':>8} "
              f"{'峰值名义':>10} {'起点':<20}")
        print("  " + "-" * 76)
        for e in sorted(eps, key=lambda x: x["ts0"] or 0):
            print(f"  {e['eid']:<16} {e['n']:>4} {str(e['flat']):>5} "
                  f"{str(e.get('closed')):>7} {e['dur_s']:>8.0f} {e['notional']:>10.2f} "
                  f"{str(e['t0'])[:19]:<20}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"n_rows": len(rows), "n_pids": len(pids), "n_episodes": len(eps),
         "n_flat": n_flat, "n_closed": n_closed,
         "episodes": [{k: v for k, v in e.items() if k not in ("pos",)} for e in eps]},
        ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n[H84] 写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
