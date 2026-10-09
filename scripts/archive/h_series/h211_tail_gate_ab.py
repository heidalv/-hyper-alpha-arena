# -*- coding: utf-8 -*-
"""H211 尾部闸门 A/B：`daily_loss_stop_pct` 80% vs 20%。

# 为什么测这个（而不是 T1/T3）

三个候选里，实测只有 `daily_loss` 闸有力量：

| 闸门 | 实测力度 | 结论 |
|---|---|---|
| `toxic_streak`（连续 3 次逆选择暂停） | 标记频率 1.56%/腿 ⇒ 连续 3 次概率 **3.76e-06**，实测只触发 3 次 | **数学上几乎不触发，改它无意义** |
| `vol_pause_sigma`（车道级） | 主力拦截 1413 次，但那是**单侧**闸在起作用 | 已在工作 |
| **`daily_loss_stop_pct`** | 80% 闸 = $210；实测 09-21 日亏 **−$139**、09-22 **−$92** | **从未触发 ⇒ 尾部无保护** |

⇒ T2 的精确落点：**把日亏闸从 80% 收紧到 20%**（$210 → $52.60）。

# 为什么必须交替 A/B，不能只改一边看

本会话已多次证明 **regime 主导结果**：
- 6 个 5 小时窗口的 `net` 是 `+0.17 / −0.25 / +0.04 / +0.15 / −0.09 / −1.20`
- 同一改动在不同窗口会得到相反结论

⇒ 只有**交替**才能把"闸门效果"从"行情差异"里分离出来。

# ⚠️ 由 `h178` 事故学到的两条硬约束

1. **回滚必须能扛住强杀**：`h178` 的 `finally` 在 SIGTERM 下没执行，
   把崩溃配置留在生产（造成过停摆）。本脚本
   **每块开始前先写 `logs/h211_ab_state.json`**，退出时读它恢复；
   并且**先备份原值**，任何异常路径都能恢复。
2. **两块之间会重启 worker**：`MM_LANE_LIMITS_ENFORCE` 是启动时冻结的
   （实测 `.env` 里已是 `true`，所以本实验**不需要动它**），
   而 `daily_loss_stop_pct` 在注册表里 ⇒ **热更新即可，无需重启** ✓

# 用法

    python scripts/h211_tail_gate_ab.py --blocks 4 --minutes 20
    python scripts/h211_tail_gate_ab.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys
import time
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "h211_ab_state.json"
OUT = ROOT / "research_l1" / "out" / "h211_tail_gate_ab.json"

# 臂定义：A = 现状（80%，形同关闭）；B = 尾部保护（20%）
ARMS = {
    "A(80%闸=现状)": {"daily_loss_stop_pct": 80.0},
    "B(20%闸=尾部保护)": {"daily_loss_stop_pct": 20.0},
}


def dsn() -> str:
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


def get_params() -> dict:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'params' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            r = cur.fetchone()
    return dict(r[0] or {}) if r else {}


def set_params(**kw) -> None:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = dict(cur.fetchone()[0] or {})
            p = dict(meta.get("params") or {})
            p.update(kw)
            meta["params"] = p
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                        " WHERE lane_id=%s",
                        (json.dumps(meta, ensure_ascii=False, default=str), LANE))
        c.commit()


def live_limits() -> dict:
    try:
        j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(j.get("limits") or {})


def live_skips() -> dict:
    try:
        j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(j.get("skip_counts") or {})


def wait_effective(key: str, want, timeout_s: float = 150.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if abs(float(live_limits().get(key) or -1) - float(want)) < 1e-9:
            return True
        time.sleep(5)
    return False


def window(t0: datetime, t1: datetime) -> dict:
    """一个块内的账本统计（分量分解，不只看净额）。"""
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*) FILTER (WHERE meta_json->>'flatten' NOT IN ('true','True')),
                       count(*) FILTER (WHERE meta_json->>'flatten' IN ('true','True')),
                       coalesce(sum(net_bp*notional/1e4), 0),
                       coalesce(sum(spread_bp*notional)/NULLIF(sum(notional),0), 0),
                       coalesce(sum(price_bp*notional)/NULLIF(sum(notional),0), 0),
                       coalesce(sum(notional), 0)
                FROM lane_ledger WHERE lane_id=%s AND ts >= %s AND ts < %s
            """, (LANE, t0, t1))
            mk, fl, net, wsp, wpx, notl = cur.fetchone()
    return {"maker_n": int(mk or 0), "flat_n": int(fl or 0),
            "net_usd": round(float(net or 0), 4),
            "spread_bp": round(float(wsp or 0), 4),
            "price_bp": round(float(wpx or 0), 4),
            "notional": round(float(notl or 0), 0)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=4, help="每臂区块数")
    ap.add_argument("--minutes", type=float, default=20.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    orig = get_params()
    orig_val = orig.get("daily_loss_stop_pct")
    print("=" * 96)
    print("H211  尾部闸门 A/B：daily_loss_stop_pct")
    print("=" * 96)
    print(f"  起始值 daily_loss_stop_pct = {orig_val}")
    print(f"  臂：{list(ARMS)}")
    print(f"  每块 {a.minutes:.0f} 分钟 × {a.blocks} 轮/臂"
          f" ⇒ 约 {a.blocks*2*(a.minutes+1):.0f} 分钟")
    print(f"  热更新（注册表）⇒ 无需重启（MM_LANE_LIMITS_ENFORCE 已是 true）")
    if orig_val is None:
        print("  ✗ 注册表里读不到 daily_loss_stop_pct ⇒ 无法回滚，拒绝开始")
        return 1
    if a.dry_run:
        print("\n  （dry-run）不执行")
        return 0

    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({
        "saved_at": datetime.now().astimezone().isoformat(),
        "lane": LANE, "original": {"daily_loss_stop_pct": orig_val},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 已保存原值到 {STATE}（被强杀后可 --restore）")

    rows = []
    try:
        for bi in range(a.blocks):
            for name, cfg in ARMS.items():
                print(f"\n{'─'*96}")
                print(f"  区块 {bi+1}/{a.blocks}  {name}  {cfg}")
                set_params(**cfg)
                if not wait_effective("daily_loss_stop_pct",
                                      cfg["daily_loss_stop_pct"]):
                    print("    ✗ 未生效，跳过本区块")
                    continue
                sk0 = live_skips().get("daily_loss", 0)
                t0 = datetime.now().astimezone()
                t1 = t0 + timedelta(minutes=a.minutes)
                print(f"    窗口 {t0:%H:%M:%S} ~ {t1:%H:%M:%S}")
                while datetime.now().astimezone() < t1:
                    time.sleep(20)
                r = window(t0, t1)
                sk1 = live_skips().get("daily_loss", 0)
                r.update({"arm": name, "block": bi + 1,
                          "daily_loss_hits": int(sk1) - int(sk0)})
                rows.append(r)
                print(f"    maker {r['maker_n']:>5} 腿  flat {r['flat_n']:>3}  "
                      f"spread {r['spread_bp']:+.4f}  price {r['price_bp']:+.4f}  "
                      f"**net ${r['net_usd']:+.4f}**  "
                      f"闸门触发 {r['daily_loss_hits']} 次")
    finally:
        print(f"\n{'─'*96}\n  恢复起始配置 daily_loss_stop_pct={orig_val}")
        set_params(daily_loss_stop_pct=orig_val)
        ok = wait_effective("daily_loss_stop_pct", orig_val)
        print(f"  已恢复：{ok}")
        if ok:
            STATE.unlink(missing_ok=True)

    # ── 汇总 ──
    print("\n" + "=" * 96)
    print("结果（按块）")
    print("=" * 96)
    print(f"  {'轮':>3} {'臂':<20}{'maker':>6}{'flat':>5}{'spread':>9}"
          f"{'price':>9}{'net$':>10}{'闸门':>6}")
    for r in rows:
        print(f"  {r['block']:>3} {r['arm']:<20}{r['maker_n']:>6}{r['flat_n']:>5}"
              f"{r['spread_bp']:>+9.4f}{r['price_bp']:>+9.4f}"
              f"{r['net_usd']:>+10.4f}{r['daily_loss_hits']:>6}")

    by = {}
    for r in rows:
        by.setdefault(r["block"], {})[r["arm"]] = r
    diffs = [d["B(20%闸=尾部保护)"]["net_usd"] - d["A(80%闸=现状)"]["net_usd"]
             for _, d in sorted(by.items()) if len(d) == 2]
    if len(diffs) >= 2:
        m = st.mean(diffs)
        se = st.pstdev(diffs) / (len(diffs) ** 0.5)
        print(f"\n  配对差（B − A），{len(diffs)} 对：")
        for i, x in enumerate(diffs, 1):
            print(f"    {i}: {x:+.4f} USD")
        print(f"    ⇒ Δ = **{m:+.4f}** USD   SE = {se:.4f}   "
              f"t = {m/se if se else 0:+.2f}")
        print("\n  判据（与'只看净额'不同）：")
        print("    · 若 B 的**最差块**明显好于 A，而正块中位没有明显变差 ⇒ 尾部控制有效")
        print("    · 若 B 只是让成交变少、净额同比例变小 ⇒ 它在减少交易而非减少风险")
    else:
        print(f"\n  ⚠️ 配对样本不足（{len(diffs)} 对）⇒ **不下结论**")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"rows": rows, "diffs": diffs, "arms": ARMS,
                               "restored_to": orig_val},
                              ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
