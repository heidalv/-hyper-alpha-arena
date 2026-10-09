# -*- coding: utf-8 -*-
"""G2 限额与频率守卫 —— 防止"一次改太多、改太频"导致无法归因。

为什么需要
==========

P2 会让 LLM 真的改参数。而参数一旦能自动改，最大的风险不是"改错值"（G1 已管），
而是**改得太频、太多，导致事后再也分不清是哪次改动起了作用**。

历史佐证：本会话就吃过一次 —— 在 A/B 跑第一臂 3 分钟时改了
`max_net_directional_ratio`，而该改动对两臂影响**不对称**（B 臂无出库单、
持仓久、更易撞上限）⇒ 会伪造出"维持现状更好"的结论。当时靠人发现并重启了实验。
**自动循环里没有这个人 ⇒ 必须由守卫机械拦截。**

三条限额（事先定死）
====================

  L1 单次调用最多对 **1 个币** 提 `adjust`
     理由：一次改多个 ⇒ 无法归因到具体哪个改动
  L2 同一币 **每小时最多 2 次** 调整
     理由：给每次调整留出 ≥30 分钟的观测窗口
  L3 全局 **10 分钟内最多 1 次** 调整
     理由：与监控频率（10 分钟）对齐，避免同一批数据触发多次改动

状态从 `logs/llm_adjustments.jsonl` 读（**持久化，跨进程**）。
只记 `applied=true` 的条目 —— 被拒绝的不占用配额。

用法：
    .venv\\Scripts\\python.exe scripts\\g2_rate_limit.py            # 自检
    from g2_rate_limit import check_call, append_adjustment
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "llm_adjustments.jsonl"

# 限额参数
MAX_COINS_PER_CALL = 1
MAX_PER_COIN_PER_HOUR = 2
MIN_GAP_MINUTES = 10.0


def _load(now: datetime) -> list:
    """读最近 24 小时的已应用调整（更早的不影响任何限额）。"""
    if not LOG.exists():
        return []
    cutoff = now - timedelta(hours=24)
    out = []
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if not r.get("applied"):
            continue          # 被拒绝的不占配额
        try:
            ts = datetime.fromisoformat(str(r.get("ts")))
        except Exception:
            continue
        if ts.tzinfo is None:
            ts = ts.astimezone()
        if ts >= cutoff:
            out.append({"ts": ts, "symbol": r.get("symbol"), "key": r.get("key")})
    return out


def check_call(items: list, *, now: datetime | None = None) -> tuple:
    """守卫 G2：判断一次调用的全部建议是否放行。

    `items` = [{"symbol":..., "key":..., "value":...}, ...]（**只传已过 G1 的**）

    返回 `(allowed_items, rejected)`：
      · `allowed_items` —— 放行的建议（**最多 1 条**，见 L1）
      · `rejected`      —— 每条被拒的 `{symbol,key,value,rule,reason}`
    """
    now = now or datetime.now().astimezone()
    hist = _load(now)
    allowed, rejected = [], []

    # L1：单次最多 1 个币（先按币去重，保留第一条）
    seen_syms = set()
    for it in items:
        s = it.get("symbol")
        if s in seen_syms:
            rejected.append({**it, "rule": "L1",
                             "reason": f"同一次调用已对 {s} 提过调整（单次最多 "
                                       f"{MAX_COINS_PER_CALL} 个币）"})
        else:
            seen_syms.add(s)

    # L3：全局 10 分钟最短间隔
    if hist:
        last = max(h["ts"] for h in hist)
        gap = (now - last).total_seconds() / 60.0
        if gap < MIN_GAP_MINUTES:
            for it in items:
                if it.get("symbol") in seen_syms:
                    rejected.append({**it, "rule": "L3",
                                     "reason": f"距上次调整仅 {gap:.1f} 分钟，"
                                               f"需 ≥{MIN_GAP_MINUTES:.0f} 分钟"})
            return [], rejected

    # L2：同一币每小时最多 2 次
    for it in items:
        s = it.get("symbol")
        if s not in seen_syms:
            continue
        if any(r.get("rule") == "L3" and r.get("symbol") == s for r in rejected):
            continue
        n_recent = sum(1 for h in hist
                       if h["symbol"] == s
                       and (now - h["ts"]).total_seconds() <= 3600)
        if n_recent >= MAX_PER_COIN_PER_HOUR:
            rejected.append({**it, "rule": "L2",
                             "reason": f"{s} 近 1 小时已调整 {n_recent} 次"
                                       f"（上限 {MAX_PER_COIN_PER_HOUR}）"})
        else:
            allowed.append(it)

    # L1 严格化：最终只放行 1 条
    if len(allowed) > 1:
        for extra in allowed[1:]:
            rejected.append({**extra, "rule": "L1",
                             "reason": f"单次调用最多放行 {MAX_COINS_PER_CALL} 条"})
        allowed = allowed[:1]

    return allowed, rejected


def append_adjustment(*, symbol: str, key: str, value, applied: bool,
                      rule: str = "", reason: str = "",
                      old_value=None, now: datetime | None = None) -> None:
    """记录一次调整（**只有 applied=True 的占配额**）。"""
    now = now or datetime.now().astimezone()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": now.isoformat(), "symbol": symbol, "key": key,
           "old": old_value, "value": value, "applied": bool(applied),
           "rule": rule, "reason": reason}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def main() -> int:
    print("=" * 92)
    print("G2 限额与频率守卫（自检）")
    print("=" * 92)
    print(f"\n  L1 单次调用最多 {MAX_COINS_PER_CALL} 个币")
    print(f"  L2 同一币每小时最多 {MAX_PER_COIN_PER_HOUR} 次")
    print(f"  L3 全局最短间隔 {MIN_GAP_MINUTES:.0f} 分钟")
    print(f"  状态文件：{LOG}")
    print(f"  当前已记录：{len(_load(datetime.now().astimezone()))} 条（近 24h，仅 applied）")

    now = datetime.now().astimezone()
    print(f"\n  ── 自检用例（用 --dry 语义，不写状态）──")
    a, r = check_call([{"symbol": "XRP", "key": "spread_mult", "value": 0.45}], now=now)
    print(f"    单币单条          -> 放行 {len(a)} 拒绝 {len(r)}")
    a, r = check_call([{"symbol": "XRP", "key": "spread_mult", "value": 0.45},
                       {"symbol": "SOL", "key": "spread_mult", "value": 0.45}], now=now)
    print(f"    两币两条          -> 放行 {len(a)} 拒绝 {len(r)}"
          f"  （期望 1/1：L1）")
    if r:
        print(f"      {r[0].get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
