# -*- coding: utf-8 -*-
"""H239 在线 A/B：`spread_mult` 的边际（1.0 / 3.0 / 12.0）。

# 离线反事实说了什么（H238，8 小时 1,546 条真实成交）

    k=1.0（现状）  成交率 71.9%  单腿 −1.401bp  总 −$38.97
    k=3.0          70.1%        −1.113         −$30.21   (+$18.61)
    k=6.0          65.8%        −0.738         −$18.95   (+$29.87)
    k=12.0         59.1%        +0.017         +$0.40    (+$49.22) ← 转正

⇒ 挂宽是**唯一被量化到能转正的杠杆**。

# 为什么仍然必须在线测

离线反事实有三处**无法自证**的假设：

  ① `spread_bp × k` 线性 —— 真实挂宽会**改变成交的构成**（避开最毒的成交），
     方向对我们有利，但幅度未知；
  ② 成交判定仍是"mid 触及"，只是用来**筛掉**不再被触及的腿；
  ③ 未建模：挂宽变大 ⇒ 库存积累更慢 ⇒ **敞口路径改变**（这是双向的：
     敞口占用更久可能阻塞新仓，也可能让单边持仓更难积累）。

⇒ 只有在线 A/B 能回答。**一次一个变量**（本会话的硬规矩）。

# 设计

· 臂：`spread_mult ∈ {1.0, 3.0, 12.0}`（1.0 = 现状的 2 倍；12.0 = 反事实转正点）
· **交替**：块 1→臂A、块 2→臂B、块 3→臂C、块 4→臂A… 以抵消 regime 漂移
· 每块指标：腿数、`spread_bp` 均值、`price_bp` 均值、`net_bp` 均值、**净额$**
· 判据：`net_bp` **随 k 单调改善** 才算成立；
  若只有 k=12 好而 k=3 不动 ⇒ 过拟合噪声，不下结论
· 回滚：`h188_param_rollback.py` 先存原值；本脚本也在 finally 里恢复

# 用法

    python scripts/h239_spread_mult_ab.py --blocks 3 --minutes 25
    python scripts/h239_spread_mult_ab.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h239_spread_mult_ab.json"
ARMS = [1.0, 3.0, 12.0]


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


def read_params() -> dict:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'params' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            return dict(cur.fetchone()[0] or {})


def set_param(key: str, val) -> None:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                " %s::text[], to_jsonb(%s::float8), true), updated_at=now()"
                " WHERE lane_id=%s",
                ("{" + f"params,{key}" + "}", float(val), LANE))
        c.commit()


def live() -> dict:
    try:
        j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    d = dict(j.get("params") or {})
    for k, v in dict(j.get("limits") or {}).items():
        d.setdefault(k, v)
    d["_avg_width_bp"] = j.get("avg_width_bp")
    d["_avg_base_bp"] = j.get("avg_base_bp")
    d["_ticks"] = j.get("ticks")
    return d


def wait_effective(want: float, timeout_s: float = 150.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        cur = live().get("spread_mult")
        if cur is not None and abs(float(cur) - float(want)) < 1e-9:
            return True
        time.sleep(5)
    return False


def window(t0, t1) -> dict:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*) FILTER (WHERE (meta_json->'flatten')::text='false'),
                       count(*) FILTER (WHERE (meta_json->'flatten')::text='true'),
                       coalesce(sum(notional) FILTER (
                           WHERE (meta_json->'flatten')::text='false'), 0),
                       coalesce(sum(spread_bp*notional) FILTER (
                           WHERE (meta_json->'flatten')::text='false')
                           / NULLIF(sum(notional) FILTER (
                           WHERE (meta_json->'flatten')::text='false'),0), 0),
                       coalesce(sum(price_bp*notional) FILTER (
                           WHERE (meta_json->'flatten')::text='false')
                           / NULLIF(sum(notional) FILTER (
                           WHERE (meta_json->'flatten')::text='false'),0), 0),
                       coalesce(sum(net_bp*notional/1e4), 0)
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= %s AND ts < %s
            """, (LANE, t0, t1))
            mk, fl, notl, wsp, wpx, net = cur.fetchone()
    return {"maker_n": int(mk or 0), "flat_n": int(fl or 0),
            "notional": round(float(notl or 0), 0),
            "spread_bp": round(float(wsp or 0), 4),
            "price_bp": round(float(wpx or 0), 4),
            "net_usd": round(float(net or 0), 4)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=3)
    ap.add_argument("--minutes", type=float, default=25.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    orig = read_params().get("spread_mult")
    print("=" * 96)
    print("H239  spread_mult 在线 A/B")
    print("=" * 96)
    print(f"  起始 spread_mult = {orig}")
    print(f"  臂 = {ARMS}　交替（块内顺序轮转，抵消 regime 漂移）")
    print(f"  每块 {a.minutes:.0f} 分钟 × {a.blocks} 轮 × {len(ARMS)} 臂"
          f" ⇒ 约 {a.blocks*len(ARMS)*(a.minutes+1)/60:.1f} 小时")
    print(f"  热更新（注册表）⇒ 无需重启；生效确认走心跳")
    if orig is None:
        print("  ✗ 读不到 spread_mult ⇒ 无法回滚，拒绝开始")
        return 1
    if a.dry_run:
        print("\n  （dry-run）不执行")
        print(f"  将依次设置：{ARMS}，最后恢复 {orig}")
        return 0

    rows = []
    try:
        for bi in range(a.blocks):
            for ai, k in enumerate(ARMS):
                arm = ARMS[(ai + bi) % len(ARMS)]      # 轮转起点，抵消漂移
                print(f"\n{'─'*96}")
                print(f"  块 {bi+1}/{a.blocks}　臂 spread_mult = {arm}")
                set_param("spread_mult", arm)
                if not wait_effective(arm):
                    print("    ✗ 未生效，跳过")
                    continue
                lv = live()
                print(f"    生效确认：spread_mult={lv.get('spread_mult')}　"
                      f"实盘挂宽={json.dumps(lv.get('_avg_width_bp'), ensure_ascii=False)}")
                t0 = datetime.now().astimezone()
                t1 = t0 + timedelta(minutes=a.minutes)
                print(f"    窗口 {t0:%H:%M:%S} ~ {t1:%H:%M:%S}")
                while datetime.now().astimezone() < t1:
                    time.sleep(20)
                r = window(t0, t1)
                r.update({"arm": arm, "block": bi + 1})
                rows.append(r)
                print(f"    maker {r['maker_n']:>5} 腿　flat {r['flat_n']:>3}　"
                      f"spread {r['spread_bp']:+.4f}　price {r['price_bp']:+.4f}　"
                      f"**净额 ${r['net_usd']:+.4f}**")
    finally:
        print(f"\n{'─'*96}\n  恢复 spread_mult = {orig}")
        set_param("spread_mult", orig)
        print(f"  已恢复：{wait_effective(orig)}")

    # ── 汇总 ──
    print("\n" + "=" * 96)
    print("结果")
    print("=" * 96)
    print(f"  {'臂':>8}{'块':>4}{'maker腿':>9}{'spread':>10}{'price':>10}"
          f"{'净额$':>11}")
    for r in rows:
        print(f"  {r['arm']:>8.1f}{r['block']:>4}{r['maker_n']:>9}"
              f"{r['spread_bp']:>+10.4f}{r['price_bp']:>+10.4f}{r['net_usd']:>+11.4f}")

    by = {}
    for r in rows:
        by.setdefault(r["arm"], []).append(r)
    print(f"\n  {'臂':>8}{'块数':>6}{'spread均值':>12}{'price均值':>12}"
          f"{'净额合计$':>12}{'单腿净bp':>12}")
    summary = {}
    for arm in ARMS:
        v = by.get(arm, [])
        if not v:
            continue
        sp = sum(x["spread_bp"] for x in v) / len(v)
        px = sum(x["price_bp"] for x in v) / len(v)
        nu = sum(x["net_usd"] for x in v)
        notl = sum(x["notional"] for x in v)
        wbp = nu / notl * 1e4 if notl else 0.0
        print(f"  {arm:>8.1f}{len(v):>6}{sp:>+12.4f}{px:>+12.4f}{nu:>+12.2f}{wbp:>+12.4f}")
        summary[arm] = {"blocks": len(v), "spread_bp": round(sp, 4),
                        "price_bp": round(px, 4), "net_usd": round(nu, 2),
                        "net_bp": round(wbp, 4), "notional": round(notl, 0)}

    print(f"\n  判据（一次一个变量 + 单调性）：")
    arms_ok = [a_ for a_ in ARMS if a_ in summary]
    if len(arms_ok) >= 2:
        bps = [summary[a_]["net_bp"] for a_ in arms_ok]
        mono = all(bps[i] >= bps[i - 1] for i in range(1, len(bps)))
        print(f"    net_bp 序列 {[round(b, 4) for b in bps]}"
              f" ⇒ {'**单调改善** ✓' if mono else '**非单调** ✗（可能是噪声）'}")
        sps = [summary[a_]["spread_bp"] for a_ in arms_ok]
        print(f"    spread_bp 序列 {[round(s, 4) for s in sps]}"
              f" ⇒ 挂宽杠杆{'生效' if sps[-1] > sps[0] * 1.5 else '**没生效**（查参数字段）'}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"rows": rows, "summary": {str(k): v for k, v in summary.items()},
                               "arms": ARMS, "restored_to": orig},
                              ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
