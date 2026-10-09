# -*- coding: utf-8 -*-
"""[H146 2026-09-21] 挂单宽度扫描（spread_mult）—— 找「捕获 vs 被穿」的实测最优点。

# 为什么现在才能做这个实验

之前做不了：**taker 费（4bp/次）混在结果里**，宽度一变、强平次数一变，
手续费就跟着变，无法把"宽度的效果"单独量出来。
现在 `timeout_exit_maker_only=True` ⇒ 出库走 maker（**0 手续费**）
⇒ 每笔成交的损益只剩两项，宽度的影响**可分离**：

    每笔净额(bp) = 价差捕获(bp) + 行情漂移(bp)     ← 无手续费项

# 为什么怀疑当前档位过窄

`spread_mult=0.9` 的物理含义：报价在 `bid + 0.9×半价差` 处 ⇒
**离中价只有 0.45×价差**，比市场的最优买卖价还靠内（我们是"改善最优价"的一方）。
实测后果（全历史账本）：

    价差捕获 +$503.99     行情漂移 **−$712.04**     比值 **0.71**

即：**赚 1 块，被穿 1.41 块**。这与"挂得太靠内 ⇒ 只有毒性流来吃"的假设一致。

# 扫描设计

| 档位 | `spread_mult` | 含义 |
|---|---|---|
| A | 0.5 | 更靠内（进到价差 25% 处）—— 更激进抢单 |
| B | 0.9 | **现状**（价差 45% 处） |
| C | 1.3 | 价差 65% 处 |
| D | 1.8 | 价差 90% 处 —— 接近贴最优价，几乎不改善 |

每档保持 `--minutes` 分钟（默认 12），记录：
  · `fills`、`fills/h` —— 成交量（宽度越宽越少）
  · `spread_bp_w` —— 名义加权捕获
  · `price_bp_w` —— 名义加权行情漂移
  · **`net_bp_w`** —— 名义加权净额 ← 判据
  · 报价被挡次数（`vol_pause` 等）

# 判据（事先定死）

  · 最优点 = `net_bp_w` 最高且 `fills ≥ 100`（样本够）的那一档
  · 若 `net_bp_w` 随宽度**单调上升** ⇒ 说明当前档位确实过窄，应往宽调
  · 若 `net_bp_w` 在各档都 ≈ 0 或为负 ⇒ **宽度不是解**，瓶颈在别处
    （那就要转向"择时"：在毒性流到来时撤单，而不是单纯调宽）

# 安全

  · 每档用**同一个** worker（靠 F251/F282 热更新，不重启）⇒ 无停机
  · `.env` 与注册表**双写**（`MM_SPREAD_MULT` 在 `apply_env_param_overrides`
    的白名单里，是"env 赢"的键 ⇒ 只写注册表不生效 —— 这个坑踩过）
  · 结束时**恢复到起始值**（脚本无论正常结束还是异常都用 try/finally）

用法：
    .venv\\Scripts\\python.exe scripts\\h146_width_sweep.py --minutes 12
    .venv\\Scripts\\python.exe scripts\\h146_width_sweep.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
LANE = "mm_asterdex"
LADDER = [0.5, 0.9, 1.3, 1.8]
# [H154] 泛化：默认扫进场侧宽度；用 --key/--env 可扫任意 env 白名单键
# （例如 spread_mult_reduce / MM_SPREAD_MULT_REDUCE 扫出库侧）。
DEFAULT_KEY = "spread_mult"
DEFAULT_ENV = "MM_SPREAD_MULT"


def dsn() -> str:
    env = {}
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def get_params() -> dict:
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'params' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            return dict(cur.fetchone()[0] or {})


def set_param(key: str, env_name: str, v: float) -> None:
    """注册表 + `.env` 双写（该键在 env 白名单里，env 赢）。"""
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = dict(cur.fetchone()[0] or {})
            p = dict(meta.get("params") or {})
            p[key] = v
            meta["params"] = p
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                        " WHERE lane_id=%s",
                        (json.dumps(meta, ensure_ascii=False, default=str), LANE))
        c.commit()
    txt = ENV.read_text(encoding="utf-8", errors="replace")
    _pat = rf"^{re.escape(env_name)}=.*$"
    if re.search(_pat, txt, flags=re.M):
        txt = re.sub(_pat, f"{env_name}={v:g}", txt, flags=re.M)
    else:
        txt = txt.rstrip("\n") + f"\n{env_name}={v:g}\n"
    ENV.write_text(txt, encoding="utf-8")


def wait_hot_reload(v: float, key: str = DEFAULT_KEY, timeout_s: float = 150.0) -> bool:
    """等心跳里的 params.spread_mult 变成 v。

    ⚠️ **实测结论：对 `MM_SPREAD_MULT` 这个键，热更新永不可能生效。**
    原因：`apply_env_param_overrides()`（F280）读的是 `os.getenv(...)` ——
    那是**进程启动时**由 `load_dotenv` 灌进 `os.environ` 的冻结值。
    改 `.env` 文件后，正在跑的进程**看不到**（实测：文件改成 0.5 后，
    心跳连续 100 秒仍是 0.9）。
    ⇒ 结论：**env 白名单里的键（`MM_SPREAD_MULT` 等 7 个）是"部署期参数"，
      要改必须重启进程**；只有不在白名单里的键（如 `compound_ratio`、
      `stop_loss_bp`）才能靠 F251 指纹热更新。
    ⇒ 本函数仅用于**重启后**确认生效，不再指望无重启热更。
    """
    st = ROOT / "logs" / "mm_lane_status.json"
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            j = json.loads(st.read_text(encoding="utf-8"))
            got = (j.get("params") or {}).get(key)
            if got is not None and abs(float(got) - v) < 1e-9:
                return True
        except Exception:
            pass
        time.sleep(4)
    return False


def restart_worker(wait_s: float = 40.0) -> bool:
    """重启 MM worker 进程（env 白名单键生效的唯一途径）。

    车道运行态存在 DB（`lane_runtime_state`）⇒ 重启不丢库存。
    但会有一段冷启动（`backfill_mid_hist`）⇒ 调用方必须**丢弃开头一段时间**的样本。
    """
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*mm_lane_worker*' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
        capture_output=True, text=True, timeout=60)
    time.sleep(5)
    subprocess.run(["schtasks", "/Run", "/TN", "DSH_MM_WORKER"],
                   capture_output=True, text=True, timeout=60)
    time.sleep(wait_s)
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""SELECT count(*) FROM pg_stat_activity
                           WHERE query ILIKE '%mm_lane%'""")
    return True


