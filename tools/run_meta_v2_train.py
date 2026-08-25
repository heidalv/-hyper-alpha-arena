import json, sys
sys.path.insert(0, ".")
try:
    from backend.services.scalp_meta_trainer import train_and_validate
    rep = train_and_validate()
    print(json.dumps(rep, ensure_ascii=False, indent=1))
except Exception as e:
    import traceback; traceback.print_exc()
    sys.exit(1)
