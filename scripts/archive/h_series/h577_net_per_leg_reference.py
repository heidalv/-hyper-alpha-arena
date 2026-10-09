"""h577 — ② 的**净/腿**判据：内置基线 vs 同体制参照（只读，R79）。

为什么要做：预演里试跑窗每腿净额一直**差于**内置基线（−1.5 vs −0.78bp），
但内置基线 A（09-28 09:48→21:48）是 **0.15 体制**窗、且其 −0.78bp **明显好于纪元均值**
（≈−1.3bp）⇒ 判据 B（Welch）可能在拿"幸运窗"当基准，把 90s 判死 ✗。

正确做法与 R72 对机制指标做的一样：**只差一个变量的窗才是可比基准**——
`ofi_confirm` 已是 0.5、`max_one_side_seconds` 仍是 45 的窗（N3：09-28 23:17→09-29 09:48）。
本脚本对 A / N3 / 当前试跑窗 并排给出：腿数、每腿净额（均值/中位）、止损腿数与贡献、
以及 **Welch 检验（试跑 vs A、试跑 vs N3）**。

用法：python scripts/h577_net_per_leg_reference.py
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
import statistics as st
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

TZ = dt.timezone(dt.timedelta(hours=8))


def _iso(y, mo, d, hh, mm=0) -> str:
    return dt.datetime(y, mo, d, hh, mm, tzinfo=TZ).astimezone(dt.timezone.utc).isoformat()


def _stops(cur, since, until, syms) -> tuple:
    cur.execute(
        "SELECT count(*), COALESCE(sum(net_bp),0)::float8,"
        " COALESCE(sum(net_bp*notional/1e4),0)::float8"
        " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
        " AND ts <= %s::timestamptz AND symbol = ANY(%s)"
        " AND meta_json->>'exit_path' LIKE 'stop_loss%%'",
        (h.LANE, since, until, syms))
    r = cur.fetchone()
    return int(r[0] or 0), float(r[1] or 0.0), float(r[2] or 0.0)


def main() -> int:
    out: dict = {}
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
        trial = dict(meta.get("h463_trial") or {})
        since = trial.get("started_at")
        t0 = dt.datetime.fromisoformat(since)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        wins = {
            "A_内置基线(0.15体制,45s)": ((t0 - dt.timedelta(hours=24)).isoformat(),
                                        (t0 - dt.timedelta(hours=12)).isoformat()),
            "N3_同体制参照(0.5体制,45s)": (_iso(2026, 9, 28, 23, 17), since),
            "T_试跑窗(0.5体制,90s,进行中)": (since, now),
        }
        legs = {}
        print("=" * 104)
        print(f"{'窗':<32}{'腿':>6}{'均值bp/腿':>11}{'中位':>8}"
              f"{'止损腿':>7}{'止损贡献bp/腿':>14}")
        for name, (a, b) in wins.items():
            arr = h._per_leg(cur, a, b, syms)
            legs[name] = {"window": [a, b], "n": len(arr),
                          "mean_bp": round(st.mean(arr), 3) if arr else None,
                          "median_bp": round(st.median(arr), 3) if arr else None}
            sl, ssum, susd = _stops(cur, a, b, syms)
            contrib = (ssum / len(arr)) if arr else 0.0
            legs[name].update({"stop_legs": sl, "stop_net_bp_sum": round(ssum, 2),
                               "stop_usd": round(susd, 3),
                               "stop_contrib_bp_per_leg": round(contrib, 3)})
            print(f"{name:<32}{len(arr):>6}"
                  f"{(f'{st.mean(arr):.3f}' if arr else '-'):>11}"
                  f"{(f'{st.median(arr):.3f}' if arr else '-'):>8}"
                  f"{sl:>7}{contrib:>14.3f}")
        out["windows"] = legs
        # [R79] **止损率标准化**：均值被尾部事件主导，而两窗的止损腿占比可差 1.8 倍
        # （A 3.3% vs N3 6.0%，纪元均值 ~4.2%）⇒ 直接比均值等于比"谁的尾部运气好"。
        # 标准化 = 把试跑窗的止损率**重估到参照窗的止损率**：
        #     μ_norm(r_ref) = m_非止损×(1−r_ref) + m_止损×r_ref
        # 这是标准的率标准化（类似年龄标准化），不是挑窗口 ⇒ 可作为补充读数。
        norm = {}
        for name in wins:
            a, b = wins[name]
            arr = h._per_leg(cur, a, b, syms)
            if not arr:
                continue
            cur.execute(
                "SELECT net_bp FROM lane_ledger WHERE lane_id=%s"
                " AND ts > %s::timestamptz AND ts <= %s::timestamptz"
                " AND symbol = ANY(%s) AND meta_json->>'exit_path' LIKE 'stop_loss%%'",
                (h.LANE, a, b, syms))
            sp = [float(r[0]) for r in cur.fetchall()]
            ns = [x for x in arr if x not in sp] or arr
            r_rate = len(sp) / len(arr)
            norm[name] = {
                "stop_rate": round(r_rate, 4),
                "mean_stop_bp": round(st.mean(sp), 3) if sp else None,
                "mean_nonstop_bp": round(st.mean(ns), 3),
                "mean_all_bp": round(st.mean(arr), 3)}
        out["stop_rate_norm"] = norm
        print("\n[止损率标准化] 各窗的止损率、止损/非止损均值：")
        for name, d in norm.items():
            print(f"  {name:<32} 止损率={d['stop_rate']:.2%} "
                  f"止损均值={d['mean_stop_bp']} 非止损均值={d['mean_nonstop_bp']}")
        _r_ref = None
        _t = norm.get("T_试跑窗(0.5体制,90s,进行中)")
        for ref in ("A_内置基线(0.15体制,45s)", "N3_同体制参照(0.5体制,45s)"):
            _r = norm.get(ref) or {}
            if _t and _r.get("stop_rate") is not None and _t.get("mean_stop_bp") is not None:
                _mu = (_t["mean_nonstop_bp"] * (1 - _r["stop_rate"])
                       + _t["mean_stop_bp"] * _r["stop_rate"])
                _d = _mu - _r["mean_all_bp"]
                out[f"normalized_delta_vs_{ref}"] = round(_d, 3)
                print(f"  ⇒ 把试跑窗止损率标准化到 {ref[:2]} 的 {_r['stop_rate']:.2%}："
                      f"μ_norm={_mu:+.3f}bp/腿，**标准化 Δ={_d:+.3f}bp**")
        print("=" * 104)
        # Welch：试跑 vs 两个参照
        t_arr = h._per_leg(cur, since, now, syms)
        for ref in ("A_内置基线(0.15体制,45s)", "N3_同体制参照(0.5体制,45s)"):
            a, b = wins[ref]
            r_arr = h._per_leg(cur, a, b, syms)
            w = h._welch(t_arr, r_arr)
            out[f"welch_vs_{ref}"] = w
            if w:
                print(f"\nWelch 试跑 vs {ref}：p={w['p']:.4f} delta={w['delta']:+.3f}bp "
                      f"n={w['n_trial']}/{w['n_base']} "
                      f"⇒ {'显著' if w['p'] <= 0.10 else '不显著'}")
        print("-" * 104)
        print("⇒ 读法（R79 初版 → **R80c 更正**）：")
        print("   · A 是**白天**窗（3.34% 止损率）≈ 白天均值 3.69% ⇒ **A 并不走运**；")
        print("   · N3 是**夜间**窗（6.03%）≫ 夜间均值 4.84% ⇒ **N3 才是异常的那个**，"
              "它作为净/腿参照属**昼夜错位** ✗（R79 拿它做标准化对比是苹果比橘子）；")
        print("   · 判定实际比的是 **A（白天）vs 试跑窗（白天）**，两者**同一钟点区间、组成匹配** ✓"
              " ⇒ 原始 Welch vs A 就是对的口径，**判负应当被认真对待**（不要用尾部运气开脱）。")
        print("   · 仍须注意样本：试跑窗 ~73 腿（SE≈1.1bp）⇒ 现在未定；12h（n≈800）才有分辨力。")
        # [R80] **同钟点市场活跃度对比**：A 与试跑窗是**同一钟点区间**（09:48→21:48，
        # 只差一天）⇒ 止损率差异来自**当日行情**，不是时段构成。
        # 频率闸在"市场更冷/波动更高"时会把短fall 归因于外部（`_market_activity`），
        # 但**只在频率闸失败时才计算** ⇒ 判据 B（净/腿）没有这层保护。这里补上同样的对照。
        _sub = min(6.0, max(0.25, (dt.datetime.now(dt.timezone.utc)
                                   - t0).total_seconds() / 3600.0))
        mk = {}
        for tag, day in (("D1_09-28", 28), ("D2_09-29", 29)):
            _a = _iso(2026, 9, day, 9, 48)
            _b = (dt.datetime(2026, 9, day, 9, 48, tzinfo=TZ)
                  + dt.timedelta(hours=_sub)).astimezone(dt.timezone.utc).isoformat()
            mk[tag] = {"window_h": round(_sub, 2), **h._market_activity(_a, _b, syms)}
        out["market_same_clock"] = mk
        print(f"\n[同钟点市场对照] 两天的 09:48 → +{_sub:.2f}h：")
        for tag, m in mk.items():
            print(f"  {tag}: {json.dumps(m, ensure_ascii=False, default=str)}")
        _a1, _b1 = mk.get("D1_09-28"), mk.get("D2_09-29")
        if _a1 and _b1:
            _ta, _tb = _a1.get("trades_per_h"), _b1.get("trades_per_h")
            _va, _vb = _a1.get("vol_bp"), _b1.get("vol_bp")
            if _ta and _tb:
                _vr = (_vb / _va) if (_va and _vb) else None
                print(f"  ⇒ 今日逐笔活跃度 = {_tb / _ta:.2f}× 昨日；"
                      f"波动代理 = {'n/a' if _vr is None else format(_vr, '.2f') + '×'}")
                print("  ⇒ 读法：若今日明显更波动（>1.2×），判据 B 的负值**部分归因于市场**，"
                      "不应全记在 90s 账上（与频率闸 `freq_shortfall_market_explained` 同源逻辑）。")
        # [R80b] **A 窗本身是不是"软基线"**：A（09-28 09:48→21:48）的止损率仅 3.34%，
        # 低于纪元均值 ≈4.2% ⇒ 若 A 当时市场**异常安静/低波**，它作为基线就偏松，
        # 用它判 90s 会系统性偏严 ✗。故拿**前一天的同一钟点**（09-27 09:48→21:48）对照。
        _A = wins["A_内置基线(0.15体制,45s)"]
        _prev = (_iso(2026, 9, 27, 9, 48), _iso(2026, 9, 27, 21, 48))
        _mA = h._market_activity(*_A, syms)
        _mP = h._market_activity(*_prev, syms)
        out["baseline_market_softness"] = {"A_window": _mA, "prev_day_same_clock": _mP}
        print("\n[基线是否偏软] A 窗 vs 前一天同钟点（09-27 09:48→21:48）：")
        print(f"  A   : {json.dumps(_mA, ensure_ascii=False, default=str)}")
        print(f"  09-27: {json.dumps(_mP, ensure_ascii=False, default=str)}")
        _pa, _pp = _mA.get("trades_per_h"), _mP.get("trades_per_h")
        _va2, _vp2 = _mA.get("vol_bp"), _mP.get("vol_bp")
        if _pa and _pp:
            print(f"  ⇒ A 的活跃度 = {_pa / _pp:.2f}× 前一日；"
                  f"波动 = {(_va2 / _vp2) if (_va2 and _vp2) else float('nan'):.2f}×")
            print("  ⇒ 若两者都 ≈1×，说明 A 不是「安静窗」⇒ 3.34% 的止损率只是**当日运气**，"
                  "不是市场构成（此时判据 B 的偏严来自小样本尾部，而非系统性偏置）。")
        # [R80c] **止损率是否随昼夜变化**（决定 R79 的"尾部运气"结论对不对）：
        # A 是白天（3.34%）、N3 是夜间（6.03%）⇒ 若止损率本来就在夜间更高，
        # 那 R79 把 N3 当"同体制参照"做净/腿对比就是**昼夜错位** ✗，
        # 而 A（白天）与试跑窗（白天）反而**可比** ✓。
        cur.execute(
            "SELECT to_char(ts AT TIME ZONE 'Asia/Shanghai', 'HH24') AS hh, count(*),"
            " count(*) FILTER (WHERE meta_json->>'exit_path' LIKE 'stop_loss%%') AS stops"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND symbol = ANY(%s) GROUP BY 1 ORDER BY 1",
            (h.LANE, (t0 - dt.timedelta(hours=24)).isoformat(), syms))
        rows = cur.fetchall()
        day = [(int(r[1]), int(r[2])) for r in rows if 8 <= int(r[0]) < 21]
        night = [(int(r[1]), int(r[2])) for r in rows if not (8 <= int(r[0]) < 21)]
        out["stop_rate_by_daynight"] = {
            "day": {"legs": sum(x[0] for x in day), "stops": sum(x[1] for x in day)},
            "night": {"legs": sum(x[0] for x in night), "stops": sum(x[1] for x in night)}}
        print("\n[止损率随昼夜] 逐小时聚合（本地 08–21 时记为昼）：")
        for tag, grp in (("昼", day), ("夜", night)):
            _l = sum(x[0] for x in grp)
            _s = sum(x[1] for x in grp)
            print(f"  {tag}：{_s}/{_l} 腿 = {(_s / _l if _l else 0):.2%}")
        _dl, _ds = out["stop_rate_by_daynight"]["day"]["legs"], out["stop_rate_by_daynight"]["day"]["stops"]
        _nl, _ns = out["stop_rate_by_daynight"]["night"]["legs"], out["stop_rate_by_daynight"]["night"]["stops"]
        if _dl and _nl:
            print(f"  ⇒ 夜/昼止损率之比 = {(_ns / _nl) / (_ds / _dl):.2f}×"
                  f"（>1.3× 则说明 R79 的 N3 参照属**昼夜错位**，应改用白天参照）")
    p = ROOT / "research_l1" / "out" / "h577_net_per_leg_reference.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                 encoding="utf-8")
    print(f"写出 {p.relative_to(ROOT).as_posix()}")
    # [R86] **白天子集对比**：框架在 INCONCLUSIVE 时把窗**延长 +13h**（`since` 不变），
    # 于是重判窗变成 ~25h、**跨夜**，而基线仍是白天 12h ⇒ 两窗昼夜组成失配 ✗
    # （R80c 的"组成匹配"前提被破坏）。补救：只取**两窗的白天子集**（本地 08–21 时）
    # 再比 —— 同钟点、组成匹配，代价是样本变小。
    print("\n" + "=" * 104)
    print("[R86] 白天子集对比（本地 08–21 时；用于'延长窗跨夜'时的补救读数）")
    with psycopg.connect(h.read_env_dsn()) as c2, c2.cursor() as cur2:
        for label, (a, b) in (("试跑窗", (since, now)),
                              ("A 内置基线", wins["A_内置基线(0.15体制,45s)"])):
            cur2.execute(
                "SELECT net_bp FROM lane_ledger WHERE lane_id=%s"
                " AND ts > %s::timestamptz AND ts <= %s::timestamptz AND symbol = ANY(%s)"
                " AND extract(hour from ts AT TIME ZONE 'Asia/Shanghai') >= 8"
                " AND extract(hour from ts AT TIME ZONE 'Asia/Shanghai') < 21",
                (h.LANE, a, b, syms))
            arr = [float(r[0]) for r in cur2.fetchall()]
            out[f"daytime_only_{label}"] = {
                "n": len(arr),
                "mean_bp": round(st.mean(arr), 3) if arr else None,
                "median_bp": round(st.median(arr), 3) if arr else None}
            print(f"  {label:<12} 白天腿数={len(arr):>4}  "
                  f"均值={st.mean(arr) if arr else float('nan'):+.3f}bp  "
                  f"中位={st.median(arr) if arr else float('nan'):+.3f}bp")
    _td = out.get("daytime_only_试跑窗", {}).get("mean_bp")
    _ad = out.get("daytime_only_A 内置基线", {}).get("mean_bp")
    if _td is not None and _ad is not None:
        print(f"  ⇒ 白天子集的均值差 = {_td - _ad:+.3f}bp/腿"
              f"（这是**组成匹配**的口径；跨夜延长窗请用它，而不是全窗均值）")
    # [R86b] **尾部归因的关键分解**：中位数接近而均值差很大 ⇒ 差异全在尾部。
    # 但"尾部差异"**不能**当借口：90s 加仓窗本身**可能**导致更大的仓位
    # （加成更多腿 ⇒ 单笔名义更大 ⇒ 止损的 USD 更大）。故必须查**止损腿的名义规模**：
    #   · 试跑窗止损腿名义 ≈ 基线 ⇒ 尾部差异来自**行情**（归因弱）；
    #   · 试跑窗止损腿名义**显著更大** ⇒ 是**参数机制**在放大尾部（归因强，应支持判负）。
    with psycopg.connect(h.read_env_dsn()) as c3, c3.cursor() as cur3:
        print("\n[R86b] 止损腿的名义规模对比（决定'尾部差异'是行情还是参数机制）")
        _rows = {}
        for label, (a, b) in (("试跑窗", (since, now)),
                              ("A 内置基线", wins["A_内置基线(0.15体制,45s)"]),
                              ("N3 同体制参照", wins["N3_同体制参照(0.5体制,45s)"])):
            cur3.execute(
                "SELECT count(*), COALESCE(percentile_cont(0.5) WITHIN GROUP"
                " (ORDER BY notional),0)::float8, COALESCE(avg(notional),0)::float8,"
                " COALESCE(avg(net_bp),0)::float8"
                " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
                " AND ts <= %s::timestamptz AND symbol = ANY(%s)"
                " AND meta_json->>'exit_path' LIKE 'stop_loss%%'",
                (h.LANE, a, b, syms))
            n, p50, avg_n, avg_bp = cur3.fetchone()
            _rows[label] = {"stop_legs": int(n), "stop_notional_p50": round(float(p50), 1),
                            "stop_notional_avg": round(float(avg_n), 1),
                            "stop_net_bp_avg": round(float(avg_bp), 2)}
            print(f"  {label:<12} 止损腿={int(n):>3}  名义P50={float(p50):>7.1f}$  "
                  f"名义均值={float(avg_n):>7.1f}$  止损均值={float(avg_bp):>7.2f}bp")
        out["stop_severity"] = _rows
        _t, _a = _rows.get("试跑窗"), _rows.get("A 内置基线")
        if _t and _a and _t["stop_legs"] and _a["stop_legs"]:
            _rn = _t["stop_notional_avg"] / _a["stop_notional_avg"]
            _rb = abs(_t["stop_net_bp_avg"]) / abs(_a["stop_net_bp_avg"])
            print(f"  ⇒ 止损腿名义均值比 = {_rn:.2f}×；止损幅度比 = {_rb:.2f}×")
            print("  ⇒ 判读（预注册）：名义比 ≈1 而幅度比 >1 ⇒ 尾部差异来自**行情**"
                  "（对 90s 的归因弱）；名义比 >1.15 ⇒ **参数机制在放大尾部**"
                  "（加成更多腿 ⇒ 仓位更大，归因强，判负应被采纳）")
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                 encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
