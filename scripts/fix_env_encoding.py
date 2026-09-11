# -*- coding: utf-8 -*-
"""修复 .env 尾部被 GBK 污染的注释行，重新以 UTF-8 写入。"""
from pathlib import Path

P = Path("D:/001Alpha/Hyper-Alpha-Arena/.env")
raw = P.read_bytes()
text = raw.decode("utf-8", errors="ignore")

# 定位我加的注释行并整行删除（污染源），保留 FUNDING_ARB_ENABLED=true
lines = text.splitlines(keepends=True)
out_lines = []
for ln in lines:
    if ln.startswith("# 2026-09"):
        continue
    out_lines.append(ln)
fixed = "".join(out_lines)
if "FUNDING_ARB_ENABLED=true" not in fixed:
    fixed = fixed.rstrip("\n") + "\n\n# 2026-09 套利引擎实盘准备：总开关开（会话级 arb_enabled 仍为关，不会交易）\nFUNDING_ARB_ENABLED=true\n"
P.write_text(fixed, encoding="utf-8")
print("已修复 .env 编码，尾部内容：")
print("\n".join(P.read_text(encoding="utf-8").splitlines()[-4:]))

# 验证 dotenv 能正常解析
import sys
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")
from dotenv import dotenv_values
vals = dotenv_values(str(P))
print("\ndotenv 解析 OK，FUNDING_ARB_ENABLED =", vals.get("FUNDING_ARB_ENABLED"))
print("DATABASE_URL =", (vals.get("DATABASE_URL") or "")[:50])
