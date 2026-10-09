"""h501：**波动基准是否陈旧？**（F82 失效模式的现场复核）

现象（2026-09-29 04:0x）：腿速掉到 12–27/h，`lane_pause = vol_pause(sigma=1.93)`，
拦截画像里 `vol_pause/vol_regime` 占大头。而用**真实逐笔**算的波动代理只比 24h 前高 19%
⇒ 怀疑不是市场真的翻倍，而是**归一化用的基准**（`replay_baseline.vol_baseline_bp`）
陈旧/偏低，把 `sigma_norm` 抬过了 1.5 的门槛。

代码里 F82 已记录过同一失效模式：
  > 崩盘后实盘 sigma=1.6（30 天旧基准 1.14 虚高放大）> 1.5 ⇒ 双侧被封数小时 0 成交

本脚本只读地给出三方对照：
  ① 注册表基准 `replay_baseline.vol_baseline_bp[币]`（以及它的 as_of/来源）；
  ② 运行态 `avg_sigma`（归一化后的 σ 均值）与 `lane_pause_last` 的 sigma 读数；
  ③ **真实逐笔**算出的当前已实现波动（与基准同量纲的近似：每 15s 桶的中价变化 bp），
     以及 24h 前的同口径值 ⇒ 判断"市场是否真的翻倍"。

用法：python scripts/h501_vol_baseline_check.py
"""
from __future__ import annotations

import json
import pathlib
import statistics as st
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATUS = ROOT / "logs" / "mm_lane_status.json"
OUT = ROOT / "research_l1" / "out" / "h501_vol_baseline.json"


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


def realised_bp(cur, syms, t0_ms, t1_ms, bucket_ms=15000):
    """真实逐笔 → 每 bucket 的最后成交价 → 相邻变化的绝对值均值（bp）。"""
    cur.execute(
        "WITH m AS (SELECT symbol, floor(event_ts_ms/%s) AS b, "
        "(ARRAY_AGG(price ORDER BY event_ts_ms DESC))[1]::float8 AS px "
        "FROM asterdex_trades WHERE symbol = ANY(%s) "
        "AND event_ts_ms > %s AND event_ts_ms <= %s GROUP BY 1,2), "
        "d AS (SELECT px, LAG(px) OVER (PARTITION BY symbol ORDER BY b) AS p0 FROM m) "
        "SELECT COALESCE(AVG(ABS(px-p0)/NULLIF(p0,0))*1e4,0)::float8, count(*) "
        "FROM d WHERE p0 IS NOT NULL",
        (bucket_ms, [s + "USDT" for s in syms], t0_ms, t1_ms))
    r = cur.fetchone()
    return float(r[0] or 0.0), int(r[1] or 0)


def read_status(retries: int = 5):
    """状态文件每 tick 被重写 ⇒ 偶发读到空/半截 JSON，退避重试。"""
    for i in range(retries):
        try:
            return json.loads(STATUS.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            time.sleep(0.6 * (i + 1))
    return {}


def main() -> int:
    stj = read_status()
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = cur.fetchone()[0]
            p = dict(meta.get("params") or {})
            rb = dict(meta.get("replay_baseline") or {})
            vb = dict(rb.get("vol_baseline_bp") or {})
            syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
    print("在役币:", syms)
    print("注册表基准 vol_baseline_bp:")
    for s in syms:
        print(f"   {s:5s} {vb.get(s)}")
    print(f"  replay_baseline 的其它键: "
          f"{[k for k in rb if k != 'vol_baseline_bp']}")
    for k in ("as_of", "updated_at", "ts", "source", "n_days"):
        if k in rb:
            print(f"  {k} = {rb[k]}")
    print(f"vol_pause_sigma(注册表 params) = {p.get('vol_pause_sigma')}")
    print(f"运行态 avg_sigma = {stj.get('avg_sigma')} | avg_sigma_all = "
          f"{stj.get('avg_sigma_all')} | sigma_decisions = {stj.get('sigma_decisions')}")
    print(f"运行态 lane_pause_last = {stj.get('lane_pause_last')}")
    now_ms = int(time.time() * 1000)
    mk = read_env_dsn().replace("/alpha_arena", "/alpha_market")
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            cur15, n15 = realised_bp(cur, syms, now_ms - 3600_000, now_ms)
            cur24, n24 = realised_bp(cur, syms, now_ms - 24 * 3600_000,
                                     now_ms - 23 * 3600_000)
    print("\n真实逐笔的已实现波动（15s 桶相邻变化绝对值均值，bp）：")
    print(f"  近 1h   = {cur15:.2f} bp（n={n15}）")
    print(f"  24h 前  = {cur24:.2f} bp（n={n24}）")
    ratio = cur15 / cur24 if cur24 else float("nan")
    print(f"  ⇒ 当前/24h前 = **{ratio:.2f}×**")
    verdict = ("基准陈旧存疑：真实波动只变 "
               f"{ratio:.2f}×，而 sigma_norm 已 >1.5 触发 vol_pause ⇒ "
               "应复核 vol_baseline_bp 是否偏低/过期（F82 同源）"
               if ratio < 1.3 else
               f"市场确实更波动（{ratio:.2f}×）⇒ vol_pause 属于按设计生效")
    print("\n⇒ 裁决:", verdict)
    OUT.write_text(json.dumps(
        {"symbols": syms, "baseline": vb, "replay_baseline_keys": list(rb),
         "vol_pause_sigma": p.get("vol_pause_sigma"),
         "runtime_avg_sigma": stj.get("avg_sigma"),
         "lane_pause_last": stj.get("lane_pause_last"),
         "realised_bp_1h": cur15, "realised_bp_24h_ago": cur24,
         "ratio": ratio, "verdict": verdict}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
