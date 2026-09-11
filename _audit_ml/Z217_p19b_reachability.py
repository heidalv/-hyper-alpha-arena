# -*- coding: utf-8 -*-
"""[§81 核验 2026-09-11] P19-B「统一出口全接」的**可达性**核验：闸门查询键 vs 归因记录键。

发现路径（本脚本固化为可复现判定）：
  * **写入侧** `unified_exit_executor._execute_raw()`：`action=="close"` 时
    `reason = req.exit_channel or req.reason`（**exit_channel 优先**）；
  * **查询侧** `unified_exit_executor.should_block()` 的通道熔断块：
    `_rsn = req.reason or req.exit_channel`（**reason 优先**）；
  * `master_execution.py` 两处构造请求时 `reason=f"master_{mode}"`（如 `master_running`）
    而 `exit_channel=f"master_{mode}_{action}"`（如 `master_running_close`）⇒ **两键不同**；
  * MLTO 路径 `_exec_close(reason=...)` 传入与落库同一个字符串 ⇒ 键一致（无此问题）。

所以 Master 半边在**键层面**不可达：闸门永远查 `mid|master_running`，而归因只会写
`mid|master_running_close`。本脚本：(A) 从源码证成这个优先级颠倒；(B) 枚举两条路径
**实际能产生的键**；(C) 与 shadow 面求交 ⇒ 今天真正生效的抑制面；(D) 量化"死键"背后的钱。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)

from backend.services.exit.channel_breaker_gate import channel_of, is_protected  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

UE = ROOT / "backend" / "services" / "unified_exit_executor.py"
ME = ROOT / "backend" / "services" / "full_auto" / "master_execution.py"
HT = ROOT / "backend" / "services" / "full_auto" / "hold_timeout_trend_review.py"
ML = ROOT / "backend" / "services" / "full_auto" / "midlong_position_manager.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def part_a() -> bool:
    print("=" * 96)
    print("(A) 源码证成：查询键与写入键的**优先级颠倒**")
    print("=" * 96)
    ue = _read(UE)
    me = _read(ME)
    ht = _read(HT)
    checks = [
        ("查询侧 reason 优先", r'_rsn\s*=\s*str\(req\.reason or ""\)\s*or\s*str\(req\.exit_channel or ""\)', ue),
        ("写入侧 exit_channel 优先", r'_reason\s*=\s*req\.exit_channel or req\.reason', ue),
        ("Master 请求 reason=master_{mode}", r'reason=f"master_\{mode\}"', me),
        ("Master 请求 exit_channel=master_{mode}_{action}", r'exit_channel=_exit_ch\b', me),
        ("hold_timeout 复查 reason==exit_channel", r'reason="trend_review_close" if _action == "close"', ht),
        ("MLTO 直接以 reason 调闸", r'_cb_gate\(str\(reason or ""\), _tier\)', _read(ML)),
    ]
    ok = True
    for name, pat, src in checks:
        hit = re.search(pat, src) is not None
        ok &= hit
        print(f"  [{'✅' if hit else '❌'}] {name}")
    # 直接打印关键行，便于人工核对
    for label, path, pat in (("查询侧", UE, r'_rsn = str'), ("写入侧", UE, r'_reason = req.exit_channel')):
        for i, line in enumerate(_read(path).splitlines(), 1):
            if pat in line:
                print(f"  {label} {path.name}:{i}  {line.strip()}")
    for i, line in enumerate(me.splitlines(), 1):
        if 'reason=f"master_{mode}"' in line or 'exit_channel=_exit_ch' in line:
            print(f"  Master master_execution.py:{i}  {line.strip()}")
    return ok


def gate_reachable_keys():
    """两条路径**今天**能产生的闸门查询键（tier 由持仓决定，这里只列通道）。"""
    return {
        "MLTO(_exec_close)": sorted({
            channel_of("thesis_should_close: x"), channel_of("thesis_invalidation: x"),
            channel_of("factor_invalidated: x"), channel_of("trend_broken: x"),
            "midlong", "no_progress", "bias_reversal", "profit_drawdown_stage",
        }),
        "Master(should_block,_pc)": ["master_running", "master_defensive", "ai_take_profit", "hold_timeout_review"],
        "hold_timeout_trend_review": ["trend_review_close", "trend_review_reduce"],
    }


def db_master_channels(days: int = 30):
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        rows = db.execute(text(f"""
            select lower(coalesce(timeframe_tier,'?')) as tier, coalesce(close_reason,'?') as reason,
                   count(*) as n,
                   sum((case when lower(side) in ('long','buy') then (close_price-entry_price)*size
                             else -(close_price-entry_price)*size end)
                       + coalesce(partial_realized_pnl,0) - coalesce(partial_fee_paid,0)) as net
            from paper_positions
            where status='closed' and closed_at >= now() - interval '{int(days)} day'
              and (close_reason ilike 'master%' or close_reason ilike '%trend_review%'
                   or close_reason ilike '%hold_timeout%')
            group by 1,2 order by 1, n desc
        """)).fetchall()
        return rows
    finally:
        db.close()


def main() -> int:
    ok = part_a()
    st = json.loads((ROOT / "data" / "fusion_attribution.json").read_text(encoding="utf-8"))
    shadow = {k for k, v in (st.get("breaker_shadow") or {}).items() if v}

    print()
    print("=" * 96)
    print("(B) 两条路径实际可产生的闸门键 / (C) 与 shadow 面求交")
    print("=" * 96)
    reach = gate_reachable_keys()
    reachable_channels = {c for lst in reach.values() for c in lst}
    for path, keys in reach.items():
        print(f"  {path}: {keys}")
    live = sorted(k for k in shadow if k.partition('|')[2] in reachable_channels and not is_protected(k.partition('|')[2]))
    dead = sorted(k for k in shadow if k not in live)
    print(f"\n  今天**真正会被命中**的 shadow 键 {len(live)} 条：{live}")
    print(f"  其余 shadow 键 {len(dead)} 条（保护性 或 键不可达）：{dead}")
    # 逐键说明不可达原因
    for k in dead:
        tier, _, ch = k.partition("|")
        why = "保护性通道" if is_protected(ch) else "键不可达（无调用点产生该键）"
        print(f"    - {k}: {why}")

    print()
    print("=" * 96)
    print("(D) DB 里 master_*/trend_review*/hold_timeout* 通道的真实经济含义（近 30 天，净口径）")
    print("=" * 96)
    try:
        rows = db_master_channels(30)
    except Exception as exc:
        print(f"  DB 读取失败：{exc}")
        return 2
    if not rows:
        print("  （无）")
    for tier, reason, n, net in rows:
        ch = channel_of(reason)
        key = f"{tier}|{ch}"
        flag = "shadow" if key in shadow else "-"
        print(f"  {tier:8s} {ch:26s} 笔数={n:<4} 净额={float(net or 0):>9.2f}  {flag}  reason={str(reason)[:44]}")
    print("\n结论：Master 半边的查询键（reason 优先）与写入键（exit_channel 优先）不一致 ⇒ 该半边"
          "\n      在键层面不可达；当前因无 master_* 键达到样本门槛，**今天的行为与不接线等价**。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
