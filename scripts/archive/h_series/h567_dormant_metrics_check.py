"""h567 — 休眠 SPEC 机制判据实现的**读数校验**（只读）。

背景：[R65] 给 `h425_repair_trial.py::_sub_stats` 补上了 4 个休眠 SPEC
（h426/h429/h432/h440）的机制口径实现。本脚本直接在真库上跑这些分支，
证明 SQL 可执行且数值合理——避免"写了实现但从未执行过"的假闭环。

用法：
    python scripts/h567_dormant_metrics_check.py
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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402

ERA_SINCE = "2026-09-28T05:00:00+00:00"   # 本地 09-28 13:00 = 现纪元起点
# 默认校验收口的 4 个休眠 SPEC；也可用命令行指定（如 `h527` 验证排队项）
KEYS = ["h426", "h429", "h432", "h440"]


def main() -> int:
    keys = sys.argv[1:] or KEYS
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    out: dict = {"era_since": ERA_SINCE, "now": now, "keys": keys, "subs": {}}
    with psycopg.connect(h.read_env_dsn()) as c:
        with c.cursor() as cur:
            meta = h._load_meta(cur)
            syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
            out["symbols"] = syms
            # 口径校准：账本里"强平/出场路径"的真实取值分布（避免判据用了不存在的键）
            cur.execute(
                "SELECT COALESCE(meta_json->>'exit_path','(null)') AS p, count(*)"
                " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
                " AND symbol = ANY(%s) GROUP BY 1 ORDER BY 2 DESC",
                (h.LANE, ERA_SINCE, syms))
            out["exit_path_dist"] = {str(r[0]): int(r[1]) for r in cur.fetchall()}
            print(f"[exit_path] {json.dumps(out['exit_path_dist'], ensure_ascii=False)}")
            cur.execute(
                "SELECT key, count(*) FROM lane_ledger,"
                " jsonb_each_text(meta_json) WHERE lane_id=%s"
                " AND ts > %s::timestamptz AND symbol = ANY(%s)"
                " GROUP BY 1 ORDER BY 2 DESC LIMIT 30",
                (h.LANE, ERA_SINCE, syms))
            out["meta_keys"] = {str(r[0]): int(r[1]) for r in cur.fetchall()}
            print(f"[meta_keys] {json.dumps(list(out['meta_keys']), ensure_ascii=False)}")
            # `flatten` 键的真实取值（用于确认它是否等价于"强平"）
            cur.execute(
                "SELECT COALESCE(meta_json->>'flatten','(null)') AS v, count(*),"
                " count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')"
                "   LIKE 'ofi_flatten%%') AS ofi"
                " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
                " AND symbol = ANY(%s) GROUP BY 1 ORDER BY 2 DESC",
                (h.LANE, ERA_SINCE, syms))
            out["flatten_key_dist"] = [
                {"value": str(r[0]), "legs": int(r[1]), "ofi_flatten_legs": int(r[2])}
                for r in cur.fetchall()]
            print(f"[flatten] {json.dumps(out['flatten_key_dist'], ensure_ascii=False)}")
            for k in keys:
                t = h._sub_stats(cur, k, ERA_SINCE, now, syms)
                out["subs"][k] = t
                print(f"[{k}] {json.dumps(t, ensure_ascii=False, default=str)}")
    p = ROOT / "research_l1" / "out" / (
        "h567_dormant_metrics_check.json" if keys == KEYS
        else f"h567_metrics_check_{'_'.join(keys)}.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写出 {p.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
