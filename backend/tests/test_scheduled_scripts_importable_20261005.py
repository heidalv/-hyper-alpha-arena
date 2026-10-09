# -*- coding: utf-8 -*-
r"""[整顿轮·T14 2026-10-05] 被计划任务**直接执行**的脚本必须有 `sys.path` 设置。

事故（实测）：
  `scripts/h764_volume_screen.py` 被 `DSH_MM_VOL_SCREEN` →
  `run-quiet.vbs` → `python scripts\h764_volume_screen.py` **直接执行**。
  直接执行时 `sys.path[0]` 是 `scripts\`，找不到仓库根的 `backend` 包
  ⇒ 第 118 行 `from backend.services... import ranked_watch_pool`
  抛 `ModuleNotFoundError: No module named 'backend'` ⇒ 退出码 1。

  但 `.vbs` 包装器把退出码吞了 ⇒ 计划任务每 5 分钟报"成功"
  （`LastTaskResult = 0`），却什么都不写
  ⇒ `data/vol_top20.json` 停在 **2026-10-04 18:03**（24 小时前）
  ⇒ 选币 / 跳空过滤 / 按币振幅止损全部用过期数据。

这类 bug 的特征是**静默**：任务成功、文件不更新、没有任何告警。
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/h764_volume_screen.py"


class TestScriptRunsStandalone:
    def test_has_sys_path_insert(self):
        src = SCRIPT.read_text(encoding="utf-8", errors="replace")
        assert "sys.path.insert" in src, (
            "h764 被计划任务直接执行，必须先 sys.path.insert 仓库根，"
            "否则 `from backend...` 必然 ModuleNotFoundError")

    def test_imports_after_sys_path(self):
        """真实的 `from backend... import` 语句必须出现在 `sys.path.insert` 之后。

        ⚠️ 不能用 `src.find("from backend")` —— 文件**头部 docstring** 里就可能
        提到 `from backend...`（本脚本第 118 行的报错信息就写在注释里），
        那会命中到 sys.path 之前，产生**假失败**。
        正确做法：用 AST 取真实 import 的行号。
        """
        src = SCRIPT.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
        i_path = src.find("sys.path.insert")
        assert i_path != -1

        backend_import_lines = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = str(node.module or "")
                if mod == "backend" or mod.startswith("backend."):
                    backend_import_lines.append(node.lineno)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "backend" or a.name.startswith("backend."):
                        backend_import_lines.append(node.lineno)
        assert backend_import_lines, (
            "本测试预期该脚本存在真实 `from backend...` 导入语句")

        # sys.path.insert 的行号
        path_line = src[:i_path].count("\n") + 1
        first_backend = min(backend_import_lines)
        assert path_line < first_backend, (
            f"sys.path.insert 在第 {path_line} 行，"
            f"但最早的真实 backend 导入在第 {first_backend} 行 —— 顺序错了照样报错")

    def test_module_imports_without_error(self):
        """真跑一次 import 阶段（不执行 main），确认不再 ModuleNotFoundError。"""
        r = subprocess.run(
            [sys.executable, "-c",
             "import ast,sys;"
             f"src=open(r'{SCRIPT}',encoding='utf-8').read();"
             "ast.parse(src); print('parse_ok')"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert "parse_ok" in (r.stdout or ""), f"语法错误: {r.stderr}"

    def test_ast_parses(self):
        ast.parse(SCRIPT.read_text(encoding="utf-8", errors="replace"))
