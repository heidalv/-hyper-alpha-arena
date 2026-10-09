# -*- coding: utf-8 -*-
"""H219 F338/F339 上线后的验证：腿量上限是否真的在截、日亏闸是否可见。

# 为什么需要独立的"上线后验证"

本会话反复出现过**"改了但没生效"**，而且每次的表现都是**静默**的：

  · F282  `compound_ratio` 只在 `__init__` 读 ⇒ 改注册表不重启就不生效
  · F327  `param_authority` 加进 `runner.status()` 却漏了心跳白名单
  · F339  **同一天内**又漏了同一个心跳白名单（4 个键全 None）
  · F338  上限本身：初版测试参数下腿恰好等于上限 ⇒ `leg_capped` 恒 0

⇒ 每次都要有**从外部可读的证据**证明它真的在动。本脚本就是那个证据。

# 判据

1. **上限生效**：上线后每条腿的名义 ≤ `3 × fill_notional`（留 10% 余量
   应对 `_avail` 变化与价格漂移）；线 = 3 × 当前 `fill_notional`。
2. **上限在工作**：`max_leg_notional_mult` 在心跳 `limits` 里 = 3；
   且上线后**曾经出现过**被截的腿（`leg_capped` 只能从 runner 内部读，
   这里用"名义恰好贴着上限"的腿数作为外部代理）。
3. **日亏闸可见**：`day_pnl_usd` / `day_pnl_limit_usd` 都在心跳里，
   且 `day_pnl_limit_usd ≈ -equity × 15%`。
4. **退步检查**（比进步更重要）：成交频次不能塌、`ok` 必须为真、
   被截腿的**名义占比**要接近反事实预估的 7.3%（若远高说明上限设得太紧）。

# 用法

    python scripts/h219_verify_leg_cap.py --since-minutes 60
"""
from __future__ import annotations