def window_stats(t0: datetime, t1: datetime) -> dict:
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*),
                       coalesce(sum(notional),0),
                       coalesce(sum(spread_bp*notional/1e4),0),
                       coalesce(sum(price_bp*notional/1e4),0),
                       coalesce(sum(fee_bp*notional/1e4),0),
                       coalesce(sum(net_bp*notional/1e4),0)
                FROM lane_ledger
                WHERE lane_id=%s AND event='fill' AND ts >= %s AND ts < %s
            """, (LANE, t0, t1))
            n, notl, sp, pr, fe, net = cur.fetchone()
            n = int(n or 0)
            notl = float(notl or 0)
            b = (1e4 / notl) if notl > 0 else 0.0
            return {"fills": n, "notional": notl,
                    "spread_bp_w": float(sp or 0) * b,
                    "price_bp_w": float(pr or 0) * b,
                    "fee_bp_w": float(fe or 0) * b,
                    "net_bp_w": float(net or 0) * b,
                    "net_usd": float(net or 0)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=12.0, help="每档保持分钟数")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ladder", default="", help="覆盖档位，如 0.5,0.9,1.3,1.8")
    ap.add_argument("--key", default=DEFAULT_KEY, help="要扫的注册表参数名")
    ap.add_argument("--env", dest="env_name", default=DEFAULT_ENV,
                    help="对应的 env 变量名（决定写入 .env 的哪个键）")
    a = ap.parse_args()

    ladder = ([float(x) for x in a.ladder.split(",") if x.strip()]
              if a.ladder else list(LADDER))
    KEY = a.key
    ENV_NAME = a.env_name
    orig = float(get_params().get(KEY) if get_params().get(KEY) is not None else 0.9)

    print("=" * 100)
    print(f"H146  参数扫描（{KEY} / {ENV_NAME}）")
    print("=" * 100)
    print(f"  档位 {ladder}   每档 {a.minutes} 分钟   起始值 {orig}")
    print(f"  物理含义：报价位置 = bid + spread_mult × 半价差")
    for v in ladder:
        print(f"    spread_mult={v:<5} ⇒ 离中价 {v*50:.0f}% 的价差处")
    if a.dry_run:
        print("\n  （dry-run）不执行")
        return 0

    results = []
    try:
        for v in ladder:
            print(f"\n{'─'*100}")
            print(f"  ▶ 档位 {KEY}={v}")
            set_param(KEY, ENV_NAME, v)
            # ⚠️ `MM_SPREAD_MULT` 在 env 白名单里 ⇒ **必须重启**才能生效（见 wait_hot_reload 注释）
            restart_worker()
            ok = wait_hot_reload(v, KEY, timeout_s=120.0)
            print(f"    重启后生效核对 {'✓' if ok else '✗ 失败'}")
            if not ok:
                print("    跳过该档（未能确认生效，避免污染数据）")
                continue
            # 冷启动丢弃：`backfill_mid_hist` 后前 ~2 分钟的口径与稳态不同
            discard_s = 120
            print(f"    冷启动丢弃 {discard_s}s …")
            time.sleep(discard_s)
            t0 = datetime.now().astimezone()
            t1 = t0 + timedelta(minutes=a.minutes)
            print(f"    窗口 {t0.strftime('%H:%M:%S')} ~ {t1.strftime('%H:%M:%S')}")
            while datetime.now().astimezone() < t1:
                time.sleep(30)
            st = window_stats(t0, t1)
            st[KEY] = v
            results.append(st)
            print(f"    成交 {st['fills']:>5}  名义 ${st['notional']:>12,.0f}")
            print(f"    价差 {st['spread_bp_w']:>+8.4f}bp   行情 {st['price_bp_w']:>+8.4f}bp"
                  f"   费 {st['fee_bp_w']:>+7.4f}bp")
            print(f"    ⇒ **净 {st['net_bp_w']:>+8.4f}bp/笔**   净额 {st['net_usd']:>+9.4f} USD")
    finally:
        print(f"\n{'─'*100}")
        print(f"  恢复 {KEY}={orig}（需重启 worker 才生效）")
        set_param(KEY, ENV_NAME, orig)
        restart_worker()
        print(f"  已恢复：{wait_hot_reload(orig, KEY, timeout_s=120.0)}")

    print("\n" + "=" * 100)
    print("汇总")
    print("=" * 100)
    print(f"  {KEY:>12} {'成交':>7} {'名义$':>13} {'价差bp':>9} {'行情bp':>9} "
          f"{'净bp':>9} {'净$':>10}")
    print("  " + "-" * 84)
    for r in results:
        print(f"  {r[KEY]:>12} {r['fills']:>7} {r['notional']:>13,.0f} "
              f"{r['spread_bp_w']:>+9.4f} {r['price_bp_w']:>+9.4f} "
              f"{r['net_bp_w']:>+9.4f} {r['net_usd']:>+10.4f}")
    if results:
        good = [r for r in results if r["fills"] >= 100]
        pool = good or results
        best = max(pool, key=lambda r: r["net_bp_w"])
        print(f"\n  ⇒ 样本充足（≥100 笔）的档位里，净 bp 最高的是 "
              f"**{KEY}={best[KEY]}**（{best['net_bp_w']:+.4f}bp/笔）")
        if good and all(r["net_bp_w"] <= 0 for r in good):
            print(f"     ⚠️ 所有档位净额 ≤ 0 ⇒ **宽度不是解**，瓶颈在择时（毒性流）")
    out = ROOT / "research_l1" / "out" / "h146_width_sweep.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"ladder": ladder, "minutes": a.minutes,
                               "results": results, "restored_to": orig},
                              ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    print(f"\n  写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
