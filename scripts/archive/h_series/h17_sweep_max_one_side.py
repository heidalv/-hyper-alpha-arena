"""H17：超时强平上限 `max_one_side_seconds` 是否应该保留？同窗口回放扫它。

## 为什么要扫这个参数（2026-09-20 19:05 现场数据）

从 `arbitrage_paper_ledgers` 按 `metadata.phase` 拆开近 3h（96 行）：

    phase=fill    （被动成交的开仓腿）  90 笔   -$0.0740   ≈ -$0.0008/笔
    phase=flatten （超时/止损 taker 平仓） 6 笔   -$0.1462   ≈ **-$0.0244/笔**

6 笔平仓腿吃掉了 **66%** 的总亏损。$30 腿量下 -$0.0244 ≈ **-8.1bp/次**，
而单笔被动成交的价差捕获只有 +1.45bp ⇒ 一次 taker 强平要 **5.6 笔**被动成交
的毛利才能填平。所以"持仓超时后打对手价平掉"这条规则的性价比必须重新评估。

## 但这里有个必须先说清的因果陷阱

**不能**用「这笔平仓亏了钱，所以平仓有害」来推论。因果是反的：
被超时强平的仓位，正是**没能在阈值内等到对手腿**的那些——
它们是"本来就出不去"的样本。把这个规则关掉，这些仓位不是消失，
而是**继续持有**，其结局同样未知（可能被对手腿吃掉 ✓，也可能继续亏更大 ✗）。

⇒ 唯一能回答的方法是**同窗口回放**：同一段真实快照数据，只改
`max_one_side_seconds`，看成交数 / 净额 / 强平次数怎么变。
（**跨窗口求和不作数**；只比同一窗口内的相对差。）

## 口径说明（为什么用 lane_registry 的真实参数）

回放默认参数是**模块常量**（`FILL_NOTIONAL=100`、`QuoteParams()` 默认值），
与线上车道当前生效的参数（`w_base_bp=1.5`、`compound_ratio=0.1` 等）完全不同。
若用默认值回放，得到的是"另一个策略"的结果，对现场无参考价值。
因此本脚本把注册表 `meta.params` 原样喂给 `replay_symbol`，只覆盖被扫的那一项。

用法：
    .venv\\Scripts\\python.exe scripts\\h17_sweep_max_one_side.py --hours 4
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

LANE = os.getenv("MM_LANE_ID", "mm_asterdex")
OUT_DIR = ROOT / "research_l1" / "out"

# 300s 是当前线上值。向两侧各扫两档，判断"更短是否更好 / 更长是否更好"。
CANDIDATES = [120.0, 300.0, 600.0, 900.0, 1800.0, 3600.0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=4.0, help="回放窗口（小时）")
    ap.add_argument("--symbols", default="", help="逗号分隔；默认取注册表宇宙")
    ap.add_argument("--equity", type=float, default=300.0)
    ap.add_argument("--dry-run", action="store_true", help="只打印候选值，不回放")
    args = ap.parse_args()

    from backend.services import lane_registry as reg

    lane = reg.get_lane(LANE)
    if not lane:
        print(f"车道不存在: {LANE}")
        return 1
    meta = dict(lane.get("meta") or {})
    p_live = dict(meta.get("params") or {})
    symbols = [s.strip().upper() for s in (args.symbols.split(",") if args.symbols
                                          else (meta.get("symbols") or [])) if s.strip()]
    print(f"lane={LANE}  窗口={args.hours}h  宇宙={len(symbols)} 币: {' '.join(symbols)}")
    print(f"线上 max_one_side_seconds = {p_live.get('max_one_side_seconds')}")
    print(f"候选: {CANDIDATES}")

    from backend.services.market_maker import replay as rp
    from backend.services.market_maker.core import LaneRiskLimits, QuoteParams

    def _typed(cls, src: dict) -> dict:
        """只保留目标 dataclass 认识的键（注册表里混着两套参数）。"""
        return {k: v for k, v in src.items() if k in cls.__dataclass_fields__}

    qp = QuoteParams(**_typed(QuoteParams, p_live))
    lim_base = _typed(LaneRiskLimits, p_live)
    # 腿量：线上是 权益 × compound_ratio。compound_ratio 不在 LaneRiskLimits 里，
    # 单独取；取不到则退回 $30（= 300 × 0.1，与现场一致）。
    leg = args.equity * float(p_live.get("compound_ratio") or 0.1)
    print(f"腿量 fill_notional = ${leg:.2f}  (权益 ${args.equity:.0f} × "
          f"compound_ratio {p_live.get('compound_ratio')})")
    print(f"线上 limits: {json.dumps(lim_base, ensure_ascii=False, default=str)}")

    if args.dry_run:
        return 0

    # 序列只加载一次（每个币 4h 盘口），6 个候选复用 ⇒ 省 5/6 的读库时间
    print("\n[1] 加载快照序列（一次，全部候选复用）…")
    series = {}
    for s in symbols:
        try:
            ser = rp._load_series(s, rp.DEFAULT_VENUE, None)
            n = len(ser[0]) if ser else 0
            series[s] = ser
            print(f"    {s:<10} snapshots={n}")
        except Exception as e:
            print(f"    {s:<10} 失败: {e}")

    # 截断到最近 --hours 小时。
    #
    # ⚠️ `_load_series` 返回的 8 个数组**长度不同**：
    #     前 3 个 (ots, bb, ba)  = 盘口快照，与 ots 等长
    #     后 5 个 (tts, lo, hi, sv, bv) = 区间成交桶，**自成一套时间轴**
    #     （实测 XRP：盘口 57152 行 vs 成交 23703 行 ⇒ 长度必然不等）
    #   早先版本对 8 个数组套同一个布尔掩码 ⇒ IndexError（boolean index did not
    #   match … size of axis is 23703 but size of corresponding boolean axis is 20525）。
    #   正确做法：两组各按**自己的时间轴**切。
    import numpy as np

    print(f"\n[2] 截断到最近 {args.hours}h …")
    cut_ms = None
    for s, ser in list(series.items()):
        if not ser or len(ser[0]) < 2:
            del series[s]
            print(f"    {s:<10} 无数据 → 剔除")
            continue
        ots, tts = ser[0], ser[3]
        if cut_ms is None:
            # 以**盘口**最新时刻为准（回放主循环走盘口快照）
            cut_ms = int(ots[-1]) - int(args.hours * 3600_000)
        m_ob = ots >= cut_ms
        m_tr = tts >= cut_ms
        keep = int(m_ob.sum())
        if keep < 100:
            print(f"    {s:<10} 截断后仅 {keep} 快照 → 剔除")
            del series[s]
            continue
        ob_part = tuple(a[m_ob] for a in ser[:3])
        tr_part = tuple(a[m_tr] for a in ser[3:])
        series[s] = ob_part + tr_part
        print(f"    {s:<10} 盘口 {keep} 快照 / 成交 {int(m_tr.sum())} 桶")

    if not series:
        print("没有可用序列，退出")
        return 1

    print(f"\n[3] 扫描 max_one_side_seconds（{len(series)} 币 × {len(CANDIDATES)} 档）…")
    results = []
    for v in CANDIDATES:
        lim = LaneRiskLimits(**{**lim_base, "max_one_side_seconds": float(v)})
        agg = {"fills": 0, "flattens": 0, "notional": 0.0, "net_usd": 0.0,
               "spread_usd": 0.0, "price_usd": 0.0, "fee_usd": 0.0,
               "flatten_usd": 0.0, "snapshots": 0}
        for s, ser in series.items():
            try:
                r = rp.replay_symbol(s, venue=rp.DEFAULT_VENUE, equity=args.equity,
                                     params=qp, limits=lim, series=ser,
                                     fill_notional=leg)
            except Exception as e:
                print(f"      {v:>7.0f}s {s:<10} 回放失败: {e}")
                continue
            agg["fills"] += int(r.fills)
            agg["flattens"] += int(r.flattens)
            agg["notional"] += float(r.notional)
            agg["net_usd"] += float(r.net_usd)
            agg["spread_usd"] += float(r.spread_usd)
            agg["price_usd"] += float(r.price_usd)
            agg["fee_usd"] += float(r.fee_usd)
            agg["flatten_usd"] += float(getattr(r, "flatten_usd", 0.0) or 0.0)
            agg["snapshots"] += int(r.snapshots)
        n = agg["notional"]
        row = {
            "max_one_side_seconds": v,
            "fills": agg["fills"],
            "flattens": agg["flattens"],
            "net_usd": round(agg["net_usd"], 4),
            "net_bp": round(agg["net_usd"] / n * 1e4, 3) if n > 0 else None,
            "spread_bp": round(agg["spread_usd"] / n * 1e4, 3) if n > 0 else None,
            "price_bp": round(agg["price_usd"] / n * 1e4, 3) if n > 0 else None,
            "fee_bp": round(agg["fee_usd"] / n * 1e4, 3) if n > 0 else None,
            "flatten_usd": round(agg["flatten_usd"], 4),
            "notional": round(n, 2),
            "snapshots": agg["snapshots"],
        }
        results.append(row)
        print("    %7.0fs  成交%5d  强平%4d  净%+9.4f$ (%+7.3fbp)  价差%+7.3f 逆选择%+8.3f 费%+6.3f"
              % (v, row["fills"], row["flattens"], row["net_usd"], row["net_bp"] or 0.0,
                 row["spread_bp"] or 0.0, row["price_bp"] or 0.0, row["fee_bp"] or 0.0))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "h17_max_one_side_sweep.json"
    out.write_text(json.dumps({
        "lane": LANE, "hours": args.hours, "symbols": list(series.keys()),
        "equity": args.equity, "fill_notional": leg,
        "live_params": p_live, "results": results,
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n已写入 {out}")

    # 结论（只做同窗口相对比较，绝不跨窗口求和）
    if len(results) >= 2:
        best = max(results, key=lambda r: (r["net_bp"] if r["net_bp"] is not None else -1e9))
        cur = next((r for r in results if r["max_one_side_seconds"] == 300.0), None)
        print("\n── 同窗口结论 ──")
        print("    最优档: %.0fs  净 %+.3fbp  成交 %d  强平 %d"
              % (best["max_one_side_seconds"], best["net_bp"] or 0.0, best["fills"], best["flattens"]))
        if cur:
            print("    当前档: 300s      净 %+.3fbp  成交 %d  强平 %d"
                  % (cur["net_bp"] or 0.0, cur["fills"], cur["flattens"]))
            d = (best["net_bp"] or 0.0) - (cur["net_bp"] or 0.0)
            print("    差值  : %+.3fbp（%s）" % (d, "改更优" if d > 0 else "当前已最优/更优"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
