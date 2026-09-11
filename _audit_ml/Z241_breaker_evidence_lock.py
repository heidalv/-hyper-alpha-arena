# -*- coding: utf-8 -*-
"""[§84 核查 2026-09-11 / 目标①③] 通道熔断的**证据自锁**（latent self-lock）核查。

假设 H：某通道一旦被 shadow，它的**离场就被抑制** ⇒ 该通道再也不会产生新样本
（`record_close` 不会被调用）⇒ 滚动窗**永久冻结**在该胜率上 ⇒ **永久抑制、无自愈**。

本脚本用三条独立证据验证 H（只读）：
  ① **代码路径**：抑制发生在 `record_close()` **之前**（MLTO `_exec_close` 直接 return None；
     Master `should_block` blocked ⇒ 不调用 `execute()`）⇒ 抑制通道不产生样本；
  ② **状态面**：`breaker.recent` 的窗口是否只增不减、以及被抑制通道的最新样本时间；
  ③ **数据面**：每个 shadow 通道在 DB 里的**最新一笔平仓距今多久**（> limit 天即"冻结候选"）。

同时给出一个**纯函数规则** `is_evidence_frozen(...)`，供决策 P27 时直接使用/测试。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)

from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.exit.channel_breaker_gate import channel_of, is_protected  # noqa: E402
from sqlalchemy import text  # noqa: E402

MLTO = ROOT / "backend" / "services" / "full_auto" / "midlong_position_manager.py"
UEX = ROOT / "backend" / "services" / "unified_exit_executor.py"
MASTER = ROOT / "backend" / "services" / "full_auto" / "master_execution.py"


def is_evidence_frozen(*, shadowed: bool, suppressible: bool, newest_age_days: Optional[float],
                       stale_days: float) -> bool:
    """纯规则：该通道是否"被旧证据锁死"（决策 P27 的判据）。

    条件：① 已 shadow；② 可被抑制（非保护通道，否则抑制不发生、无自锁）；
    ③ 窗口里**最新样本**已超过 `stale_days` 天（证据过期）。
    样本时间为 None（窗口里没有任何 DB 记录）⇒ 视为过期。
    """
    if not (shadowed and suppressible):
        return False
    if newest_age_days is None:
        return True
    return float(newest_age_days) > float(stale_days)


def _code_proof() -> list[tuple[str, bool, str]]:
    """代码路径证据：抑制必须先于 `record_close` 生效（用源码位置比较）。"""
    out = []
    ml = MLTO.read_text(encoding="utf-8", errors="replace")
    i_gate = ml.find("should_suppress as _cb_gate")
    i_close = ml.find("paper_engine.close_position", i_gate if i_gate > 0 else 0)
    out.append(("MLTO: 熔断判定在 close_position 之前（抑制 ⇒ 不落账 ⇒ 无样本）",
                i_gate > 0 and i_close > i_gate,
                f"gate@{i_gate} < close@{i_close}"))
    ue = UEX.read_text(encoding="utf-8", errors="replace")
    i_sup = ue.find('event_type="exit_channel_broken"')
    i_exec = ue.find("def _execute_raw")
    out.append(("Master: 抑制返回 blocked（调用方 continue）⇒ 不执行 execute()",
                i_sup > 0 and i_exec > i_sup,
                f"blocked@{i_sup} < _execute_raw@{i_exec}"))
    me = MASTER.read_text(encoding="utf-8", errors="replace")
    i_blocked = me.find("_gate.blocked")
    i_cont = me.find("continue", i_blocked if i_blocked > 0 else 0)
    out.append(("Master: blocked ⇒ 调用点 continue（不执行离场）",
                i_blocked > 0 and i_cont > i_blocked, f"blocked@{i_blocked} → continue@{i_cont}"))
    return out


def main() -> int:
    stale_days = float(os.environ.get("BREAKER_EVIDENCE_STALE_DAYS", "7") or 7)
    print("=" * 104)
    print(f"通道熔断的证据自锁核查（证据过期阈值 {stale_days:.0f} 天）")
    print("=" * 104)

    print("\n① 代码路径（抑制先于记账 ⇒ 抑制通道不产生新样本）")
    for name, ok, detail in _code_proof():
        print(f"  [{'✅' if ok else '❌'}] {name}  （{detail}）")

    st = json.loads((ROOT / "data" / "fusion_attribution.json").read_text(encoding="utf-8"))
    shadow = {k: bool(v) for k, v in (st.get("breaker_shadow") or {}).items()}
    breaker = st.get("breaker") or {}

    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        newest = {r[0]: r[1] for r in db.execute(text("""
            select lower(coalesce(timeframe_tier,'?')) || '|' || coalesce(close_reason,'?') as k,
                   max(closed_at)
            from paper_positions
            where status='closed' and lower(coalesce(timeframe_tier,'')) in ('short','mid','long','research')
            group by 1
        """)).fetchall()}
    finally:
        db.close()
    newest_ch = {}
    for k, ts in newest.items():
        tier, _, reason = k.partition("|")
        key = f"{tier}|{channel_of(reason)}"
        if key not in newest_ch or ts > newest_ch[key]:
            newest_ch[key] = ts

    now = datetime.now()
    print("\n②/③ 状态窗 × 数据面（shadow 通道的最新样本年龄）")
    print(f"  {'key':34s} {'窗口':>5} {'累计n':>6} {'最新样本':>12} {'年龄(天)':>9} {'可抑制':>7} {'冻结?':>7}")
    frozen = []
    for key, flag in sorted(shadow.items()):
        if not flag:
            continue
        tier, _, ch = key.partition("|")
        rec = [x for x in (breaker.get(key, {}).get("recent") or []) if x in (0, 1, True, False)]
        n_cum = int((breaker.get(key) or {}).get("n") or 0)
        ts = newest_ch.get(key)
        age = ((now - ts).total_seconds() / 86400.0) if ts else None
        supp = not is_protected(ch)
        fz = is_evidence_frozen(shadowed=True, suppressible=supp, newest_age_days=age,
                                stale_days=stale_days)
        if fz:
            frozen.append((key, age, len(rec), n_cum))
        print(f"  {key:34s} {len(rec):>5} {n_cum:>6} "
              f"{(ts.strftime('%m-%d %H:%M') if ts else '—'):>12} "
              f"{(f'{age:.1f}' if age is not None else '—'):>9} "
              f"{('是' if supp else '否(保护)'):>7} {('❗是' if fz else '否'):>7}")

    print("\n④ 汇总")
    sup_n = sum(1 for k, v in shadow.items() if v and not is_protected(k.partition('|')[2]))
    print(f"  shadow 通道 {sum(1 for v in shadow.values() if v)} 个，其中可抑制 {sup_n} 个")
    print(f"  证据冻结（shadow + 可抑制 + 最新样本 > {stale_days:.0f} 天）：{len(frozen)} 个")
    for key, age, wlen, n_cum in frozen:
        age_txt = "无记录" if age is None else f"{age:.1f} 天"
        print(f"    ❗ {key}: 最新样本 {age_txt}｜窗口 {wlen} 笔｜累计 {n_cum} 笔")
    print("\n  机制说明：抑制发生在 `record_close()` **之前** ⇒ 被抑制的通道不再产生样本 ⇒")
    print("  窗口只增不减（在别的通道上增），该通道的胜率**永久冻结**；")
    print("  自愈只能靠：① DB 重回填（`backfill_exit_channel_breaker.py`，手动）")
    print("  ② 证据过期规则（本脚本的 `is_evidence_frozen`，待决策 P27）③ 定期放行一次探针离场。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
