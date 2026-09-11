import sys
import importlib

print("executable:", sys.executable)
print("version:", sys.version.split()[0])

mods = ["fastapi", "uvicorn", "psycopg", "sqlalchemy", "numpy", "pandas",
        "ccxt", "apscheduler", "pydantic", "redis", "httpx", "sklearn"]
missing = []
for name in mods:
    try:
        mod = importlib.import_module(name)
        print("  {:12} {}".format(name, getattr(mod, "__version__", "?")))
    except Exception as exc:  # noqa: BLE001
        missing.append(name)
        print("  {:12} MISSING ({})".format(name, type(exc).__name__))
print("MISSING:", missing)
