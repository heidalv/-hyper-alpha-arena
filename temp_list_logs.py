"""列出最新日志文件"""
import os, time

d = r'D:\001Alpha\Hyper-Alpha-Arena\logs'
files = []
for f in os.listdir(d):
    if f.endswith('.log'):
        p = os.path.join(d, f)
        files.append((f, os.path.getmtime(p), os.path.getsize(p) / 1024))
files.sort(key=lambda x: x[1], reverse=True)
for f, t, kb in files[:10]:
    print('{}  {:8.0f}KB  {}'.format(time.strftime('%m-%d %H:%M', time.localtime(t)), kb, f))