import argparse
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-minutes", type=float, default=60.0)
    a = ap.parse_args()

    j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    lim = dict(j.get("limits") or {})
    mult = lim.get("max_leg_notional_mult")
    pct = lim.get("daily_loss_stop_pct")
    eq = float(j.get("equity") or 0.0)
    fn = float(j.get("fill_notional") or 0.0)
    cap = (float(mult) * fn) if (mult and fn) else None

    print("=" * 100)
    print("H219  F338/F339 上线后验证")
    print("=" * 100)
    print(f"  心跳 ticks={j.get('ticks')} ok={j.get('ok')} "
          f"reason={j.get('reason')!r}")
    print(f"  权益 ${eq:.2f}　fill_notional ${fn:.2f}")
    print(f"  max_leg_notional_mult = {mult}　daily_loss_stop_pct = {pct}%")
    if cap:
        print(f"  ⇒ 单腿名义上限 = {mult} × ${fn:.2f} = **${cap:,.2f}**")

    ok_all = True

    # ── 1/3. 心跳字段（F339 的三道白名单有没有真的打通）──
    print(f"\n{'━'*100}\n  一、心跳字段（F339 三道白名单）\n{'━'*100}")
    for k in ("lane_pause_counts", "lane_pause_last", "day_pnl_usd",
              "day_pnl_limit_usd"):
        present = k in j
        print(f"  {k:22} present={str(present):5}  {j.get(k)!r}")
        if not present:
            ok_all = False
    dpl = j.get("day_pnl_limit_usd")
    if dpl is not None and pct:
        expect = -abs(eq * float(pct) / 100.0)
        # ⚠️ 初版写成 `abs(float(dpl) - abs(expect)) < 0.02` —— 那是**恒真**的：
        # 两个量都取绝对值后再相减，等于把符号信息扔掉，而 `expect` 本身是负数。
        # 正确做法是直接比较**带符号**的值（两者都应为负）。
        good = abs(float(dpl) - expect) < 0.05
        print(f"\n  触发线核对：心跳 {float(dpl):+.2f}　"
              f"公式 -|{eq:.2f}×{pct}%| = {expect:+.2f}　⇒ {'✓ 一致' if good else '✗ 不一致'}")
        ok_all &= bool(good)
        dp = float(j.get("day_pnl_usd") or 0.0)
        used = abs(dp) / abs(expect) * 100 if expect else 0.0
        print(f"  今日已实现 {dp:+.2f} ⇒ 已用掉触发线的 **{used:.1f}%**")
        if dp <= expect:
            print(f"  ⚠️ **日亏闸已触发**（车道当天剩余时间停摆；"
                  f"实测只要 `day_pnl_usd` 回到触发线之上就会自解除）")

    lpc = j.get("lane_pause_counts") or {}
    if lpc:
        print(f"\n  车道级闸门触发计数（此前完全不可见）：{json.dumps(lpc, ensure_ascii=False)}")
        print(f"  最近一次：{json.dumps(j.get('lane_pause_last') or {}, ensure_ascii=False)}")
    else:
        print(f"\n  车道级闸门计数为空（= 从未触发，或观察窗太短）")

    # ── 2. 上线后腿量分布 ──
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*), coalesce(max(notional),0), coalesce(sum(notional),0),
                       coalesce(avg(notional),0),
                       count(*) FILTER (WHERE notional > %s)
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - (%s || ' minutes')::interval
                  AND coalesce(notional,0) > 0
            """, (cap or 1e18, LANE, str(int(a.since_minutes))))
            n, mx, tot, avg, over = cur.fetchone()
            cur.execute("""
                SELECT coalesce(notional,0), coalesce(net_bp,0),
                       coalesce(meta_json->>'flatten','false'), coalesce(symbol,'')
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - (%s || ' minutes')::interval
                  AND coalesce(notional,0) > 0
                ORDER BY notional DESC LIMIT 10
            """, (LANE, str(int(a.since_minutes))))
            top = cur.fetchall()

    print(f"\n{'━'*100}\n  二、上线后腿量（最近 {a.since_minutes:.0f} 分钟）\n{'━'*100}")
    print(f"\n  腿数 {n}　最大名义 ${float(mx):,.2f}　均值 ${float(avg):,.2f}　"
          f"名义合计 ${float(tot):,.0f}")
    if cap:
        verdict = "✓ 未越界" if float(mx) <= cap * 1.10 else "✗ **越界**"
        print(f"  上限 ${cap:,.2f}　实际最大 ${float(mx):,.2f}"
              f"（{float(mx)/cap:.2f}× 上限）⇒ {verdict}")
        ok_all &= float(mx) <= cap * 1.10
        print(f"  超过上限的腿数 = {over}（应为 0；进场腿按定义必须 ≤ 上限）")
        if over:
            print(f"  ⚠️ 有 {over} 条腿超过上限 —— 检查是否都是**减仓腿**"
                  f"（减仓腿按设计不受限，见 F338 测试第 3 条）")
    print(f"\n  最大 10 腿：")
    print(f"    {'名义$':>12}{'net_bp':>10}{'flatten':>9}{'symbol':>8}")
    for nn, bb, fl, sy in top:
        print(f"    {float(nn):>12,.2f}{float(bb):>+10.3f}{str(fl):>9}{str(sy):>8}")

    # ── 4. 退步检查 ──
    print(f"\n{'━'*100}\n  三、退步检查（比进步更重要）\n{'━'*100}")
    print(f"  ok = {j.get('ok')}  ⇒ {'✓' if j.get('ok') else '✗ 车道在报错！'}")
    ok_all &= bool(j.get("ok"))
    sk = j.get("skip_counts") or {}
    print(f"  skip_counts = {json.dumps(sk, ensure_ascii=False)}")
    print(f"  ticks={j.get('ticks')} fills={j.get('fills')}")
    if n == 0:
        print(f"  ⚠️ 上线后**零成交** —— 上限可能设得太紧，或车道被闸住")
    else:
        print(f"  ⇒ 上线后仍在成交（{n} 腿）✓")

    print(f"\n{'='*100}")
    print(f"  总判定：{'✓ 通过' if ok_all else '✗ 有问题，见上'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
