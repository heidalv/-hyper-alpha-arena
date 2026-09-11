# -*- coding: utf-8 -*-
"""[2026-09-10 §63] env_registry 校验链路的「可见性/有效性」契约测试。

背景（§63 实证）：
  1. `validate_strict()`（防"静默失效 flag"的闸）原先在 `main.py` **第 34 行**被调用——
     那时**文件日志 handler 还没挂上**（`_bootstrap_logging()` 在第 119 行），
     于是它发现的 153 个未登记 flag 告警只走 stderr，`logs/backend.log` 里**一条都没有**；
  2. 它此前能扫到 `.env` 是**偶然**（靠 `framework_rollout → settings → load_dotenv` 的导入顺序），
     而它自己的 CLI（`python -m backend.config.env_registry --check`）**不加载 .env** ⇒
     在空环境上扫描 ⇒ 恒返回"未知 flag: 0 / 退出码 0"的**假绿**；
  3. `SYSTEM_PREFIXES` 漏了 ANOMALY_/ONCHAIN_/HERMES_/NSGA2_/REENTRY_/MARKET_SCANNER_/TIER_/KLINE_ 等
     命名空间 ⇒ 这些前缀下的死键/拼错键**永远不会被报出**。

本测试锁住修复后的不变量：
  A. `.env` 懒加载：校验函数在 env=None 时会先加载 `.env`（CLI 不再假绿）；
  B. **两套独立工具一致**：registry 报出的"未登记 flag" ⊆ 静态审计工具的"真·死键"（且非前缀键为已知 3 个）；
  C. settings 定义且被读取的 `.env` 键**全部已登记**（0 缺口）；
  D. mid/long 闸门键在 `SAFETY_CRITICAL_FLAGS` 中（关掉会被显式告警）；
  E. 源码顺序：`main.py` 里 `validate_strict()` 必须在 `_bootstrap_logging()` 与 `load_dotenv()` **之后**。
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

import pytest  # noqa: E402

from backend.config import env_registry as reg  # noqa: E402

ace = importlib.import_module("audit_config_effective")

MAIN_SRC = ROOT / "backend" / "main.py"

# 关掉即"保护消失"的 mid/long 闸门键（§63 登记）
MIDLONG_GATE_KEYS = {
    "MIDLONG_PORTFOLIO_GATE_ENABLED",
    "MIDLONG_CHOP_GATE_ENABLED",
    "MIDLONG_FUNDING_GATE_ENABLED",
    "MIDLONG_NO_PROGRESS_EXIT_ENABLED",
    "MIDLONG_POSITION_MGMT_ENABLED",
    "TIER_MID_ENABLED",
    "TIER_LONG_ENABLED",
    "LIVE_TPSL_SYNC",
}


# ── A. .env 懒加载 ⇒ CLI 不再假绿 ──

def test_env_lazy_loaded_before_validation():
    assert reg.ensure_env_loaded() is True, "找不到/未能加载 .env（校验会在空环境上给出假绿）"
    # 加载后应能看到 .env 里的系统前缀键
    assert "MIDLONG_PORTFOLIO_GATE_ENABLED" in reg.os.environ


def test_unknown_flags_nonempty_after_load():
    """修复前：CLI（未加载 .env）恒得 0 ⇒ 假绿；现在应报出真实的未登记键（≥1）。"""
    assert len(reg.find_unknown_flags()) >= 1


# ── B. 两套独立工具一致 ──

def test_registry_unknowns_are_exactly_the_dead_keys():
    findings = ace.build_report(ROOT)["findings"]
    dead = set(findings["env_truly_dead"])
    unknown = set(reg.find_unknown_flags())
    assert unknown <= dead, f"registry 报出但静态审计不认为是死键: {sorted(unknown - dead)}"
    only_nonprefix = dead - unknown
    assert only_nonprefix <= {"LOG_FILE", "NO_PROXY", "no_proxy"}, \
        f"死键中未被 registry 覆盖（且非已知非前缀键）: {sorted(only_nonprefix)}"


# ── C. 登记缺口为 0 ──

def test_settings_defined_keys_all_registered():
    findings = ace.build_report(ROOT)["findings"]
    gap = findings["settings_not_in_registry"]
    assert gap == [], f"settings 定义但未登记 env_registry: {gap}"


# ── D. mid/long 闸门键必须在安全名单 ──

def test_midlong_gate_keys_are_safety_critical():
    missing = sorted(MIDLONG_GATE_KEYS - set(reg.SAFETY_CRITICAL_FLAGS))
    assert not missing, f"以下闸门键关掉时不会有显式告警: {missing}"


def test_known_flags_are_all_uppercase_and_unique_shape():
    src = (ROOT / "backend" / "config" / "env_registry.py").read_text(encoding="utf-8")
    # 登记块不得引入重复项（frozenset 去重会掩盖手误，故显式检查文本）
    block_start = src.index("KNOWN_FLAGS: frozenset[str] = frozenset({")
    block = src[block_start: src.index("})", block_start)]
    keys = re.findall(r'"([A-Z][A-Z0-9_]{2,})"', block)
    dup = {k for k in keys if keys.count(k) > 1}
    assert not dup, f"KNOWN_FLAGS 文本中存在重复键: {sorted(dup)}"
    assert len(reg.KNOWN_FLAGS) >= 1500, f"白名单规模异常缩小: {len(reg.KNOWN_FLAGS)}"


# ── E. 启动顺序护栏 ──

def test_validate_strict_runs_after_logging_and_dotenv():
    src = MAIN_SRC.read_text(encoding="utf-8")
    i_boot = src.index("_bootstrap_logging()")
    i_dotenv = src.index("load_dotenv(os.path.join(os.path.dirname(__file__)")
    i_val = src.index("validate_strict()")
    assert i_val > i_boot, "validate_strict() 必须在日志 bootstrap 之后（否则告警进不了文件日志）"
    assert i_val > i_dotenv, "validate_strict() 必须在 load_dotenv 之后（否则扫不到 .env）"
    # 修复前的早期调用点不得复现
    assert src.index("validate_strict()") == src.rindex("validate_strict()"), \
        "存在多处 validate_strict() 调用（可能保留了被下移前的旧调用）"


def test_warning_falls_back_to_stderr_without_handlers(monkeypatch, capsys):
    """无任何 handler 时（启动极早期）必须落到 stderr，不能被静默丢弃。

    注意：pytest 自身会挂 logging handler ⇒ 必须先清空 `root.handlers` 才能复现
    "启动极早期"的真实条件（这正是修复前告警进不了文件日志的原因）。
    """
    import logging

    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    # 必须用**系统前缀**且未登记的键，否则 find_unknown_flags 不会命中
    reg.validate_strict({"MIDLONG_FAKE_FLAG_FOR_TEST": "1"}, on_unknown="warn")
    captured = capsys.readouterr()
    assert "MIDLONG_FAKE_FLAG_FOR_TEST" in (captured.err or ""), captured.err
