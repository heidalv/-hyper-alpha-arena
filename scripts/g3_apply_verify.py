# -*- coding: utf-8 -*-
"""G3 应用与生效核对 —— 改完必须证明"真的生效了"，否则回滚。

为什么这是守卫链的最后一环
==========================

历史事故（本仓库反复出现，同一根因）：**"改了但没生效"**。

  · F189 模型以为挂 12bp，实盘实际只挂 5.5bp
  · F280 env 与注册表两个真相源，env 赢
  · F287 注册表 0.3 / 消费者读 0.95
  · F292 同一类
  · **本会话实测**：把 `.env` 的 `MM_SPREAD_MULT` 改成 0.5 后，
    worker 心跳**连续 100 秒仍是 0.9** —— 因为 `apply_env_param_overrides`
    读的是 `os.getenv`（进程启动时冻结值），**改文件对已运行进程无效**。

⇒ 自动循环里若"写了注册表就当改好了"，会出现**静默偏差**：
LLM 以为调了参、实际没生效，而后续所有归因都建立在错误前提上。

# 本模块做三件事

  1. `needs_restart(key)` —— 该键是否在 env 白名单里（须重启才生效）
  2. `apply_param(key, value)` —— 写注册表（**本阶段只写注册表，不重启**）
  3. `verify(key, expect, timeout)` —— 读**运行态心跳**确认；失败则**自动回滚**

# 为什么失败要自动回滚

写进注册表的"意图"若没生效，留在那里会成为**下一次归因的污染源**
（下次读注册表会以为参数是新的，而实盘跑的是旧值）。
⇒ **验证不通过就写回原值**，让注册表始终等于实盘。

用法：
    .venv\\Scripts\\python.exe scripts\\g3_apply_verify.py            # 自检
    .venv\\Scripts\\python.exe scripts\\g3_apply_verify.py --dry-run --key spread_mult --value 0.45
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
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


def env_whitelist() -> dict:
    """参数 -> env 变量。**不在白名单里的键可热更新；在白名单里的必须重启。**"""
    try:
        from h149_effective_check import env_whitelist as _wl
        return _wl()
    except Exception:
        return {}


def needs_restart(key: str) -> bool:
    return key in env_whitelist()


def read_registry_param(key: str):
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'params' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            row = cur.fetchone()
    return (dict(row[0] or {}) if row else {}).get(key)


def write_registry_param(key: str, value) -> None:
    """只写注册表。**不写 `.env`** —— 那会让"环境变量赢"的键变成必须重启，
    而 P2 的目标是让调整**不重启就生效**（快速闭环）。"""
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = dict(cur.fetchone()[0] or {})
            p = dict(meta.get("params") or {})
            p[key] = value
            meta["params"] = p
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                        " WHERE lane_id=%s",
                        (json.dumps(meta, ensure_ascii=False, default=str), LANE))
        c.commit()


def read_effective(key: str):
    """读**运行态心跳**（事实）里的生效值。params 优先，其次 limits。"""
    try:
        j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    for sec in ("params", "limits"):
        d = j.get(sec)
        if isinstance(d, dict) and key in d:
            return d[key]
    return None


def apply_and_verify(key: str, value, *, timeout_s: float = 150.0,
                     dry_run: bool = False) -> dict:
    """写注册表 → 核对生效 → 失败自动回滚。

    返回 `{ok, applied, verified, rolled_back, old, new, effective, note}`。
    """
    old = read_registry_param(key)
    eff_before = read_effective(key)
    out = {"key": key, "old": old, "new": value, "effective_before": eff_before,
           "applied": False, "verified": False, "rolled_back": False, "note": ""}

    if needs_restart(key):
        out["note"] = (
            "该键在 env 白名单里（" + str(env_whitelist().get(key)) + "）"
            "⇒ 改注册表**不会生效**（apply_env_param_overrides 读 os.getenv，"
            "进程启动时冻结）⇒ **必须重启 worker 才能生效**。")
        out["requires_restart"] = True
        if dry_run:
            return out
        # 仍写注册表（保持意图一致），但**明确标注未能验证**，不声称成功
        write_registry_param(key, value)
        out["applied"] = True
        out["note"] += " 已写注册表但**必须重启 worker 才生效**；本阶段不自动重启。"
        return out

    out["requires_restart"] = False
    if dry_run:
        return out

    write_registry_param(key, value)
    out["applied"] = True

    # 核对：读心跳（F251 指纹 60s 周期 ⇒ 给 150s 余量）
    import time
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        v = read_effective(key)
        try:
            if v is not None and abs(float(v) - float(value)) < 1e-9:
                out["verified"] = True
                out["effective"] = v
                out["note"] = f"心跳已确认生效（{time.time()-t0:.0f}s）"
                return out
        except Exception:
            pass
        time.sleep(4)

    # 未生效 ⇒ **回滚**，避免注册表与实盘分叉污染后续归因
    out["effective"] = read_effective(key)
    if old is not None:
        write_registry_param(key, old)
        out["rolled_back"] = True
        out["note"] = (f"{timeout_s:.0f}s 内未生效（心跳读到 {out['effective']!r}）"
                       f"⇒ 已回滚为 {old!r}")
    else:
        out["note"] = (f"{timeout_s:.0f}s 内未生效（心跳读到 {out['effective']!r}）"
                       f"，且无旧值可回滚 —— 需人工检查")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="")
    ap.add_argument("--value", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    print("=" * 92)
    print("G3 应用与生效核对")
    print("=" * 92)
    wl = env_whitelist()
    print(f"  env 白名单（须重启才生效）：{len(wl)} 个键")
    for k, e in sorted(wl.items()):
        print(f"    {k:<24} {e}")
    print(f"  ⇒ 其余键改注册表即可热更新（F251 指纹，≤60s）")

    if a.key:
        try:
            v = float(a.value)
        except Exception:
            print(f"  --value 必须是数值")
            return 1
        print(f"\n  ── 执行：{a.key} = {v}（dry_run={a.dry_run}）──")
        r = apply_and_verify(a.key, v, dry_run=a.dry_run)
        print(f"    旧值 {r['old']}  生效前 {r.get('effective_before')}")
        print(f"    已写注册表 {r['applied']}   已验证 {r['verified']}   "
              f"已回滚 {r['rolled_back']}")
        print(f"    {r['note']}")
        return 0 if (a.dry_run or r["verified"] or r.get("requires_restart")) else 1

    print(f"\n  ── 自检：白名单键应被标为「需重启」 ──")
    for k in ("spread_mult", "spread_mult_reduce", "compound_ratio",
              "take_profit_bp", "stop_loss_bp"):
        nr = needs_restart(k)
        eff = read_effective(k)
        print(f"    {k:<24} 需重启={str(nr):<6} 当前生效={eff}")
    print(f"\n  ⇒ 自检判据：spread_mult/spread_mult_reduce 应为**需重启=True**；")
    print(f"     compound_ratio/take_profit_bp/stop_loss_bp 应为 False（可热更新）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
