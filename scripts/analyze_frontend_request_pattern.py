# -*- coding: utf-8 -*-
"""前端腿取证：真实浏览器使用时段（我的探测之前）的请求量/状态码/续期频率。

访问日志每条请求被记 3 次（两种 INFO 格式 + uvicorn.access），按
(客户端端口, 方法, 路径, 秒) 去重后统计。
"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]

LINE = re.compile(
    r'(\d{1,3}(?:\.\d{1,3}){3}):(\d+) - "(\w+) (\S+) HTTP/1\.1" (\d{3})'
)
TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

# 我的探测从 15:26 开始（backend-8000 侧）
CUT = datetime(2026, 9, 18, 15, 25, 0)

seen: set[tuple] = set()
reqs: list[tuple[datetime, str, str, str, int]] = []   # ts, method, path, port, status
cur_ts: datetime | None = None

for ln in (ROOT / "logs" / "backend.log").read_text(
        encoding="utf-8", errors="replace").splitlines():
    m = TS.match(ln)
    if m:
        try:
            cur_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            cur_ts = None
    if cur_ts is None or cur_ts >= CUT:
        continue
    a = LINE.search(ln)
    if not a:
        continue
    _ip, port, method, path, status = a.groups()
    key = (cur_ts, port, method, path)
    if key in seen:
        continue
    seen.add(key)
    reqs.append((cur_ts, method, path, port, int(status)))

print(f"去重后请求数（15:25 之前）= {len(reqs):,}")
if not reqs:
    raise SystemExit("无数据")

t0, t1 = reqs[0][0], reqs[-1][0]
mins = (t1 - t0).total_seconds() / 60
print(f"时间跨度 {t0:%H:%M} → {t1:%H:%M}（{mins:.0f} 分钟）"
      f"⇒ 平均 {len(reqs)/max(mins,1):.1f} 请求/分钟（≈{len(reqs)/max(mins,1)/60:.2f}/秒）\n")


def norm(p: str) -> str:
    p = p.split("?")[0]
    p = re.sub(r"/(fa_[0-9a-f]+|[0-9a-f]{8}-[0-9a-f-]{27,})", "/{sid}", p)
    p = re.sub(r"/\d+", "/{id}", p)
    return p


print("=" * 100)
print("【1】请求量 Top15 端点（占比 = 该端点请求数 / 总请求数）")
print("=" * 100)
cnt = Counter(norm(p) for _, _, p, _, _ in reqs)
print(f"  {'端点':50s} {'次数':>7s} {'占比':>7s} {'次/分':>7s}")
for ep, n in cnt.most_common(15):
    print(f"  {ep[:50]:50s} {n:7d} {n/len(reqs)*100:6.1f}% {n/max(mins,1):7.1f}")

print()
print("=" * 100)
print("【2】状态码分布（按端点，只看非 2xx）")
print("=" * 100)
bad = Counter((norm(p), s) for _, _, p, _, s in reqs if s >= 300)
if not bad:
    print("  无任何非 2xx 响应")
for (ep, s), n in bad.most_common(15):
    print(f"  {s} ×{n:5d}  {ep[:70]}")
print(f"  非 2xx 合计 = {sum(bad.values())} / {len(reqs)} = {sum(bad.values())/len(reqs)*100:.2f}%")

print()
print("=" * 100)
print("【3】认证续期 / 登录相关")
print("=" * 100)
for pat in ("/auth/refresh", "/auth/login", "/auth/logout", "/auth/me"):
    n = sum(1 for _, _, p, _, _ in reqs if p.startswith(pat))
    if n:
        st = Counter(s for _, _, p, _, s in reqs if p.startswith(pat))
        print(f"  {pat:16s} {n:6d} 次   状态码分布={dict(st)}")

print()
print("=" * 100)
print("【4】每分钟请求量（判断轮询是否超额；列前 20 高）")
print("=" * 100)
permin = Counter(t.replace(second=0) for t, _, _, _, _ in reqs)
for t, n in permin.most_common(20):
    print(f"  {t:%H:%M}  {n:5d} 请求/分")
print(f"  中位 {sorted(permin.values())[len(permin)//2]} 请求/分")

print()
print("=" * 100)
print("【5】纸面/实盘页的轮询密度（paper/*、market/ticker-bar、full-auto/*）")
print("=" * 100)
for pat in ("/paper/", "/market/ticker-bar", "/full-auto/", "/auto-coin/", "/sessions", "/account"):
    sel = [t for _, _, p, _, _ in reqs if p.startswith("/api" + pat)]
    if not sel:
        continue
    print(f"  {pat:20s} {len(sel):7d} 次  = {len(sel)/max(mins,1):7.1f}/分"
          f"  （占全部 {len(sel)/len(reqs)*100:4.1f}%）")
