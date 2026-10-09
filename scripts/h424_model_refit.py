# -*- coding: utf-8 -*-
"""H424 P4 模型工件滚动重拟合：h300 方向模型（r60 线性）用近期账本重估 θ。

设计对应《随市进化系统设计_20260927.md》§4.1.2：
"h300 方向工件的滚动重拟合——用近期账本重估 r60 权重/θ，产新版工件
→ 人工评审 → 发布（评审是安全门）"。本脚本只产工件与评审卡，**永不自动发布**。

口径（挂单时刻归因，杜绝桶延迟污染，h402/h413 的 quote_ts）：
  · 样本：近 7 天 lane_ledger 中 quote_ts>0 的腿（h413 起才记录，414 腿起步，
    约 10-03 后满 7 天；未满直接 no-op，不产垃圾卡）；
  · 特征 x：挂单时刻 r60（60s 中价收益 bp，5s 网格 12 步，book_ticker 源）；
  · 目标 y：买腿 net_bp、卖腿 −net_bp（镜像合并）——θ̂>0 = 动量结构（跟随 r60 正期望），
    θ̂<0 = 反转结构（当前模型方向为负期望）；
  · 拟合：OLS 正规方程 y = α + θ·x，报 θ/t/R² 与样本量；
  · 裁决：|t_θ|≥2 且 θ̂ 符号与当前工件方向相反 ⇒ 产出"模型方向翻转"评审卡（人工）；
    |t_θ|≥2 且同号 ⇒ 产出"θ 权重更新"评审卡（人工）。
只读：不改 lane_registry、不改 h300 工件、不改任何状态。

用法: python scripts/h424_model_refit.py [--dry-run]
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h424_refit_artifact.json"
BACKLOG = ROOT.parent / "研究结论" / "h375_phase2_backlog_20260927.md"
MIN_SPAN_DAYS = 7.0
MIN_LEGS = 1000
T_THETA = 2.0


def read_env_dsn() -> str:
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
    return url


def _ols(xs, ys):
    n = len(xs)
    if n < 30:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0:
        return None
    th = sxy / sxx
    al = my - th * mx
    resid = sum((y - al - th * x) ** 2 for x, y in zip(xs, ys))
    syy = sum((y - my) ** 2 for y in ys)
    r2 = 1.0 - resid / syy if syy > 0 else 0.0
    se = (resid / (n - 2) / sxx) ** 0.5
    t = th / se if se > 0 else None
    return {"n": n, "alpha": round(al, 4), "theta": round(th, 4),
            "t_theta": None if t is None else round(t, 2), "r2": round(r2, 3)}


def _grid_for(cur, sym, t0, t1):
    """(ts5, mid) 5s 桶末网格（book_ticker 全量 + Python 降采样）。"""
    cur.execute("""
        SELECT event_ts_ms, bid_px, ask_px FROM asterdex_book_ticker
        WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
          AND bid_px>0 AND ask_px>bid_px
        ORDER BY event_ts_ms
    """, (sym + "USDT", t0, t1))
    grid = {}
    for ms, bid, ask in cur.fetchall():
        b = (int(ms) // 5000) * 5
        grid[b] = (float(bid) + float(ask)) / 2.0
    return grid


def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT MIN(to_timestamp((meta_json->>'quote_ts')::float8)),
                       MAX(to_timestamp((meta_json->>'quote_ts')::float8)),
                       COUNT(*)
                FROM lane_ledger
                WHERE (meta_json->>'quote_ts') ~ '^[0-9.]+$'
                  AND (meta_json->>'quote_ts')::float8 > 0
            """)
            mn, mx, n_legs = cur.fetchone()
            cur.execute("""
                SELECT symbol, meta_json->>'side' AS side, net_bp,
                       (meta_json->>'quote_ts')::float8
                FROM lane_ledger
                WHERE (meta_json->>'quote_ts') ~ '^[0-9.]+$'
                  AND (meta_json->>'quote_ts')::float8 > 0
                  AND net_bp IS NOT NULL
                ORDER BY ts
            """)
            legs = [(str(s), str(sd), float(nb), float(qt))
                    for s, sd, nb, qt in cur.fetchall()]
            syms = sorted({s for s, *_ in legs})

    now = dt.datetime.now(dt.timezone.utc)
    span_days = (mx - mn).total_seconds() / 86400.0 if mn and mx else 0.0
    print(f"quote_ts 覆盖：{mn} → {mx}（{span_days:.1f} 天），可拟合腿 {len(legs)}")

    res = {"generated_at": now.isoformat(), "span_days": round(span_days, 1),
           "legs_with_quote_ts": len(legs), "eligible": False, "cards": []}
    if span_days < MIN_SPAN_DAYS or len(legs) < MIN_LEGS:
        print(f"[skip] 数据未满门（{MIN_SPAN_DAYS} 天 / {MIN_LEGS} 腿）——no-op，"
              f"预计 {(mx + dt.timedelta(days=MIN_SPAN_DAYS)).date()} 起可用")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    # 每币 5s 网格（7 天全量；腿窗口前后各留 5min）
    t0 = int(mx.timestamp()) - 300
    t1 = int(now.timestamp())
    xs = []
    ys = []
    per_sym = {}
    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                         autocommit=True) as cm:
        with cm.cursor() as cur:
            for sym in syms:
                grid = _grid_for(cur, sym, (t0 - 7 * 86400) * 1000, (t1 + 300) * 1000)
                ts = sorted(grid)
                cnt = 0
                for s, sd, nb, qt in legs:
                    if s != sym or qt < t0 - 7 * 86400:
                        continue
                    qb = int(qt) // 5 * 5
                    k = bisect.bisect_right(ts, qb) - 1          # ≤ 挂单时刻的最后一桶
                    j = bisect.bisect_right(ts, qb - 60) - 1     # 60s 前最后一桶
                    if k < 1 or j < 0 or k >= len(ts) or j >= len(ts):
                        continue
                    m0 = grid[ts[k]]
                    m1 = grid[ts[j]]
                    if m1 <= 0 or m0 <= 0:
                        continue
                    r60 = (m0 - m1) / m1 * 1e4
                    xs.append(r60)
                    ys.append(nb if sd == "buy" else -nb)
                    cnt += 1
                per_sym[sym] = cnt

    st = _ols(xs, ys)
    print(f"拟合样本 {len(xs)}（每币：{per_sym}）")
    if not st:
        print("[skip] 有效样本不足")
        return 0
    print(f"θ̂={st['theta']} t={st['t_theta']} R²={st['r2']} n={st['n']} "
          f"（θ̂>0 动量 / <0 反转）")

    res["eligible"] = True
    res["fit"] = st
    res["per_symbol_n"] = per_sym
    cards = []
    if st["t_theta"] is not None and abs(st["t_theta"]) >= T_THETA:
        kind = ("反转结构（当前模型方向为负期望）——建议翻转工件方向"
                if st["theta"] < 0 else "动量结构——θ 权重更新")
        cards.append({"kind": kind, "theta": st["theta"], "t": st["t_theta"],
                      "r2": st["r2"], "n": st["n"]})
    res["cards"] = cards
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    if cards and not a.dry_run:
        sec = [f"\n## [h424 模型工件重拟合 · {dt.datetime.now():%Y-%m-%d %H:%M}]（P4，人工评审后发布）\n"]
        for c in cards:
            sec.append(f"- θ̂={c['theta']:+.4f}（t={c['t']:+.1f}, R²={c['r2']}, n={c['n']}）："
                       f"{c['kind']}")
        if BACKLOG.exists():
            with open(BACKLOG, "a", encoding="utf-8") as f:
                f.write("\n".join(sec) + "\n")
            print(f"已写入 {BACKLOG.name}")
    print(f"已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
