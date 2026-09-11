# -*- coding: utf-8 -*-
"""LLM 论题方向的统计边际审计（中长线）。

## 数据源

1. **主源（新，2026-09-09 起）**：`alpha_analytics.ai_decision_logs` 中
   `decision_source='hub'` 的行——由 `mlto/hub_decision_log.py` 写入，
   `decision_snapshot` 含 `direction` / `dir_src` / `llm_qual` / `fw_mean`，
   可按 **方向来源** 精确分组（llm_qual vs orch_bias vs framework）。
2. **回退源**：`alpha_arena.brain_theses` 的 `direction/updated_at`
   （本次调查用过的近似口径，样本少且无 dir_src）。

## 背景（2026-09-09 根因调查）

用 brain_theses 近似实测：

  - mid/short 方向：n=18，24h 均值 **-2.72%**，胜率 0.167
  - long/short 方向：n=19，24h 均值 -1.35%，胜率 0.158
  - mid/long 方向：n=20，24h 均值 -0.03%，胜率 0.550
  - long/long 方向：n=45，24h 均值 +0.09%，胜率 0.689

即 LLM 的**方向**在短线侧是负边际、长线侧接近零。据此已实施：
`MLTO_LLM_WEIGHT_MID/LONG=0.10`、`MLTO_LLM_DIRECTION_REQUIRE_FW_AGREE=true`
（LLM 定方向必须与框架同向）。

用法：
    .venv\\Scripts\\python.exe backend/scripts/audit_llm_direction_edge.py
输出：控制台表格 + `data/llm_direction_edge.json`
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
ANALYTICS_URL = os.getenv("ANALYTICS_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
HORIZONS = (12, 24, 72)
OUT = ROOT / "data" / "llm_direction_edge.json"


def _stats(vals):
    if not vals:
        return None
    vals = sorted(vals)
    n = len(vals)
    return {
        "n": n,
        "mean": round(sum(vals) / n, 4),
        "median": round(vals[n // 2], 4),
        "win_rate": round(sum(1 for x in vals if x > 0) / n, 4),
    }


def _load_hub_decisions():
    """主源：ai_decision_logs 的方向归因行。

    两个写入端：
      - `decision_source='brain'`：`mlto/hub_decision_log.persist_brain_decision`
        （2026-09-09 起，主脑是 mid/long 唯一方向决定者）；
      - `decision_source='hub'`：`persist_hub_decision`（旧 orchestrator 路径，已下线，
        仅存量数据）。
    """
    out = []
    try:
        eng = create_engine(ANALYTICS_URL)
        with eng.connect() as c:
            c.execute(text("set app.is_admin='on'"))
            for r in c.execute(text("""
                select symbol, decision_time, decision_snapshot, decision_source
                from ai_decision_logs
                where decision_source in ('hub', 'brain') and decision_snapshot is not null
                order by id desc limit 20000
            """)):
                try:
                    snap = json.loads(r[2]) if isinstance(r[2], str) else (r[2] or {})
                except Exception:
                    continue
                d = str(snap.get("direction") or "")
                if d not in ("long", "short"):
                    continue
                out.append({
                    "symbol": r[0], "ts": r[1], "direction": d,
                    "dir_src": str(snap.get("dir_src") or ""),
                    "tier": str(snap.get("tier") or ""),
                    "llm_qual": snap.get("llm_qual"),
                    "source": f"decision_log:{r[3]}",
                })
    except Exception as exc:
        print(f"[WARN] ai_decision_logs 读取失败，回退 brain_theses: {exc}")
    return out


def _load_theses():
    """回退源：brain_theses。"""
    out = []
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        for r in c.execute(text("""
            select symbol, tier, direction, updated_at
            from brain_theses
            where direction in ('long','short') and updated_at is not null
            order by updated_at desc limit 2000
        """)):
            out.append({
                "symbol": r[0], "tier": r[1], "direction": r[2], "ts": r[3],
                "dir_src": "thesis(unknown)", "llm_qual": None, "source": "brain_theses",
            })
    return out


def main() -> int:
    rows = _load_hub_decisions()
    primary = bool(rows)
    if not rows:
        rows = _load_theses()
    if not rows:
        print("无 LLM 方向样本，退出")
        return 0
    print(f"数据源: {'ai_decision_logs(decision_source=hub)' if primary else 'brain_theses'}  样本: {len(rows)}")

    syms = sorted({r["symbol"] for r in rows if r.get("symbol")})
    series = defaultdict(list)
    market = create_engine(MARKET_URL)
    with market.connect() as c:
        c.execute(text("set statement_timeout='600000'"))
        for exch in ("asterdex", "binance"):
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1h' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": syms}).fetchall():
                series[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))

    def pick(sym):
        for ex in ("asterdex", "binance"):
            v = series.get((ex, sym))
            if v and len(v) > 500:
                return v
        return None

    buckets = defaultdict(lambda: defaultdict(list))
    for t in rows:
        ser = pick(t["symbol"])
        if not ser:
            continue
        try:
            ts = int(t["ts"].timestamp())
        except Exception:
            continue
        i = next((k for k, row in enumerate(ser) if row[0] >= ts), None)
        if i is None:
            continue
        entry = ser[i][1]
        sgn = 1 if t["direction"] == "long" else -1
        for h in HORIZONS:
            if i + h >= len(ser):
                continue
            ret = (ser[i + h][4] - entry) / entry * 100 * sgn
            buckets[(t["tier"] or "?", t["direction"])][h].append(ret)
            buckets[("ALL", t["direction"])][h].append(ret)
            buckets[("src", t["dir_src"] or "unknown")][h].append(ret)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": ("alpha_analytics.ai_decision_logs(decision_source=hub)"
                   if primary else "alpha_arena.brain_theses"),
        "note": "方向调整后的前瞻收益（价格口径 %）；正=LLM 方向对",
        "buckets": {},
    }
    print(f"\n{'tier/dir':<22}{'H':>5}{'n':>6}{'mean':>10}{'median':>10}{'win':>8}")
    for key in sorted(buckets, key=lambda k: (str(k[0]), str(k[1]))):
        report["buckets"][f"{key[0]}/{key[1]}"] = {}
        for h in HORIZONS:
            s = _stats(buckets[key][h])
            if not s:
                continue
            report["buckets"][f"{key[0]}/{key[1]}"][f"{h}h"] = s
            print(f"{key[0] + '/' + key[1]:<22}{h:>5}{s['n']:>6}{s['mean']:>10.3f}{s['median']:>10.3f}{s['win_rate']:>8.3f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")

    # 结论提示（ASCII 输出，避免 Windows GBK 控制台 UnicodeEncodeError）
    short_24 = report["buckets"].get("ALL/short", {}).get("24h")
    long_24 = report["buckets"].get("ALL/long", {}).get("24h")
    if short_24 and short_24["win_rate"] < 0.45:
        print(f"[WARN] LLM short direction 24h win_rate={short_24['win_rate']:.3f} (<0.45) -> stop opening / flip")
    if long_24 and long_24["win_rate"] < 0.52:
        print(f"[WARN] LLM long direction 24h win_rate={long_24['win_rate']:.3f} (<0.52) -> no statistical edge, downweight")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
