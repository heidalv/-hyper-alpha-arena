# -*- coding: utf-8 -*-
"""F341 给「无波动基准」的币补上基准（消除免暂停特权）。

# 缺陷（可验证，非推断）

心跳实测：

    replay_baseline.vol_baseline_bp = {ASTER:3.67, XRP:3.91, SOL:3.27}   ← HYPE 不在里面
    sigma_decisions = {all: 136, quoted: 31}
    states = {ASTER: qty=0, XRP: qty=0, SOL: qty=0, HYPE: qty=-2.854}    ← 只有 HYPE 持仓

`runner.py` 的 σ 计算：

    sigma = max(0.0, vol_cur / st.vol_baseline_bp - 1.0) if st.vol_baseline_bp > 0 else 0.0
                                                                        ↑ 基准=0 ⇒ σ 恒为 0
    if sigma > vol_pause_sigma:  → 整车道暂停

⇒ **基准缺失的币享有「车道 σ 闸永不触发」的特权。**

仓库自己早已记录过这个坑（`mm_anchor_vol_baseline_ticks.py` 的 docstring）：

    「这是已知的公平性坑：换宇宙后新币没有锚定基准，等于给它们开了免暂停特权，
      使对照实验不可比（2026-09-17 已因此得出过一次错误结论）。」

# 为什么这次后果具体

H230 实测（最近 6 小时，逐 15 分钟片）：

    ASTER  vol 8.708  σ=1.37  闸开 100%
    XRP    vol 6.900  σ=0.76  闸开 100%
    SOL    vol 3.725  σ=0.14  闸开   0%
    HYPE   vol 6.766  σ=0.62  闸开   0%（**因为它没有基准，σ 被强制为 0**）

⇒ 在 ASTER/XRP 被拦住的那段时间里，**HYPE 是唯一能报价的币**，
   而它恰好是唯一留下持仓的币（qty=−2.854）。
**引擎被自己的闸门赶到了唯一一个没有保护的标的上。**

# 本脚本

只做一件事：把当前宇宙里**缺基准**的币补上，数值由仓库自己的
`compute_vol_baselines`（与实盘同实现、同 15s 网格、同 20 期窗口）算出。

- `--dry-run`（默认）：只打印将要写入的值，**不写**
- `--apply`：写入 `meta.replay_baseline.vol_baseline_bp`
- 写入前把原值存进 `meta.f341_baseline_backfill_rollback`（可回滚）
- **只补缺失的键**，已存在的币一律不动（避免顺手改掉别人的基准）

# 用法

    python scripts/h233_backfill_vol_baseline.py              # 看要写什么
    python scripts/h233_backfill_vol_baseline.py --apply      # 写入
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LANE = "mm_asterdex"


def dsn() -> str:
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


def market_dsn() -> str:
    u = dsn()
    return u.rsplit("/", 1)[0] + "/alpha_market"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--days", type=float, default=14.0)
    ap.add_argument("--window", type=int, default=20)
    a = ap.parse_args()

    import psycopg
    from backend.services.market_maker.core import realized_vol_bp  # noqa: F401
    from backend.services.market_maker.portfolio_replay import compute_vol_baselines

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = dict(cur.fetchone()[0] or {})
    syms = [str(s).upper() for s in (meta.get("symbols") or [])]
    base = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})

    print("=" * 96)
    print("F341  补齐「无波动基准」的币")
    print("=" * 96)
    print(f"\n  车道宇宙 = {syms}")
    missing = [s for s in syms if float(base.get(s) or 0.0) <= 0]
    print(f"  已有基准 = {[s for s in syms if s not in missing]}")
    print(f"  **缺基准** = {missing}")
    if not missing:
        print("\n  ⇒ 无需补齐")
        return 0

    tks = [s + "USDT" if not s.endswith("USDT") else s for s in missing]
    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, event_ts_ms, (bid_px + ask_px) / 2.0
                FROM asterdex_book_ticker
                WHERE ingest_ts >= now() - (%s || ' days')::interval
                  AND symbol = ANY(%s) AND bid_px > 0 AND ask_px > 0
                ORDER BY symbol, event_ts_ms ASC
            """, (str(float(a.days)), tks))
            rows = cur.fetchall()

    per = {}
    for sym, ms, mid in rows:
        per.setdefault(str(sym), {})[int(ms // 1000 // 15 * 15)] = float(mid)
    series = {s: [d[k] for k in sorted(d)] for s, d in per.items()}
    print(f"\n  15s 网格样本："
          + "　".join(f"{s}={len(v)}" for s, v in sorted(series.items())))
    calc = compute_vol_baselines(series, window=a.window)

    print(f"\n  {'币':<10}{'原值':>10}{'新值(同实现)':>16}")
    patch = {}
    for s in missing:
        tk = s + "USDT" if not s.endswith("USDT") else s
        v = float(calc.get(tk) or 0.0)
        print(f"  {s:<10}{float(base.get(s) or 0.0):>10.4f}{v:>16.4f}")
        if v > 0:
            patch[s] = round(v, 6)

    if not patch:
        print("\n  ✗ 算不出有效基准（tick 数据不足）⇒ 不写")
        return 1
    if not a.apply:
        print(f"\n  （dry-run）将要写入 {patch}")
        print(f"  ⇒ 确认后加 --apply")
        return 0

    rb = dict(meta.get("replay_baseline") or {})
    old = dict(rb.get("vol_baseline_bp") or {})
    meta.setdefault("f341_baseline_backfill_rollback", {
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "reason": "补齐无基准币的车道 σ 闸免暂停特权（HYPE 缺失 ⇒ σ 恒为 0）",
        "before": old,
    })
    merged = dict(old)
    merged.update(patch)          # 只新增/覆盖**缺失**的键
    rb["vol_baseline_bp"] = merged
    rb["as_of"] = datetime.now(timezone.utc).isoformat()
    rb["f341_note"] = f"补入 {list(patch)}（compute_vol_baselines, {a.days}天, 15s网格, 窗口{a.window}）"
    meta["replay_baseline"] = rb

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                        " WHERE lane_id=%s",
                        (json.dumps(meta, ensure_ascii=False, default=str), LANE))
        c.commit()
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'replay_baseline'->'vol_baseline_bp'"
                        " FROM lane_registry WHERE lane_id=%s", (LANE,))
            back = dict(cur.fetchone()[0] or {})
    print(f"\n  ✓ 已写入。读回：{json.dumps(back, ensure_ascii=False)}")
    print(f"  回滚点：meta.f341_baseline_backfill_rollback.before")
    print(f"\n  ⇒ 生效时机：`vol_baseline_bp` 由 `_refresh_meta` 热更新（≤60s 指纹周期），")
    print(f"     **无需重启 worker**。观察 `skip_counts`/`lane_pause_counts` 是否变化。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
