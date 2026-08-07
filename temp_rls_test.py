import os, sys
sys.path.insert(0, r'D:\001Alpha\Hyper-Alpha-Arena')
os.environ['NO_PROXY']='127.0.0.1,localhost'; os.environ['no_proxy']='127.0.0.1,localhost'
os.environ.pop('HTTP_PROXY',None); os.environ.pop('http_proxy',None); os.environ.pop('HTTPS_PROXY',None); os.environ.pop('https_proxy',None)
from dotenv import load_dotenv
load_dotenv(r'D:\001Alpha\Hyper-Alpha-Arena\.env', override=True)
print('AUTH_LOCAL_TENANT =', repr(os.environ.get('AUTH_LOCAL_TENANT')))
print('AUTO_COIN_FORBID_LONG =', repr(os.environ.get('AUTO_COIN_FORBID_LONG')))

# 1) 模拟后台线程：直接调用 is_long_allowed（无 ContextVar 身份）
from backend.services.auto_coin_selector import is_long_allowed, get_fixed_symbols_for_session
print('=== 后台线程视角（无身份）===')
print('get_fixed_symbols_for_session ->', get_fixed_symbols_for_session('fa_10d44c724e', db=None))
for sym in ('BTC','ETH','SOL','ENA'):
    print(f'is_long_allowed({sym}) ->', is_long_allowed(sym, 'fa_10d44c724e'))

# 2) 设置 admin 身份后再测
from backend.core.tenant import set_request_identity
set_request_identity(tenant_id=326, user_id=326, is_admin=True)
print('=== 设置 admin+tenant=326 后 ===')
print('get_fixed_symbols_for_session ->', get_fixed_symbols_for_session('fa_10d44c724e', db=None))
for sym in ('BTC','ETH','SOL','ENA'):
    print(f'is_long_allowed({sym}) ->', is_long_allowed(sym, 'fa_10d44c724e'))
