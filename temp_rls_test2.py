import os, sys
sys.path.insert(0, r'D:\001Alpha\Hyper-Alpha-Arena')
os.environ['NO_PROXY']='127.0.0.1,localhost'; os.environ['no_proxy']='127.0.0.1,localhost'
os.environ.pop('HTTP_PROXY',None); os.environ.pop('http_proxy',None); os.environ.pop('HTTPS_PROXY',None); os.environ.pop('https_proxy',None)
from dotenv import load_dotenv
load_dotenv(r'D:\001Alpha\Hyper-Alpha-Arena\.env', override=True)

# 确保 ContextVar 为空（模拟后台线程无身份）
from backend.core.tenant import clear_request_identity
clear_request_identity()

from backend.services.auto_coin_selector import is_long_allowed, get_fixed_symbols_for_session
print('=== 后台线程视角（无身份）— 修复后 ===')
print('get_fixed_symbols_for_session ->', get_fixed_symbols_for_session('fa_10d44c724e', db=None))
for sym in ('BTC','ETH','SOL','ENA','FARTCOIN'):
    print(f'is_long_allowed({sym}) ->', is_long_allowed(sym, 'fa_10d44c724e'))
