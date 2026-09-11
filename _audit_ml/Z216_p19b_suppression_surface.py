# -*- coding: utf-8 -*-
"""[§81 核验 2026-09-11] P19-B 统一熔断闸的**抑制面**安全核验（只读）。

要回答三个问题：
  ① 现在**真的会被抑制**的通道有哪些（shadow 面 ∩ 非保护面）？
  ② 这些通道在 DB 里的历史经济含义是什么（是不是"叙事/裁量型"而不是"降险型"）？
  ③ **潜在越界风险**：DB 里实际出现过的、语义上属于"降险"的通道里，有哪些**没被**
     `PROTECTED_CHANNEL_MARKERS` 覆盖？——没覆盖的通道一旦胜率掉到阈值下，就会被熔断
     ⇒ 等于把降险出口堵死（这是本闸最危险的失效模式，必须在数据上枚举出来）。
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# 关键：**必须先加载 .env**，否则本进程看不到 `EXIT_CHANNEL_SHADOW_MIN_N=15`，
# 会退回代码内默认 30 ⇒ 用错窗口口径（这正是本脚本第一版犯过的错）。
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)

from backend.services.exit.channel_breaker_gate import (  # noqa: E402
    PROTECTED_CHANNEL_MARKERS, channel_of, is_protected, unified_enabled,
)
from backend.services.source_attribution import attribution  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

#: 语义上属于"降低风险"的通道关键词（用于题③的越界扫描）
RISK_REDUCING_HINTS = (
    "timeout", "hold", "超时", "兜底", "fallback", "review", "sentinel", "哨兵",
    "protect", "prot",
)


def effective_thresholds():
    """与生产**同源**的阈值：`record_close`/`rebuild_breaker_shadow` 都直接读 os.environ。"""
    try:
        min_n = int(float(os.environ.get("EXIT_CHANNEL_SHADOW_MIN_N", "30") or 30))
        max_wr = float(os.environ.get("EXIT_CHANNEL_SHADOW_MAX_WR", "0.40") or 0.40)
    except (TypeError, ValueError):
        min_n, max_wr = 30, 0.40
    rolling = int(os.environ.get("SOURCE_ATTR_ROLLING_WINDOW", "30") or 30)
    return min_n, max_wr, rolling, min(min_n, rolling)


def shadow_rows():
    """返回 `[(key, win_n, wr, live_flag)]`。

    `win_n` = 生产实际评估窗口 `min(MIN_N, len(recent), ROLLING_WINDOW)`；
    `live_flag` = **权威判定**（调用生产函数 `attribution.exit_channel_shadow`）。
    """
    p = Path(ROOT) / "data" / "fusion_attribution.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    shadow_map = data.get("breaker_shadow") or {}
    stats = data.get("breaker") or {}
    _, max_wr, _, win_n_cfg = effective_thresholds()
    out = []
    for key in sorted(shadow_map.keys()):
        st = stats.get(key) or {}
        recent = [x for x in (st.get("recent") or []) if x in (0, 1, True, False)]
        win_n = min(win_n_cfg, len(recent)) if recent else 0
        wr = (sum(1 for x in recent[-win_n:] if x) / win_n) if win_n else None
        tier, _, ch = key.partition("|")
        live = bool(attribution.exit_channel_shadow(ch, tier))
        out.append((key, win_n, wr, live))
    return out


def db_channels(days: int = 30):
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        rows = db.execute(text(f"""
            select lower(coalesce(timeframe_tier,'?')) as tier,
                   coalesce(close_reason,'?') as reason,
                   count(*) as n,
                   sum((case when lower(side) in ('long','buy')
                             then (close_price-entry_price)*size
                             else -(close_price-entry_price)*size end)
                       + coalesce(partial_realized_pnl,0)
                       - coalesce(partial_fee_paid,0)) as net
            from paper_positions
            where status='closed' and closed_at >= now() - interval '{int(days)} day'
              and lower(coalesce(timeframe_tier,'')) in ('mid','long')
            group by 1,2 order by n desc
        """)).fetchall()
        return rows
    finally:
        db.close()


def main() -> int:
    min_n, max_wr, rolling, win_n = effective_thresholds()
    print("=" * 96)
    print("① 当前真实 shadow 面 × 保护面 ⇒ 实际可抑制通道")
    print("=" * 96)
    print(f"生效阈值（与生产同源，读 os.environ，已加载 .env）：MIN_N={min_n} MAX_WR={max_wr} "
          f"ROLLING_WINDOW={rolling} ⇒ 评估窗口 win_n=min(MIN_N,ROLLING_WINDOW)={win_n}")
    print(f"unified_enabled() = {unified_enabled()}；PROTECTED markers = {len(PROTECTED_CHANNEL_MARKERS)} 个")
    rows = shadow_rows()
    real, blocked_by_protection, mismatch = [], [], []
    for key, w_n, wr, live in sorted(rows, key=lambda kv: (kv[2] if kv[2] is not None else 1)):
        tier, _, ch = key.partition("|")
        prot = is_protected(ch)
        recomputed = (wr is not None and wr < max_wr and w_n >= win_n)
        if recomputed != live:
            mismatch.append((key, w_n, wr, live, recomputed))
        tag = "保护⇒永不抑制" if prot else ("**可抑制**" if live else "未熔断")
        print(f"  {key:36s} n={w_n:<3} wr={(wr if wr is None else round(wr*100,1))!s:>6}  live={str(live):5s} {tag}")
        if live:
            (blocked_by_protection if prot else real).append((key, w_n, wr))
    print(f"\n  实际可抑制 {len(real)} 条：{[k for k, _, _ in real]}")
    print(f"  被保护名单兜住 {len(blocked_by_protection)} 条：{[k for k, _, _ in blocked_by_protection]}")
    print(f"  持久化标志 vs 重算 不一致：{len(mismatch)} 条 {mismatch if mismatch else ''}")

    print()
    print("=" * 96)
    print("② 这些通道的历史经济含义（DB 近 30 天 mid/long，净口径）")
    print("=" * 96)
    try:
        chans = db_channels(30)
    except Exception as exc:  # pragma: no cover
        print(f"  DB 读取失败：{exc}")
        return 2
    agg = defaultdict(lambda: [0, 0.0])
    for tier, reason, n, net in chans:
        ch = channel_of(reason)
        agg[ch][0] += int(n)
        agg[ch][1] += float(net or 0)
    sup_set = {k.partition("|")[2] for k, _, _ in real}
    print(f"  {'通道':26s} {'笔数':>5} {'净额$':>10}  判定")
    for ch, (n, net) in sorted(agg.items(), key=lambda kv: kv[1][1]):
        mark = "**可抑制**" if ch in sup_set else ("保护" if is_protected(ch) else "-")
        print(f"  {ch:26s} {n:>5} {net:>10.2f}  {mark}")

    print()
    print("=" * 96)
    print("③ 越界风险扫描：DB 出现过的『降险语义』通道是否都被保护名单覆盖")
    print("=" * 96)
    riskish = [ch for ch in agg if any(h in ch for h in RISK_REDUCING_HINTS)]
    holes = [ch for ch in riskish if not is_protected(ch)]
    for ch in sorted(riskish):
        tag = "保护✅" if is_protected(ch) else "**未保护⚠️**"
        print(f"  {ch:26s} {tag}  笔数={agg[ch][0]} 净额={agg[ch][1]:.2f}")
    print(f"\n  未保护的降险语义通道 {len(holes)} 条：{sorted(holes)}")
    hot = [ch for ch in holes if ch in sup_set]
    print(f"  其中**当前已被 shadow** 的 {len(hot)} 条：{sorted(hot)}  ← 非空即为越界（降险出口被堵）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
