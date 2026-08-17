import os, sys, traceback
sys.path.insert(0, ".")
sys.path.append("backend")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")
try:
    import backend.services.factor_engine.factors.derivatives.derivatives_factors as m
    print("import OK")
    print("classes:", [n for n in dir(m) if n.endswith("Factor")])
except Exception:
    traceback.print_exc()
