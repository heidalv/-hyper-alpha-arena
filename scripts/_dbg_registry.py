import os, sys
sys.path.insert(0, ".")
sys.path.append("backend")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")
from backend.services.factor_engine.factor_registry import registry
from backend.services.factor_engine.factor_service import factor_service

factor_service._ensure_registry_loaded()
print("registry 因子数:", len(registry._factors))
print("metadata_cache 大小:", len(registry._metadata_cache))
cats = {}
for fid, cls in list(registry._factors.items())[:60]:
    meta = registry._metadata_cache.get(fid)
    cat = getattr(meta, "category", None) if meta else None
    cats[cat] = cats.get(cat, 0) + 1
print("category 分布(前60):", cats)
print("--- 样例 id/metadata ---")
for fid, cls in list(registry._factors.items())[:12]:
    meta = registry._metadata_cache.get(fid)
    print(fid, "| meta:", type(meta).__name__ if meta else None,
          "| cat:", getattr(meta, "category", None) if meta else None,
          "| cls module:", getattr(cls, "__module__", "?"))
