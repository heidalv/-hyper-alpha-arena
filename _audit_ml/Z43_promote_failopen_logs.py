# -*- coding: utf-8 -*-
"""Z43：把中长线路径上「fail-open 静默放行」的日志从 debug 提升为可见级别。

依据（第 3 轮审计）：闸函数本身把 fail-open 理由写得清清楚楚，但**调用方**
用 `logger.debug` 记录「闸失效 → 放行」，生产日志级别 INFO ⇒ 完全静默。
实测 19 处，其中中长线入口路径 8 处。

分级原则：
  - **入口路径闸**（每次开仓尝试一次）→ `warning`：闸坏了却不拦，等于无保护 → 必须可见；
  - **数据缺失类**（可能每 tick 触发）→ `info`：可见但不刷 warning。

对现有行为零影响（只改日志级别）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (相对路径, 原文, 新文, 级别理由)
PATCHES = [
    # ── midlong_executor：4 道入口闸（swing 共识/熔断/图审/位置）→ warning ──
    ("backend/services/full_auto/midlong_executor.py",
     'logger.debug("[MidLong] swing 共识闸异常(fail-open): %s", e)',
     'logger.warning("[MidLong] swing 共识闸异常(fail-open): %s", e)', "入口闸"),
    ("backend/services/full_auto/midlong_executor.py",
     'logger.debug("[MidLong] 熔断闸检查跳过(fail-open): %s", _ml_gate_err)',
     'logger.warning("[MidLong] 熔断闸检查跳过(fail-open): %s", _ml_gate_err)', "入口闸"),
    ("backend/services/full_auto/midlong_executor.py",
     'logger.debug("[MidLong] 图审闸检查跳过(fail-open): %s", _cg_err)',
     'logger.warning("[MidLong] 图审闸检查跳过(fail-open): %s", _cg_err)', "入口闸"),
    ("backend/services/full_auto/midlong_executor.py",
     'logger.debug("[MidLong] 位置闸检查跳过(fail-open): %s", _lg_err)',
     'logger.warning("[MidLong] 位置闸检查跳过(fail-open): %s", _lg_err)', "入口闸"),
    # ── midlong_circuit_gate：learned 门特征读取失败 / 检查异常 → warning ──
    ("backend/services/full_auto/midlong_circuit_gate.py",
     'logger.debug("[MidCircuit] learned 多头特征读取失败(fail-open) %s: %s", sym, exc)',
     'logger.warning("[MidCircuit] learned 多头特征读取失败(fail-open) %s: %s", sym, exc)',
     "learned 门"),
    ("backend/services/full_auto/midlong_circuit_gate.py",
     'logger.debug("[MidCircuit] 检查异常(fail-open): %s", exc)',
     'logger.warning("[MidCircuit] 检查异常(fail-open): %s", exc)', "熔断闸"),
    # ── 数据缺失类 → info（可见但不刷 warning）──
    ("backend/services/factor_engine/midlong_flow_gate.py",
     'logger.debug("[MidFlowGate] %s flow 获取失败(fail-open): %s", symbol, e)',
     'logger.info("[MidFlowGate] %s flow 获取失败(fail-open): %s", symbol, e)', "数据缺失"),
    ("backend/services/factor_engine/midlong_factor_route.py",
     'logger.debug("[FactorRoute] 资金流门跳过(fail-open): %s", _fg_err)',
     'logger.info("[FactorRoute] 资金流门跳过(fail-open): %s", _fg_err)', "数据缺失"),
]


def main() -> int:
    ok, fail = 0, []
    for rel, old, new, why in PATCHES:
        p = ROOT / rel
        text = p.read_text(encoding="utf-8")
        if old not in text:
            fail.append((rel, why, "原文未命中"))
            continue
        if text.count(old) != 1:
            fail.append((rel, why, f"命中 {text.count(old)} 次"))
            continue
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        ok += 1
        print(f"  ✓ [{why}] {rel}")
    print(f"\n成功 {ok} / 失败 {len(fail)}")
    for rel, why, msg in fail:
        print(f"  ✗ {rel} [{why}] {msg}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
