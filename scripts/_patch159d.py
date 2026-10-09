import pathlib
p = pathlib.Path("backend/tests/unit/test_debate_schema_20260921.py")
s = p.read_text(encoding="utf-8")
a = '''        "swing": {"stance": "long", "confidence": 0.6, "argument": "中期偏多"},
'''
if a in s:
    s = s.replace(a, "")
    p.write_text(s, encoding="utf-8")
    print("已从样例里删掉 swing 档")
else:
    print("样例里没有 swing 行（或格式不同）")
print("残留 swing：", [l.strip() for l in s.splitlines() if "swing" in l][:3])
