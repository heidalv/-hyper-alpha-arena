from pathlib import Path
import subprocess

paths = [
    Path(r"D:\001Alpha\Hyper-Alpha-Arena\logs\pg-d-longwait.log"),
    Path(r"D:\001Alpha\Hyper-Alpha-Arena\logs\pg-d-ready.flag"),
    Path(r"D:\PostgreSQL\15\data\postmaster.pid"),
    Path(r"D:\PostgreSQL\15\data\pg_start_d.log"),
]
for p in paths:
    print("====", p)
    print("exists", p.exists(), "size", p.stat().st_size if p.exists() else 0)
    if p.exists():
        t = p.read_text(encoding="utf-8", errors="replace")
        print("\n".join(t.splitlines()[-25:]))

r = subprocess.run(
    ["tasklist", "/FI", "IMAGENAME eq postgres.exe"],
    capture_output=True,
    text=True,
    errors="replace",
)
print("==== tasklist")
print(r.stdout)
r2 = subprocess.run(
    "netstat -ano | findstr :5432 | findstr LISTENING",
    shell=True,
    capture_output=True,
    text=True,
    errors="replace",
)
print("==== listen")
print(r2.stdout)

logs = sorted(
    Path(r"D:\PostgreSQL\15\data\log").glob("postgresql-*.log"),
    key=lambda x: x.stat().st_mtime,
    reverse=True,
)
if logs:
    print("==== latest", logs[0].name)
    print("\n".join(logs[0].read_text(encoding="utf-8", errors="replace").splitlines()[-40:]))
