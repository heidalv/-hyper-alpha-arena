# -*- coding: utf-8 -*-
"""[P8 / §67 执行 2026-09-10] 死键登记册契约测试。

现状（本测试锁定）：
  * `.env` 946 键中，**19 个**属于"没有读取方 / 只在测试里被读"；
  * 两套**独立工具**对同一集合给出一致结论：
      静态 AST 审计（`backend/scripts/audit_config_effective.py`）与
      运行期注册表校验（`backend/config/env_registry.find_unknown_flags()`）
      —— 差集只允许出现的非系统前缀键（`LOG_FILE`/`NO_PROXY`/`no_proxy`）；
  * 处置方式：**不删 `.env`**，改为 `data/dead_keys_register.json` 登记（键/分类/证据/建议/复核时间），
    并由本测试保证登记册与真实代码状态**不会静默漂移**：
      - 有新死键 → `--check` 失败（要求登记）；
      - 某键被接线（不再是死键）→ 也失败（要求从登记册移除）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REGISTER = ROOT / "data" / "dead_keys_register.json"
SCRIPT = ROOT / "backend" / "scripts" / "audit_dead_keys_register.py"


def _load() -> dict:
    assert REGISTER.exists(), f"登记册缺失: {REGISTER}（跑 {SCRIPT.name} 生成）"
    return json.loads(REGISTER.read_text(encoding="utf-8"))


def test_register_structure_and_vocabulary():
    data = _load()
    entries = data["entries"]
    assert entries, "登记册为空"
    keys = [e["key"] for e in entries]
    assert len(keys) == len(set(keys)), f"登记册有重复键: {keys}"
    allowed = set(data["decisions_vocabulary"])
    for e in entries:
        assert e["decision"] in allowed, f"{e['key']} 的处置 {e['decision']} 不在词汇表内"
        assert e["kind"] in {"truly_dead", "used_only_in_tests"}
        assert e["evidence"], f"{e['key']} 缺少证据来源"
    assert data["env_keys_total"] and data["env_keys_total"] > 500


def test_cross_tool_agreement_and_no_drift():
    """`--check` 会重跑两套工具比对：新增死键 / 已接线 / 工具分歧都会让它非零退出。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"死键登记册与当前状态不一致（漂移）:\n{out[-1500:]}"
    assert "一致" in out, out[-500:]


def test_register_is_machine_readable_for_downstream_tools():
    data = _load()
    # 下游（报告/前端/报警）依赖这些字段名，改名即破坏契约
    for field in ("generated_at", "env_keys_total", "truly_dead_n", "registry_unknown_n",
                  "cross_check_ok", "cross_check_diff", "entries"):
        assert field in data, f"登记册缺字段 {field}"
    wire_up = [e["key"] for e in data["entries"] if e["decision"] == "wire-up"]
    assert wire_up, "本应存在『代码里有人写过读取方、但只在测试里生效』的键（P8 的核心清单）"
