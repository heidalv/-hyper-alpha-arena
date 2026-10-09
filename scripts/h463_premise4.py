"""h463 前提检查 ④（只读）：直接读运行态，量出"45s 加仓封锁"到底管到多少仓。

依据（runner.py:1289-1333）：
  持仓年龄 > `_effective_hold_sec()` 时，若 `timeout_exit_maker_only=True`：
    · 年龄 ≤ `timeout_hard_taker_sec`(300s) ⇒ **只封锁加仓侧**、继续挂减仓单，
      并把 `state.timeout_exit_blocked += 1`（每个 tick 一次）；
    · 年龄 > 300s ⇒ 无条件 taker（`timeout_hard_taker`）。
  `_effective_hold_sec` = P1→60s / P45→300s / 其余→`max_one_side_seconds`（刚由 45 改 90）。

因此 `timeout_exit_blocked` 的**增速**就是"超过有效持有期"的暴露强度，
而 `pattern_tag` 的取值分布直接给出"未被形态标记"（即 45/90 参数真正管辖）的仓位占比。

用法：python scripts/h463_premise4.py            # 打印 + 追加快照
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
SNAP = ROOT / "research_l1" / "out" / "h463_runtime_snapshots.jsonl"


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


def main() -> int:
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            m = cur.fetchone()[0]
            p = dict(m.get("params") or {})
            print("timeout_exit_maker_only =", p.get("timeout_exit_maker_only"),
                  "| maker_only =", p.get("maker_only"),
                  "| max_one_side_seconds =", p.get("max_one_side_seconds"),
                  "| timeout_hard_taker_sec =", p.get("timeout_hard_taker_sec"))
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='lane_runtime_state' ORDER BY ordinal_position")
            _cols = [r[0] for r in cur.fetchall()]
            print("lane_runtime_state 列:", ", ".join(_cols))
            _symcol = "symbol" if "symbol" in _cols else None
            if _symcol:
                cur.execute(
                    f"SELECT {_symcol}, state_json FROM lane_runtime_state "
                    "WHERE lane_id=%s", (LANE,))
                _rows = cur.fetchall()
            else:
                cur.execute("SELECT state_json FROM lane_runtime_state WHERE lane_id=%s",
                            (LANE,))
                _one = cur.fetchone()
                _rows = [(None, _one[0])] if _one else []
            print(f"运行态行数: {len(_rows)}")
            st = {}
            for _s, _j in _rows:
                j = _j or {}
                st[str(_s or j.get("symbol") or "?")] = j
    now = time.time()
    rows = []
    print("=" * 78)
    print(f"{'sym':6s} {'qty':>12s} {'age_s':>8s} {'tag':>5s} {'teb':>7s} {'stop':>6s}")
    for sym, v in sorted(st.items()):
        if not isinstance(v, dict):
            continue
        qty = float(v.get("qty") or 0.0)
        opened = float(v.get("opened_ts") or 0.0)
        age = (now - opened) if opened > 0 and abs(qty) > 1e-12 else 0.0
        rows.append({"sym": sym, "qty": qty, "age_s": round(age, 1),
                     "tag": str(v.get("pattern_tag") or ""),
                     "teb": int(v.get("timeout_exit_blocked") or 0),
                     "opened_ts": opened})
        print(f"{sym:6s} {qty:12.4f} {age:8.0f} {str(v.get('pattern_tag') or '-'):>5s} "
              f"{int(v.get('timeout_exit_blocked') or 0):7d} {v.get('stop_since')}")
    snap = {"ts": now, "iso": time.strftime("%Y-%m-%dT%H:%M:%S"), "states": rows,
            "params": {"max_one_side_seconds": p.get("max_one_side_seconds"),
                       "p1_hold_sec": p.get("p1_hold_sec"),
                       "p45_hold_sec": p.get("p45_hold_sec"),
                       "timeout_exit_maker_only": p.get("timeout_exit_maker_only")}}
    with SNAP.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap, ensure_ascii=False) + "\n")
    # 与上一快照比较 teb 增速
    if SNAP.exists():
        lines = [ln for ln in SNAP.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if len(lines) >= 2:
            prev = json.loads(lines[-2])
            dt = snap["ts"] - prev["ts"]
            pm = {r["sym"]: r for r in prev["states"]}
            print("=" * 78)
            print(f"与上一快照间隔 {dt/60:.1f} min 的 `timeout_exit_blocked` 增量：")
            for r in rows:
                pv = pm.get(r["sym"])
                if pv and pv.get("teb") is not None:
                    d = r["teb"] - int(pv["teb"])
                    print(f"  {r['sym']:6s} +{d:6d}  ({d/max(dt/3600,1e-9):8.0f}/h)")
    print("已追加快照:", SNAP.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
