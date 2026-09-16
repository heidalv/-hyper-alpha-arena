# -*- coding: utf-8 -*-
"""[调研轮16 2026-09-16] **AI 候选 × 策略供给**：找"选了也开不了单"的 symbol。

## 背景（真实漏斗）

`data/midlong_direction_audit.jsonl` 近 7 天按 symbol 归因（session=fa_7e12e7a1b6）：

    DOT  25 行 全部 skip@exec  reason=eval_false:no_active_strategy
                extra={"block_layer": "proposal_execution", "block_detail": "tier=mid"}
    FET  31 行 全部 skip@writer  regime_extreme×26 / long_regime_block×5
    TIA  51 行 全部 skip@writer  long_regime_block×51

`no_active_strategy` 来自 `proposal_execution.py:93` —— 当
`resolve_independent_strategy(db, session, sym, tier)` 查不到
`ai_strategies(primary_symbol=sym, timeframe_tier=tier, status='active')` 时，
**提案在进入任何风险闸之前就被丢弃**。也就是说：AI 选币选中了某 symbol、
论题也通过了，只要执行层没有该 symbol+层的策略行，就永远开不了仓 —— 而这不是
风险控制，是**策略供给缺口**。

本脚本把两侧对齐输出（完全只读）：
  A. 生产函数给出的**当前 AI 候选池**（mid / long，与扫描批次同源）；
  B. 每个候选 symbol 是否有可执行策略（tier × symbol × account × status）；
  C. 全部 active 策略的覆盖清单（按层分组），便于一眼看出池子有多窄。

用法：
  python backend/scripts/diag_strategy_coverage.py
  python backend/scripts/diag_strategy_coverage.py --session fa_7e12e7a1b6
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="fa_7e12e7a1b6")
    ap.add_argument("--show-config", action="store_true",
                    help="列出每个 active 策略的关键配置（判断能否克隆新 symbol 策略）")
    args = ap.parse_args()

    from backend.database.connection import SessionLocal
    from backend.database.models import AIStrategy, FullAutoSession

    db = SessionLocal()
    try:
        session = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == args.session).first()
        if session is None:
            print(f"[FAIL] session 不存在: {args.session}")
            return 2
        acct = int(getattr(session, "paper_account_id", None)
                   or getattr(session, "account_id", None) or 0)

        # ── A. 当前 AI 候选池（与扫描批次同源）────────────────────
        from backend.services.auto_coin_selector import (
            get_ai_long_candidates_for_session, get_ai_mid_candidates_for_session,
        )

        mid_pool = list(get_ai_mid_candidates_for_session(args.session, db=db) or [])
        long_pool = list(get_ai_long_candidates_for_session(args.session, db=db) or [])
        print(f"== A. AI 候选池 (session={args.session}, paper_account={acct}) ==")
        print(f"  mid : {mid_pool or '（空）'}")
        print(f"  long: {long_pool or '（空）'}")

        print(f"  session.symbols         = "
              f"{(getattr(session, 'symbols', None) or '')[:160]}")
        print(f"  session.auto_coin_symbols = "
              f"{(getattr(session, 'auto_coin_symbols', None) or '')[:160]}")
        print(f"  session.active_strategy_ids = "
              f"{len(getattr(session, 'active_strategy_ids', None) or [])} 个")

        # ── B. 策略供给：全部 active 策略 ────────────────────────
        rows = db.query(AIStrategy).filter(AIStrategy.status == "active").all()
        by_tier: dict[str, list] = defaultdict(list)
        for s in rows:
            by_tier[(getattr(s, "timeframe_tier", None) or "?").lower()].append(s)

        print(f"\n== B. active 策略总数 {len(rows)} ==")
        for t in sorted(by_tier):
            syms = sorted({(getattr(s, "primary_symbol", "") or "?").upper()
                           for s in by_tier[t]})
            print(f"  tier={t:<6} {len(by_tier[t]):>4} 条, {len(syms):>3} 个 symbol: "
                  f"{','.join(syms[:40])}{' …' if len(syms) > 40 else ''}")

        if args.show_config:
            print("\n== B2. 策略关键配置（tier=mid/long）==")
            print(f"  {'sid':<12}{'sym':<9}{'tier':<6}{'acct':<6}{'size%':>7}"
                  f"{'lev':>5}{'maxlev':>7}{'sl%':>6}{'tp%':>6}{'auto_mode':<12}{'factors':>8}")
            for t in ("mid", "long"):
                for s in sorted(by_tier.get(t, []),
                                key=lambda x: (getattr(x, "primary_symbol", "") or "")):
                    print(f"  {str(getattr(s, 'strategy_id', ''))[:11]:<12}"
                          f"{str(getattr(s, 'primary_symbol', '') or ''):<9}{t:<6}"
                          f"{str(getattr(s, 'account_id', '') or ''):<6}"
                          f"{float(getattr(s, 'max_position_size', 0) or 0) * 100:>7.2f}"
                          f"{float(getattr(s, 'default_leverage', 0) or 0):>5.1f}"
                          f"{float(getattr(s, 'max_leverage', 0) or 0):>7.1f}"
                          f"{float(getattr(s, 'stop_loss_pct', 0) or 0) * 100:>6.1f}"
                          f"{float(getattr(s, 'take_profit_pct', 0) or 0) * 100:>6.1f}"
                          f"{str(getattr(s, 'auto_mode', '') or '-'):<12}"
                          f"{len(getattr(s, 'enabled_factors', None) or []):>8}")

        # ── C. 交叉：候选是否有策略 ─────────────────────────────
        def _has(sym: str, tier: str) -> list:
            return [s for s in rows
                    if (getattr(s, "primary_symbol", "") or "").upper() == sym
                    and (getattr(s, "timeframe_tier", None) or "").lower() == tier]

        print("\n== C. 候选落地可行性（exec 层 resolve_independent_strategy）==")
        gaps = []
        for tier, pool in (("mid", mid_pool), ("long", long_pool)):
            if not pool:
                continue
            for sym in pool:
                sym_u = str(sym).upper()
                hits = _has(sym_u, tier)
                same_acct = [s for s in hits
                             if int(getattr(s, "account_id", 0) or 0) == acct]
                mark = "✅" if hits else "❌ 无策略 ⇒ no_active_strategy"
                extra = ""
                if hits and not same_acct:
                    extra = f"（跨账户可用: {sorted({int(getattr(s,'account_id',0) or 0) for s in hits})}）"
                print(f"  {tier:<5}{sym_u:<9}{mark} {extra}")
                if not hits:
                    gaps.append((tier, sym_u))
        print(f"\n  缺口 {len(gaps)} 个: {gaps}")
        print("  说明：这些 symbol 无论论题/闸门多好，都会在 proposal_execution "
              "第 93 行被丢弃（不消耗风险预算，但也永不成交）。")

        # ── D. 会话 symbol 的策略不变量（零 active = 该币静默开不出单）──────
        print("\n== D. 不变量：会话交易 symbol 是否都有 active 策略 ==")
        broken = []
        for s in db.query(FullAutoSession).filter(
            FullAutoSession.status.in_(["running", "defensive"])
        ).all():
            _acct = getattr(s, "paper_account_id", None) or getattr(s, "account_id", None)
            _syms = sorted({str(x).upper() for x in (
                list(s.symbols or []) + list(getattr(s, "auto_coin_symbols", None) or []))
                if x})
            if not _acct or not _syms:
                continue
            _have = {(str(r.primary_symbol or "").upper(),
                      str(r.timeframe_tier or "mid").lower())
                     for r in rows if int(getattr(r, "account_id", 0) or 0) == int(_acct)}
            print(f"  {s.session_id} (acct={_acct}, {len(_syms)} symbols):")
            for _sym in _syms:
                _miss = [t for t in ("short", "mid", "long") if (_sym, t) not in _have]
                if len(_miss) == 3:
                    print(f"     ❌ {_sym:<9} 三层全无 active ⇒ 该币开不出单")
                    broken.append(f"{s.session_id}:{_sym}")
                elif _miss:
                    print(f"     ⚠️ {_sym:<9} 缺 {'/'.join(_miss)}（其它层可用）")
        if broken:
            print(f"\n  **不变量破坏 {len(broken)} 处**: {broken}")
            print("  成因常见于：启动去重 keeper 选了 paused 那条（调研轮17 已修）／"
                  "健康或生命周期暂停最后一条 active。检测只报警，不自动改状态。")
        else:
            print("\n  ✅ 所有会话 symbol 均至少有一层 active 策略")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
