import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
print("os.getenv:", os.getenv("MIDLONG_SHORT_MODE"))
from backend.services.full_auto.midlong_circuit_gate import _short_mode
print("_short_mode():", _short_mode())
