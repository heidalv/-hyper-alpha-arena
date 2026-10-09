# -*- coding: utf-8 -*-
"""[H143 2026-09-21] 查两件事：① 手续费数据有没有落库；② 权益/余额/账本为什么对不上。

# 用户报的两个问题

  ① 「没有手续费数据」—— 前端手续费一栏为空
  ② 「账户权益和余额对不上」—— 权益 $270.69、可用余额 $185.94

# 查法

① 逐表逐列找手续费：我们自己的账本 `lane_ledger` 有 `fee_bp`（顶层列），
   但**前端读的可能是另一本账**（`arbitrage_paper_ledger` / `paper_pnl`）。
   ⇒ 先确认前端那一路的数据源有没有 fee 字段、有没有值。

② 把三个数字放在一起对账：
   · `meta.shadow_equity` = 270.69   （前端显示的"账户权益"）
   · 可用余额 = 185.94
   · 账本已实现 = −90.9489
   三者若不等，差额必须能被解释（持仓占用 / 未实现 / 影子权益是陈旧快照）。

用法：
    .venv\\Scripts\\python.exe scripts\\h143_equity_and_fee_audit.py
"""
from __future__ import annotations

import json
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
ACCT = 101


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
    url = dsn()
    print("=" * 100)
    print("H143  手续费数据 + 权益对账")
    print("=" * 100)

    with psycopg.connect(url) as c:
        with c.cursor() as cur:
            # ── ① 手续费 ────────────────────────────────────────
            print("\n【① 手续费数据】")
            for t in ("lane_ledger", "arbitrage_paper_ledgers", "paper_pnl",
                      "paper_funding_ledger"):
                cur.execute("""SELECT column_name FROM information_schema.columns
                               WHERE table_name=%s ORDER BY ordinal_position""", (t,))
                cols = [r[0] for r in cur.fetchall()]
                if not cols:
                    print(f"  [{t}] 表不存在")
                    continue
                fee_cols = [x for x in cols if "fee" in x.lower()]
                print(f"  [{t}] 手续费相关列 = {fee_cols or '**无**'}")

            cur.execute("""SELECT count(*), count(fee_bp),
                                  round(sum(fee_bp*notional/1e4)::numeric,4),
                                  round(min(fee_bp)::numeric,4),
                                  round(max(fee_bp)::numeric,4)
                           FROM lane_ledger WHERE lane_id=%s AND event='fill'""", (LANE,))
            n, nf, tot, mn, mx = cur.fetchone()
            print(f"\n  lane_ledger: 成交 {n} 行，其中 fee_bp 非空 **{nf}** 行")
            print(f"      累计手续费 = **{tot} USD**   fee_bp 范围 [{mn}, {mx}]")
            if nf == 0:
                print("      ⇒ **手续费列全空** —— 这就是前端「没有手续费数据」的原因")

            # 前端可能读的另一本账
            for t, col in (("arbitrage_paper_ledgers", "amount_usd"),):
                try:
                    cur.execute(f"""SELECT count(*) FROM "{t}"
                                    WHERE account_id=%s""", (ACCT,))
                    print(f"\n  [{t}] account_id={ACCT} 行数 = {cur.fetchone()[0]}")
                    cur.execute("""SELECT column_name FROM information_schema.columns
                                   WHERE table_name=%s""", (t,))
                    print(f"     列 = {[r[0] for r in cur.fetchall()]}")
                except Exception as e:
                    print(f"  [{t}] {type(e).__name__}: {str(e)[:80]}")

            # ── ② 对账 ─────────────────────────────────────────
            print("\n【② 权益对账】")
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = dict(cur.fetchone()[0] or {})
            shadow = meta.get("shadow_equity")
            print(f"  meta.shadow_equity（前端「账户权益」）   = {shadow}")

            # 账户余额
            for t in ("paper_accounts", "arbitrage_accounts", "accounts"):
                try:
                    cur.execute("""SELECT column_name FROM information_schema.columns
                                   WHERE table_name=%s""", (t,))
                    cols = [r[0] for r in cur.fetchall()]
                    if not cols:
                        continue
                    bal = [x for x in cols if "balance" in x.lower() or "equity" in x.lower()]
                    print(f"  [{t}] 余额相关列 = {bal}")
                    if bal:
                        cur.execute(f'SELECT * FROM "{t}" WHERE id=%s', (ACCT,))
                        row = cur.fetchone()
                        if row:
                            d = dict(zip(cols, row))
                            for k in bal:
                                print(f"      {k} = {d.get(k)}")
                except Exception:
                    pass

            # 账本累计
            cur.execute("""SELECT round(sum(net_bp*notional/1e4)::numeric,4),
                                  round(sum(fee_bp*notional/1e4)::numeric,4),
                                  round(sum(spread_bp*notional/1e4)::numeric,4),
                                  round(sum(price_bp*notional/1e4)::numeric,4)
                           FROM lane_ledger WHERE lane_id=%s AND event='fill'""", (LANE,))
            net, fee, sp, pr = cur.fetchone()
            print(f"\n  lane_ledger 累计（全历史）：")
            print(f"      净额 {net}   其中 手续费 {fee} / 价差 {sp} / 行情 {pr}")

            # 运行态持仓
            cur.execute("""SELECT symbol, state_json->>'qty' FROM lane_runtime_state
                           WHERE lane_id=%s""", (LANE,))
            pos = [(r[0], float(r[1] or 0)) for r in cur.fetchall()]
            held = [p for p in pos if abs(p[1]) > 1e-9]
            print(f"\n  运行态持仓 {len(held)} 个: "
                  f"{[(s, round(q,4)) for s, q in held]}")

    print(f"\n  ── 对账 ──")
    if shadow is not None and net is not None and fee is not None:
        print(f"    shadow_equity              {float(shadow):>12.4f}")
        print(f"    300 + 账本净额             {300 + float(net):>12.4f}")
        print(f"    ⇒ 差额                     {float(shadow) - (300 + float(net)):>+12.4f}")
        print(f"\n    若差额 ≈ 0 ⇒ 影子权益是对的（随时可复算）")
        print(f"    若差额 ≠ 0 ⇒ **影子权益是陈旧快照**（它只在 F290 同步脚本运行时更新），")
        print(f"       前端却把它当「账户权益」显示 ⇒ 与「可用余额」必然对不上。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
