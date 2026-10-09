"""H29：特征信息系数（IC）扫描 —— 重建论文的四组特征，看谁能预测。

## 背景（用户 2026-09-20：「还是聚焦在预测算法上」）

已知：
  · H18-a：**最优价失衡** → 未来收益 IC = **+0.037~+0.056**（t=+3.4~+4.6，10 币同号）
    —— 统计上很硬，但换算成 bp 只有 0.11~0.22bp，覆盖不了成本。
  · 论文 Albers et al. §7.2 用 **173 个特征**预测"反转"，才拿到可用的（含返佣后）结果。

⇒ 只测过一个特征就断言"预测无效"是不成立的。**必须把论文的四组特征尽量重建一遍。**

## 论文的四组特征（§7.2）与我们的可复现性

  **1. Price Dynamics**   —— 可复现 ✓
     `stdev_100/500`（近期收益标准差）、多尺度（100ms/1s/5s/30s/300s）的
     `amplitude`（窗口内极差）与 `ret_vwap`（VWAP 相对当前价的收益）
  **2. Trade Volume**     —— 可复现 ✓（我们的 15s 桶字段几乎一一对应）
     `max size` → `largest_trade_usd`；`avg size` → notional/count；
     `buy count`/`sell count` → `taker_buy_count`/`taker_sell_count`；
     `total buy`/`total sell` → `taker_buy_notional`/`taker_sell_notional`
  **3. Momentum**         —— 可复现 ✓
     `ret autocov`（相邻收益自协方差）、`ret sum`（收益累加）、
     `trade intensity`（相邻成交平均间隔 → 用 count 的倒数代理）
  **4. LOB State**        —— **部分可复现 ⚠️**
     `top bid/ask liq` → `bid_depth_top5`/`ask_depth_top5` ✓
     `age`（距上次最优价变化时长）→ 从 book_ticker 可算 ✓
     `totb mean`（最优价存活时间）→ 同上 ✓
     `ob bid/ask half`（执行 $500K 的冲击成本）→ 需 20 档累加，本期用 top5 近似 ⚠️

## 本期做法

用 `market_trades_aggregated`（15s 桶，含全部成交类字段 + top5 深度）
+ `asterdex_depth_snapshots`（20 档，补深度失衡）。

对每个特征算：
  · **IC** = Spearman(特征_t, 未来收益_{t→t+h})，h ∈ {1,2,4,8,20} 桶（15s~5min）
  · **分币 + 跨币**，报均值、t 值、同号币数
  · **时间折半分**：前 50% 训练 / 后 50% 检验，看 IC 是否稳定（防过拟合）

## 判据（事先定死，不许事后挑）

  · |IC| ≥ **0.05** 且分折同号 且 ≥7/10 币同号 ⇒ **值得进模型**
  · |IC| ≥ 0.03 但 < 0.05 ⇒ 弱，需与其他特征组合才有意义
  · |IC| < 0.03 ⇒ 单特征无效
  · **跨折符号翻转** ⇒ 判为不稳定，无论 IC 多大都不用

用法：
    .venv\\Scripts\\python.exe scripts\\h29_feature_ic_scan.py --hours 72
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

OUT_DIR = ROOT / "research_l1" / "out"
# 预测视界（桶数；桶 = 15s）⇒ 15s / 30s / 1min / 2min / 5min
HORIZONS = [1, 2, 4, 8, 20]


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def _spearman(x, y):
    """Spearman 相关（对秩做 Pearson）。返回 (rho, n)。"""
    import numpy as np
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    n = len(x)
    if n < 60:
        return float("nan"), n
    # 秩（平均秩处理并列）
    def _rank(a):
        order = a.argsort()
        r = np.empty(n, dtype=float)
        r[order] = np.arange(n, dtype=float)
        # 并列取平均秩
        _, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
        if (cnt > 1).any():
            sums = np.zeros(len(cnt))
            np.add.at(sums, inv, r)
            r = (sums / cnt)[inv]
        return r
    rx, ry = _rank(x), _rank(y)
    if rx.std() == 0 or ry.std() == 0:
        return float("nan"), n
    return float(np.corrcoef(rx, ry)[0, 1]), n


def build_features(rows):
    """把某个币的 15s 桶序列转成特征矩阵（**只用当期及过去**）。"""
    import numpy as np

    ts = np.array([int(r["timestamp"]) for r in rows], dtype=np.int64)
    tb = np.array([float(r["taker_buy_volume"] or 0) for r in rows])
    tsv = np.array([float(r["taker_sell_volume"] or 0) for r in rows])
    tbc = np.array([float(r["taker_buy_count"] or 0) for r in rows])
    tsc = np.array([float(r["taker_sell_count"] or 0) for r in rows])
    tbn = np.array([float(r["taker_buy_notional"] or 0) for r in rows])
    tsn = np.array([float(r["taker_sell_notional"] or 0) for r in rows])
    vwap = np.array([float(r["vwap"] or 0) for r in rows])
    hi = np.array([float(r["high_price"] or 0) for r in rows])
    lo = np.array([float(r["low_price"] or 0) for r in rows])
    bd5 = np.array([float(r["bid_depth_top5"] or 0) for r in rows])
    ad5 = np.array([float(r["ask_depth_top5"] or 0) for r in rows])
    big = np.array([float(r["largest_trade_usd"] or 0) for r in rows])

    n = len(vwap)
    if n < 300:
        return None

    # 中价代理：用 (high+low)/2（vwap 有成交才更新，hi/lo 同理）
    mid = (hi + lo) / 2.0
    good = (mid > 0) & np.isfinite(mid)
    if good.sum() < 300:
        return None

    ret = np.zeros(n)
    ret[1:] = np.where(mid[:-1] > 0, (mid[1:] - mid[:-1]) / mid[:-1], 0.0)
    ret_bp = ret * 1e4

    def _roll(a, w, fn):
        out = np.full(n, np.nan)
        for i in range(w, n):
            out[i] = fn(a[i - w + 1:i + 1])
        return out

    F = {}

    # ── 1. Price Dynamics ────────────────────────────────────────
    F["ret_1"] = np.concatenate([[np.nan], ret_bp[:-1]])          # 上一桶收益
    F["ret_4"] = _roll(ret_bp, 4, np.sum)                          # 近 1 分钟
    F["ret_16"] = _roll(ret_bp, 16, np.sum)                        # 近 4 分钟
    F["stdev_16"] = _roll(ret_bp, 16, lambda a: a.std())
    F["stdev_64"] = _roll(ret_bp, 64, lambda a: a.std())           # 近 16 分钟
    F["amp_1"] = (hi - lo) / np.where(mid > 0, mid, np.nan) * 1e4
    F["amp_4"] = _roll((hi - lo), 4, np.max) / np.where(mid > 0, mid, np.nan) * 1e4
    F["vwap_dev"] = (vwap - mid) / np.where(mid > 0, mid, np.nan) * 1e4

    # ── 2. Trade Volume Patterns ─────────────────────────────────
    tot_n = tbn + tsn
    F["notional_ratio_4"] = tot_n / np.where(_roll(tot_n, 16, np.mean) > 0,
                                             _roll(tot_n, 16, np.mean), np.nan)  # 相对放量
    F["avg_size"] = np.where((tbc + tsc) > 0, tot_n / (tbc + tsc), 0.0)
    F["max_size_rel"] = big / np.where(tot_n > 0, tot_n, np.nan)
    F["trade_count"] = tbc + tsc
    F["count_ratio_4"] = (tbc + tsc) / np.where(
        _roll(tbc + tsc, 16, np.mean) > 0, _roll(tbc + tsc, 16, np.mean), np.nan)

    # ── 3. Momentum ──────────────────────────────────────────────
    # 相邻收益自协方差（论文的 ret_autocov，负系数 ⇒ 反转更可能）
    def _autocov(a):
        if len(a) < 3:
            return np.nan
        x = a[:-1] - a.mean()
        y = a[1:] - a.mean()
        return float((x * y).mean())
    F["ret_autocov_8"] = _roll(ret_bp, 8, _autocov)
    F["ret_autocov_32"] = _roll(ret_bp, 32, _autocov)
    # 成交强度：相邻成交平均间隔的代理 = 1 / 平均笔数
    F["trade_intensity_4"] = _roll(tbc + tsc, 4, np.mean)
    F["trade_intensity_32"] = _roll(tbc + tsc, 32, np.mean)

    # ── 4. LOB State ─────────────────────────────────────────────
    denom = bd5 + ad5
    F["depth_imb"] = np.where(denom > 0, (bd5 - ad5) / denom, np.nan)
    F["depth_total"] = denom
    F["depth_total_rel"] = denom / np.where(_roll(denom, 64, np.mean) > 0,
                                            _roll(denom, 64, np.mean), np.nan)
    # 我们已有的最优价失衡（H18-a 的那个）用 top5 代替（更粗但同向）
    F["flow_imb"] = np.where((tb + tsv) > 0, (tb - tsv) / (tb + tsv), 0.0)          # 当期主动流
    F["flow_imb_4"] = ((_roll(tbn, 4, np.sum) - _roll(tsn, 4, np.sum)) /
                       np.where((_roll(tbn, 4, np.sum) + _roll(tsn, 4, np.sum)) > 0,
                                _roll(tbn, 4, np.sum) + _roll(tsn, 4, np.sum), np.nan))
    F["flow_imb_16"] = ((_roll(tbn, 16, np.sum) - _roll(tsn, 16, np.sum)) /
                        np.where((_roll(tbn, 16, np.sum) + _roll(tsn, 16, np.sum)) > 0,
                                 _roll(tbn, 16, np.sum) + _roll(tsn, 16, np.sum), np.nan))

    # ── 目标：未来 h 桶的收益（从本桶末到 t+h 桶末）─────────────
    fwd = {}
    for h in HORIZONS:
        f = np.full(n, np.nan)
        if n > h:
            f[:-h] = (mid[h:] - mid[:-h]) / mid[:-h] * 1e4
        fwd[h] = f
    return {"n": n, "ts": ts, "F": F, "fwd": fwd, "mid": mid}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=72.0)
    ap.add_argument("--exchange", default="asterdex")
    ap.add_argument("--min-buckets", type=int, default=800)
    args = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        "SELECT DISTINCT symbol FROM market_trades_aggregated"
        " WHERE exchange = %s AND timestamp > (extract(epoch from now())*1000)::bigint - %s",
        (args.exchange, int(args.hours * 3600_000)),
    )
    syms = [r["symbol"] for r in cur.fetchall()]
    print(f"H29 特征 IC 扫描   exchange={args.exchange}  窗口={args.hours}h  "
          f"候选币={len(syms)}")
    print(f"预测视界（桶=15s）: {HORIZONS}  ⇒ {[h*15 for h in HORIZONS]} 秒\n")

    per_sym = {}
    for s in syms:
        cur.execute(
            "SELECT timestamp, taker_buy_volume, taker_sell_volume,"
            "       taker_buy_count, taker_sell_count,"
            "       taker_buy_notional, taker_sell_notional,"
            "       vwap, high_price, low_price,"
            "       bid_depth_top5, ask_depth_top5, largest_trade_usd"
            "  FROM market_trades_aggregated"
            " WHERE exchange = %s AND symbol = %s"
            "   AND timestamp > (extract(epoch from now())*1000)::bigint - %s"
            " ORDER BY timestamp",
            (args.exchange, s, int(args.hours * 3600_000)),
        )
        rows = cur.fetchall()
        if len(rows) < args.min_buckets:
            continue
        d = build_features(rows)
        if d:
            per_sym[s] = d
            print(f"  {s:<10} 桶 {d['n']:>6}")

    if not per_sym:
        print("\n没有足够的桶数据")
        return 1

    names = sorted(next(iter(per_sym.values()))["F"].keys())
    print(f"\n特征 {len(names)} 个: {' '.join(names)}\n")

    # ── IC 扫描：分币 → 汇总 ─────────────────────────────────────
    # 结果结构: rows[feat][h] = list of (symbol, ic_full, ic_first, ic_second, n)
    res = defaultdict(lambda: defaultdict(list))
    for s, d in per_sym.items():
        F, fwd = d["F"], d["fwd"]
        n = d["n"]
        half = n // 2
        for name in names:
            x = F[name]
            for h in HORIZONS:
                y = fwd[h]
                ic_all, nn = _spearman(x, y)
                ic1, _ = _spearman(x[:half], y[:half])
                ic2, _ = _spearman(x[half:], y[half:])
                if np.isfinite(ic_all):
                    res[name][h].append((s, ic_all, ic1, ic2, nn))

    print("[1] 全窗口 IC（跨币均值；只列 |IC| 最大的前 20 个组合）")
    flat = []
    for name in names:
        for h in HORIZONS:
            v = res[name][h]
            if not v:
                continue
            ics = np.array([x[1] for x in v])
            same = int((ics > 0).sum())
            flat.append((name, h, float(ics.mean()), len(v), same,
                         float(np.std(ics, ddof=1) / max(1e-9, np.sqrt(len(ics))))))
    flat.sort(key=lambda t: -abs(t[2]))
    print("    %-20s %5s %9s %9s %8s %10s" %
          ("feature", "h(桶)", "IC均值", "t值", "同号", "样本币"))
    for name, h, ic, n_s, same, se in flat[:20]:
        t = ic / se if se > 0 else 0.0
        print("    %-20s %5d %+9.4f %+9.2f %4d/%-3d %10d"
              % (name, h, ic, t, max(same, n_s - same), n_s, n_s))

    # ── 判据 ────────────────────────────────────────────────────
    print("\n[2] 按事先定死的判据分类（|IC|≥0.05 且分折同号 且 ≥7/10 币同号）")
    print("    ⚠️ 下表**同时给出分折 IC** —— 只有前折/后折/全窗口三者同号才算稳定。")
    keep, weak, dead, unstable = [], [], [], []
    for name in names:
        for h in HORIZONS:
            v = res[name][h]
            if not v:
                continue
            ics = np.array([x[1] for x in v])
            ic1 = np.array([x[2] for x in v if np.isfinite(x[2])])
            ic2 = np.array([x[3] for x in v if np.isfinite(x[3])])
            same = max(int((ics > 0).sum()), int((ics < 0).sum()))
            n_s = len(v)
            # 跨折符号一致性：全窗口符号 vs 两折各自符号
            sign_ok = True
            if len(ic1) and len(ic2):
                s0 = np.sign(ics.mean())
                if np.sign(ic1.mean()) != s0 or np.sign(ic2.mean()) != s0:
                    sign_ok = False
            rec = (name, h, float(ics.mean()), n_s, same,
                   float(ic1.mean()) if len(ic1) else None,
                   float(ic2.mean()) if len(ic2) else None)
            if not sign_ok:
                unstable.append(rec)
            elif abs(ics.mean()) >= 0.05 and same >= max(7, int(0.7 * n_s)):
                keep.append(rec)
            elif abs(ics.mean()) >= 0.03:
                weak.append(rec)
            else:
                dead.append(rec)

    def _show(title, lst, note=""):
        print(f"\n  {title}  ({len(lst)} 个) {note}")
        print("    %-20s %5s %9s %9s %9s %8s" %
              ("feature", "h(桶)", "IC全窗", "IC前折", "IC后折", "同号"))
        for name, h, ic, n_s, same, a, b in sorted(lst, key=lambda t: -abs(t[2]))[:14]:
            f = lambda v: ("%+9.4f" % v) if v is not None else "        —"
            print("    %-20s %5d %+9.4f %s %s %4d/%d"
                  % (name, h, ic, f(a), f(b), same, n_s))

    _show("✓ 值得进模型", keep, "（|IC|≥0.05 且**三处同号**）")
    _show("~ 弱（需组合）", weak, "（0.03≤|IC|<0.05）")
    if unstable:
        _show("✗ 跨折符号翻转 —— 不稳定，无论 IC 多大都不用", unstable)
    print(f"\n  ✗ 无效（|IC|<0.03）: {len(dead)} 个组合（略）")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "h29_feature_ic_scan.json"
    # ⚠️ 落盘必须**自包含**：早先版本只存 (feature, h, ic, n_sym, same_sign, t)，
    #    分折 IC（ic1/ic2）没进去 ⇒ 事后无法复核"跨折是否同号"这个核心判据，
    #    而且按 (feature,h) 去回查会撞上重复项、取到错的 t 值（实测发生）。
    #    现在把每个 (feature, h) 的完整统计一次性存全。
    detail = []
    for name in names:
        for h in HORIZONS:
            v = res[name][h]
            if not v:
                continue
            ics = np.array([x[1] for x in v])
            ic1s = np.array([x[2] for x in v if np.isfinite(x[2])])
            ic2s = np.array([x[3] for x in v if np.isfinite(x[3])])
            se = float(np.std(ics, ddof=1) / np.sqrt(len(ics))) if len(ics) > 1 else 0.0
            detail.append({
                "feature": name, "h_buckets": h, "h_seconds": h * 15,
                "ic_mean": float(ics.mean()),
                "ic_std": float(np.std(ics, ddof=1)) if len(ics) > 1 else 0.0,
                "t": float(ics.mean() / se) if se > 0 else 0.0,
                "n_sym": len(v),
                "n_pos": int((ics > 0).sum()),
                "ic_first_half": float(ic1s.mean()) if len(ic1s) else None,
                "ic_second_half": float(ic2s.mean()) if len(ic2s) else None,
                # 跨折同号：全窗口符号 == 前折符号 == 后折符号
                "folds_same_sign": bool(
                    len(ic1s) and len(ic2s)
                    and np.sign(ic1s.mean()) == np.sign(ics.mean())
                    and np.sign(ic2s.mean()) == np.sign(ics.mean())),
                "per_symbol": [{"symbol": a, "ic": b, "ic_fold1": c, "ic_fold2": d2, "n": e}
                               for a, b, c, d2, e in v],
            })
    p.write_text(json.dumps({
        "hours": args.hours, "exchange": args.exchange,
        "horizons_buckets": HORIZONS, "symbols": list(per_sym),
        "features": names, "detail": detail,
        "keep": keep, "weak": weak, "unstable": unstable,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {p}（含分折 IC，可复核跨折稳定性）")
    cur.close()
    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
