"""清洁窗口参数扫描（研究用，**不落地**任何参数）。

[F181 2026-09-15] 为什么需要这个脚本
--------------------------------------------------
数据修复（F171，08:00 部署）之后，「模型绝对水平 vs 实盘」的差从 ~2.5bp 收敛到
0.82bp（不显著，3σ=4.31bp）⇒ 模型与实盘**同号同量级**（都是负的 −1.07 vs −1.89bp）。
这同时意味着：

  前 3 轮坐标下降（w_base→4.0 / k_inv→0.5 / min_width_reduce→2.0 / frozen_lookback→120）
  是在**被高估约 2.5bp 的口径**上做排序的 ✗ —— 排序本身可能仍然有效（bias 相近时排名
  不变），但"绝对水平为正"的前提被推翻，必须用干净数据重新验证。

`run_evolution_round` 不能直接用于这个目的：它的窗口按 `window_days` 从**当前时刻**回溯
（默认 14 天），而干净数据只有几小时；且它一轮只评估"当前配置 ± 单维一格"。本研究脚本：

  · 把窗口**钉死在 `--since`**（默认今日 08:00，即 F171 部署时刻）；
  · 复用进化链路**完全相同**的评分路径（QuoteParams/LaneRiskLimits + 锚定 vol_baseline +
    enforce_lane_limits + 复利腿量 + tick_delay_ms）；
  · 允许任意参数组合的并列比较（`--grid w_base_bp=4,6,8,12`），含在位配置作基准；
  · 默认跑**多口径**（滞后网格 18.2/25.1/31.8s，见 evolution.ROBUST_DELAYS_MS），
    并要求"所有口径都不劣"才算胜出（F116 的教训：单口径的改进常是口径噪声）。

用法
----
    python scripts/mm_clean_sweep.py --since 2026-09-15T08:00:00+08:00 \
        --grid w_base_bp=4,6,8,12,16,24

    # 多维笛卡尔积（自动设上限）
    python scripts/mm_clean_sweep.py --grid w_base_bp=4,8 --k_vol=0,0.3,0.6 --max-combos 12
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from backend.services import lane_registry as reg  # noqa: E402
from backend.services.market_maker import evolution as evo  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.portfolio_replay import _load_all, replay_portfolio  # noqa: E402

LANE = os.getenv("MM_SWEEP_LANE", "mm_asterdex")


def parse_since(s: Optional[str]) -> int:
    if not s:
        # 默认：今天 08:00 (+08:00) = 00:00 UTC
        now = datetime.now(timezone.utc)
        return int(datetime(now.year, now.month, now.day, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def parse_grid(specs: List[str]) -> Dict[str, List[Any]]:
    """`--grid k=v1,v2` / `--grid k=4` → {k: [v,...]}，值按 JSON 解析（保 int/float/bool）。"""
    grid: Dict[str, List[Any]] = {}
    for spec in specs or []:
        for part in spec.split(";"):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                raise SystemExit(f"参数网格格式应为 k=v1,v2：{part!r}")
            k, vs = part.split("=", 1)
            vals: List[Any] = []
            for v in vs.split(","):
                v = v.strip()
                try:
                    vals.append(json.loads(v))
                except Exception:
                    vals.append(v)
            grid[k.strip()] = vals
    return grid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default=LANE)
    ap.add_argument("--since", default=None,
                    help="窗口起点（ISO，含时区）；默认今天 00:00 UTC（=北京 08:00）")
    ap.add_argument("--equity", type=float, default=300.0)
    ap.add_argument("--grid", action="append", default=[],
                    help="k=v1,v2[;k2=v3,v4]，可重复")
    ap.add_argument("--max-combos", type=int, default=16)
    ap.add_argument("--base", default="",
                    help="覆盖基准配置（k=v,k2=v2），叠在车道现值之上。用来把单维扫描的"
                         "起点挪到**已验证更优**的配置上（否则只能从在位配置单维变动，"
                         "而 '基线本身是负的' 时单维扫描会漏掉交互项）。")
    ap.add_argument("--delays", default="",
                    help="逗号分隔的 tick_delay_ms；留空=ROBUST_DELAYS_MS（多口径）")
    ap.add_argument("--single-delay", type=float, default=evo.DEFAULT_TICK_DELAY_MS,
                    help="--delays 为空且 --no-robust 时使用的单一滞后")
    ap.add_argument("--no-robust", action="store_true", help="只跑 --single-delay 一个口径")
    ap.add_argument("--leg-ratio", default="",
                    help="腿量/权益 组合（逗号分隔）。[F185] 实盘腿量 $276 ≈ 1 腿上限"
                         "（max_net_directional_ratio=1.0 × 权益 $300）⇒ 一笔成交就顶到单币"
                         "方向上限 ⇒ 只剩减仓侧可挂 ⇒ 单侧报价 71%%。缩小腿量是解开它的"
                         "**唯一不影响风险上限**的方向，故单独做成一个维度。")
    ap.add_argument("--json-out", default="logs/mm_clean_sweep.json")
    ap.add_argument("--until", default="",
                    help="窗口终点（ISO，含时区）。[F207] **同窗口对照是硬要求**："
                         "本会话已三次因为拿「不同窗口/不同工况」的数字对比而得出错误结论 ✗"
                         "（F196 挂宽、F206 成交规模）。要比实盘，就必须把窗口钉成实盘那一段 ✓。"
                         "留空=到现在。")
    args = ap.parse_args()

    since_ms = parse_since(args.since)
    since_txt = datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc).astimezone().isoformat()

    if args.delays:
        delays = [float(x) for x in args.delays.split(",") if x.strip()]
    elif args.no_robust:
        delays = [float(args.single_delay)]
    else:
        delays = list(evo.ROBUST_DELAYS_MS)

    lane = reg.get_lane(args.lane)
    if not lane:
        print(f"车道不存在: {args.lane}")
        return 2
    meta = dict(lane.get("meta") or {})
    symbols = list(meta.get("symbols") or ["BTC"])
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    if args.base.strip():
        for part in args.base.split(","):
            part = part.strip()
            if not part:
                continue
            k, _, v = part.partition("=")
            try:
                cur[k.strip()] = json.loads(v.strip())
            except Exception:
                cur[k.strip()] = v.strip()
        print(f"[基准覆盖] {args.base.strip()}")
    fn = float(cur.get("fill_notional") or args.equity)
    compound = float(cur.get("compound_ratio") or 0.0)
    anchored_vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})

    print(f"车道 {args.lane}  标的 {symbols}  场馆 {venue}")
    print(f"窗口起点 {since_txt} ({since_ms})  权益 ${args.equity:.0f}  "
          f"腿量 ${fn:.2f}  复利比 {compound}  口径 {[f'{d/1000:.1f}s' for d in delays]}")
    print(f"在位参数 " + " ".join(f"{k}={cur.get(k)}" for k in sorted(evo.GRID)))
    print(f"锚定 vol_baseline {anchored_vb or '(无)'}")

    t0 = time.time()
    data = _load_all(symbols, venue)
    print(f"[数据] 载入 {len(symbols)} 标的耗时 {time.time()-t0:.1f}s")

    sub: Dict[str, Dict[str, np.ndarray]] = {}
    # [F207] 支持 --until：把窗口钉成"实盘那一段"，否则任何实盘/模型对照都是错配 ✗
    until_ms = None
    if args.until.strip():
        _u = datetime.fromisoformat(args.until.strip())
        if _u.tzinfo is None:
            _u = _u.replace(tzinfo=timezone.utc)
        until_ms = int(_u.timestamp() * 1000)
    for s in symbols:
        d = data[s]
        a2 = int(np.searchsorted(d["ots"], since_ms, "left"))
        t2 = int(np.searchsorted(d["tts"], since_ms, "left"))
        a3 = int(np.searchsorted(d["ots"], until_ms, "left")) if until_ms else None
        t3 = int(np.searchsorted(d["tts"], until_ms, "left")) if until_ms else None
        sub[s] = {k: d[k][a2:(a3 if a3 is not None else len(d[k]))] for k in ("ots", "bb", "ba")}
        for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
            sub[s][k] = d[k][t2:(t3 if t3 is not None else len(d[k]))]
    if until_ms:
        print(f"窗口终点 {args.until.strip()} ({until_ms}) —— 与实盘同窗口 ✓")
    n_ob = {s: len(sub[s]["ots"]) for s in symbols}
    n_tr = {s: len(sub[s]["tts"]) for s in symbols}
    span_h = 0.0
    if n_tr and sub[symbols[0]]["tts"] is not None and len(sub[symbols[0]]["tts"]):
        allt = np.concatenate([sub[s]["tts"] for s in symbols if len(sub[s]["tts"])])
        if len(allt):
            span_h = float(allt.max() - allt.min()) / 3.6e6
    print(f"[数据] 快照 {sum(n_ob.values())}  成交 {sum(n_tr.values())}  跨度 {span_h:.2f}h  "
          f"每标的成交 {n_tr}")
    if sum(n_tr.values()) < 200:
        print("⚠ 成交样本过少（<200），结论仅供方向参考")

    # 候选集：在位 + 网格笛卡尔积（把在位值并入每维候选，保证是同一"从在位出发"的比较）
    #
    # [F181] 允许**任意** QuoteParams / LaneRiskLimits 字段，而不只是 evo.GRID：
    # 进化网格里没有 `max_net_exposure_ratio` / `max_net_directional_ratio` 这类**风险上限**
    # 维度（它们是 LaneRiskLimits ✓），而干净数据下 dd 8.55%、4.8h 亏 $25 的表现，
    # 首要嫌疑正是"6× 权益的净敞口（$1800 / $300 账户）当方向性赌注"✗ ⇒ 必须能直接扫。
    _allowed = set(QuoteParams.__dataclass_fields__) | set(LaneRiskLimits.__dataclass_fields__)
    _req = parse_grid(args.grid)
    keys = sorted(k for k in _req if k in _allowed)
    unknown = [k for k in _req if k not in _allowed]
    if unknown:
        print(f"⚠ 忽略未知维度（不在 QuoteParams/LaneRiskLimits）: {unknown}")
    gvals = parse_grid(args.grid)
    combos: List[Dict[str, Any]] = [dict(cur)]
    if args.leg_ratio.strip():
        lr_vals = [float(x) for x in args.leg_ratio.split(",") if x.strip()]
        if compound not in lr_vals:
            lr_vals = [compound] + lr_vals
        axes = [[("_leg_ratio", v) for v in lr_vals]]
    else:
        axes = []
    for k in keys:
        vals = list(gvals[k])
        if cur.get(k) not in vals:
            vals = [cur.get(k)] + vals
        axes.append([(k, v) for v in vals])
    if axes:
        chg_keys = list(keys) + (["_leg_ratio"] if args.leg_ratio.strip() else [])
        for combo in itertools.product(*axes):
            p = dict(cur)
            for k, v in combo:
                p[k] = v
            if any(p.get(k) != cur.get(k) for k in chg_keys):
                combos.append(p)
    # 去重
    seen, uniq = set(), []
    for p in combos:
        key = json.dumps({k: p.get(k) for k in sorted(p)}, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    if len(uniq) > int(args.max_combos):
        print(f"⚠ 组合数 {len(uniq)} 超上限 {args.max_combos}，截断（在位配置始终保留）")
        uniq = uniq[: int(args.max_combos)]
    combos = uniq
    print(f"[扫描] {len(combos)} 个组合 × {len(delays)} 个口径 = {len(combos)*len(delays)} 次回放\n")

    rows: List[Dict[str, Any]] = []
    for i, p in enumerate(combos):
        qp = QuoteParams(**{k: v for k, v in p.items() if k in QuoteParams.__dataclass_fields__})
        lim = LaneRiskLimits(**{k: v for k, v in p.items() if k in LaneRiskLimits.__dataclass_fields__})
        per: List[Dict[str, Any]] = []
        _leg = float(p.get("_leg_ratio") or compound or 0.0)
        for d in delays:
            t1 = time.time()
            r = replay_portfolio(symbols, venue=venue, equity=args.equity, params=qp, limits=lim,
                                 fill_notional=fn, data=sub, vol_baseline=(anchored_vb or None),
                                 enforce_lane_limits=True, tick_delay_ms=float(d),
                                 fill_notional_ratio=(_leg if _leg > 0 else None))
            per.append({"delay_ms": d, "fills": r.get("fills"), "net_bp": r.get("net_bp"),
                        "net_usd": r.get("net_usd"), "max_dd_pct": r.get("max_dd_pct"),
                        "flatten_share": r.get("flatten_share"), "sec": round(time.time() - t1, 1),
                        # [F186] 分类诊断：被动腿（maker）与主动平仓腿（flatten）分开记账 ⇒
                        # 能直接回答"亏损来自被动被逆向选择，还是来自主动平仓的滑点"✗✓
                        "maker_bp": r.get("maker_net_bp"), "flatten_bp": r.get("flatten_net_bp"),
                        "flattens": r.get("flattens"), "notional": r.get("notional"),
                        "pos_sym": r.get("positive_symbols"),
                        # [F205b] 报价分支：与实盘 /shadow 同名字段 ⇒ 可直接对照
                        # （F189 的失败正是两边对"同一参数挂多宽"的认知不一致 ✗）
                        "frozen_share": r.get("frozen_share"),
                        "avg_base_bp": r.get("avg_base_bp"),
                        "modes": r.get("quote_modes"),
                        # [F205b] **最终挂宽**（偏斜/地板之后）——与实盘 `avg_width_bp`
                        # 才是同一量；`avg_base_bp` 是偏斜前的基准，两者不可混比 ✗
                        "avg_width_bp": r.get("avg_width_bp"),
                        # [F206] 报价侧分布：实盘 13:12 实测 one/(both+one)=70% 单边 ✗，
                        # 而单边 ⇒ 只挂**减仓侧** ⇒ 被 k_inv 腰斩 ⇒ 均值远低于"两侧都挂" ✗
                        # ⇒ 解释"实盘比模型窄 1.6×"必须比这个分布，不能只比均值 ✓
                        "side_counts": r.get("side_counts"),
                        "skip_counts": r.get("skip_counts")})
        diff = {k: p[k] for k in sorted(p) if cur.get(k) != p[k]}
        rows.append({"params": {k: p[k] for k in sorted(p)}, "diff": diff, "per": per})
        tag = ",".join(f"{k}={v}" for k, v in diff.items()) or "(在位)"
        line = f"[{i+1}/{len(combos)}] {tag:<46}"
        for e in per:
            line += (f" | {e['delay_ms']/1000:.1f}s {e['fills']:>4}f "
                     f"{float(e['net_bp'] or 0):+.3f}bp ${float(e['net_usd'] or 0):+7.2f}"
                     f" dd{float(e['max_dd_pct'] or 0):.2f}%")
        print(line, flush=True)

    # 汇总：以每个口径的"在位"为基准算 Δ
    base = rows[0]
    print("\n" + "=" * 118)
    print("汇总（Δ 相对在位；'全口径不劣'= 每个滞后下 USD 都不低于在位的 98%）")
    print("=" * 118)
    print(f"{'候选':<40}" + "".join(f"{'ΔUSD@' + f'{d/1000:.1f}s':>11}" for d in delays)
          + f"{'Δbp均值':>10}{'被动腿bp':>10}{'平仓腿bp':>10}{'名义$':>10}{'正币':>5}"
          + f"{'冻结%':>7}{'基准bp':>8}{'挂宽bp':>8}{'判定':>10}")
    summary = []
    for r in rows:
        tag = ",".join(f"{k}={v}" for k, v in r["diff"].items()) or "(在位)"
        dusd = [float(e["net_usd"] or 0) - float(b["net_usd"] or 0)
                for e, b in zip(r["per"], base["per"])]
        dbp = [float(e["net_bp"] or 0) - float(b["net_bp"] or 0)
               for e, b in zip(r["per"], base["per"])]
        not_worse = all(float(e["net_usd"] or 0) >= float(b["net_usd"] or 0) * (1.0 - evo.FULL_TOL)
                        for e, b in zip(r["per"], base["per"]))
        wins = sum(1 for e, b in zip(r["per"], base["per"])
                   if float(e["net_usd"] or 0) > float(b["net_usd"] or 0))
        if r is base:
            verdict = "基准"
        elif not_worse and wins * 2 >= len(delays):
            verdict = "★胜出"
        elif wins * 2 < len(delays):
            verdict = "劣"
        else:
            verdict = "口径不稳"
        mk_vals = [float(e["maker_bp"]) for e in r["per"] if e.get("maker_bp") is not None]
        fl_vals = [float(e["flatten_bp"]) for e in r["per"] if e.get("flatten_bp") is not None]
        mk = sum(mk_vals) / len(mk_vals) if mk_vals else 0.0
        fl = sum(fl_vals) / len(fl_vals) if fl_vals else 0.0
        ntl = [float(e["notional"] or 0) for e in r["per"]]
        ps = [int(e["pos_sym"] or 0) for e in r["per"]]
        # [F205b] 模型侧的报价分支（冻结档占比 / 基准半宽），与实盘 /shadow 对照 ✓
        fz = [float(e["frozen_share"]) for e in r["per"] if e.get("frozen_share") is not None]
        bb = [float(e["avg_base_bp"]) for e in r["per"] if e.get("avg_base_bp") is not None]
        # [F205b] 最终挂宽（买/卖均值）——与实盘 avg_width_bp 同一量 ✓
        wz = [e["avg_width_bp"] or {} for e in r["per"]]
        _wv = [float(v) for w in wz for v in ((w.get("bid"), w.get("ask")) if isinstance(w, dict) else ())
               if v is not None]
        wr = (sum(_wv) / len(_wv)) if _wv else float("nan")
        line = f"{tag:<40}" + "".join(f"{x:>+11.2f}" for x in dusd)
        line += (f"{sum(dbp)/len(dbp):>+10.3f}{mk:>+10.3f}{fl:>+10.3f}"
                 f"{sum(ntl)/len(ntl):>10.0f}{max(ps) if ps else 0:>5}"
                 f"{(100*sum(fz)/len(fz) if fz else float('nan')):>7.1f}"
                 f"{(sum(bb)/len(bb) if bb else float('nan')):>8.2f}{wr:>8.2f}{verdict:>10}")
        print(line)
        summary.append({"params": r["params"], "diff": r["diff"], "per": r["per"],
                        "d_usd": dusd, "d_bp": dbp, "verdict": verdict})

    try:
        os.makedirs(os.path.dirname(args.json_out) or ".", exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"lane": args.lane, "since_ms": since_ms, "since_txt": since_txt,
                       "symbols": symbols, "delays_ms": delays, "incumbent": cur,
                       "n_trades": n_tr, "n_snapshots": n_ob, "span_h": span_h,
                       "rows": summary}, f, ensure_ascii=False, indent=2)
        print(f"\n明细已写入 {args.json_out}")
    except Exception as e:  # pragma: no cover
        print(f"⚠ 写 JSON 失败: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
