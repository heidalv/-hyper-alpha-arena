# -*- coding: utf-8 -*-
"""[h716 阶段2 2026-10-02] 选币 bandit(影子提案模式)。

设计文档 §L3:选币 = 带切换成本的 bandit。
  - 每币后验 = 7 天指数衰减每腿净 bp(均值/σ/n,来自 h714);
  - UCB 得分 = mean + κ·σ/√n_eff(κ=2,不确定性奖励探索);
  - 切换成本:非在任币的得分减去 switch_cost(实测中位;缺省 2bp,来源 L9 冷启动档);
  - 在任保护:入槽 <1h 的币,除非后验**显著为负**(mean+2·SE<0)不得换出;
  - 亏损淘汰(pnl_decayed)与 v5 同源,直接排除;
  - 探索预算:每次最多换入 2 个新币(与 v5 max_new 同);
  - **默认影子模式**:只写 data/bandit_proposal_last.json + 控制台报告,
    不落库;--apply 才经 evolution 落库(DSH 桥复核后使用)。

24h 影子对比口径:对比"bandit 提案宇宙"与"v5 实际宇宙"在随后 24h 的
每腿净 bp(由 h714 的每日后验更新来判定)。
"""
from __future__ import annotations

import importlib.util
import io
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"
KAPPA = 2.0
SLOTS = 6
MAX_NEW = 2
MIN_TENURE_SEC = 3600
DEFAULT_SWITCH_COST_BP = 2.0


def _dsn() -> str:
    _spec = importlib.util.spec_from_file_location("h425", ROOT / "scripts" / "h425_repair_trial.py")
    _h = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_h)
    return _h.read_env_dsn()


