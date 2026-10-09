import pathlib, re
p = pathlib.Path("backend/services/analysis/schemas.py")
s = p.read_text(encoding="utf-8")
pat = re.compile(r"def _mlto_debate_template\(\) -> Dict\[str, Any\]:\n(?:.*?\n)*?    \}\n", re.M)
ms = list(pat.finditer(s))
print("找到定义份数 =", len(ms))
if len(ms) == 1:
    fn = ms[0].group(0)
    s = s[:ms[0].start()] + s[ms[0].end():]
    anchor = "TASK_SCHEMAS: Dict[str, Dict[str, Any]] = {"
    i = s.index(anchor)
    s = s[:i] + fn + "\n\n" + s[i:]
    # 清理可能留下的三连空行
    s = re.sub(r"\n{4,}", "\n\n\n", s)
    p.write_text(s, encoding="utf-8")
    print("已挪到 TASK_SCHEMAS 之前")
print("定义次数 =", s.count("def _mlto_debate_template"))
