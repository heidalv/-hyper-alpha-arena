# -*- coding: utf-8 -*-
"""[2026-10-10] 只读：定位 .env 尾部混合编码区域并逐行尝试解码。

背景：`.env` 是 UTF-8（`python-dotenv` 用 UTF-8 读，坏一个字节就 UnicodeDecodeError
⇒ **后端下次启动会崩**）。实测 17:30:50 有另一个写入者往尾部追加了一段
**GBK 编码**的注释（里面有 `# [2026-10-10 盯手头寸] brain 出进…` 这类乱码）。
本脚本只诊断、**不修改**任何文件；修法见其后的报告。
"""
from pathlib import Path

P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\.env")
raw = P.read_bytes()
print(f"size={len(raw)}")


def try_dec(b: bytes):
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return enc, b.decode(enc)
        except UnicodeDecodeError:
            continue
    return None, None


# 找第一个 UTF-8 解码失败点
try:
    raw.decode("utf-8")
    print("整文件 UTF-8 OK")
    raise SystemExit(0)
except UnicodeDecodeError as e:
    first = e.start
print(f"首个非法 UTF-8 字节位置: {first}")

# 往前找最近的换行，作为"坏行"起点
nl = raw.rfind(b"\n", 0, first)
start = nl + 1
print(f"坏行起点: {start}")
chunk = raw[start:]
print(f"坏区长度: {len(chunk)}")

for i, line in enumerate(chunk.split(b"\r\n")):
    enc, dec = try_dec(line)
    tag = f"[{enc}]" if enc else "[UNDECODABLE]"
    show = dec if dec is not None else repr(line[:80])
    print(f"  L{i} {tag} {show[:120]}")
