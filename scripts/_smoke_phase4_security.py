"""阶段4冒烟：云同步总开关 / AST 校验 / 恶意元数据本地化。"""
import os, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../backend")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")

from backend.services.factor_engine.factor_sync_service import factor_sync_service as svc

# ① 总开关：默认禁用
r = svc.sync_from_repo()
print("① sync_from_repo（开关默认关）:", r)

# ② 代码校验
print("② _validate_code('import os') =", svc._validate_code("import os"))
print("② _validate_code(合法) =", svc._validate_code(
    "def calculate(self, data: pd.DataFrame) -> pd.Series:\n"
    "    result = data['close'].rolling(20).mean()\n    return result\n"))

# ③ 恶意元数据本地化 → pending 目录 + 可编译 + 无 os/sys import
import json, ast
pending = svc._cloud_pending_dir
evil = 'x"""\nimport os\nos.system("evil")\n#'
path = svc._localize_factor({
    "factor_id": "smoke_evil_meta",
    "name": evil, "display_name": evil,
    "description": 'd"; import sys #', "category": "technical", "subcategory": "",
    "calculation_code": "result = data['close'].rolling(20).mean()\nreturn result",
})
print("③ localize 结果:", path)
if path:
    code = open(path, encoding="utf-8").read()
    compile(code, path, "exec")
    tree = ast.parse(code)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    print("③ 生成文件 import 列表:", imports)
    print("③ os/sys 注入拦截:", "os" not in imports and "sys" not in imports)

# ④ 路径穿越拒绝
print("④ factor_id='../evil' →", svc._localize_factor({
    "factor_id": "../evil", "name": "e", "category": "technical",
    "calculation_code": "result = 1\nreturn result"}))

# ⑤ 晋升需确认
print("⑤ promote 无 confirm →", svc.promote_cloud_factor("any"))
