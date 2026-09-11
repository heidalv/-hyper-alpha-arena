import os, sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT/".env")
import pandas as pd
import backend.services.long_trend_v2 as lv2

def stub(score, state):
    def _f(symbol):
        return pd.DataFrame({"close": [1.0]}), {"state": state, "score": score, "close": 1.0}
    return _f

lv2.long_v2_enabled = lambda: True          # 强制启用闸（生产当前关闭）
for thr, score, state, note in [(3,3.0,"up","默认阈值"),(2,2.0,"sideways","放宽到2"),(4,3.0,"up","收紧到4"),(4,4.0,"up","阈值4/score4")]:
    os.environ["LONG_V2_L1_UP_SCORE"] = str(thr)
    lv2._get_l1_classification = stub(score, state)
    ok, why = lv2.entry_gate("BTC", "buy")
    sig = lv2.entry_signal("BTC")
    print(f"  阈值={thr} score={score} state={state:<9} allowed={ok!s:<5} reason={why}")
    print(f"        hold_reason={sig.get('hold_reason')}   ({note})")
