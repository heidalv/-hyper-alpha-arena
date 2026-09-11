# -*- coding: utf-8 -*-
"""[§81/§82 变异测试 2026-09-11] 证明本轮修复的测试**拆掉修复点就会变红**。

覆盖四组（缺陷 #65 持久化 / #66 查询键 / #67 OWM 去重契约 / #68 审计夹具污染 / P5-A MM 车道闸）：
  M1  加载期判据退回旧写法（无条件按旧文件处理）        → 标志持久化类用例必须红
  M2  保存载荷去掉 `shadow_mode` 标记                   → 标记类用例必须红
  M3  空态合并无条件清空 `breaker_shadow`               → 合并类用例必须红
  M4  去掉"重建结果落盘"                                → 落盘一致类用例必须红
  M5  查询键优先级退回 `reason` 优先（= 缺陷 #66）       → 半边键用例必须红
  M6  查询键改用裸 `reason`（与写入侧彻底分叉）          → 同上
  M7  OWM 去重判据恒 False（去重失效）                  → 反向契约必须红
  M8  拆掉 MM 车道闸门调用（回到死闸）                  → P5-A 用例必须红
  M9  日亏闸判据恒不触发                                → 同上
  M10 车道闸门把手伸到敞口（会停掉减仓腿）              → 边界契约必须红
  M11 拆掉审计的 pytest 守卫（夹具再进生产）            → #68 用例必须红
  M12 审计写失败退回 debug 静默                         → 可见性护栏必须红
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
LOG = ROOT / "data" / "audit_reports" / "z219_mutation.log"   # 不放 --basetemp（会被清理占用）

SA = ROOT / "backend" / "services" / "source_attribution.py"
UE = ROOT / "backend" / "services" / "unified_exit_executor.py"
LB = ROOT / "backend" / "services" / "mlto" / "learning_bridge.py"
MMRUN = ROOT / "backend" / "services" / "market_maker" / "runner.py"
MMCORE = ROOT / "backend" / "services" / "market_maker" / "core.py"
AUDIT = ROOT / "backend" / "services" / "mlto" / "midlong_direction_audit.py"
GATE = ROOT / "backend" / "services" / "source_attribution.py"
CGATE = ROOT / "backend" / "services" / "exit" / "channel_breaker_gate.py"

T_PERSIST = "backend/tests/unit/test_shadow_state_persistence_20260911.py"
T_KEY = "backend/tests/unit/test_breaker_lookup_key_20260911.py"
T_OWM = "backend/tests/unit/test_owm_thesis_bind_20260906.py"
T_MM = "backend/tests/unit/test_mm_lane_limits_enforce_20260911.py"
T_AUDIT = "backend/tests/unit/test_audit_pytest_pollution_20260911.py"
T_FRESH = "backend/tests/unit/test_breaker_evidence_freshness_20260911.py"
T_UNIFIED = "backend/tests/unit/test_exit_channel_breaker_unified_20260911.py"

MUTATIONS = [
    (
        "M1 加载期判据退回旧写法", SA,
        "                _mode = str(d.get(SHADOW_MODE_KEY) or \"\").strip().lower()\n"
        "                _legacy_file = _mode != SHADOW_MODE_ROLLING\n",
        "                _legacy_file = True\n",
        T_PERSIST,
    ),
    (
        "M2 保存载荷去掉口径标记", SA,
        "                    SHADOW_MODE_KEY: SHADOW_MODE_ROLLING,\n",
        "",
        T_PERSIST,
    ),
    (
        "M3 空态合并无条件清空标志", SA,
        "                            self._breaker_shadow = (\n"
        "                                {} if _disk_legacy else dict(disk.get(\"breaker_shadow\") or {})\n"
        "                            )\n",
        "                            self._breaker_shadow = {}\n",
        T_PERSIST,
    ),
    (
        "M4 去掉重建结果落盘", SA,
        "                        if rebuilt and not _under_pytest() and rebuilt != _disk_flags:\n"
        "                            self._maybe_save(force=True)\n",
        "                        if False:\n"
        "                            self._maybe_save(force=True)\n",
        T_PERSIST,
    ),
    (
        "M5 查询键优先级退回 reason 优先", UE,
        '                _rsn = str(req.exit_channel or "") or str(req.reason or "")\n',
        '                _rsn = str(req.reason or "") or str(req.exit_channel or "")\n',
        T_KEY,
    ),
    (
        "M6 查询键改用裸 reason（与写入侧彻底分叉）", UE,
        '                _rsn = str(req.exit_channel or "") or str(req.reason or "")\n',
        '                _rsn = str(req.reason or "")\n',
        T_KEY,
    ),
    (
        "M7 OWM 去重判据恒 False（去重失效）", LB,
        "                .first()\n"
        "            )\n"
        "            return n is not None\n",
        "                .first()\n"
        "            )\n"
        "            return False\n",
        T_OWM,
    ),
    (
        "M8 拆掉 MM 车道闸门调用（回到死闸）", MMRUN,
        "    if lane_limits_enforce_enabled():\n"
        "        _lane_pause, _lane_why = lane_pause_reason(\n",
        "    if False:\n"
        "        _lane_pause, _lane_why = lane_pause_reason(\n",
        T_MM,
    ),
    (
        "M9 日亏闸判据失效（恒不触发）", MMCORE,
        "        if float(day_pnl_usd or 0.0) <= _limit_usd:\n"
        "            return True, f\"daily_loss({float(day_pnl_usd or 0.0):.2f}<={_limit_usd:.2f})\"\n",
        "        if False:\n"
        "            return True, f\"daily_loss({float(day_pnl_usd or 0.0):.2f}<={_limit_usd:.2f})\"\n",
        T_MM,
    ),
    (
        "M10 车道闸门把手伸到敞口（会停掉减仓腿）", MMCORE,
        "    if equity <= 0:\n"
        "        return True, \"equity<=0\"\n",
        "    if equity <= 0:\n"
        "        return True, \"equity<=0\"\n"
        "    if abs(book.net_notional(marks)) > equity * limits.max_net_exposure_ratio:\n"
        "        return True, \"net_exposure(x)\"\n",
        T_MM,
    ),
    (
        "M11 拆掉审计的 pytest 守卫（夹具再进生产）", AUDIT,
        "        if _skip_write_under_pytest():\n            return row\n",
        "        if False:\n            return row\n",
        T_AUDIT,
    ),
    (
        "M12 审计写失败退回 debug 静默", AUDIT,
        '        logger.warning("[MidLongAudit] 审计写入失败(漏斗统计会缺行): %s", exc)\n',
        '        logger.debug("[MidLongAudit] write skip: %s", exc)\n',
        T_AUDIT,
    ),
    (
        "M13 拆掉证据新鲜度检查（回到用旧证据抑制）", CGATE,
        "        fresh, age_days, limit = attribution.exit_channel_evidence_fresh(ch, t)\n",
        "        fresh, age_days, limit = True, None, 0.0\n",
        T_UNIFIED,
    ),
    (
        "M14 无时间戳即视为新鲜（旧状态文件照样抑制）", GATE,
        "        if not ts:\n            return False, None, float(limit)\n",
        "        if not ts:\n            return True, None, float(limit)\n",
        T_FRESH,
    ),
]


def _run_pytest(test_path: str) -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"\n===== {time.strftime('%H:%M:%S')} {test_path} =====\n")
        return subprocess.call(
            [str(PY), "-m", "pytest", test_path, "-q", "--no-header", "-p", "no:cacheprovider",
             f"--basetemp={ROOT / 'data' / '_pytest_tmp'}"],
            cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
        )


def main() -> int:
    originals = {p: p.read_text(encoding="utf-8")
                 for p in {SA, UE, LB, MMRUN, MMCORE, AUDIT, GATE, CGATE}}
    bad = []
    try:
        print("=" * 96)
        print("基线：修复在位时五个测试文件都应当全绿")
        print("=" * 96)
        base_ok = True
        for t in (T_PERSIST, T_KEY, T_OWM, T_MM, T_AUDIT, T_FRESH, T_UNIFIED):
            rc = _run_pytest(t)
            print(f"  {t} -> 退出码 {rc} {'[OK]' if rc == 0 else '[基线红]'}")
            base_ok &= rc == 0
        if not base_ok:
            return 2
        for name, path, old, new, test in MUTATIONS:
            src = originals[path]
            if old not in src:
                print(f"  [!] 变异 {name}: 目标片段未找到（源码已变动，需更新变异器）")
                bad.append(name)
                continue
            path.write_text(src.replace(old, new, 1), encoding="utf-8")
            rc = _run_pytest(test)
            print(f"  [{'变红 OK' if rc else '仍绿 !!'}] {name}（{Path(test).name} 退出码 {rc}）")
            if rc == 0:
                bad.append(name)
            path.write_text(src, encoding="utf-8")
    finally:
        for p, src in originals.items():
            p.write_text(src, encoding="utf-8")
    print("=" * 96)
    print(f"结论：{'全部变异都被测试挡住（%d/%d）' % (len(MUTATIONS), len(MUTATIONS)) if not bad else '未被覆盖：' + ', '.join(bad)}")
    print(f"（详细输出：{LOG}）")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
