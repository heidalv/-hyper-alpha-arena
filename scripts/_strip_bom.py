# -*- coding: utf-8 -*-
"""去掉被 PowerShell Set-Content 加上的 BOM（AST/import 会因此炸）。"""
import io
import sys

p = sys.argv[1]
raw = io.open(p, "rb").read()
if raw.startswith(b"\xef\xbb\xbf"):
    io.open(p, "wb").write(raw[3:])
    print("stripped BOM:", p)
else:
    print("no BOM:", p)
