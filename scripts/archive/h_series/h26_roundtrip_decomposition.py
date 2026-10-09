"""H26：往返分解 —— 亏损到底落在「入场腿」还是「出场腿」？

## 为什么必须做这一步（H23/H24/H25 的结论都指向它）

  · **H23**（距 touch 档位阶梯）：touch 档成交率最高（25.8%）且 mk@1s 近中性（−0.083bp）
  · **H24**（真实报价反事实，队列消耗制）：现状 mid±1.5bp 净@1s **+1.272bp**、
    净@30s **+1.718bp**；贴 touch 反而更差（+0.674bp）⇒ **报价位置不是问题**
  · **H25**（markout 衰减曲线，452 笔）：入场侧 Π=s/2−|ΔM| 在 **10 分钟内一直 +1.2~1.4bp**，
    到 30 分钟才穿零（−4.41bp）⇒ **入场 markout 也不是问题**

 ⇒ 三个独立实验都说"入场侧是赚的"，而实盘是 **−0.60bp/笔**。
 **差额只能落在出场腿。**

## 本脚本做什么

把账户账本按 `position_id` 配对成**往返**，把每一侧分开算：

    · 入场腿：使仓位从 0 → ±q 的那一笔
    · 出场腿：使仓位回到 0（或反向）的那些笔
    · 分别统计：笔数、净额、每笔均、占往返总盈亏的比例

并给出**往返级**的分布（而不是腿级）——因为腿级的 "-2.02bp" 是把整段持仓漂移
都记在**减仓那一笔**上（`InventoryBook.apply_fill` 的 `realized_price_usd`
只在减仓腿结算，见 F187/F204 的归因假象），所以腿级数字会误导。

## 判据（事先定死）

  · 若出场腿吃掉了往返盈利的绝大部分 ⇒ **改出场机制**是最高价值项
    （而不是找信号、也不是改报价位置）。
  · 若入场腿本身为负 ⇒ H24/H25 的反事实模型有系统性偏差，需先修模型再谈策略。

用法：
    .venv\\Scripts\\python.exe scripts\\h26_roundtrip_decomposition.py --hours 8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--account-id", type=int, default=101)
    ap.add_argument("--lane", default=os.getenv("MM_LANE_ID", "mm_asterdex"))
    args = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ── 账户账本：逐笔成交 + 数量 + 方向 ────────────────────────
    cur.execute(
        """
        SELECT created_at, amount_usd, metadata_json
          FROM arbitrage_paper_ledgers
         WHERE account_id = %s AND action = 'paper_pnl'
           AND created_at > now() - make_interval(secs => %s)
         ORDER BY created_at
        """,
        (args.account_id, args.hours * 3600.0),
    )
    rows = cur.fetchall()
    print(f"H26 往返分解  账户={args.account_id}  窗口={args.hours}h\n")

    # 重建每个币的库存轨迹 → 判定"入场腿 / 出场腿"
    #
    # 方向来自 meta.side（runner 写入了 side）；qty 的符号随 side。
    # 判定：|库存| 从 0 变大 = 入场；|库存| 变小 = 出场。
    legs = []
    for r in rows:
        try:
            md = json.loads(r["metadata_json"] or "{}")
        except Exception:
            md = {}
        side = str(md.get("side") or "").lower()
        qty = float(md.get("qty") or 0.0)
        if not side or qty == 0:
            continue
        signed = qty if side.startswith("b") else -qty
        legs.append({
            "ts": r["created_at"], "symbol": md.get("symbol") or "-",
            "signed": signed, "amt": float(r["amount_usd"] or 0.0),
            "phase": str(md.get("phase") or "?"),
        })

    if not legs:
        print("账本里没有可用的 side/qty（旧版本写入的行）⇒ 无法分解")
        print("（runner 自 F279 起写 side；此前的行没有该字段）")
        return 1

    by_sym = defaultdict(list)
    for x in legs:
        by_sym[x["symbol"]].append(x)

    print("[1] 逐币重建库存轨迹并分类（|库存| 变大=入场，变小=出场）")
    print("    %-10s %8s %8s %14s %14s" % ("symbol", "入场腿", "出场腿", "入场净额$", "出场净额$"))
    tot = {"entry": [0, 0.0], "exit": [0, 0.0]}
    rt_list = []          # 往返级：(symbol, 入场净额, 出场净额, 持仓秒)
    for s, xs in sorted(by_sym.items()):
        pos = 0.0
        entry_amt = exit_amt = 0.0
        n_in = n_out = 0
        cur_entry = None      # (ts, amt) 开仓起点
        for x in xs:
            prev = pos
            pos += x["signed"]
            grew = abs(pos) > abs(prev) + 1e-12
            if grew:
                n_in += 1
                entry_amt += x["amt"]
                tot["entry"][0] += 1
                tot["entry"][1] += x["amt"]
                if cur_entry is None:
                    cur_entry = [x["ts"], x["amt"]]
                else:
                    cur_entry[1] += x["amt"]
            else:
                n_out += 1
                exit_amt += x["amt"]
                tot["exit"][0] += 1
                tot["exit"][1] += x["amt"]
                # 回到零 ⇒ 一个往返完成
                if abs(pos) < 1e-12 and cur_entry is not None:
                    dur = (x["ts"] - cur_entry[0]).total_seconds()
                    rt_list.append((s, cur_entry[1], exit_amt, dur))
                    cur_entry = None
                    entry_amt = exit_amt = 0.0
        print("    %-10s %8d %8d %+14.6f %+14.6f"
              % (s, n_in, n_out, entry_amt, exit_amt))

    print("\n[2] 腿级汇总（**注意：腿级有归因假象，仅供参考**）")
    for k, v in tot.items():
        lab = "入场腿" if k == "entry" else "出场腿"
        print("    %-8s %5d 笔  净额 %+14.6f $   每笔均 %+14.6f $"
              % (lab, v[0], v[1], v[1] / max(1, v[0])))
    tt = tot["entry"][1] + tot["exit"][1]
    print("    ---- 合计 %+.6f $" % tt)

    print("\n[3] 往返级汇总（**这才是可下结论的口径**）")
    if not rt_list:
        print("    没有完整的往返（库存未回到零）—— 说明持仓期跨越了窗口边界")
        print("    ⇒ 窗口太短或持仓过长。请加大 --hours 或先解决出场问题。")
    else:
        e = np.array([r[1] for r in rt_list])
        x_ = np.array([r[2] for r in rt_list])
        d = np.array([r[3] for r in rt_list])
        net = e + x_
        print("    往返数 %d" % len(rt_list))
        print("    入场腿 合计 %+.6f $   每往返均 %+.6f $" % (e.sum(), e.mean()))
        print("    出场腿 合计 %+.6f $   每往返均 %+.6f $" % (x_.sum(), x_.mean()))
        print("    往返净 合计 %+.6f $   每往返均 %+.6f $" % (net.sum(), net.mean()))
        print("    持仓时长 中位 %.0fs   p25 %.0fs  p75 %.0fs  max %.0fs"
              % (np.median(d), np.percentile(d, 25), np.percentile(d, 75), d.max()))
        share = abs(x_.sum()) / max(1e-12, abs(e.sum()) + abs(x_.sum()))
        print("\n[4] 判定")
        print("    出场腿占往返总盈亏波动的 %.1f%%" % (100 * share))
        if x_.mean() < 0 and e.mean() > 0:
            print("    ⇒ **入场腿为正、出场腿为负** ⇒ 亏损集中在出场。")
            print("      与 H24/H25 的结论一致（入场侧 10 分钟内是赚的）。")
            print("      ⇒ 最高价值改动是**出场机制**：不是找信号，也不是改报价位置。")
        elif net.mean() > 0:
            print("    ⇒ 往返净为正 ⇒ 本窗口的策略在盈利（与账本腿级数字符号相反，")
            print("      说明腿级口径确有归因假象）。")
        else:
            print("    ⇒ 入场腿也为负 ⇒ H24/H25 的反事实模型存在系统性偏差，")
            print("      需先复查模型（尤其：反事实用了队列消耗制，实盘用的是穿过制）。")

    cur.close()
    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
