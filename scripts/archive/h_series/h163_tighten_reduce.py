# -*- coding: utf-8 -*-
"""[H163 2026-09-21] 收紧出库挂宽 —— 让减仓腿真的能被吃到。

# 用户反复指出的现象

    XRP 多  520.7162 → 924.9362 → 760.4462   开仓 1.4297~1.4300
    持有 6m26s → 10m45s

单边行情里**持仓长期不清、库存在累积**，且超过 `max_one_side_seconds=300` 很久。
（`timeout_exit_maker_only=True` 的预期行为，但**出库太慢**是缺陷）

# 机制（读 core.py:246-249）

```python
_red_bid = inv_ratio < 0     # 空头 ⇒ 买侧减仓
_red_ask = inv_ratio > 0     # 多头 ⇒ 卖侧减仓
_base_bid = _base_reduce if _red_bid else base
_base_ask = _base_reduce if _red_ask else base
```

**减仓侧挂宽只在持有库存时生效**。具体到用户看到的场景：

  · 多头持仓 ⇒ 减仓腿是**卖侧** ⇒ 按 `spread_mult_reduce × 半价差` 挂
  · `spread_mult_reduce = 0.95` ⇒ 卖单在 `mid + 0.475×价差` 处
  · 行情单边上涨 ⇒ 卖单**一路往上移、始终差一口气**不被吃
  · 而买侧（加仓）在 `mid − 0.25×价差`（spread_mult=0.5）持续成交
  ⇒ **净买入累积**，与实测的净差额 +$1,199.5 完全一致

## 更根本的问题：0.95 把出库单挂在**盘口之外**

市场价差 = 2×半价差（best_bid 到 best_ask）。我们的出库单挂在 `mid ± 0.475×价差`，
而对手方最优价在 `mid ± 0.5×价差` ⇒ **我们挂在对手方后面**。

⇒ **"等被动出库"却把单挂到盘口外，逻辑上自相矛盾**：
   要等成交，就必须比对手方更靠内（`spread_mult_reduce < 1.0` 且越小越靠内）。

# 改什么

`spread_mult_reduce: 0.95 → 0.4`

  · 出库单落在 `mid ± 0.2×价差` 处 ⇒ 明显进到价差内侧 ⇒ 有流量就能被吃
  · 仍然是 maker（费率 0）⇒ 不产生 taker 费
  · 代价：每笔出库少赚一点价差。实测该币出库腿的价差项是 +0.11~0.75bp，
    收紧后大约减半 —— 但换来的是**持仓时间与方向性敞口的下降**

# 同时把进场侧设回已验证的 0.5

（A/B 测试被中止，未出结论；进场侧两次扫描的最优是 0.5：
净 +0.377bp / 行情项 +0.107bp，而 0.9/1.3/1.8 全为负）

用法：
    .venv\\Scripts\\python.exe scripts\\h163_tighten_reduce.py
    .venv\\Scripts\\python.exe scripts\\h163_tighten_reduce.py --apply
    .venv\\Scripts\\python.exe scripts\\h163_tighten_reduce.py --rollback
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from h146_width_sweep import restart_worker, wait_hot_reload  # noqa: E402

ENV_FILE = ROOT / ".env"
TARGET = {"spread_mult": 0.5, "spread_mult_reduce": 0.4,
          # [F301/H164/H165] 止盈主动平仓阈值（bp）。0 = 关闭。
          # 用真实盘口两轮模拟定出：T=12 时规则期望 +2.560bp/笔，是现实基准
          # (+0.1374bp) 的 18.6×，且严格口径触发率 32.0% ≈ 乐观口径 31.3%。
          # ⚠️ 必须 > taker 费(4bp)；实测 T=3 时整体期望为负。
          "take_profit_bp": 12.0,
          # 触发后先给减仓侧 maker 单 30s（免费）尝试成交，超时才付 taker。
          # 与 `stop_maker_grace_sec=30` 对称。
          "take_profit_maker_grace_sec": 30.0}
ENV_KEYS = {"spread_mult": "MM_SPREAD_MULT",
            "spread_mult_reduce": "MM_SPREAD_MULT_REDUCE"}
# `take_profit_bp` / `take_profit_maker_grace_sec` **不在** env 白名单
# ⇒ 只需写注册表，靠 F251 指纹热更新即可（无需重启）。这是刻意的：
# 止盈阈值属于"运行期议题"（会反复调），不该被启动时冻结的 env 锁住。


def dsn() -> str:
    env = {}
    for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def write_both(key: str, env_key: str | None, v: float) -> None:
    """写注册表；**只有该键在 env 白名单里时才同时写 `.env`**。

    ⚠️ 为什么必须允许 `env_key=None`：`take_profit_bp` 这类键**不在** env 白名单，
    只由注册表热更新。第一版无条件写 `.env` ⇒ `re.escape(None)` 直接 TypeError。
    **白名单键与非白名单键的落盘路径不同，这个差别必须在函数签名里体现出来**，
    否则"给它一个 env 名"这种无意义的调用会静默变成崩溃。
    """
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
            meta = dict(cur.fetchone()[0] or {})
            p = dict(meta.get("params") or {})
            p[key] = v
            meta["params"] = p
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                        " WHERE lane_id='mm_asterdex'",
                        (json.dumps(meta, ensure_ascii=False, default=str),))
        c.commit()
    if not env_key:
        return          # 非白名单键 ⇒ 只写注册表（靠 F251 指纹热更新）
    txt = ENV_FILE.read_text(encoding="utf-8", errors="replace")
    pat = rf"^{re.escape(env_key)}=.*$"
    if re.search(pat, txt, flags=re.M):
        txt = re.sub(pat, f"{env_key}={v:g}", txt, flags=re.M)
    else:
        txt = txt.rstrip("\n") + f"\n{env_key}={v:g}\n"
    ENV_FILE.write_text(txt, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()

    print("=" * 92)
    print("H163  收紧出库挂宽（减仓腿真的能被吃到）")
    print("=" * 92)

    if a.rollback:
        r = {"spread_mult": 0.5, "spread_mult_reduce": 0.95}
        for k, v in r.items():
            write_both(k, ENV_KEYS.get(k), v)
        print(f"  已回退：{r}（需重启 worker）")
        restart_worker()
        return 0

    print(f"\n  {'参数':<24} {'env 键':<26} {'旧':>8} {'新':>8}")
    print("  " + "-" * 70)
    import psycopg
    old = {}
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'params' FROM lane_registry WHERE lane_id='mm_asterdex'")
            cur_p = dict(cur.fetchone()[0] or {})
    for k, v in TARGET.items():
        old[k] = cur_p.get(k)
        print(f"  {k:<24} {ENV_KEYS.get(k) or "（非白名单）":<26} {str(old[k]):>8} {v:>8}")

    print(f"\n  机理：减仓侧 = mid ± {TARGET['spread_mult_reduce']}×半价差 "
          f"= mid ± {TARGET['spread_mult_reduce']*50:.0f}% 价差")
    print(f"        对手方最优价 = mid ± 50% 价差 ⇒ 我们进到价差内侧 "
          f"{(0.5 - TARGET['spread_mult_reduce']*0.5)*100:.0f}%")
    print(f"        （旧值 0.95 ⇒ 挂在对手方**之后** 2.5%，等不到成交）")

    if not a.apply:
        print("\n  （预览）加 --apply 执行")
        return 0

    for k, v in TARGET.items():
        write_both(k, ENV_KEYS.get(k), v)
    print(f"\n  [1/2] 已双写注册表 + .env")
    print(f"  [2/2] 重启 worker（`spread_mult*` 在 env 白名单 ⇒ 必须重启）…")
    restart_worker()
    ok1 = wait_hot_reload(TARGET["spread_mult"], "spread_mult", timeout_s=120.0)
    ok2 = wait_hot_reload(TARGET["spread_mult_reduce"], "spread_mult_reduce",
                          timeout_s=60.0)
    # 止盈阈值不在白名单 ⇒ 热更新即可，但重启后立即就在，顺带核一下
    ok3 = wait_hot_reload(TARGET["take_profit_bp"], "take_profit_bp", timeout_s=60.0)
    print(f"        生效核对：spread_mult={ok1}  spread_mult_reduce={ok2}  "
          f"take_profit_bp={ok3}")
    return 0 if (ok1 and ok2 and ok3) else 1


if __name__ == "__main__":
    raise SystemExit(main())
