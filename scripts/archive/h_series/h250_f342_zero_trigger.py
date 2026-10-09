# -*- coding: utf-8 -*-
"""H250 F342 触发率为 0 的排查：先证明**执行路径可达**，再核对阈值。

# 现象

F342 上线后 30 分钟、122 个 tick、4 个币，`skip_counts.sudden_move = 0`。
设计值 18.3 次/天（跨 4 币）⇒ 30 分钟应约 0.38 次 ⇒ **0 次本身不算异常**，
但必须排除两类原因：

  ① **执行路径不可达**（代码没进、参数没传进 limits、`mid_hist` 为空）
  ② **阈值与 mid_hist 的真实步长不匹配**（校准时我用 19s 网格近似，
     而引擎只在**快照更新时**追加 ⇒ 真实步长可能明显更长 **或更短**）

# 本脚本做三件事

1. **可执行性自证**：用真实参数在真实 `mid_hist` 尺度上跑
   `core.sudden_move_hit`，证明函数本身会命中（不是恒 False）；
2. **量实际步长**：从 `asterdex_book_ticker` 按**引擎的追加语义**
   （快照更新时才追加）估真实 tick 周期，而不是假设 19s；
3. **反推应有的阈值**：给出"多少 bp 对应多少次/天"的对照表，
   以及**当前 20bp 在这个步长下应该触发多少次/天**。

# 用法

    python scripts/h250_f342_zero_trigger.py --hours 6
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h250_f342_zero.json"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]


def market_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    base = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        base = base.replace(j, "")
    return base.rsplit("/", 1)[0] + "/alpha_market"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    a = ap.parse_args()

    # ── 1. 执行路径自证（不依赖生产状态）──────────────────────────────
    from backend.services.market_maker.core import (LaneRiskLimits,
                                                    sudden_move_hit)
    lim = LaneRiskLimits(sudden_move_bp=20.0, sudden_move_k=1)
    print("=" * 96)
    print("H250  F342 触发率为 0 的排查")
    print("=" * 96)
    print(f"\n  ① 执行路径自证")
    print(f"     LaneRiskLimits(sudden_move_bp=20) → 读回 "
          f"{lim.sudden_move_bp!r}  k={lim.sudden_move_k!r}")
    for hist, label in (
        ([100.0, 100.0], "无移动"),
        ([100.0, 100.1], "+10bp（应不命中）"),
        ([100.0, 100.25], "+25bp（应命中）"),
        ([100.0, 99.75], "−25bp（应命中）"),
    ):
        hit, mv = sudden_move_hit(hist, lim.sudden_move_bp, lim.sudden_move_k)
        print(f"     {label:<22} mv={mv:+8.2f}bp  hit={hit}")
    ok_path = sudden_move_hit([100.0, 100.25], 20.0, 1)[0]
    print(f"     ⇒ 函数本身{'可命中 ✓' if ok_path else '**恒 False ✗**'}")

    # ── 2. 量实际步长 ────────────────────────────────────────────────
    import psycopg
    RAW = {}
    for sym in CUR:
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                    FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                          FROM asterdex_book_ticker
                          WHERE ingest_ts >= now() - (%s || ' hours')::interval
                            AND symbol = %s AND bid_px > 0 AND ask_px > bid_px) t
                    ORDER BY bucket, bid_px
                """, (str(float(a.hours) + 0.2), sym))
                RAW[sym] = cur.fetchall()
    series = {}
    for sym, recs in RAW.items():
        d = {int(b): ((float(bi) + float(ak)) / 2.0) for b, bi, ak in recs}
        ks = sorted(d)
        series[sym] = (ks, [d[k] for k in ks])

    print(f"\n  ② 实际步长（1s 网格上的价格序列）")
    print(f"     {'币':<12}{'点数':>8}{'1s 网格连续率':>15}{'估算快照间隔':>15}")
    gaps = []
    for sym, (ks, px) in sorted(series.items()):
        if len(ks) < 3:
            continue
        d = [ks[i + 1] - ks[i] for i in range(len(ks) - 1)]
        cont = sum(1 for x in d if x == 1) / len(d) * 100
        # 快照间隔 ≈ 数据里"价格变化"的平均间隔（引擎只在快照更新时追加）
        changes = [ks[i + 1] - ks[i] for i in range(len(px) - 1) if px[i + 1] != px[i]]
        med = st.median(changes) if changes else 0
        gaps.append(med)
        print(f"     {sym:<12}{len(ks):>8}{cont:>14.1f}%{med:>14.1f}s")
    est = st.median(gaps) if gaps else 19.0
    print(f"     ⇒ 估算快照间隔中位 = **{est:.0f}s**（校准脚本用的是 19s）")

    # ── 3. 按估算步长重算触发率 ──────────────────────────────────────
    print(f"\n  ③ 按 {est:.0f}s 步长重算：多少 bp 对应多少次/天")
    total_h = 0.0
    allsteps = []
    for sym, (ks, px) in series.items():
        if len(ks) < 2:
            continue
        total_h += (ks[-1] - ks[0]) / 3600.0
        i = 0
        while i < len(ks) - 1:
            j = i + 1
            while j + 1 < len(ks) and ks[j + 1] - ks[i] < est:
                j += 1
            if j <= i:
                i += 1
                continue
            if px[i] > 0:
                allsteps.append(abs(px[j] - px[i]) / px[i] * 1e4)
            i = j
    if allsteps:
        allsteps.sort()
        n = len(allsteps)
        print(f"     （样本 {n}，总时长 {total_h:.1f} 币·小时）")
        print(f"\n     {'阈值bp':>8}{'命中数':>9}{'命中率':>9}{'次/天(4币)':>13}")
        for thr in (5, 8, 10, 12, 15, 20, 30, 50):
            hits = sum(1 for x in allsteps if x >= thr)
            pd = hits / total_h * 24 if total_h else 0
            print(f"     {thr:>8}{hits:>9}{hits/n*100:>8.2f}%{pd:>13.1f}")
        print(f"\n     ⇒ **当前 sudden_move_bp=20 在此步长下应触发 "
              f"{sum(1 for x in allsteps if x >= 20)/total_h*24 if total_h else 0:.1f} 次/天**")
        q50 = allsteps[n // 2]
        q90 = allsteps[int(n * 0.9)]
        q99 = allsteps[int(n * 0.99)]
        print(f"     步长分布：P50 {q50:.2f}　P90 {q90:.2f}　P99 {q99:.2f}　max {allsteps[-1]:.2f} bp")

    print(f"\n{'━'*96}\n  结论\n{'━'*96}")
    if allsteps:
        pd20 = sum(1 for x in allsteps if x >= 20) / total_h * 24 if total_h else 0
        obs_h = 0.6   # 观测了约 36 分钟
        expect_in_window = pd20 / 24 * obs_h
        print(f"\n  按 {est:.0f}s 步长，20bp 阈值应触发约 {pd20:.1f} 次/天"
              f" ⇒ 观测 {obs_h*60:.0f} 分钟应约 {expect_in_window:.2f} 次")
        if expect_in_window < 1.0:
            print(f"  ⇒ **0 次触发是正常的**（期望值 < 1），不是 bug")
        else:
            print(f"  ⇒ 期望值 ≥ 1 却 0 次 ⇒ 需要继续排查（可能行情特别平静）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "est_tick_sec": est,
                               "n_steps": len(allsteps)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
