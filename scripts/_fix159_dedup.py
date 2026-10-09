import pathlib, re
p = pathlib.Path("backend/services/analysis/schemas.py")
s = p.read_text(encoding="utf-8")
# 末尾那份重复定义（在 TASK_SCHEMAS 之后）整体删除
pat = re.compile(r"\n\ndef _mlto_debate_template\(\) -> Dict\[str, Any\]:\n(?:.*\n)*?    \}\n", re.M)
m = pat.search(s)
if m:
    s = s[:m.start()] + "\n" + s[m.end():]
    p.write_text(s, encoding="utf-8")
    print("已删除末尾重复定义")
else:
    print("未找到重复定义（可能只有一份）")
print("文件中 _mlto_debate_template 定义次数 =", s.count("def _mlto_debate_template"))
