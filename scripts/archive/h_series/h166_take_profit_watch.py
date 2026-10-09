# -*- coding: utf-8 -*-
"""[H166 2026-09-21] 止盈（take_profit_bp）落地监看。

# 背景

`take_profit_bp = 12` 已启用（模拟定出的最优阈值：规则期望 +2.560bp/笔，
是现实基准 +0.1374bp 的 18.6×；严格口径触发率 32.0%）。

# 这条改动的**成败判据**（事先定死，避免事后挑数）

**收益侧**
  G1 `take_profit_hits` 计数开始增长（改动的直接证据）
  G2 `skip_counts` 出现 `take_profit`（`dec.skip` 落账路径）
  G3 **每笔净 bp 上升**（对照基准：本时代 +0.1374 bp/笔）

**代价侧 / 失败模式**
  C1 触发率过高 ⇒ 等于把出库全换成 taker，必亏
     判据：`take_profit_hits` / 成交笔数 > 50% ⇒ 阈值太低
  C2 提前砍掉赢家 ⇒ 右尾被削。看强平/止盈腿的 `price_bp` 是否本该更大
  C3 taker 费回升：`fee_usd` 开始变负（止盈腿付 4bp）

# 理论预期（用于对照，不是结论）

    触发率 32%  ⇒ 每 100 笔成交约 32 次止盈
    每次止盈净 = 12bp − 4bp = +8bp（相对被动出库的 ~+0.2bp ⇒ 每次多赚 ~7.8bp）
    ⇒ 规则期望 ≈ 0.32 × 8 = **+2.56bp/笔**

用法：
    .venv\\Scripts\\python.exe scripts\\h166_take_profit_watch.py
    .venv\\Scripts\\python.exe scripts\\h166_take_profit_watch.py --since 2026-09-21T15:00:00
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
BASELINE_BP = 0.1374


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="")
    a = ap.parse_args()

    st = ROOT / "logs" / "mm_lane_status.json"
    j = json.loads(st.read_text(encoding="utf-8"))
    age = int((datetime.now() - datetime.fromtimestamp(st.stat().st_mtime)).total_seconds())
    lim, par = j.get("limits") or {}, j.get("params") or {}

    print("=" * 96)
    print("H166  止盈（take_profit_bp）落地监看")
    print("=" * 96)
    print(f"  现在 {datetime.now().strftime('%H:%M:%S')}   心跳 {age}s   ok={j.get('ok')}")
    print(f"\n  【开关核对】")
    print(f"    take_profit_bp              = **{lim.get('take_profit_bp')}**  (0=关闭)")
    print(f"    take_profit_maker_grace_sec = {lim.get('take_profit_maker_grace_sec')}")
    print(f"    timeout_exit_maker_only     = {lim.get('timeout_exit_maker_only')}")
    print(f"    stop_loss_bp                = {lim.get('stop_loss_bp')} "
          f"(vol_min={lim.get('stop_loss_vol_min')})")
    print(f"    spread_mult / reduce        = {par.get('spread_mult')} / "
          f"{par.get('spread_mult_reduce')}")
    print(f"    compound_ratio              = {par.get('compound_ratio')}")

    print(f"\n  【G1】止盈触发次数（按币）: {j.get('take_profit_hits')}")
    print(f"  【C1】超时被挡次数（按币）: {j.get('timeout_exit_blocked')}")
    print(f"  【G2】skip_counts: {j.get('skip_counts')}")
    print(f"       ticks {j.get('ticks')}  fills {j.get('fills')}  "
          f"flattens {j.get('flattens')}  equity {j.get('equity'):.4f}")

    since = a.since
    if not since:
        with psycopg.connect(dsn()) as c:
            with c.cursor() as cur:
                cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry"
                            " WHERE lane_id=%s", (LANE,))
                since = cur.fetchone()[0] or "2026-09-21T12:28:25"

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*),
                       coalesce(sum(spread_bp*notional/1e4),0),
                       coalesce(sum(price_bp*notional/1e4),0),
                       coalesce(sum(fee_bp*notional/1e4),0),
                       coalesce(sum(notional),0),
                       count(*) FILTER (WHERE fee_bp < 0)
                FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= %s
            """, (LANE, since))
            cnt, sp, pr, fe, notl, ntk = cur.fetchone()
            cnt, notl, ntk = int(cnt), float(notl or 0), int(ntk or 0)
            b = 1e4 / notl if notl else 0.0
            net_bp = (float(sp) + float(pr) + float(fe)) * b
            print(f"\n  【G3】本时代累计（{since} 起）")
            print(f"    成交 {cnt} 笔   名义 ${notl:,.0f}   taker {ntk} 笔")
            print(f"    价差 {float(sp)*b:+.4f}bp   行情 {float(pr)*b:+.4f}bp   "
                  f"费 {float(fe)*b:+.4f}bp")
            print(f"    ⇒ **每笔净 {net_bp:+.4f} bp**")
            print(f"    对照基准（止盈启用前）{BASELINE_BP:+.4f} bp")
            delta = net_bp - BASELINE_BP
            print(f"    ⇒ 变化 **{delta:+.4f} bp/笔**"
                  f"（{'改善' if delta > 0 else '恶化'}）")
            if ntk and cnt:
                print(f"\n  【C3】taker 占比 {ntk/cnt*100:.1f}%"
                      f"（止盈腿每笔付 4bp；若 >50% 说明阈值太低）")

    print(f"\n  ── 判读 ──")
    print(f"    · 止盈触发数×8bp 的「额外收益」应体现在每笔净 bp 上；")
    print(f"      若每笔净 bp **没升**，先查 `take_profit_hits` 是否为 0（没触发）。")
    print(f"    · 若 taker 占比 > 50% ⇒ 阈值 12bp 偏低，应上调到 20bp。")
    print(f"    · ⚠️ 样本不足时不要下结论：至少 ≥200 笔成交。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
