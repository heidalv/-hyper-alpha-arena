"""h553：**③ 部署链的上线前预检**（只读，不改任何东西）。

③（`ofi_confirm_threshold` 0.5→0.9）由串行链 `DSH_HFT_H464_CHAIN` 在 ② 判定之后自动部署。
本脚本把链的**每一步前置条件**逐条验证，避免 21:55 才发现问题（那时人可能不在）：

  1. 链任务本身：存在 / 已启用 / 下次触发时刻 / 命令行完整（`wscript → run-quiet.vbs → py`）；
  2. 依赖文件：`run-quiet.vbs`、`h425_repair_trial.py`、`h481_runtime_param_echo.py`；
  3. ② 的前置：`DSH_HFT_H463_JUDGE` 已启用且在链之前触发；`h463_trial` 状态与窗口；
  4. 链的复用判据（`_fresh_verdict`：`age_h ≤ 2.0 且 hours ≥ 10`）在 21:55 是否成立
     —— 用"② 判定时刻 + 窗长"推算，并给出不成立时的回退路径（链会自己跑一次 ② 判定）；
  5. ③ 的部署可行性：SPEC 值可经 `_coerce_param`、字段存在于 `QuoteParams`、
     `--force` 会绕过串行守卫；
  6. **③ 的窗口清洁度**：21:55→次日 ~09:55 之间是否有**会写参数**的任务（除链自身）。

用法：python scripts/h553_chain_preflight.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h425_repair_trial import (  # noqa: E402
    SPECS, LANE, _coerce_param, read_env_dsn,
)

CHAIN = "DSH_HFT_H464_CHAIN"
JUDGE2 = "DSH_HFT_H463_JUDGE"
# 会不会**写参数**的任务（判定/部署/回滚类）——出现在 ③ 窗口内即为风险
RISK_PREFIX = ("DSH_HFT_H4", "DSH_HFT_P2", "DSH_HFT_H356")


def task_info(name: str) -> dict:
    p = subprocess.run(["schtasks", "/query", "/tn", name, "/fo", "LIST", "/v"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    if p.returncode != 0:
        return {}
    out = {}
    for line in (p.stdout or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def main() -> int:
    ok = True
    print("=" * 92)
    print("③ 部署链上线前预检（只读）")
    print("=" * 92)

    print("\n[1] 链任务")
    ci = task_info(CHAIN)
    if not ci:
        print("  ✗ 任务不存在"); ok = False
    else:
        print(f"  Status={ci.get('Status')}  Next={ci.get('Next Run Time')}  "
              f"Logon={ci.get('Logon Mode')}")
        tr = ci.get("Task To Run", "")
        for need in ("run-quiet.vbs", "h464_chain.py"):
            mark = "✓" if need in tr else "✗"
            print(f"    {mark} 命令行含 {need}")
            ok &= need in tr
        if ci.get("Status") != "Ready":
            print("  ✗ 未处于 Ready"); ok = False

    print("\n[2] 依赖文件")
    for rel in ("scripts/run-quiet.vbs", "scripts/h425_repair_trial.py",
                "scripts/h481_runtime_param_echo.py", "scripts/h464_chain.py"):
        p = ROOT / rel
        print(f"  {'✓' if p.exists() else '✗'} {rel}")
        ok &= p.exists()

    print("\n[3] ② 的前置")
    ji = task_info(JUDGE2)
    if ji:
        print(f"  {JUDGE2}: Status={ji.get('Status')} Next={ji.get('Next Run Time')}")
        ok &= ji.get("Status") == "Ready"
    else:
        print(f"  ✗ {JUDGE2} 不存在"); ok = False
    import psycopg
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = cur.fetchone()[0] or {}
    t = dict(meta.get("h463_trial") or {})
    print(f"  h463_trial: started_at={t.get('started_at')} judge_at={t.get('judge_at')} "
          f"verdict={t.get('verdict')}")
    print(f"  max_one_side_seconds={((meta.get('params') or {}).get('max_one_side_seconds'))}")

    print("\n[4] 链的复用判据推算（`_fresh_verdict`）")
    try:
        ja = dt.datetime.fromisoformat(str(t.get("judge_at")).replace("Z", "+00:00"))
        sa = dt.datetime.fromisoformat(str(t.get("started_at")).replace("Z", "+00:00"))
        hours = (ja - sa).total_seconds() / 3600.0
        print(f"  ② 判定时刻 judge_at={ja.astimezone().strftime('%H:%M')}L，"
              f"窗长 hours={hours:.2f}")
        ci_next = ci.get("Next Run Time", "")
        print(f"  链触发时刻 = {ci_next}")
        # [R69] 规则已改：**终局判决不过期**（复用）；只有非终局才要求 age ≤2h 且 hours ≥10。
        # 本预检判断"到链触发时会不会退化成链自己重跑 ② 判定"——那会把 ② 的原窗
        # 覆盖成更长的窗（昼夜混合）⇒ 必须提前知道。
        _judged = str(t.get("verdict") or "") in ("PASS", "ROLLBACK")
        print(f"  ② 当前 verdict={t.get('verdict')!r}"
              f" ⇒ {'**终局，无条件复用** ✓（R69）' if _judged else '非终局'}")
        if not _judged:
            print(f"  判据① hours ≥ 10 ⇒ {'✓' if hours >= 10 else '✗'}；"
                  f"判据② age ≤ 2h ⇒ 取决于链是否在 ② 判定后 2h 内触发")
            # [R188] 上面这行只在"到链触发时仍是 None"的情况下成立；今晚 21:48 判定一旦
            # 落地为 PASS/ROLLBACK，**新鲜度就不再看**（R69）⇒ 把两种未来各写清楚，
            # 免得明天 09:50 读的人误以为"age>2h ⇒ 链会重跑 ② 判定、覆盖干净窗口"。
            print(f"  ⇒ 两种未来：① 21:48 判定产出终局（PASS/ROLLBACK）⇒ 链在 "
                  f"{ci_next} **无条件复用**该判决，与 age 无关 ✓（② 的 12h 窗不会被覆盖）；"
                  f"② 判定未落地（verdict 仍空）⇒ 链走自带补判路径（R45 放行）")
            if hours < 10:
                print("  ⚠️ 窗长 <10h ⇒ 链会**自己再跑一次 ② 判定**（回退路径，仍可用）")
        else:
            print("  判据①② 不适用（终局判决不看新鲜度）")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 推算失败（不阻塞）：{str(e)[:80]}")

    print("\n[5] ③ 的部署可行性")
    spec = SPECS["h464"]
    fname = spec["field"].split(".")[-1]
    to = _coerce_param(spec["to"])
    rb = _coerce_param(spec["rollback_to"])
    print(f"  field={fname} to={to} rollback_to={rb}")
    from backend.services.market_maker.core import QuoteParams, LaneRiskLimits
    in_q = fname in QuoteParams.__dataclass_fields__
    in_l = fname in LaneRiskLimits.__dataclass_fields__
    print(f"  字段存在：QuoteParams={'✓' if in_q else '✗'} LaneRiskLimits={'✓' if in_l else '✗'}")
    ok &= (in_q or in_l)
    cur_val = (meta.get("params") or {}).get(fname)
    print(f"  注册表现值={cur_val}（部署后应为 {to}）")

    print("\n[6] ③ 的窗口清洁度（链触发 → +12h 内**会写参数**的任务）")
    # 只有能**写 lane_registry.params** 的任务才算风险：判定/部署/回滚类脚本。
    # 判定框架（h425_repair_trial.py）在 ROLLBACK 时会写参数；h356/h354 是另两条链。
    WRITERS = ("h425_repair_trial.py", "h356_universe_trial.py", "h354_p2_judge.py",
               "h464_chain.py", "h144_restart_backend.py")
    p = subprocess.run(["schtasks", "/query", "/fo", "CSV", "/v"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    import csv
    import io
    rows = list(csv.DictReader(io.StringIO(p.stdout or "")))
    risks, benign = [], []
    # ③ 的窗口 = 链触发时刻 → +12h
    win_start = win_end = None
    try:
        win_start = dt.datetime.strptime(ci.get("Next Run Time", ""), "%Y/%m/%d %H:%M:%S")
        win_end = win_start + dt.timedelta(hours=12)
        print(f"  ③ 的窗口 = {win_start:%m-%d %H:%M} → {win_end:%m-%d %H:%M}")
    except Exception:  # noqa: BLE001
        print("  （链触发时刻解析失败 ⇒ 退化为「列出全部 Ready 的写参数任务」）")
    for r in rows:
        name = (r.get("TaskName") or "").replace("\\", "")
        if not name.startswith("DSH_"):
            continue
        if (r.get("Status") or "") != "Ready":
            continue
        nxt = r.get("Next Run Time") or ""
        if nxt in ("N/A", ""):
            continue
        tr = r.get("Task To Run") or ""
        if not any(w in tr for w in WRITERS):
            continue
        if name == CHAIN:
            continue
        inside = True
        if win_start is not None:
            try:
                dt_next = dt.datetime.strptime(nxt, "%Y/%m/%d %H:%M:%S")
                inside = (win_start < dt_next <= win_end)
            except Exception:  # noqa: BLE001
                inside = True
        (risks if inside else benign).append((name, nxt, tr[-60:]))
    for name, nxt, tail in benign:
        print(f"  · {name} next={nxt} ⇒ 在窗口**之外**（无害）")
    if risks:
        for name, nxt, tail in risks:
            print(f"  ⚠️ {name}  next={nxt}  …{tail}")
        print("  ⇒ 上列任务若在 ③ 的窗口内**真的回滚**，会把 ③ 判成 INCONCLUSIVE。"
              "处置：确认其判决为 INCONCLUSIVE，或停掉/改期。")
        ok = False
    else:
        print("  ✓ 窗口内没有「会写参数」的任务在启用状态（链自身除外）")
    print(f"  （已停的风险集不在统计内；扫描/桥接/队列类任务不写参数，故不计。）")

    print("\n" + "=" * 92)
    print("预检结论：" + ("**可以按计划自动部署** ✓" if ok else "**存在问题 ✗（见上）**"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
