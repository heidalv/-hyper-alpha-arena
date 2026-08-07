"""精确profile health端点的7个子系统耗时 - 输出到文件"""
import sys, os, time
sys.path.insert(0, 'd:/BaiduNetdiskDownload/001Alpha/001Alpha/Hyper-Alpha-Arena')
os.chdir('d:/BaiduNetdiskDownload/001Alpha/001Alpha/Hyper-Alpha-Arena')

out = []
timings = []

def profile(name, fn):
    t0 = time.perf_counter()
    try: fn(); timings.append((name, time.perf_counter() - t0))
    except Exception as e: timings.append((name, -1, str(e)[:80]))

profile('framework_rollout', lambda: None)  # import already done above
t0 = time.perf_counter()
from backend.config.framework_rollout import _AGGRESSIVE_DEFAULTS
rollout = {k: os.environ.get(k, '?') for k in sorted(_AGGRESSIVE_DEFAULTS)}
timings.insert(0, ('framework_rollout', time.perf_counter() - t0))

t0 = time.perf_counter()
from backend.services.event_sourcing.phase2 import get_reconcile_stats
from backend.services.event_sourcing.phase3 import get_phase3_stats
from backend.services.event_sourcing.phase4 import get_phase4_stats
es = {'reconcile': get_reconcile_stats(), 'phase3': get_phase3_stats(), 'phase4': get_phase4_stats()}
timings.append(('event_sourcing', time.perf_counter() - t0))

t0 = time.perf_counter()
from backend.services.ml.activation_service import get_activation_stats
ma = get_activation_stats()
timings.append(('ml_activation', time.perf_counter() - t0))

t0 = time.perf_counter()
from backend.services.resource_guard import get_guard_stats
rg = get_guard_stats()
timings.append(('resource_guard', time.perf_counter() - t0))

t0 = time.perf_counter()
from backend.services.promotion_scan_service import get_scan_stats
pg = get_scan_stats()
timings.append(('promotion_gate', time.perf_counter() - t0))

t0 = time.perf_counter()
from backend.services.full_auto_trading_service import full_auto_trading_service
from backend.services.full_auto.orchestrator import get_orchestrator
orch = get_orchestrator(full_auto_trading_service).get_loop_stats()
timings.append(('orchestrator', time.perf_counter() - t0))

t0 = time.perf_counter()
from backend.services.qaa_trade_memory_bridge import get_qaa_trade_memory_stats
rag = get_qaa_trade_memory_stats()
timings.append(('qaa_rag', time.perf_counter() - t0))

total = 0
out.append("Health endpoint subsystem timings (cold start):")
for t in timings:
    ms = t[1] * 1000
    total += ms
    out.append(f"  {t[0]:25s}: {ms:8.1f}ms" if len(t)==2 else f"  {t[0]:25s}: ERROR {t[2]}")
out.append(f"  {'TOTAL':25s}: {total:8.1f}ms")

open('d:/BaiduNetdiskDownload/001Alpha/001Alpha/Hyper-Alpha-Arena/_profile_out.txt','w').write('\n'.join(out))
print('done')
