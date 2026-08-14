"""阶段 4（安全加固）回归测试（2026-08-14）。

锁定：
- AST 白名单（code_safety）：注入用例全部拒绝、合法因子代码放行
- P1-F1 云端元数据 json.dumps 转义（引号/换行无法逃逸出字符串字面量）
- P1-F1/P1-F2 factor_id 路径穿越拒绝
- P1-E5 promote_cloud_factor 需要显式 confirm
"""
from __future__ import annotations

import ast
import json
import os
import shutil

import pytest

_TMP_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_phase4")


def _ws_tmp(name: str) -> str:
    d = os.path.join(_TMP_ROOT, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


def teardown_module(module):
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
# code_safety AST 白名单
# ═══════════════════════════════════════════════════════════

def test_ast_whitelist_rejects_injections():
    from backend.services.factor_engine.code_safety import ast_whitelist_check

    for bad in [
        "import os",
        "from os import system",
        "os.system('calc')",
        "__import__('os')",
        "eval('1+1')",
        "x.__class__",
        "data.__globals__",
        "open('file')",
        "getattr(data, 'x')",
        "subprocess.run('x')",
    ]:
        ok, reason = ast_whitelist_check(bad)
        assert ok is False, f"应拒绝: {bad!r}（reason={reason}）"


def test_ast_whitelist_accepts_legit_factor_code():
    from backend.services.factor_engine.code_safety import ast_whitelist_check

    # 注：因子代码不允许 import（np/pd 由生成模板提供）；np/pd 可直接使用。
    good_cases = [
        "result = data['close'].rolling(20).mean()\nreturn result",
        "result = np.log(data['close'])\nreturn result",
        "result = pd.Series(data['close']).pct_change()\nreturn result.fillna(0)",
        "x = abs(min(-1, max(1, len(data))))\nresult = float(x)\nreturn result",
    ]
    for code in good_cases:
        ok, reason = ast_whitelist_check(code)
        assert ok is True, f"应放行: {code!r}（reason={reason}）"


# ═══════════════════════════════════════════════════════════
# P1-F1：云端同步（validate + localize 转义 + 路径穿越）
# ═══════════════════════════════════════════════════════════

def test_sync_validate_code_blocks_import(monkeypatch):
    from backend.services.factor_engine.factor_sync_service import factor_sync_service as svc

    assert svc._validate_code("import os") is False
    assert svc._validate_code("os.system('x')") is False
    # 合法因子代码（含 pd 注解与 data/np/pd 属性链）
    assert svc._validate_code(
        "def calculate(self, data: pd.DataFrame) -> pd.Series:\n"
        "    result = data['close'].rolling(20).mean()\n"
        "    return result\n"
    ) is True


def test_localize_metadata_escaping(monkeypatch):
    """恶意元数据（引号/换行/import）无法逃逸出字符串字面量，生成文件可编译。"""
    from backend.services.factor_engine.factor_sync_service import factor_sync_service as svc

    pending = _ws_tmp("cloud_pending")
    monkeypatch.setattr(svc, "_cloud_pending_dir", pending)
    monkeypatch.setattr(svc, "_external_dir", _ws_tmp("cloud_external"))

    malicious_name = 'x"""\nimport os\nos.system("evil")\n#'
    definition = {
        "factor_id": "cloud_test_1",
        "name": malicious_name,
        "display_name": malicious_name,
        "description": 'bad""" + os.system("pwn") + """',
        "category": 'cat"; import sys #',
        "subcategory": "",
        "calculation_code": "result = data['close'].rolling(20).mean()\nreturn result",
    }
    path = svc._localize_factor(definition)
    assert path is not None
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()
    # 1) 必须能编译
    compile(code, path, "exec")
    # 2) 不允许引入 os/sys（注入若逃逸必然出现 import）
    tree = ast.parse(code)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert "os" not in imported and "sys" not in imported
    # 3) 模块 docstring 是转义后的原值（证明值被 json 字符串化，而非裸插值）
    doc = ast.get_docstring(tree)
    assert malicious_name in doc


def test_localize_rejects_path_traversal(monkeypatch):
    from backend.services.factor_engine.factor_sync_service import factor_sync_service as svc

    monkeypatch.setattr(svc, "_cloud_pending_dir", _ws_tmp("cloud_pending2"))
    monkeypatch.setattr(svc, "_external_dir", _ws_tmp("cloud_external2"))
    definition = {
        "factor_id": "../evil",
        "name": "evil",
        "category": "technical",
        "calculation_code": "result = 1\nreturn result",
    }
    assert svc._localize_factor(definition) is None


# ═══════════════════════════════════════════════════════════
# P1-E5：晋升需显式确认
# ═══════════════════════════════════════════════════════════

def test_promote_cloud_factor_requires_confirm():
    from backend.services.factor_engine.factor_sync_service import factor_sync_service as svc

    res = svc.promote_cloud_factor("any_id")  # confirm 默认 False
    assert res["status"] == "skipped"
    assert "confirm" in res["reason"]


# ═══════════════════════════════════════════════════════════
# P1-F2：AI 生成因子
# ═══════════════════════════════════════════════════════════

def test_ai_factor_validation_rejects_injection_and_traversal():
    from backend.services.ai_factor_discovery_service import (
        AIFactorDiscoveryService,
        GeneratedFactor,
    )

    svc = AIFactorDiscoveryService()

    def make(**kw):
        base = dict(
            factor_id="ai_test_factor", name="TestFactor",
            display_name="Test Factor", description="d",
            category="technical", subcategory="",
            python_code=(
                "def calculate(self, data: pd.DataFrame) -> pd.Series:\n"
                "    result = data['close'].rolling(20).mean()\n"
                "    return result\n"
            ),
        )
        base.update(kw)
        return GeneratedFactor(**base)

    assert svc.validate_generated_factor(make()) is True
    # 注入：import 绕过旧黑名单
    assert svc.validate_generated_factor(make(python_code="import os\nresult = os.listdir('/')\nreturn result")) is False
    # 注入：dunder 逃逸
    assert svc.validate_generated_factor(make(python_code="result = self.__class__.__mro__[0]\nreturn result")) is False
    # 路径穿越
    assert svc.validate_generated_factor(make(factor_id="../evil")) is False
    # 非法裸函数调用
    assert svc.validate_generated_factor(make(python_code="result = system('x')\nreturn result")) is False
