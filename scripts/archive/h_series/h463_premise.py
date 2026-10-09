"""h463 前提检查（只读）：max_one_side_seconds 45s 上限到底约束了哪些腿？

问题：`_effective_hold_sec` 只对**没有 P1/P45 形态标记**的状态用 `max_one_side_seconds`
（P1→p1_hold_sec=60s，P45→p45_hold_sec=300s）。若绝大多数腿都带形态标记、
或持仓根本到不了 45s，则 45→90 是一个近乎无效的改动 ⇒ 12h 试跑白等。

本脚本只读，回答三问：
  Q1 近 24h 各 pattern_tag 的腿数与占比？
  Q2 各 tag 的持仓时长分布（p50/p75/p90/max），有多少腿 ≥40s（即真的贴上界）？
  Q3 exit_path 里"上界触发"类出场的实际发生次数？

用法：python scripts/h463_premise.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h463_premise.json"


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


def q(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()


def main() -> int:
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='lane_ledger' ORDER BY ordinal_position")
            cols = [r[0] for r in cur.fetchall()]
            print("lane_ledger 列:", ", ".join(cols))
            print("=" * 70)

            # 近 24h 每腿：pattern_tag / exit_path / 持仓秒数
            cur.execute(
                "SELECT meta_json, ts FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - interval '24 hours' ORDER BY ts", (LANE,))
            rows = cur.fetchall()
            print(f"近 24h 腿数: {len(rows)}")
            if not rows:
                return 1
            keys = set()
            for m, _ in rows[:200]:
                if isinstance(m, dict):
                    keys |= set(m)
            print("meta_json 键:", sorted(keys))
            print("=" * 70)

            by_tag: dict = {}
            hold_all = []
            for m, _ts in rows:
                m = m if isinstance(m, dict) else {}
                tag = str(m.get("pattern_tag") or "") or "(空)"
                hold = None
                for k in ("hold_sec", "age_sec", "hold_seconds", "held_sec",
                          "duration_sec", "pos_age_sec"):
                    if m.get(k) is not None:
                        try:
                            hold = float(m[k])
                        except Exception:
                            hold = None
                        break
                d = by_tag.setdefault(tag, {"n": 0, "holds": [], "paths": {}})
                d["n"] += 1
                p = str(m.get("exit_path") or "(空)")
                d["paths"][p] = d["paths"].get(p, 0) + 1
                if hold is not None:
                    d["holds"].append(hold)
                    hold_all.append(hold)

            def pct(v, q_):
                if not v:
                    return None
                v = sorted(v)
                i = min(len(v) - 1, int(q_ * (len(v) - 1)))
                return v[i]

            print("Q1/Q2 分形态：腿数 / 持仓秒数分布 / ≥40s 占比")
            for tag, d in sorted(by_tag.items(), key=lambda kv: -kv[1]["n"]):
                h = d["holds"]
                ge40 = sum(1 for x in h if x >= 40.0)
                print(f"  {tag:8s} n={d['n']:5d} "
                      f"hold(p50/p75/p90/max)="
                      f"{pct(h,.5)}/{pct(h,.75)}/{pct(h,.9)}/{max(h) if h else None} "
                      f"≥40s={ge40} ({100.0*ge40/max(len(h),1):.1f}%)")
            print("=" * 70)
            print("Q3 出场路径 Top（近 24h）")
            paths: dict = {}
            for _m, _ts in rows:
                _m = _m if isinstance(_m, dict) else {}
                p = str(_m.get("exit_path") or "(空)")
                paths[p] = paths.get(p, 0) + 1
            for p, n in sorted(paths.items(), key=lambda kv: -kv[1])[:15]:
                print(f"  {p:34s} {n}")
            print("=" * 70)
            hw = [x for x in hold_all if x >= 40.0]
            print(f"全样本持仓可用秒数: n={len(hold_all)}  ≥40s: {len(hw)} "
                  f"({100.0*len(hw)/max(len(hold_all),1):.1f}%)")
            OUT.write_text(json.dumps(
                {"legs_24h": len(rows),
                 "by_tag": {k: {"n": v["n"], "holds": len(v["holds"]),
                                "ge40": sum(1 for x in v["holds"] if x >= 40.0),
                                "paths": v["paths"]} for k, v in by_tag.items()},
                 "paths": paths, "hold_n": len(hold_all), "hold_ge40": len(hw)},
                ensure_ascii=False, indent=2), encoding="utf-8")
            print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