def main() -> int:
    from backend.services import lane_registry as reg

    meta = (reg.get_lane(LANE) or {}).get("meta") or {}
    current = [str(s).upper() for s in (meta.get("symbols") or [])]
    # 在任币龄(与 h708 同法)
    from datetime import datetime
    now = time.time()
    entry_ts: Dict[str, float] = {}
    for o in sorted(meta.get("ops_changes") or [], key=lambda x: x.get("ts") or ""):
        if o.get("op") != "set_symbols" or not o.get("after"):
            continue
        t = datetime.fromisoformat(str(o["ts"]).replace("Z", "+00:00")).timestamp()
        for s in o["after"]:
            entry_ts.setdefault(str(s).upper(), t)

    post_path = ROOT / "data" / "coin_posterior_last.json"
    try:
        post = json.loads(post_path.read_text(encoding="utf-8"))
    except Exception:
        print("无 coin_posterior_last.json(先跑 h714)")
        return 1
    pcoins = post.get("per_coin") or {}
    summary = post.get("summary") or {}
    sw_cost = float(summary.get("switch_cost_median_bp") or DEFAULT_SWITCH_COST_BP)

    # [h732 2026-10-02 审计修复] bandit 必须与选币的**价差地板**同口径:
    # 否则它会提名窄价差币(如 XRP),而概率引擎在那类币上无燃料(捕获被
    # 不穿越钳制锁死)——与 h731 是同一类"机制在、前提不满足"的管道断裂。
    # 用 v5 选择器同源的 SPREAD_MIN_BP + 近 2h 全价差过滤。
    spread_ok: Dict[str, bool] = {}
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("h329_selector_v5",
                                             ROOT / "scripts" / "h329_selector_v5.py")
        _v5 = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_v5)
        _thr = float(getattr(_v5, "SPREAD_MIN_BP", 5.0))
        from backend.services.market_maker.attribution import _market_dsn
        import psycopg as _pg
        _since = time.time() - 7200
        with _pg.connect(_market_dsn(), autocommit=True) as _c, _c.cursor() as _cur:
            _cur.execute(
                "SELECT symbol, AVG((ask_px-bid_px)/((ask_px+bid_px)/2)*1e4), count(*)"
                " FROM asterdex_book_ticker WHERE event_ts_ms >= %s"
                " AND bid_px>0 AND ask_px>bid_px GROUP BY 1",
                (int(_since * 1000),))
            for _r in _cur.fetchall():
                _sym = str(_r[0]).upper().replace("USDT", "")
                if int(_r[2]) >= 100 and float(_r[1]) >= _thr:
                    spread_ok[_sym] = True
    except Exception:
        spread_ok = {k: True for k in pcoins}   # 过滤失败则不误伤(与旧行为同)

    # 亏损淘汰(与 v5 同源)
    try:
        from backend.services.market_maker.qspeed import pnl_decayed_coins
        pnl_dec = set(pnl_decayed_coins(LANE))
    except Exception:
        pnl_dec = set()

    scored: List[dict] = []
    for s, v in pcoins.items():
        if not spread_ok.get(s, False):
            continue          # [h732] 窄价差币不进提案(无燃料)
        mean = float(v.get("mean_bp") or 0.0)
        std = float(v.get("std_bp") or 0.0)
        n = max(1, int(v.get("n") or 0))
        se = std / math.sqrt(n)
        ucb = mean + KAPPA * se
        # 切换成本:非在任扣一次
        if s not in current:
            ucb -= sw_cost
        # 在任保护:入槽<1h 且后验不显著为负 ⇒ 上浮(强保护)
        protected = False
        if s in current and (now - entry_ts.get(s, now)) < MIN_TENURE_SEC:
            if mean + 2 * se >= 0:
                protected = True
                ucb += 999.0  # 保住在任
        scored.append({"sym": s, "mean": mean, "std": std, "n": n, "ucb": ucb,
                       "in_universe": s in current, "protected": protected,
                       "decayed": s in pnl_dec})
    scored.sort(key=lambda x: -x["ucb"])

    print(f"切换成本 {sw_cost}bp/腿 | 现役 {current} | 亏损淘汰 {sorted(pnl_dec)}\n")
    print(f"  {'币':<10}{'mean':>7}{'σ':>6}{'n':>5}{'UCB':>8}{'状态':>12}")
    for r in scored[:16]:
        st = "现役" if r["in_universe"] else ("淘汰" if r["decayed"] else "")
        if r["protected"]:
            st = "现役(保护)"
        print(f"  {r['sym']:<10}{r['mean']:>+7.2f}{r['std']:>6.2f}{r['n']:>5}{r['ucb']:>+8.2f}  {st}")

    # 选前 SLOTS 个(排除亏损淘汰;保护币强制占位)
    picked: List[str] = []
    protected_held = [r for r in scored if r["protected"]]
    picked = [r["sym"] for r in protected_held][:SLOTS]
    for r in scored:
        if len(picked) >= SLOTS:
            break
        if r["sym"] in picked or r["decayed"]:
            continue
        picked.append(r["sym"])
    # 探索预算:最多 MAX_NEW 个新币
    new_in = [s for s in picked if s not in current]
    if len(new_in) > MAX_NEW:
        for s in new_in[MAX_NEW:]:
            if s in picked:
                picked.remove(s)
        for r in scored:
            if len(picked) >= SLOTS:
                break
            if r["sym"] in picked or r["decayed"] or r["sym"] not in current:
                continue
            picked.append(r["sym"])
    picked = picked[:SLOTS]

    proposal = {
        "ts": now, "mode": "shadow", "kappa": KAPPA, "switch_cost_bp": sw_cost,
        "current": sorted(current), "proposed": sorted(picked),
        "new_in": sorted(new_in[:MAX_NEW]),
        "dropped": sorted(set(current) - set(picked)),
        "top_scored": [{"sym": r["sym"], "mean": r["mean"], "n": r["n"],
                        "ucb": round(r["ucb"], 2)} for r in scored[:10]],
    }
    (ROOT / "data" / "bandit_proposal_last.json").write_text(
        json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
    # [h721] 提案历史(供 24h 影子对比:每 6h 一条,compare 脚本回溯评估)
    try:
        with open(ROOT / "data" / "bandit_proposal_history.jsonl", "a",
                  encoding="utf-8") as _fh:
            _fh.write(json.dumps({"ts": now, "current": sorted(current),
                                  "proposed": sorted(picked),
                                  "new_in": proposal["new_in"],
                                  "dropped": proposal["dropped"]},
                                 ensure_ascii=False) + "\n")
    except Exception:
        pass
    print(f"\n== 影子提案 ==\n  现役: {sorted(current)}\n  提案: {sorted(picked)}")
    print(f"  换入: {proposal['new_in']} | 换出: {proposal['dropped']}")
    print("✓ 已写 data/bandit_proposal_last.json(影子;--apply 未启用,等 24h 影子对比)")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
