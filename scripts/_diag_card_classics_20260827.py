# -*- coding: utf-8 -*-
"""经典因子过门禁诊断: rev_5/mom_5 走 build_factor_card 看 admission 判定。"""
import sys, os
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.kline_data_service import kline_service
from backend.services.factor_engine.factor_card import build_factor_card

SYMS = ["BTC", "ETH", "SOL", "BNB", "ASTER", "UNI", "VIRTUAL", "XPL", "XRP"]
PERIOD = "4h"
LIMIT = 720

class SimpleExpr:
    def __init__(self, expr_id, fn):
        self.expr_id = expr_id
        self._fn = fn
        self.ast = {"name": expr_id}
    def evaluate(self, fields):
        return self._fn(fields)

def _kline_to_fields_local(df):
    out = {}
    for c in ("open","high","low","close","volume"):
        out[c] = df[c].to_numpy(dtype=float)
    return out

def load(sym):
    rows = kline_service.get_klines_from_db(sym, PERIOD, LIMIT)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for c in ("open","high","low","close","volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close","high","low","volume"]).reset_index(drop=True)

class Rev5Expr(SimpleExpr):
    def __init__(self):
        super().__init__("diag_rev5", None)
    def evaluate(self, fields):
        c = pd.Series(fields["close"])
        return -(c.pct_change(5)).to_numpy()

class Mom5Expr(SimpleExpr):
    def __init__(self):
        super().__init__("diag_mom5", None)
    def evaluate(self, fields):
        c = pd.Series(fields["close"])
        return (c.pct_change(5)).to_numpy()

def main():
    dfs = {}
    for sym in SYMS:
        df = load(sym)
        if df is not None and len(df) > 200:
            dfs[sym] = df
    for name, expr in (("rev_5", Rev5Expr()), ("mom_5", Mom5Expr())):
        card = build_factor_card(factor_id="diag_" + name, expr=expr, dfs=dfs,
                                 period=PERIOD, horizon=5, source="diag")
        adm = (card or {}).get("admission") or {}
        ic = (card or {}).get("ic") or {}
        print("== %s ==" % name)
        print("admission:", adm)
        print("ic:", {k: (round(float(v), 4) if isinstance(v, (int, float)) else v) for k, v in ic.items()})
        print("quantile:", (card or {}).get("quantile", {}))
        print()

if __name__ == "__main__":
    main()
