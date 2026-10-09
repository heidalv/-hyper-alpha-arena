# -*- coding: utf-8 -*-
"""[H122 2026-09-21] 单币绩效记账 —— 「亏损到一定程度就立刻停掉、移除」的判定核心。

# 用户需求（原话）

    「一个交易对亏损一定程度，马上停掉，移除。不给冻结机会，然后从 AI 选币补充」

# 为什么必须用**我们自己的成交**来判，而不是用选币器的打分

现行选币目标函数（`coin_select_hft.score`）是 `点差 × log10(吞吐) − 拥挤`。
问题在数学上很直白：**它随点差单调增**。实测后果 ——

    选出来的 AI 槽：SEI(中位点差 **19.67bp**)、ONDO(61.2% 强平率)、
                     PENDLE(91.7%)、ARB(53.6%)、VIRTUAL(100%)
    而固定 5 币：    ASTER(6.6%)、SOL(6.6%)、XRP(10.2%)

⇒ 打分器在**专挑被强平的那批**。点差宽不等于赚钱：点差宽 = 波动大 =
价格从我们挂单前穿过去，最后按 taker 平掉（`stop_loss` / `max_one_side`）。
所以判定必须建立在**已实现结果**上，而不是事前特征。

# 记账口径（每个币独立）

从 `logs/mm_fill_basis.jsonl`（我们自己的每一笔成交）+ `lane_ledger`（六维归因）：

  · `capture_bp`   入场腿（`flatten=false`）的价差捕获 —— 用 `edge_bp`
  · `flatten_rate` 含强平腿的周期占比（H84 周期口径，不是按笔数）
  · `net_usd`      该币**已实现净额** = 入场价差 − 强平成本 − 手续费
  · `cycles`       周期数（样本量，用于"样本不足不判"）

# 判据（事先定死，避免事后挑数）

一个币满足**任一**条件 ⇒ 判 `remove`：

  R1 样本够（`cycles ≥ min_cycles`）且 `net_usd ≤ -min_loss_usd`
      —— 「亏损到一定程度」，按已实现净额，最直接
  R2 样本够且 `flatten_rate ≥ max_flatten_rate`（默认 0.35）
      —— 强平率过高 ≈ 这个币的波动结构不适合做被动挂单
  R3 样本够且 `capture_bp ≤ 0`
      —— 入场腿本身没有捕获（挂单方向长期错），无药可救

判 `probation`（观察，不动）：`cycles < min_cycles` —— **样本不足不判**。

判 `keep`：以上都不满足。

**不给冻结机会**：R1/R2/R3 一旦成立就立刻摘，不等"再观察一会儿"。
冻结（`vol_pause`）是逐 tick 的临时闸，不是淘汰机制 —— 一个币可以在
被反复冻结的同时一直占着槽位、断断续续亏钱，这正是要消灭的模式。

用法：
    .venv\\Scripts\\python.exe scripts\\h122_symbol_scorecard.py
    .venv\\Scripts\\python.exe scripts\\h122_symbol_scorecard.py --since 2026-09-21T00:00:00
    .venv\\Scripts\\python.exe scripts\\h122_symbol_scorecard.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from h84_derive_episodes import derive, load  # noqa: E402

# ── 判据默认值（可被命令行覆盖，改这里就是改规则）──────────────────────
MIN_CYCLES = 30          # 样本量下限：低于此判 probation，不下结论
MIN_LOSS_USD = 3.0       # R1：已实现净额亏到这个数就摘
MAX_FLATTEN_RATE = 0.35  # R2：强平率超过这个就摘
MAX_CAPTURE_BP = 0.0     # R3：入场捕获 ≤ 0 就摘


def compute(since_iso: str = "", *, taker_fee_bp: float = 4.36) -> dict:
    """返回 {symbol: metrics}（纯函数，便于测试与复用）。"""
    rows = load()
    if since_iso:
        rows = [r for r in rows if (r.get("iso") or "") >= since_iso]
    eps = [e for e in derive(rows) if not since_iso or (e.get("t0") or "") >= since_iso]

    by_sym_fills = defaultdict(list)
    for r in rows:
        by_sym_fills[r.get("symbol")].append(r)
    by_sym_eps = defaultdict(list)
    for e in eps:
        by_sym_eps[e["sym"]].append(e)

    out: dict = {}
    for s in sorted(set(list(by_sym_fills) + list(by_sym_eps))):
        fs = by_sym_fills[s]
        es = by_sym_eps[s]
        if not fs:
            continue

        entry = [r for r in fs if not r.get("flatten")]
        flat = [r for r in fs if r.get("flatten")]

        # 入场腿捕获：用 edge_bp（若缺失则用 (mid−px)/mid 估）
        caps = []
        for r in entry:
            e = r.get("edge_bp")
            if e is None:
                px = float(r.get("fill_px") or 0.0)
                mid = float(r.get("engine_mid") or 0.0)
                if px > 0 and mid > 0:
                    sgn = 1.0 if str(r.get("side")).lower() == "buy" else -1.0
                    e = sgn * (mid - px) / mid * 1e4
            if e is not None:
                caps.append(float(e))

        # 已实现净额：入场 = 价差捕获；强平 = −taker 费 − 逆向移动
        entry_usd = 0.0
        for r in entry:
            px = float(r.get("fill_px") or 0.0)
            q = float(r.get("qty") or 0.0)
            mid = float(r.get("engine_mid") or 0.0)
            if px > 0 and mid > 0:
                sgn = 1.0 if str(r.get("side")).lower() == "buy" else -1.0
                entry_usd += sgn * (mid - px) * q
        flat_usd = 0.0
        flat_fee_usd = 0.0
        for r in flat:
            px = float(r.get("fill_px") or 0.0)
            q = float(r.get("qty") or 0.0)
            notional = px * q
            fr = float(r.get("fee_rate") or 0.0)
            flat_fee_usd += fr * notional
            # 强平腿本身按对手价成交 ⇒ 相对 mid 的损失
            mid = float(r.get("engine_mid") or 0.0)
            if mid > 0:
                sgn = 1.0 if str(r.get("side")).lower() == "buy" else -1.0
                flat_usd += sgn * (mid - px) * q

        n_cyc = len(es)
        n_flat = sum(1 for e in es if e["flat"])
        notl = [e["notional"] for e in es if e["notional"] > 0]

        out[s] = {
            "symbol": s,
            "fills": len(fs),
            "entry_fills": len(entry),
            "flat_fills": len(flat),
            "cycles": n_cyc,
            "flatten_cycles": n_flat,
            "flatten_rate": (n_flat / n_cyc) if n_cyc else None,
            "capture_bp_med": (st.median(caps) if caps else None),
            "capture_bp_mean": (sum(caps) / len(caps) if caps else None),
            "entry_usd": entry_usd,
            "flat_usd": flat_usd,
            "flat_fee_usd": flat_fee_usd,
            "net_usd": entry_usd + flat_usd - flat_fee_usd,
            "peak_notional_med": (st.median(notl) if notl else None),
            "notional_total": sum(float(r.get("fill_px") or 0) * float(r.get("qty") or 0)
                                  for r in fs),
        }
    return out


def verdict(m: dict, *, min_cycles: int = MIN_CYCLES, min_loss_usd: float = MIN_LOSS_USD,
            max_flatten_rate: float = MAX_FLATTEN_RATE,
            max_capture_bp: float = MAX_CAPTURE_BP) -> tuple:
    """返回 (判定, 理由)。判定 ∈ {keep, remove, probation}。"""
    if (m.get("cycles") or 0) < min_cycles:
        return "probation", f"样本不足（周期 {m.get('cycles')} < {min_cycles}）"
    reasons = []
    if m["net_usd"] <= -abs(min_loss_usd):
        reasons.append(f"R1 已实现净额 {m['net_usd']:+.2f} ≤ -{abs(min_loss_usd):.2f}")
    if m.get("flatten_rate") is not None and m["flatten_rate"] >= max_flatten_rate:
        reasons.append(f"R2 强平率 {m['flatten_rate']*100:.1f}% ≥ {max_flatten_rate*100:.0f}%")
    if m.get("capture_bp_med") is not None and m["capture_bp_med"] <= max_capture_bp:
        reasons.append(f"R3 入场捕获中位 {m['capture_bp_med']:+.3f}bp ≤ {max_capture_bp}bp")
    if reasons:
        return "remove", "；".join(reasons)
    return "keep", "各项达标"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-21T00:00:00")
    ap.add_argument("--min-cycles", type=int, default=MIN_CYCLES)
    ap.add_argument("--min-loss-usd", type=float, default=MIN_LOSS_USD)
    ap.add_argument("--max-flatten-rate", type=float, default=MAX_FLATTEN_RATE)
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    metrics = compute(a.since)
    print("=" * 108)
    print("H122  单币绩效记账（判据：亏损 / 强平率 / 入场捕获，样本不足不判）")
    print("=" * 108)
    print(f"  窗口起点 {a.since}   币数 {len(metrics)}")
    print(f"  阈值：周期≥{a.min_cycles}  净额≤-{a.min_loss_usd}  "
          f"强平率≥{a.max_flatten_rate*100:.0f}%  捕获≤{MAX_CAPTURE_BP}bp")

    print(f"\n  {'币':<10} {'周期':>5} {'强平率':>7} {'捕获bp':>8} {'入场$':>9} "
          f"{'强平$':>9} {'强平费$':>9} {'净额$':>9}  判定")
    print("  " + "-" * 96)
    verdicts = {}
    for s, m in sorted(metrics.items(), key=lambda kv: kv[1]["net_usd"]):
        v, why = verdict(m, min_cycles=a.min_cycles, min_loss_usd=a.min_loss_usd,
                         max_flatten_rate=a.max_flatten_rate)
        verdicts[s] = (v, why)
        fr = f"{m['flatten_rate']*100:.1f}%" if m["flatten_rate"] is not None else "—"
        cb = f"{m['capture_bp_med']:+.3f}" if m["capture_bp_med"] is not None else "—"
        mark = {"remove": "**摘**", "keep": "留", "probation": "观察"}[v]
        print(f"  {s:<10} {m['cycles']:>5} {fr:>7} {cb:>8} {m['entry_usd']:>9.3f} "
              f"{m['flat_usd']:>9.3f} {m['flat_fee_usd']:>9.3f} {m['net_usd']:>9.3f}  {mark}")

    print("\n" + "=" * 108)
    print("判定明细")
    print("=" * 108)
    for grp, lab in (("remove", "摘除"), ("probation", "观察（样本不足）"), ("keep", "保留")):
        ss = [s for s, (v, _) in verdicts.items() if v == grp]
        print(f"\n  [{lab}] {len(ss)} 个: {', '.join(sorted(ss)) or '(无)'}")
        for s in sorted(ss):
            print(f"      {s:<10} {verdicts[s][1]}")

    if a.json:
        Path(a.json).write_text(json.dumps(
            {"since": a.since, "metrics": metrics,
             "verdicts": {k: {"verdict": v, "reason": w}
                          for k, (v, w) in verdicts.items()}},
            ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n  写出 {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
