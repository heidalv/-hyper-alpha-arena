import pathlib, re
p = pathlib.Path(".env")
s = p.read_text(encoding="utf-8")
if "MIDLONG_DEBATE_MAX_OUTPUT_TOKENS=" in s:
    s = re.sub(r"(?m)^MIDLONG_DEBATE_MAX_OUTPUT_TOKENS=.*$", "MIDLONG_DEBATE_MAX_OUTPUT_TOKENS=2500", s)
    print("已改 .env 里的既有键 → 2500")
else:
    s += "\n# [轮158 2026-09-21] 辩论轮被 900 截断（175 条失败的 output_tokens 全部=900）\nMIDLONG_DEBATE_MAX_OUTPUT_TOKENS=2500\n"
    print("已追加 MIDLONG_DEBATE_MAX_OUTPUT_TOKENS=2500")
p.write_text(s, encoding="utf-8")
