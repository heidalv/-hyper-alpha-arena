# -*- coding: utf-8 -*-
"""H196 参数敏感性扫描（用回放引擎，不是账本切片）。

# 为什么必须用回放而不是账本

账本只记录**已实现**结果，改不了参数重跑。而"这个参数值是不是过拟合"
本质上是一个**反事实**问题：换个值会怎样？

用户质疑"是不是有点过拟合了"，H195 审计已给出三条支持证据：
  · 我最后那次改动（stop_maker_grace_sec 30→0）在**周期级**只有 2 笔样本，
    A/B 的 95%CI 完全重叠 ⇒ 无法断定更好（我之前用腿级数据过度解读了）
  · "自由度"超标：37 个强平样本上调了 ≥10 个参数（经验门槛只够 ~3.7 个）
  · 持仓周期**时间严重重叠**（同一秒开的仓，有的 2h 出、有的 13h 出）
    ⇒ 68 个周期**不是 68 个独立观测**

# 本脚本的判据

真正稳健的参数值：**在它附近（±20%）表现应该差不多**（平坦高原）。
过拟合的参数值：**稍微动一下性能就明显变差**（尖锐最优点）。

⇒ 输出一条"参数 → 净额"的曲线，看它的形状。

# 用法

    python scripts/h196_param_sensitivity.py --key stop_loss_bp --values 10,15,20,25,30,40,60
    python scripts/h196_param_sensitivity.py --key take_profit_maker_grace_sec --values 0,30,60,120,240
    python scripts/h196_param_sensitivity.py --key spread_mult --values 0.3,0.4,0.5,0.7,1.0
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_SYMS = ["ASTER", "XRP", "SOL"]
LANE = "mm_asterdex"


def _live_params_from_registry() -> dict:
    """读实盘注册表的 `meta.params`，让回放与实盘同口径。

    必须这么做：手抄参数会在下一次改参后静默过期，
    而**静默过期正是本项目反复发作的那类事故**（F189/F280/F287/F292）。
    """
    import json
    import psycopg
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    with psycopg.connect(url) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'params' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            r = cur.fetchone()
    return dict(r[0] or {}) if r else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True, help="要扫的参数名（params 或 limits 里的字段）")
    ap.add_argument("--values", required=True, help="逗号分隔的取值")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMS))
    ap.add_argument("--days", type=float, default=14.0, help="回放窗口（天）")
    ap.add_argument("--equity", type=float, default=300.0)
    a = ap.parse_args()

    from backend.services.market_maker.core import LaneRiskLimits, QuoteParams
    from backend.services.market_maker import replay as RP

    vals = []
    for x in a.values.split(","):
        x = x.strip()
        if not x:
            continue
        vals.append(float(x) if ("." in x or "e" in x.lower()) else int(x))

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    key = a.key
    in_params = key in QuoteParams.__dataclass_fields__
    in_limits = key in LaneRiskLimits.__dataclass_fields__
    if not (in_params or in_limits):
        print(f"  ✗ {key} 不在 QuoteParams 也不在 LaneRiskLimits 里")
        return 1

    print("=" * 100)
    print(f"H196  参数敏感性扫描：{key}")
    print("=" * 100)
    print(f"  标的 {syms}   窗口 {a.days:.0f} 天   权益 ${a.equity:.0f}")
    print(f"  字段位置：{'QuoteParams' if in_params else 'LaneRiskLimits'}")
    print(f"  取值：{vals}")
    print("  判据：**平坦 = 稳健；尖锐 = 过拟合**")

    # ── 基础配置：**必须与实盘同口径**，否则回放被闸门全挡（本脚本首版踩过）──
    #
    # 首版失败现场：全部取值为 0 fills、`skipped={'symbol_exposure': 26097}`。
    # 原因：`replay_symbol` 的 `fill_notional` 默认取模块常量 **$100**，
    # 而 `LaneRiskLimits()` 的默认 `max_net_directional_ratio` 只有 **0.1**
    # ⇒ 单币上限 = 0.1 × $300 = **$30** < $100 ⇒ 每一侧都被敞口闸拒绝 ✗。
    #
    # 实盘口径（runner.py:2606）是 `fill_notional = compound_ratio × equity`
    # = 1.0 × $300 = **$300**，配 `max_net_directional_ratio = 2.0`（上限 $600）。
    # ⇒ 回放要复现实盘，这两项都必须显式给对，不能吃默认值。
    try:
        live = _live_params_from_registry()
    except Exception as e:
        print(f"  ⚠️ 读注册表失败（{type(e).__name__}: {str(e)[:60]}）⇒ 用实盘已知值兜底")
        live = {}
    fill_notional = float(a.equity) * float(live.get("compound_ratio", 1.0) or 1.0)
    print(f"  单腿名义 fill_notional = ${fill_notional:.2f}（= equity × compound_ratio，与实盘同口径）")

    base_p = QuoteParams(**{k: v for k, v in live.items()
                            if k in QuoteParams.__dataclass_fields__})
    base_l = LaneRiskLimits(**{k: v for k, v in live.items()
                               if k in LaneRiskLimits.__dataclass_fields__})
    # 回放的快照间隔约 14s（见 replay.replay_symbol 的注释）：
    # 超时阈值必须按快照数折算，否则 14 天历史只够 ~4.2 快照/分钟，库存会长期挂账。
    base_l = LaneRiskLimits(**{**{k: getattr(base_l, k)
                                 for k in LaneRiskLimits.__dataclass_fields__},
                               "max_one_side_seconds": RP.MAX_ONE_SIDE_SNAPSHOTS * 14.0})
    print(f"  限额：max_net_directional_ratio={base_l.max_net_directional_ratio} "
          f"（单币上限 ${base_l.max_net_directional_ratio*fill_notional:.0f}）"
          f"  max_one_side_seconds={base_l.max_one_side_seconds}")

    print(f"\n  预热：加载 {len(syms)} 个标的的序列（一次，之后复用）…")
    t0 = time.time()
    cache = {}
    for s in syms:
        try:
            cache[s] = RP._load_series(s, RP.DEFAULT_VENUE, None)
        except Exception as e:
            print(f"    {s}: 加载失败 {type(e).__name__}: {str(e)[:70]}")
    print(f"  加载完成 {time.time()-t0:.1f}s；可用 {list(cache)}")
    if not cache:
        return 1

    print(f"\n  {'值':>12}{'fills':>9}{'flattens':>10}{'net_bp':>10}{'spread_bp':>11}"
          f"{'price_bp':>10}{'fee_bp':>9}{'net_usd':>10}")
    print("  " + "-" * 82)

    curve = []
    for v in vals:
        pd_kw = {k: getattr(base_p, k) for k in QuoteParams.__dataclass_fields__}
        ld_kw = {k: getattr(base_l, k) for k in LaneRiskLimits.__dataclass_fields__}
        if in_params:
            pd_kw[key] = v
        else:
            ld_kw[key] = v
        p = QuoteParams(**pd_kw)
        l = LaneRiskLimits(**ld_kw)

        tot_notional = 0.0
        tot_net = tot_spread = tot_price = tot_fee = 0.0
        tot_fills = tot_fl = 0
        ok = True
        for s, ser in cache.items():
            try:
                r = RP.replay_symbol(s, params=p, limits=l, equity=a.equity,
                                     series=ser, record=False,
                                     fill_notional=fill_notional)
            except Exception as e:
                import traceback
                print(f"    {s} @ {key}={v} 失败: {type(e).__name__}: {str(e)[:100]}")
                traceback.print_exc()
                ok = False
                continue
            tot_notional += r.notional
            tot_net += r.net_usd
            tot_spread += r.spread_usd
            tot_price += r.price_usd
            tot_fee += r.fee_usd
            tot_fills += r.fills
            tot_fl += r.flattens
        if not ok or tot_notional <= 0:
            continue
        # 自证：全被闸门挡掉时（fills=0）必须报出来。
        # 首版就是这样静默失败：10 个取值全部 0 成交，输出只有一行"全部失败"，
        # 看不出是 `symbol_exposure` 挡的 ⇒ 排查多花了一轮。
        if tot_fills == 0 and tot_fl == 0:
            sk = {}
            for s, ser in cache.items():
                try:
                    _r = RP.replay_symbol(s, params=p, limits=l, equity=a.equity,
                                          series=ser, record=False,
                                          fill_notional=fill_notional)
                    for k2, v2 in (_r.skipped or {}).items():
                        sk[k2] = sk.get(k2, 0) + int(v2)
                except Exception:
                    pass
            print(f"  {v:>12}{0:>9}{0:>10}{'--':>10}{'--':>11}{'--':>10}{'--':>9}{'--':>10}"
                  f"   ← **0 成交**，闸门分布 {sk}")
            continue

        def bp(x):
            return round(x / tot_notional * 1e4, 4)
        row = {"value": v, "fills": tot_fills, "flattens": tot_fl,
               "net_bp": bp(tot_net), "spread_bp": bp(tot_spread),
               "price_bp": bp(tot_price), "fee_bp": bp(tot_fee),
               "net_usd": round(tot_net, 4)}
        curve.append(row)
        print(f"  {v:>12}{tot_fills:>9}{tot_fl:>10}{row['net_bp']:>10.4f}"
              f"{row['spread_bp']:>11.4f}{row['price_bp']:>10.4f}"
              f"{row['fee_bp']:>9.4f}{row['net_usd']:>10.3f}")

    if not curve:
        print("  ✗ 全部失败")
        return 1

    # ── 形状判读 ──
    print(f"\n  ── 曲线形状判读 ──")
    nets = [r["net_bp"] for r in curve]
    best_i = max(range(len(curve)), key=lambda i: nets[i])
    best = curve[best_i]
    lo, hi = min(nets), max(nets)
    spread = hi - lo
    print(f"    最优值 = {best['value']}  net_bp = {best['net_bp']:+.4f}")
    print(f"    全区间 net_bp ∈ [{lo:+.4f}, {hi:+.4f}]   极差 = {spread:.4f} bp")
    if abs(best["net_bp"]) > 0:
        print(f"    极差 / |最优| = {abs(spread/best['net_bp']):.2f}")
    # 平坦度：最优值相邻两点的落差
    for d in (1, 2):
        for j in (best_i - d, best_i + d):
            if 0 <= j < len(curve):
                drop = best["net_bp"] - curve[j]["net_bp"]
                print(f"    距最优 {d} 档（{curve[j]['value']}）落差 = {drop:+.4f} bp")
    print("\n    判读：")
    print("      · 若极差远小于 |最优|，且相邻档落差很小 ⇒ **平坦高原** ⇒ 该参数不敏感、不像过拟合")
    print("      · 若只有最优点好、旁边明显差 ⇒ **尖锐峰** ⇒ 那个值很可能是拟合出来的噪声")

    out = ROOT / "research_l1" / "out" / f"h196_sens_{key}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    import json
    out.write_text(json.dumps({"key": key, "symbols": syms, "curve": curve},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
