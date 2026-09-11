import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
syms=[s.strip() for s in os.getenv("FACTOR_SCORER_SYMBOLS","").split(",") if s.strip()]
print("面板 symbol 数:", len(syms))
print(syms)
