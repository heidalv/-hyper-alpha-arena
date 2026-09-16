# -*- coding: utf-8 -*-
"""[§76 落地] 中长线**例行审计套件** —— 观测期每周/每日可重复运行的一条命令。

为什么需要：§23 验收标准里的"2 周 paper 观测 + 每周重跑三个审计脚本"此前只是**文字承诺**，
没有可执行入口；而且这些审计各自输出格式不同，人工比对容易漏看。本脚本：

  1. 依次运行既有审计（只读、不改任何数据）：
     * `audit_profit_giveback.py`          浮盈回吐（§44 类问题的持续监控）
     * `audit_gate_edge.py`                各闸门边际效益
     * `audit_position_event_consistency.py`  持仓/事件流一致性
     * `audit_paper_live_divergence.py`    paper/live 分叉（§65/§66 之后仍需盯）
     * `audit_dead_keys_register.py --check`  配置死键登记册是否漂移
     * `audit_defect_inventory.py`         缺陷清单自校验
  2. 追加**运行期快照**：漏斗（旋转感知）、当前闸门叠加、回撤判据与冻结状态、账户/持仓；
  3. 结果写入 `data/audit_reports/midlong_audit_<YYYYmmdd_HHMM>.json` 与同名 `.md` 摘要；
  4. 任一子项失败 ⇒ 退出码非 0（可挂调度器/人工巡检查看）。

用法：
  python backend/scripts/midlong_audit_suite.py              # 全量跑并落盘
  python backend/scripts/midlong_audit_suite.py --list       # 只列子项（供测试/巡检）
  python backend/scripts/midlong_audit_suite.py --timeout 600
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# [2026-09-16 调研轮7] Windows 控制台默认 cp936(GBK)：本套件打印 ✅/❌ 汇总行会在
# 最后一步崩溃（实测 exit=1 且看不到任何子项结果）→ 审计"看着失败"其实是编码。
# 子进程侧另经 subprocess env PYTHONIOENCODING=utf-8 强制。
try:  # pragma: no cover
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
OUT_DIR = ROOT / "data" / "audit_reports"

#: (标识, 命令行参数, 说明)  —— 全部只读
CHECKS: list[tuple[str, list[str], str]] = [
    ("defect_inventory", ["backend/scripts/audit_defect_inventory.py"],
     "缺陷清单自校验（§62，含格式/证据/统计行一致性）"),
    ("dead_keys", ["backend/scripts/audit_dead_keys_register.py", "--check"],
     "死键登记册漂移检测（§67.3；双工具交叉验证）"),
    ("profit_giveback", ["backend/scripts/audit_profit_giveback.py"],
     "浮盈回吐监控（§44/§57 类问题的常态监控）"),
    ("gate_edge", ["backend/scripts/audit_gate_edge.py"],
     "各闸门边际效益（哪道闸在真正拦钱 / 拦错）"),
    ("position_event_consistency", ["backend/scripts/audit_position_event_consistency.py"],
     "持仓与事件流一致性（记账口径 §43/§44）"),
    ("paper_live_divergence", ["backend/scripts/audit_paper_live_divergence.py"],
     "paper/live 两态分叉清单（§65/§66 之后持续盯）"),
    # [§83 2026-09-11 / 目标①③] 统一熔断的两个"必须每天看"的面：
    ("breaker_harm", ["_audit_ml/Z235_breaker_harm_check.py"],
     "熔断是否误伤盈利通道 / 漏报亏损通道（净额口径 × 滚动窗胜率）"),
    ("suppression_safety", ["_audit_ml/Z236_suppression_safety_monitor.py", "7"],
     "出场抑制安全：保护性通道永不被抑制 + 无悬挂仓位（无事件则 NO_DATA）"),
    # [§84 2026-09-11 / 缺陷 #69] 熔断的"证据自锁"：用两周前的证据拦今天的出场
    ("breaker_evidence_age", ["_audit_ml/Z241_breaker_evidence_lock.py"],
     "熔断证据新鲜度：shadow+可抑制通道的最新样本年龄（过期=冻结候选）"),
]


def _runtime_snapshot() -> dict:
    """运行期快照：漏斗 + 闸门叠加 + 回撤判据 + 账户/持仓。全部只读、异常不抛出。"""
    snap: dict = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        from backend.services.mlto.midlong_direction_audit import summarize_decision_funnel

        for h in (2, 24, 24 * 7):
            s = summarize_decision_funnel(lookback_hours=h)
            snap[f"funnel_{h}h"] = {
                "skips": s.get("skips"), "opened": s.get("opened"),
                "open_attempts": s.get("open_attempts"),
                "by_stage_skip": s.get("by_stage_skip"),
                "top_skip_reasons": (s.get("top_skip_reasons") or [])[:6],
            }
    except Exception as exc:  # noqa: BLE001
        snap["funnel_error"] = f"{type(exc).__name__}: {exc}"[:200]

    try:
        from backend.config import settings as S
        from backend.services.risk_management import portfolio_budget as pbm
        from backend.services.risk_management.portfolio_budget import portfolio_budget as pb

        snap["gates"] = {
            "MIDLONG_MAX_NET_EXPOSURE_PCT": getattr(S, "MIDLONG_MAX_NET_EXPOSURE_PCT", None),
            "MIDLONG_MAX_OPEN_POSITIONS": getattr(S, "MIDLONG_MAX_OPEN_POSITIONS", None),
            "MIDLONG_MAX_SAME_SYMBOL_POSITIONS": getattr(S, "MIDLONG_MAX_SAME_SYMBOL_POSITIONS", None),
            "MIDLONG_CORR_CLUSTER_MAX": getattr(S, "MIDLONG_CORR_CLUSTER_MAX", None),
            "MIDLONG_EV_ENFORCE_MID": getattr(S, "MIDLONG_EV_ENFORCE_MID", None),
            "PB_MIDLONG_DRAWDOWN_SIGMA": pbm._cfg_float("PB_MIDLONG_DRAWDOWN_SIGMA", 10.0),
            "PB_DD_STALE_HOURS": pbm._cfg_float("PB_DD_STALE_HOURS", 12.0),
            "PB_FREEZE_ENABLED": pbm._cfg_bool("PB_FREEZE_ENABLED", True),
            "TIER_mid_cooldown_sec": (S.TIER_PROTECTION_PARAMS.get("mid") or {}).get("cooldown_sec"),
            "TIER_long_cooldown_sec": (S.TIER_PROTECTION_PARAMS.get("long") or {}).get("cooldown_sec"),
        }
        from backend.database.connection import SessionLocal

        db = SessionLocal()
        try:
            m = pb._strategy_drawdown_metric("midlong", db, 14)
            snap["drawdown"] = m
        finally:
            db.rollback()
            db.close()
    except Exception as exc:  # noqa: BLE001
        snap["gates_error"] = f"{type(exc).__name__}: {exc}"[:200]
    return snap


def run_suite(*, timeout: int = 600, write: bool = True) -> dict:
    results = []
    for cid, argv, desc in CHECKS:
        t0 = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, *argv], cwd=str(ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                # [2026-09-16 调研轮7] 强制子进程 stdout/stderr 用 UTF-8：Windows 控制台默认
                # cp936(GBK)，子脚本打印 ✅/❌ 会抛 UnicodeEncodeError → rc=1 → 例行审计
                # 报「FAIL」但其实是编码崩溃（实测 dead_keys 天天假 FAIL，真失败被淹没）。
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            rc, out = 124, f"TIMEOUT after {timeout}s"
        results.append({
            "id": cid, "desc": desc, "argv": argv, "rc": rc,
            "seconds": round(time.time() - t0, 1),
            "tail": out.strip().splitlines()[-8:],
        })
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "checks": results,
        "failed": [r["id"] for r in results if r["rc"] != 0],
        "runtime_snapshot": _runtime_snapshot(),
    }
    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M")
        (OUT_DIR / f"midlong_audit_{ts}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [f"# 中长线例行审计 {payload['generated_at']}", ""]
        for r in results:
            lines.append(f"- [{'OK' if r['rc'] == 0 else 'FAIL'}] {r['id']}（{r['seconds']}s）{r['desc']}")
        snap = payload["runtime_snapshot"]
        for k in ("funnel_2h", "funnel_24h"):
            v = snap.get(k)
            if isinstance(v, dict):
                lines += ["", f"## {k}", f"- skips={v['skips']} opened={v['opened']} "
                              f"attempts={v['open_attempts']}",
                          f"- top: {[x['reason'] for x in (v['top_skip_reasons'] or [])]}"]
        if snap.get("drawdown"):
            d = snap["drawdown"]
            lines += ["", "## 回撤判据", f"- ratio={d['ratio']}σ age={d['age_hours']}h n={d['n_samples']}"]
        (OUT_DIR / f"midlong_audit_{ts}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        payload["written_to"] = str(OUT_DIR / f"midlong_audit_{ts}.json")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只列子项")
    ap.add_argument("--timeout", type=int, default=600, help="每个子项超时秒数")
    ap.add_argument("--no-write", action="store_true", help="不落盘（仅打印）")
    args = ap.parse_args()

    if args.list:
        for cid, argv, desc in CHECKS:
            print(f"{cid:28s} {' '.join(argv):60s} {desc}")
        return 0

    payload = run_suite(timeout=args.timeout, write=not args.no_write)
    for r in payload["checks"]:
        flag = "✅" if r["rc"] == 0 else "❌"
        print(f"{flag} {r['id']:28s} rc={r['rc']:<4d} {r['seconds']:>6.1f}s  {r['desc']}")
        if r["rc"] != 0:
            for line in r["tail"][-4:]:
                print(f"      | {line[:150]}")
    snap = payload["runtime_snapshot"]
    for k in ("funnel_2h", "funnel_24h"):
        v = snap.get(k)
        if isinstance(v, dict):
            print(f"   {k}: skip={v['skips']} opened={v['opened']} "
                  f"top={[x['reason'] for x in (v['top_skip_reasons'] or [])][:3]}")
    if snap.get("drawdown"):
        d = snap["drawdown"]
        print(f"   回撤判据: ratio={d['ratio']}σ age={d['age_hours']}h n={d['n_samples']}")
    if payload.get("written_to"):
        print(f"\n已写入 {payload['written_to']}")
    if payload["failed"]:
        print(f"\n❌ 失败子项: {payload['failed']}")
        return 1
    print("\n✅ 全部子项通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
