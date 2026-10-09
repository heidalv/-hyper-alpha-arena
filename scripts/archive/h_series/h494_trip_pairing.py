"""h494：**可靠的往返配平**——用带符号 qty 累计归零重建持仓周期（不依赖 position_id）。

为什么必须换口径：
  · `lane_ledger.position_id` 形如 `mm:BNB:1` 会被**反复复用**
    （近 3h：213 行只有 12 个不同 id），`position_id_state` 恒为 `'paired'`；
  · 早期脚本（h461/h469/h470/h489）用"按 position_id 的顺序状态机"配平，
    结果出现过"硬顶出场的持仓 1467s / 2772s"这种与 300s 硬顶**自相矛盾**的样本。

新口径（只用账本自身字段，**不需要改引擎**）：
  逐 symbol 按时间遍历所有成交行，维护**带符号累计持仓**
      cum += (+qty if side=='buy' else −qty)      # qty 取绝对值
  一次"往返"= 从空仓开始、到累计回到 ≈0 为止的一段；
  段内的**非 flatten 行 = 开仓/加仓腿**，flatten 行 = 出场腿。
  这样得到的 `trip_id = (symbol, 段起点 ts)` 是**稳定且唯一**的往返键。

自检（本脚本会打印）：
  · flatten 行处 `|cum| ≈ 0` 的比例（应接近 100%；不为 0 说明有部分平仓/漏行）；
  · 每个往返的腿数、持仓时长分布（应落在 30s–5min 量级）；
  · 与旧口径（position_id 状态机）对比，列出**两者不一致**的往返。

用法：python scripts/h494_trip_pairing.py [--hours 6]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h494_trip_pairing.json"
EPS = 1e-6


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


def load(hours: float):
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                # ⚠️ `lane_ledger` **没有** side/qty 列：两者都在 `meta_json` 里
                # （列只有 id/lane_id/symbol/position_id/event/ts/notional/…
                #   /net_bp/meta_json/position_id_state）。
                "SELECT ts, symbol, COALESCE(meta_json->>'side',''), "
                "ABS(COALESCE((meta_json->>'qty')::float8,0))::float8, "
                "COALESCE(meta_json->>'exit_path',''), net_bp::float8, "
                "notional::float8, position_id "
                "FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - make_interval(hours => %s::int) "
                "ORDER BY symbol, ts", (LANE, int(hours)))
            return cur.fetchall()


def build_trips(rows):
    """带符号累计归零 ⇒ 往返。返回 (trips, 诊断)。"""
    by_sym: dict = collections.OrderedDict()
    for r in rows:
        by_sym.setdefault(r[1], []).append(r)
    trips, diag = [], {"flatten_at_zero": 0, "flatten_nonzero": 0, "unclosed": 0}
    for sym, rs in by_sym.items():
        cum = 0.0
        cur_trip = None
        for ts, _s, side, qty, ep, net, noti, pid in rs:
            signed = qty if str(side).lower() == "buy" else -qty
            flatten = ep != ""
            if cur_trip is None:
                cur_trip = {"sym": sym, "open_ts": ts, "legs": 0, "entry_legs": 0,
                            "exit_legs": 0, "entry_notional": 0.0, "cost": 0.0,
                            "qty": 0.0, "exit_ts": None, "exit_net_bp": None,
                            "exit_path": "", "pids": set()}
            cum += signed
            cur_trip["legs"] += 1
            cur_trip["pids"].add(pid)
            if not flatten:
                cur_trip["entry_legs"] += 1
                cur_trip["qty"] += qty
                cur_trip["cost"] += qty * 0.0        # 价格不在本查询里，仅记名义
                cur_trip["entry_notional"] += float(noti or 0.0)
            else:
                cur_trip["exit_legs"] += 1
                cur_trip["exit_ts"] = ts
                cur_trip["exit_net_bp"] = float(net or 0.0)
                cur_trip["exit_path"] = ep
                if abs(cum) < 1e-3:
                    diag["flatten_at_zero"] += 1
                else:
                    diag["flatten_nonzero"] += 1
            if abs(cum) < 1e-3 and cur_trip["exit_legs"] > 0:
                cur_trip["hold"] = (cur_trip["exit_ts"] - cur_trip["open_ts"]).total_seconds()
                cur_trip["pids"] = sorted(str(p) for p in cur_trip["pids"])
                trips.append(cur_trip)
                cur_trip = None
        if cur_trip is not None and cur_trip["legs"] > 0:
            diag["unclosed"] += 1
            cur_trip["hold"] = None
            cur_trip["pids"] = sorted(str(p) for p in cur_trip["pids"])
            trips.append(cur_trip)
    return trips, diag


def old_pairing(rows):
    """旧口径（按 position_id 状态机），仅用于对比。"""
    seq, trips = {}, []
    for ts, sym, side, qty, ep, net, noti, pid in rows:
        d = seq.setdefault((pid, sym), {"open": None})
        if ep == "":
            if d["open"] is None:
                d["open"] = ts
        elif d["open"] is not None:
            trips.append({"sym": sym, "open_ts": d["open"], "exit_ts": ts,
                          "hold": (ts - d["open"]).total_seconds()})
            d["open"] = None
    return trips


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    a = ap.parse_args()
    rows = load(a.hours)
    print(f"近 {a.hours:.0f}h 成交行 {len(rows)}")
    trips, diag = build_trips(rows)
    closed = [t for t in trips if t["hold"] is not None]
    print("=" * 92)
    print("自检：")
    print(f"  flatten 处累计≈0（正常收口）= {diag['flatten_at_zero']}，"
          f"≠0（部分平仓/缺行）= {diag['flatten_nonzero']}，"
          f"未收口段 = {diag['unclosed']}")
    print(f"  重建往返 = {len(trips)}（其中已收口 {len(closed)}）")
    if closed:
        holds = sorted(t["hold"] for t in closed)
        legs = [t["legs"] for t in closed]
        print(f"  持仓时长 p10/p50/p90/max = {holds[int(.1*(len(holds)-1))]:.0f}/"
              f"{st.median(holds):.0f}/{holds[int(.9*(len(holds)-1))]:.0f}/{holds[-1]:.0f} s")
        print(f"  单往返腿数 中位={st.median(legs):.0f} 均值={st.mean(legs):.2f} max={max(legs)}")
        over = sum(1 for h in holds if h > 320)
        print(f"  持仓 >320s 的往返 = {over}（合规硬顶 300s ⇒ 应≈0，"
              f"非 0 说明仍有配平误差或跨桶）")
        # 出场路径分布
        paths = collections.Counter(t["exit_path"] for t in closed)
        print("  出场路径:", dict(paths.most_common(8)))
    # 与旧口径对比
    old = old_pairing(rows)
    print("=" * 92)
    print(f"旧口径（position_id 状态机）往返 = {len(old)}")
    old_holds = sorted(t["hold"] for t in old)
    if old_holds:
        print(f"  旧口径持仓时长 中位={st.median(old_holds):.0f}s "
              f"p90={old_holds[int(.9*(len(old_holds)-1))]:.0f}s "
              f"max={old_holds[-1]:.0f}s")
        bad = sum(1 for h in old_holds if h > 320 or h < 0)
        print(f"  旧口径里 >320s 或 <0 的**异常往返 = {bad}**"
              f"（新口径同口径下应为 0 ⇒ 这就是旧口径不可用的直接证据）")
    verdict = ("新口径可用" if (closed and sum(
        1 for t in closed if t["hold"] > 320) == 0) else "新口径仍需检查（有 >320s 往返）")
    print("\n⇒ 裁决:", verdict)
    OUT.write_text(json.dumps(
        {"hours": a.hours, "rows": len(rows), "trips": len(trips),
         "closed": len(closed), "diag": diag,
         "hold_p50": (st.median([t["hold"] for t in closed]) if closed else None),
         "old_trips": len(old), "verdict": verdict,
         "sample": [{k: (v if k != "pids" else v) for k, v in t.items()}
                    for t in closed[:30]]}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
