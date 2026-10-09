# -*- coding: utf-8 -*-
"""[H178 2026-09-21] 实盘交替 A/B：撤掉减仓侧出库挂单（方案 ①）到底好不好。

# 为什么用实盘而不是模拟

本轮我写了 **四个** 模拟脚本（H172/H173/H176/H177），**全部与实盘对不上**：

    H173「现状」行 −1.6553 bp/笔      实盘基准 **+0.1374 bp/笔**
    H177「现况」行 −1.3456 bp/笔      同上
    ⇒ 差 1.5~1.8bp 且符号相反

根因是我无法正确重建"入场腿在成交那一刻已经赚到的挂宽"，起点偏了。
**既然四个模拟都不可信，就不要再写第五个。**

# 本脚本：直接在实盘上交替切换，读真账本

  Arm A（对照）：`reduce_quote_disabled=False`、`timeout_exit_maker_only=True`（现状）
  Arm B（方案①）：`reduce_quote_disabled=True`、`timeout_exit_maker_only=False`
                  （撤出库单 + 超时用 taker 收尾，避免仓位永久滞留）

两块交替 `--blocks` 轮，每块 `--minutes` 分钟。**两者都在同一时段** ⇒
行情漂移对两臂对称，做配对差即可消掉时段效应。

# 判据（事先定死）

  用**每块的配对差**（B − A）做统计：
    · Δ = mean(净bp @ B) − mean(净bp @ A)，SE = 配对差的样本标准误
    · `Δ > 2×SE` ⇒ B 更好，采用方案 ①
    · `Δ < −2×SE` ⇒ A 更好，回退
    · 否则无法区分 ⇒ 看**标准差**，取波动小的（用户的"亏没了"感受来自大方差）

# 为什么这两个键可以热更新（不用重启）

`reduce_quote_disabled` 与 `timeout_exit_maker_only` **都不在**
`apply_env_param_overrides` 的 7 键白名单里 ⇒ 改注册表后 ≤60s 内被 F251 指纹热采用。
⇒ **本 A/B 不重启 worker**，无停机、无冷启动污染。

用法：
    .venv\\Scripts\\python.exe scripts\\h178_ab_reduce_quote.py --dry-run
    .venv\\Scripts\\python.exe scripts\\h178_ab_reduce_quote.py --blocks 3 --minutes 20
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import time
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"

ARMS = {
    "A(现状)": {"reduce_quote_disabled": False, "timeout_exit_maker_only": True},
    "B(方案①)": {"reduce_quote_disabled": True, "timeout_exit_maker_only": False},
}


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


def get_limits() -> dict:
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'params' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            return dict(cur.fetchone()[0] or {})


def set_limits(**kw) -> None:
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


def wait_effective(expect: dict, timeout_s: float = 150.0) -> bool:
    """轮询心跳 limits，确认两键都生效（F251 指纹 60s 周期）。"""
    stf = ROOT / "logs" / "mm_lane_status.json"
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            j = json.loads(stf.read_text(encoding="utf-8"))
            lim = j.get("limits") or {}
            if all(bool(lim.get(k)) == bool(v) for k, v in expect.items()):
                return True
        except Exception:
            pass
        time.sleep(4)
    return False


def window(t0: datetime, t1: datetime) -> dict:
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*), coalesce(sum(spread_bp*notional/1e4),0),
                       coalesce(sum(price_bp*notional/1e4),0),
                       coalesce(sum(fee_bp*notional/1e4),0),
                       coalesce(sum(notional),0)
                FROM lane_ledger WHERE lane_id=%s AND event='fill'
                  AND ts >= %s AND ts < %s
            """, (LANE, t0, t1))
            n, sp, pr, fe, notl = cur.fetchone()
            n, notl = int(n or 0), float(notl or 0)
            b = 1e4 / notl if notl else 0.0
            return {"fills": n, "notional": notl,
                    "spread_bp": float(sp or 0) * b, "price_bp": float(pr or 0) * b,
                    "fee_bp": float(fe or 0) * b,
                    "net_bp": (float(sp) + float(pr) + float(fe)) * b,
                    "net_usd": float(sp) + float(pr) + float(fe)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=3, help="每臂区块数")
    ap.add_argument("--minutes", type=float, default=20.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    orig = get_limits()
    orig_pair = {k: bool(orig.get(k)) for k in
                 ("reduce_quote_disabled", "timeout_exit_maker_only")}

    print("=" * 96)
    print("H178  实盘交替 A/B：撤掉减仓侧出库挂单")
    print("=" * 96)
    print(f"  区块 {a.blocks} 轮/臂，每块 {a.minutes:.0f} 分钟")
    print(f"  顺序：{' → '.join(list(ARMS)[i % 2] for i in range(a.blocks * 2))}")
    print(f"  起始值：{orig_pair}")
    print(f"  （两键均不在 env 白名单 ⇒ 热更新，不重启 worker）")
    est = a.blocks * 2 * (a.minutes + 1.0)
    print(f"  预计耗时 ≈ {est:.0f} 分钟")

    if a.dry_run:
        print("\n  （dry-run）不执行")
        return 0

    rows = []
    try:
        for bi in range(a.blocks):
            for name, cfg in ARMS.items():
                print(f"\n{'─'*96}")
                print(f"  区块 {bi+1}/{a.blocks}  {name}  {cfg}")
                set_limits(**cfg)
                if not wait_effective(cfg):
                    print("    ✗ 未生效，跳过本区块")
                    continue
                t0 = datetime.now().astimezone()
                t1 = t0 + timedelta(minutes=a.minutes)
                print(f"    窗口 {t0.strftime('%H:%M:%S')} ~ {t1.strftime('%H:%M:%S')}")
                while datetime.now().astimezone() < t1:
                    time.sleep(20)
                r = window(t0, t1)
                r.update({"arm": name, "block": bi + 1})
                rows.append(r)
                print(f"    成交 {r['fills']:>5}  ${r['notional']:>12,.0f}   "
                      f"价差 {r['spread_bp']:+.4f}  行情 {r['price_bp']:+.4f}  "
                      f"费 {r['fee_bp']:+.4f}")
                print(f"    ⇒ **净 {r['net_bp']:+.4f} bp/笔**   ({r['net_usd']:+.4f} USD)")
    finally:
        print(f"\n{'─'*96}\n  恢复起始配置 {orig_pair}")
        set_limits(**orig_pair)
        print(f"  已恢复：{wait_effective(orig_pair)}")

    print("\n" + "=" * 96)
    print("结果")
    print("=" * 96)
    print(f"  {'轮':>3} {'臂':<10} {'成交':>6} {'名义$':>12} {'价差bp':>9} "
          f"{'行情bp':>9} {'费bp':>8} {'净bp':>9}")
    print("  " + "-" * 76)
    for r in rows:
        print(f"  {r['block']:>3} {r['arm']:<10} {r['fills']:>6} {r['notional']:>12,.0f} "
              f"{r['spread_bp']:>+9.4f} {r['price_bp']:>+9.4f} {r['fee_bp']:>+8.4f} "
              f"{r['net_bp']:>+9.4f}")

    by = {}
    for r in rows:
        by.setdefault(r["block"], {})[r["arm"]] = r
    diffs = [d["B(方案①)"]["net_bp"] - d["A(现状)"]["net_bp"]
             for _, d in sorted(by.items()) if len(d) == 2]
    ns = [min(d["A(现状)"]["fills"], d["B(方案①)"]["fills"])
          for _, d in sorted(by.items()) if len(d) == 2]

    if len(diffs) >= 2 and all(x >= 50 for x in ns):
        m = st.mean(diffs)
        se = st.pstdev(diffs) / (len(diffs) ** 0.5)
        print(f"\n  配对差（B − A），{len(diffs)} 对：")
        for i, x in enumerate(diffs, 1):
            print(f"    {i}: {x:+.4f} bp")
        print(f"    ⇒ Δ = **{m:+.4f} bp**   SE = {se:.4f}   t = {m/se if se else 0:+.2f}")
        if se and m > 2 * se:
            print(f"\n  ⇒ **Δ > 2×SE ⇒ 方案 ①（撤出库单）显著更好** ⇒ 采用")
        elif se and m < -2 * se:
            print(f"\n  ⇒ **Δ < −2×SE ⇒ 现状更好** ⇒ 回退，不采用方案 ①")
        else:
            print(f"\n  ⇒ **无法区分**（|Δ| ≤ 2×SE）⇒ 维持现状（改动更小、风险更低）")
    else:
        print(f"\n  ⚠️ 配对样本不足或单块成交 <50 笔（各对最小成交 {ns}）⇒ **不下结论**")
        print(f"     建议加大 --blocks 或 --minutes 后重跑")

    out = ROOT / "research_l1" / "out" / "h178_ab_reduce_quote.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rows": rows, "diffs": diffs, "arms": ARMS,
                               "restored_to": orig_pair},
                              ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    print(f"\n  写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
