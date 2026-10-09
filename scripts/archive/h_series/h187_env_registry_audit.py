# -*- coding: utf-8 -*-
"""H187 环境变量 vs 注册表 —— 弄清两个真相源差在哪，然后消掉差异。

为什么必须做这一步
==================

P2 的目标是让 LLM 调整参数**不重启就生效**（快速闭环）。

但 `apply_env_param_overrides`（F280）里有一个 7 键白名单：

    spread_mult          <- MM_SPREAD_MULT
    spread_mult_reduce   <- MM_SPREAD_MULT_REDUCE
    min_edge_frac        <- MM_MIN_EDGE_FRAC
    spread_cross_margin  <- MM_SPREAD_CROSS_MARGIN
    w_base_bp            <- MM_W_BASE_BP
    min_width_bp         <- MM_MIN_WIDTH_BP
    k_inv                <- MM_K_INV

这些键读的是 `os.getenv`，**进程启动时冻结**。后果：

  · 写注册表 → 不生效（env 赢）
  · 改 .env 文件 → 对已运行进程也不生效（要重启）
  · 二者不一致时，任何人读注册表都会得到**错误前提**

而 5 个可调参数里有 4 个在这个白名单里
（能热更新的只有 `take_profit_bp`）。

⇒ 必须先把注册表**同步成 env 的实际生效值**，
   这样接下来把白名单清空时，**行为变化为零**（纯机制改动）。

判据
====

本脚本只做一件事：逐键比较 `os.getenv` 值（冻结的真相）
与注册表值，列出分叉；`--sync` 时把注册表写成 env 值。

用法：
    .venv\\Scripts\\python.exe scripts\\h187_env_vs_registry.py
    .venv\\Scripts\\python.exe scripts\\h187_env_vs_registry.py --sync
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

LANE = "mm_asterdex"


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def dotenv_map() -> dict:
    out = {}
    p = ROOT / ".env"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def whitelist() -> dict:
    from h149_effective_check import env_whitelist as _wl
    return _wl()


def read_registry() -> dict:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            row = cur.fetchone()
    meta = dict(row[0] or {}) if row else {}
    return dict(meta.get("params") or {})


def write_registry(params: dict) -> None:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = dict(cur.fetchone()[0] or {})
            meta["params"] = params
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() WHERE lane_id=%s",
                        (json.dumps(meta, ensure_ascii=False, default=str), LANE))
        c.commit()


def heartbeat() -> dict:
    try:
        return json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _f(x):
    try:
        return float(x)
    except Exception:
        return None


def _s(x):
    if x is None:
        return "-"
    try:
        return f"{float(x):g}"
    except Exception:
        return str(x)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", action="store_true",
                    help="write env values into the registry (kill divergence)")
    a = ap.parse_args()

    wl = whitelist()
    env_file = dotenv_map()
    reg = read_registry()
    hb = heartbeat()
    live = dict(hb.get("params") or {})
    for k, v in dict(hb.get("limits") or {}).items():
        if k not in live:
            live[k] = v

    print("=" * 96)
    print("H187 env vs registry")
    print("=" * 96)
    if not hb:
        print("  [warn] heartbeat missing -> live column blank")

    print(f"  {'key':<22}{'env_var':<26}{'dotenv':>10}{'registry':>12}"
          f"{'live':>14}   verdict")
    print("  " + "-" * 92)

    diverged = []
    sync_to = {}
    for key, ev in sorted(wl.items()):
        raw_file = env_file.get(ev)
        raw_reg = reg.get(key)
        raw_live = live.get(key)

        v_file, v_reg = _f(raw_file), _f(raw_reg)

        if raw_file is None:
            verdict = "env unset -> registry is authority"
        elif v_file is not None and v_reg is not None and abs(v_file - v_reg) < 1e-9:
            verdict = "in sync"
        else:
            verdict = "DIVERGED (env wins)"
            diverged.append(key)
            if v_file is not None:
                sync_to[key] = v_file

        print(f"  {key:<22}{ev:<26}{_s(raw_file):>10}{_s(raw_reg):>12}"
              f"{_s(raw_live):>14}   {verdict}")

    print()
    print(f"  divergent keys: {len(diverged)} {diverged if diverged else ''}")

    tunable = ("spread_mult", "spread_mult_reduce", "min_edge_frac",
               "min_width_bp", "k_inv")
    hot = sorted(k for k in tunable if k not in wl)
    bound = sorted(k for k in tunable if k in wl)
    print(f"  tunable + hot-updatable : {hot}")
    print(f"  tunable but restart-bound: {bound}")

    if not a.sync:
        print()
        print("  dry run. add --sync to write env values into the registry.")
        return 0

    if not sync_to:
        print()
        print("  nothing to sync (already consistent).")
        return 0

    new_params = dict(reg)
    new_params.update(sync_to)
    write_registry(new_params)
    print()
    print(f"  synced {len(sync_to)} keys into registry: {sync_to}")
    print("  -> removing env overrides is now a NO-OP (registry already equals env).")
    print("  -> re-run without --sync to confirm 0 divergences.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
