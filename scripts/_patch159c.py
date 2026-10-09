import pathlib
p = pathlib.Path("backend/tests/unit/test_analysis_schemas_20260904.py")
s = p.read_text(encoding="utf-8")
a = '        tpl = spec.get("template") or {}'
b = ('        tpl = spec.get("template") or {}\n'
     '        if callable(tpl):   # [轮159] 支持可调用模板（周期档从 HORIZONS 派生）\n'
     '            tpl = tpl()')
if a in s:
    s = s.replace(a, b, 1)
    p.write_text(s, encoding="utf-8")
    print("已支持 callable 模板")
else:
    print("!! 未命中")
