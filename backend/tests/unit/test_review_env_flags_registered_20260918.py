# -*- coding: utf-8 -*-
"""[F373 2026-09-18] 本次复查新增的 env flag **必须登记**（否则启动期告警 + 静默风险）。

## 背景（我先重复了别人做过的事 —— 第 22 次自我更正）

我写了一个 `.env` 死开关审计脚本，报出 19 个"设了却没人读"的键，
**随后发现这套治理 2026-09-10 就做过且成熟得多**：
`data/dead_keys_register.json` + `backend/tests/unit/test_config_dead_keys_20260910.py`（DEAD_ALLOWLIST 21 条，
每条带分类原因：`non_defect` / `intentionally_removed` / `enforced_by_code_constant` /
`lane_unimplemented` / `near_miss`），并且专门处理了**动态前缀读取**
（`os.getenv(f"PC_{param}_{lane}")`）与**后缀拼接读取**（`name + "_PAPER"`）——
这两类正是朴素扫描器的假阳性来源；该文件还记录了一次同类误报（`PC_RISK_PER_TRADE_PCT_LONG`
曾被误判死键）。该测试 11/11 通过 ⇒ **登记册未漂移，治理仍然有效**。

## 但该治理有一个盲区（本轮的可行动项）

`env_registry.find_unknown_flags()` 只检查 **匹配 `SYSTEM_PREFIXES`** 的键
（`BACKTEST_`/`FACTORS_`/`MLTO_`/`V7_` 等在列，`LEARNING_READBACK_` 不在）
⇒ **非系统前缀下的 flag 不登记也不会被任何机制发现**。
本次复查新增了 10 个 flag，其中 6 个已被登记册自己的扫描列成候选，现已补登记。
本文件锁住"这些键必须在 `KNOWN_FLAGS` 里"，防止将来被删掉或新增后忘记登记。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.config.env_registry import KNOWN_FLAGS  # noqa: E402

#: 本次复查（2026-09-18）新增或用到的 flag —— 必须全部登记
REVIEW_FLAGS = (
    "BACKTEST_FACTOR_ATTR",
    "BACKTEST_FACTOR_ATTR_PATH",
    "FACTORS_LAB_ALIGN_MIN",
    "MLTO_FACTOR_ANCHOR_PERIOD",
    "MLTO_FACTOR_ANCHOR_WEIGHT",
    "V7_CODEGEN_FULL_POOL",
    "LEARNING_READBACK_ENABLED",
    "LEARNING_READBACK_CHARS",
    "LEARNING_READBACK_DEDUPE_SECONDS",
    "MLTO_DECAY_PENALTY_ENABLED",
)


def test_review_flags_are_registered():
    missing = [k for k in REVIEW_FLAGS if k not in KNOWN_FLAGS]
    assert not missing, (
        f"本次复查的 flag 未登记到 KNOWN_FLAGS: {missing} —— "
        "未登记 ⇒ 启动期告警，且属本模块文件头记录的『未登记 flag 静默无效』风险"
    )


def test_registry_scan_no_longer_lists_our_candidates():
    """登记册自带扫描的候选清单里不得再出现这些键（否则等于补登记失效）。"""
    import importlib
    import io
    import contextlib
    er = importlib.import_module("backend.config.env_registry")
    fn = getattr(er, "scan_code_flags", None) or getattr(er, "_scan_code_flags", None)
    if fn is None:
        import pytest
        pytest.skip("该版本 env_registry 未提供可调用的扫描函数（--scan 走 __main__）")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn()
    out = buf.getvalue()
    listed = [k for k in REVIEW_FLAGS if f'"{k}"' in out]
    assert not listed, f"仍被列为未登记候选: {listed}"


def test_documented_blind_spot_is_still_true():
    """记录事实：`LEARNING_READBACK_` 不在 SYSTEM_PREFIXES ⇒ 该前缀不被强制登记。

    若哪天它被加入 SYSTEM_PREFIXES，本用例会失败 —— 那是**好事**（说明治理收紧了），
    届时请把这条断言改成"必须登记"，并同步报告 §30。
    """
    try:
        from backend.config.env_registry import SYSTEM_PREFIXES
    except Exception:
        return  # 常量不在则不约束
    in_prefix = [p for p in SYSTEM_PREFIXES if "LEARNING_READBACK_".startswith(p)]
    assert not in_prefix, (
        "LEARNING_READBACK_ 已被纳入 SYSTEM_PREFIXES ⇒ 治理已收紧，请同步报告 §30 并改断言"
    )
