import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.fee_schedule_service import get_fee_rate, get_all_exchange_summary
print("=== 当前系统配置的费率 ===")
for row in get_all_exchange_summary():
    print(f"  {row.get('exchange'):<12} maker={row.get('maker_fee_pct')}%  taker={row.get('taker_fee_pct')}%")
print("\nasterdex maker:", get_fee_rate('asterdex', True), " taker:", get_fee_rate('asterdex', False))
