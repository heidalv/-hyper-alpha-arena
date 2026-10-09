# -*- coding: utf-8 -*-
"""H188 参数实验的**崩溃安全回滚** —— 专治 2026-09-21 那次生产停摆。

# 事故（真实，停摆 ~1 小时，`ok=False` 15 次）

H178 的 A/B 在 B 臂把 `reduce_quote_disabled` 设成 `True`。
该组合触发了一个未被单测覆盖的 `NameError`（F328）⇒ 车道每个 tick 都失败。
而我 kill 掉 A/B 任务后，它的 `finally: set_limits(**orig_pair)` **没有执行**
⇒ **崩溃配置被留在生产注册表里**，车道继续崩。

两层根因：
  ① 代码：`plan_tick` 读了只在某分支绑定的 `_pos_d`（已修，见 F328）
  ② **工程：实验脚本的回滚只在 `finally` 里，而 `finally` 在 SIGTERM 下不执行**

本脚本治的是 ②。

# 为什么"写状态文件 + 独立恢复脚本"而不是只靠 `finally`

`finally` 只在**进程能跑完收尾代码**时有效。以下全部绕过它：

  · `Stop-Process -Force` / `taskkill /F`（Windows 上不发 SIGTERM，直接终止）
  · 任务被 kill、机器重启、断电
  · 脚本自身抛异常后进程被外部杀掉
  · **实测**：本会话两次 kill 实验任务，两次 `finally` 都没跑

⇒ 回滚必须做成**进程外可执行**的：实验开始时把原值落盘，任何人事后都能恢复。

# 用法

    # 实验脚本开始时：先存原值（这一步必须在改任何参数之前）
    python scripts/h188_param_rollback.py --save --keys reduce_quote_disabled,timeout_exit_maker_only

    # 任何时候（哪怕实验进程已被强杀）：
    python scripts/h188_param_rollback.py --status     # 看有没有未完成的实验
    python scripts/h188_param_rollback.py --restore    # 恢复并清除状态文件
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"
STATE = ROOT / "logs" / "param_experiment_state.json"

# 实验允许改的键（**安全参数永不入内**，见 G1 的 FORBIDDEN）
ALLOWED_KEYS = {
    "reduce_quote_disabled", "timeout_exit_maker_only", "take_profit_bp",
    "take_profit_maker_grace_sec", "spread_mult", "spread_mult_reduce",
    "min_edge_frac", "min_width_bp", "k_inv", "max_one_side_seconds",
    "compound_ratio", "stop_loss_bp", "max_net_directional_ratio",
    # [F338 2026-09-22] 单腿名义硬上限（只约束加仓腿，0=关闭）。
    # 为什么它够安全可以进白名单：它是 `qty` 的**乘数上限**，只会让腿更小，
    # 永不放大敞口、永不放宽任何风控；且 0 = 与旧行为逐字一致（一键回退）。
    "max_leg_notional_mult",
    # [H212 2026-09-22] 日亏闸（当前 80% = 12 天从未触发 ⇒ 尾部零保护）。
    # 它是**收紧型**风控（把 80 改小只会更早停），且跨日自然解除。
    # ⚠️ 它会锁存到当天 24:00 ⇒ 实验被强杀后必须 `--restore`，否则车道整天停摆。
    "daily_loss_stop_pct",
}


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


def read_params() -> dict:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'params' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            row = cur.fetchone()
    return dict(row[0] or {}) if row else {}


def write_params(patch: dict) -> None:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = dict(cur.fetchone()[0] or {})
            p = dict(meta.get("params") or {})
            p.update(patch)
            meta["params"] = p
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() WHERE lane_id=%s",
                        (json.dumps(meta, ensure_ascii=False, default=str), LANE))
        c.commit()


def live_params() -> dict:
    try:
        j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    d = dict(j.get("params") or {})
    for k, v in dict(j.get("limits") or {}).items():
        d.setdefault(k, v)
    return d


def cmd_save(keys: list) -> int:
    bad = [k for k in keys if k not in ALLOWED_KEYS]
    if bad:
        print(f"  ✗ 拒绝：{bad} 不在实验可改白名单内（安全参数禁止被实验触碰）")
        return 1
    if STATE.exists():
        old = json.loads(STATE.read_text(encoding="utf-8"))
        print(f"  ⚠️ 已存在未完成的实验状态（{old.get('saved_at')}，键 "
              f"{list((old.get('original') or {}))}）")
        print(f"  ⇒ 先执行 --restore，或确认那是你自己刚写的、可覆盖")
        return 1
    cur = read_params()
    orig = {k: cur.get(k) for k in keys}
    missing = [k for k, v in orig.items() if v is None]
    if missing:
        print(f"  ✗ 拒绝：注册表里读不到 {missing} 的当前值 ⇒ 无法回滚，不许开始实验")
        return 1
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({
        "saved_at": datetime.now().astimezone().isoformat(),
        "lane": LANE, "original": orig}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"  ✓ 已保存原值到 {STATE}")
    for k, v in orig.items():
        print(f"      {k} = {v!r}")
    print(f"  ⇒ 实验可以开始了。被强杀后执行：--restore")
    return 0


def cmd_status() -> int:
    if not STATE.exists():
        print("  ✓ 无未完成的参数实验（状态文件不存在）")
        return 0
    st = json.loads(STATE.read_text(encoding="utf-8"))
    orig = dict(st.get("original") or {})
    cur = read_params()
    live = live_params()
    print(f"  ⚠️ 有未完成的实验：{st.get('saved_at')}（lane={st.get('lane')}）")
    print(f"  {'key':<28}{'original':>12}{'registry now':>16}{'live now':>12}  verdict")
    print("  " + "-" * 78)
    drift = []
    for k, ov in sorted(orig.items()):
        cv, lv = cur.get(k), live.get(k)
        same = (cv == ov)
        if not same:
            drift.append(k)
        print(f"  {k:<28}{str(ov):>12}{str(cv):>16}{str(lv):>12}  "
              f"{'unchanged' if same else 'DRIFTED'}")
    print()
    if drift:
        print(f"  ⇒ {len(drift)} 个键与实验前不同 ⇒ **需要 --restore**：{drift}")
        return 2
    print(f"  ⇒ 所有键都等于实验前 ⇒ 实验已自行回滚，状态文件可清（--restore 会清）")
    return 0


def cmd_restore() -> int:
    if not STATE.exists():
        print("  ✓ 无未完成的参数实验 ⇒ 无需恢复")
        return 0
    st = json.loads(STATE.read_text(encoding="utf-8"))
    orig = dict(st.get("original") or {})
    if not orig:
        print("  ✗ 状态文件里没有原值 ⇒ 需人工检查")
        return 1
    write_params(orig)
    # 核对：写进去不等于生效，必须读**运行态心跳**确认
    import time
    expect = {k: v for k, v in orig.items()}
    print(f"  已写入原值，等待生效（F251 指纹周期 ≤60s）…")
    t0 = time.time()
    while time.time() - t0 < 150:
        live = live_params()
        if all(live.get(k) == v for k, v in expect.items()):
            STATE.unlink(missing_ok=True)
            print(f"  ✓ 心跳确认 {len(expect)} 个键已恢复（{time.time()-t0:.0f}s）")
            for k, v in expect.items():
                print(f"      {k} = {v!r}")
            return 0
        time.sleep(5)
    live = live_params()
    print(f"  ⚠️ 150s 内未确认生效。注册表已写回原值，但实盘读到的是：")
    for k, v in expect.items():
        print(f"      {k}: 期望 {v!r}  实盘 {live.get(k)!r}")
    print(f"  ⇒ 状态文件**保留**，请人工检查（勿当成功）")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--keys", default="")
    a = ap.parse_args()

    print("=" * 92)
    print("H188  参数实验崩溃安全回滚")
    print("=" * 92)

    if a.save:
        keys = [k.strip() for k in a.keys.split(",") if k.strip()]
        if not keys:
            print("  ✗ --save 需要 --keys")
            return 1
        return cmd_save(keys)
    if a.restore:
        return cmd_restore()
    return cmd_status()


if __name__ == "__main__":
    raise SystemExit(main())
