"""h598 — ① 的**运行态验证**取数（只读，R147）。

objective 要求每一项都做"部署后读运行时状态验证"。① 是**保留**（未改参数），
故其运行态验证 = 两件事：
  1. `reversal_decay` 出场路径**现在仍在发生**（纪元内计数 + 最近一次时间）；
  2. 与它相关的参数**没有被改动过**（注册表里相关的键现值）；
  3. 顺带对照 `trail_lock`（同族出场）与"已被修掉的旧路径"（`ofi_flatten_taker` 应≈0 ✓）。

用法：python scripts/h598_item1_runtime.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import importlib.util
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

ERA_SINCE = "2026-09-28T05:00:00+00:00"
PATHS = ("reversal_decay", "trail_lock", "ofi_flatten", "timeout_hard", "take_profit")


def main() -> int:
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        params = dict(meta.get("params") or {})
        print("=" * 92)
        print("① 运行态验证（reversal_decay 出场：保留，未改参数）")
        print("=" * 92)
        print(f"  注册表里与出场/衰减有关的键（共 {len(params)} 键中筛出）：")
        hit = {k: v for k, v in params.items()
               if any(t in k.lower() for t in ("reversal", "decay", "trail", "exit",
                                               "skew", "hold", "timeout"))}
        for k in sorted(hit):
            print(f"    {k} = {hit[k]}")
        print("\n  纪元内各出场路径的腿数 / 净额 / 最近一次：")
        for p in PATHS:
            cur.execute(
                "SELECT count(*), COALESCE(sum(net_bp*notional/1e4),0)::float8, max(ts)"
                " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
                " AND meta_json->>'exit_path' LIKE %s",
                (h.LANE, ERA_SINCE, f"{p}%%"))
            n, net, mx = cur.fetchone()
            print(f"    {p:<16} {n:>4} 腿  净 {net:>+8.2f}$  最近 {mx}")
        print("\n  ⇒ 判读：`reversal_decay` 腿数 >0 且最近时间接近当下 ⇒ 该路径**仍在工作** ✓；"
              "相关参数未被改动 ⇒ ① 的'保留'是**持续性**结论，不是一次性判定 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
