# -*- coding: utf-8 -*-
"""[工作流④] 数据中台看门狗 / 启动脚本修复的护栏测试（2026-10-04）。

## 事故链（全部有实测日志证据）
```
/health 端点抖动(超时)
  → 看门狗判 kind=zombie
  → 杀掉【正在正常采集】的 DC        ← logs: 04:33:03 DC zombie - restarting pids=32084,31800
                                       （当时日志持续写入、kline lag=0）
  → 用 hidden vbs 重启（不落地）      ← logs: 04:33:14 launched → 04:34:46 未健康 → 04:35:22 down
  → DC 长期 DOWN
  → 决策全是 [StrictData] missing=price / K线DATA_MISSING
  → 伪装成"策略不开仓"（根因被完全掩盖）
```

## 本轮修复（5 项）
1. 探针判定以「进程 + 端口 + **日志新鲜度**」为准，`/health` 降为参考
2. 看门狗：**数据仍在流动时绝不重启**（活体证据 `health timeout but data flowing - NOT restarting`）
3. 幂等启动器 `start-dc-watchdog.ps1` 保证**恰好 1 个**看门狗（实测 2→1，重复调用不新增）
4. vbs 启动 → 直接 `python -m backend.workers.market_data_center`（保留 `DC_WATCHDOG_USE_VBS` 回滚）
5. **编码规范**：含中文的 .ps1 必须带 UTF-8 BOM（PS 5.1 无 BOM 时按 ANSI 解析 → mojibake
   破坏赋值行 ⇒ 实测 `healthy:null` 假警报，两次修复失败后才定位到编码）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"

PROBE = SCRIPTS / "verify-data-center.ps1"
WATCHDOG = SCRIPTS / "data-center-watchdog.ps1"
LAUNCHER = SCRIPTS / "start-dc-watchdog.ps1"
START_DEV = SCRIPTS / "start-dev.ps1"
STOP_DEV = SCRIPTS / "stop-dev.ps1"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig")


def _has_bom(p: Path) -> bool:
    return p.read_bytes().startswith(b"\xef\xbb\xbf")


def test_all_repaired_scripts_exist():
    for p in (PROBE, WATCHDOG, LAUNCHER):
        assert p.exists(), f"缺少 {p.name}"


def test_scripts_with_non_ascii_must_have_bom():
    """PS 5.1 对无 BOM 的中文脚本按 ANSI 解析 —— 本会话实测导致逻辑静默失效。"""
    for p in (PROBE, WATCHDOG, START_DEV, STOP_DEV):
        src = _read(p)
        has_non_ascii = any(ord(ch) > 127 for ch in src)
        if has_non_ascii:
            assert _has_bom(p), f"{p.name} 含非 ASCII 但无 UTF-8 BOM（PS 5.1 会误解析）"


def test_probe_verdict_uses_freshness_not_health_only():
    src = _read(PROBE)
    assert "data-center.log" in src, "新鲜度判据必须基于 DC 日志写入时间"
    assert "$healthy = [bool](($procCount -ge 1) -and [bool]$portListening -and [bool]$freshOk)" in src
    assert "StaleThresholdMin" in src and "verdict" in src
    # 输出前重算（防汇总与逐项信号不一致）
    assert src.count("$healthy = [bool](") >= 2


def test_watchdog_never_kills_while_data_flows():
    src = _read(WATCHDOG)
    assert "NOT restarting" in src, "必须有'数据在流则不重启'分支"
    assert "data-center.log" in src and "LastWriteTime" in src, "判据必须是日志写入时间"
    assert "$ageMin -lt $staleMin" in src, "只有陈旧才允许重启"


def test_watchdog_restart_is_direct_not_vbs():
    src = _read(WATCHDOG)
    assert "backend.workers.market_data_center" in src, "重启必须直接启动 DC 进程"
    assert "DC_WATCHDOG_USE_VBS" in src, "vbs 旧路径必须可回滚"


def test_launcher_is_idempotent_and_ascii_only():
    src = _read(LAUNCHER)
    # 不变量修正：允许"纯 ASCII" 或 "含非 ASCII 但带 BOM"（两种都安全）；禁止的是
    # "含非 ASCII 且无 BOM"（PS 5.1 会按 ANSI 误解析 —— 本会话实测的病根）。
    if any(ord(ch) > 127 for ch in src):
        assert _has_bom(LAUNCHER), "含非 ASCII 必须带 BOM"
    assert "killed" in src and "duplicate" in src, "必须清理重复实例"
    assert "already running" in src, "必须复用既有实例（幂等）"
    assert "'-Stop'" in src or "$Stop" in src


def test_start_dev_probes_dc_and_stop_dev_keeps_one_watchdog():
    assert "verify-data-center.ps1" in _read(START_DEV), "start-dev 必须调用陈旧感知探针"
    assert "data-center-watchdog" in _read(STOP_DEV), "stop-dev 保留 DC 时必须保证看门狗存活"
