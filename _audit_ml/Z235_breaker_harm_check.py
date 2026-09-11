# -*- coding: utf-8 -*-
"""[§83 核验 2026-09-11 / 目标①] 统一熔断是否**减少出血**、是否**误伤盈利通道**（只读）。

为什么需要：P19-B/P21 之后，"哪些通道会被抑制"由**滚动窗胜率**决定，而钱是按**净额**算的。
两者的口径差可能造成两种错误，必须逐通道量化：

  * **误伤**：某通道近期胜率 <40% 被判 shadow，但其**净额是正的**（赢小亏大 vs 赢大亏小）
    ⇒ 抑制它等于砍掉盈利来源；
  * **漏报**：某通道净额很负、但胜率 ≥40%（小赢多、大亏少）⇒ 熔断看不到它。

同时做一次**独立复核**：状态文件里 `breaker.recent`（熔断窗）与 **DB 重算**是否一致
（P21 回填后二者应当同源；不一致说明回填/记账口径仍有偏差）。

口径（与 §70/P3 一致）：净额 = `(close-entry)*size*dir + partial_realized_pnl - partial_fee_paid`，
`dir = +1 if side in (long,buy) else -1`；窗口 = **生效值** `min(EXIT_CHANNEL_SHADOW_MIN_N, 30)`。
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)   # 不加载 .env 会读到代码默认 30（口径错）

from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.exit.channel_breaker_gate import channel_of, is_protected  # noqa: E402
from sqlalchemy import text  # noqa: E402

NET = ("(case when lower(side) in ('long','buy') then (close_price-entry_price)*size "
       "else -(close_price-entry_price)*size end) "
       "+ coalesce(partial_realized_pnl,0) - coalesce(partial_fee_paid,0)")


def thresholds():
    try:
        min_n = int(float(os.environ.get("EXIT_CHANNEL_SHADOW_MIN_N", "30") or 30))
        max_wr = float(os.environ.get("EXIT_CHANNEL_SHADOW_MAX_WR", "0.40") or 0.40)
    except (TypeError, ValueError):
        min_n, max_wr = 30, 0.40
    rolling = int(os.environ.get("SOURCE_ATTR_ROLLING_WINDOW", "30") or 30)
    return min_n, max_wr, rolling, min(min_n, rolling)


def db_channels():
    """按 (tier, 通道) 取全部近 30 天平仓，按时间升序（用于算最近 N 笔窗口）。"""
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        rows = db.execute(text(f"""
            select lower(coalesce(timeframe_tier,'?')) as tier,
                   coalesce(close_reason,'?') as reason,
                   {NET} as net,
                   closed_at
            from paper_positions
            where status='closed' and closed_at >= now() - interval '30 day'
              and lower(coalesce(timeframe_tier,'')) in ('short','mid','long','research')
            order by closed_at
        """)).fetchall()
        return rows
    finally:
        db.close()


def stats(items, w, wr_thr):
    """items: [(net, closed_at)]；返回最近 w 笔与全部的 (n, net, wr, avg)。"""
    if not items:
        return None
    win = items[-w:] if w > 0 else items
    def agg(xs):
        n = len(xs)
        if not n:
            return (0, 0.0, 0.0, 0.0)
        net = sum(x[0] for x in xs)
        wins = sum(1 for x in xs if x[0] > 0)
        return (n, net, wins / n, net / n)
    return {"win": agg(win), "all": agg(items)}


def classify_shadow_verdict(*, is_shadow: bool, n_win: int, w: int,
                            net_win: float, wr_win: float, wr_thr: float) -> str:
    """纯函数：判定某通道的熔断状态与净额口径**是否一致**（可红可绿，供契约测试）。

    返回：`误伤`（shadow 但窗口净额>0）/ `漏报`（未 shadow 但窗口胜率<阈值且净额<0）/
    `一致`（shadow 且净额<=0）/ `-`（样本不足或其它情形，不表态）。
    """
    if n_win < max(1, int(w)):
        return "-"
    if is_shadow:
        return "误伤" if net_win > 0 else "一致"
    if wr_win < wr_thr and net_win < 0:
        return "漏报"
    return "-"


def main() -> int:
    min_n, max_wr, rolling, w = thresholds()
    print("=" * 104)
    print(f"熔断口径（生效值）：MIN_N={min_n} MAX_WR={max_wr} ROLLING={rolling} ⇒ 评估窗口 {w} 笔")
    print("=" * 104)

    st = json.loads((ROOT / "data" / "fusion_attribution.json").read_text(encoding="utf-8"))
    shadow = {k: v for k, v in (st.get("breaker_shadow") or {}).items()}
    breaker = st.get("breaker") or {}

    rows = db_channels()
    by_key = defaultdict(list)
    for tier, reason, net, _ts in rows:
        key = f"{tier}|{channel_of(reason)}"
        by_key[key].append((float(net or 0), _ts))

    print("\n【A. 逐通道：熔断窗 vs DB 重算 vs 净额经济含义】")
    print(f"{'key':34s} {'state窗':>14s} {'DB窗':>14s} {'DB近30笔净额':>13s} {'30天净额':>11s} {'判定':>8s}")
    miss, hurt, agree = [], [], []
    for key in sorted(shadow.keys() | set(by_key.keys())):
        tier, _, ch = key.partition("|")
        rec = [x for x in (breaker.get(key, {}).get("recent") or []) if x in (0, 1, True, False)]
        st_wr = (sum(1 for x in rec[-w:] if x) / min(w, len(rec))) if len(rec) >= w else None
        s = stats(by_key.get(key, []), w, max_wr)
        if not s:
            db_win, db_all = (0, 0.0, 0.0, 0.0), (0, 0.0, 0.0, 0.0)
        else:
            db_win, db_all = s["win"], s["all"]
        n_w, net_w, wr_w, avg_w = db_win
        n_a, net_a, wr_a, avg_a = db_all
        is_shadow = bool(shadow.get(key))
        # 判定（纯函数，见 classify_shadow_verdict）
        verdict = classify_shadow_verdict(
            is_shadow=is_shadow, n_win=n_w, w=w, net_win=net_w, wr_win=wr_w, wr_thr=max_wr)
        if verdict == "误伤":
            hurt.append((key, n_w, net_w, wr_w))
        elif verdict == "一致":
            agree.append((key, n_w, net_w, wr_w))
        elif verdict == "漏报":
            miss.append((key, n_w, net_w, wr_w))
        st_txt = "—" if st_wr is None else f"{st_wr*100:.1f}%/{len(rec)}"
        db_txt = "—" if n_w < w else f"{wr_w*100:.1f}%/{n_w}"
        print(f"{key:34s} {st_txt:>14s} {db_txt:>14s} {net_w:>13.2f} {net_a:>11.2f} {verdict:>8s}")

    print("\n【B. 当前会被抑制的通道（非保护 ∩ shadow）与其经济规模】")
    sup = [k for k in shadow if shadow[k] and not is_protected(k.partition("|")[2])]
    tot_sup_net = 0.0
    for key in sorted(sup):
        s = stats(by_key.get(key, []), w, max_wr)
        n_a, net_a, wr_a, _ = (s["all"] if s else (0, 0.0, 0.0, 0.0))
        tot_sup_net += net_a
        print(f"  {key:34s} 30天 {n_a:>3} 笔 净额 {net_a:>9.2f} 胜率 {wr_a*100:>5.1f}%")
    print(f"  ⇒ 被抑制通道 30 天合计净额 = {tot_sup_net:>9.2f}（为负=抑制方向正确）")
    protected_shadow = [k for k in shadow if shadow[k] and is_protected(k.partition("|")[2])]
    print(f"  被保护名单兜住（永不抑制）：{protected_shadow}")

    print("\n【C. 独立复核：状态窗口 vs DB 重算】")
    mism = []
    for key in sorted(shadow.keys()):
        rec = [x for x in (breaker.get(key, {}).get("recent") or []) if x in (0, 1, True, False)]
        items = by_key.get(key, [])
        if len(rec) < w or len(items) < w:
            continue
        db_wins = sum(1 for x in items[-w:] if x[0] > 0)
        st_wins = sum(1 for x in rec[-w:] if x)
        if db_wins != st_wins:
            mism.append((key, st_wins, db_wins, len(rec), len(items)))
    if mism:
        for k, sw, dw, lr, li in mism:
            print(f"  ❗ {k}: 状态窗 {sw} 胜 / DB 窗 {dw} 胜（状态 {lr} 行 / DB {li} 笔）")
    else:
        print("  ✅ 全部一致（窗口内胜场数逐键相同）")

    print("\n【D. 判定汇总】")
    print(f"  误伤（shadow 但窗口净额>0）：{len(hurt)} 条 {[h[0] for h in hurt]}")
    print(f"  漏报（未 shadow 但窗口胜率<{max_wr} 且净额<0）：{len(miss)} 条 {[m[0] for m in miss]}")
    print(f"  一致（shadow 且窗口净额<=0）：{len(agree)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
