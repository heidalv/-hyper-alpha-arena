# -*- coding: utf-8 -*-
"""轮77 P1-10 回归：`discover_and_load_all()` 无缓存 → 按符号循环里反复全量重扫。

## 事故

`discover_and_load_all()` 每次调用都要**重读并重导入 242 个因子模块**
（审计实测 0.212s/次、344,379 字节源码、259 个因子以 override=True 重注册；
新建 `FactorEngine()` 同样 0.223s）。而调用方里有**按符号循环**构造引擎的写法
（`strategy_intelligence_engine.py:72/244`），等于每个符号白付一次全量扫描。
日志侧证：2 小时内 97 条 `前视因子跳过加载` warning，集中在 19 个秒级桶、
每 ~7-10 分钟一串 5-8 条 = 每次扫描一条。

## 修复

模块级目录指纹缓存：`(相对路径, mtime_ns, size)` 指纹不变即复用已加载的**类对象**
（模块本就只导入一次），每个实例仍往自己的 registry 注册一份，
保持「每个 FactorLoader 独立注册表」的既有契约。

实测（本机）：

    指纹计算      :  26.7 ms
    冷（全量扫描）: 243.2 ms
    热（命中缓存）:  29.9 ms      → 加速 8.1x
"""
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.factor_engine import factor_loader as FL


def _fresh_loader():
    return FL.FactorLoader()


def setup_function(_fn):
    FL._DISCOVERY_CACHE.clear()


# ══════════════════════════════════════════════════════════════════════
# 1. 缓存命中
# ══════════════════════════════════════════════════════════════════════

def test_first_call_populates_cache():
    a = _fresh_loader()
    n = a.discover_and_load_all()
    assert n > 0
    assert a.cache_hit is False, '首次应为未命中'
    assert FL._DISCOVERY_CACHE, '首次加载后应写入指纹缓存'


def test_second_call_hits_cache():
    a = _fresh_loader()
    a.discover_and_load_all()
    b = _fresh_loader()
    m = b.discover_and_load_all()
    assert b.cache_hit is True, '第二次应命中缓存'
    assert m == len(a.loaded_factors)


def test_hit_returns_same_factor_ids():
    a = _fresh_loader()
    a.discover_and_load_all()
    b = _fresh_loader()
    b.discover_and_load_all()
    assert set(b.loaded_factors) == set(a.loaded_factors), '缓存命中不得丢因子'


def test_hit_repopulates_registry():
    """命中缓存时新实例的 registry 仍须被填满（独立注册表契约）。"""
    a = _fresh_loader()
    n = a.discover_and_load_all()
    b = _fresh_loader()
    b.discover_and_load_all()
    assert len(b.registry.list_factors()) == len(a.registry.list_factors()) == n


def test_warm_is_cheaper_than_cold():
    """性能护栏（宽松）：热路径应显著快于冷路径。"""
    t = time.perf_counter()
    a = _fresh_loader()
    a.discover_and_load_all()
    cold = time.perf_counter() - t

    t = time.perf_counter()
    b = _fresh_loader()
    b.discover_and_load_all()
    warm = time.perf_counter() - t

    assert b.cache_hit is True
    assert warm < cold, f'热 {warm:.3f}s 应快于冷 {cold:.3f}s'


# ══════════════════════════════════════════════════════════════════════
# 2. 文件变化必须失效缓存（不牺牲热加载正确性）
# ══════════════════════════════════════════════════════════════════════

def test_touching_a_factor_file_invalidates_cache():
    a = _fresh_loader()
    a.discover_and_load_all()
    assert FL._DISCOVERY_CACHE

    from pathlib import Path
    fdir = Path(FL.__file__).parent / 'factors'
    victim = next(fdir.glob('*/*.py'))
    old = victim.stat().st_mtime_ns
    os.utime(victim, None)
    cur = victim.stat().st_mtime_ns
    if cur == old:                      # 文件系统精度不足时强制改小
        os.utime(victim, ns=(old - 10**9, old - 10**9))
    b = _fresh_loader()
    b.discover_and_load_all()
    assert b.cache_hit is False, '文件 mtime 变化后必须重扫（缓存失效）'


def test_signature_reflects_file_set_and_mtime():
    from pathlib import Path
    fdir = Path(FL.__file__).parent / 'factors'
    sig1 = FL._dir_signature(fdir)
    sig2 = FL._dir_signature(fdir)
    assert sig1 == sig2, '同一状态两次指纹必须一致'
    assert sig1, '指纹不得为空（否则缓存会误命中）'
    # 每一项是 (relpath, mtime_ns, size)
    rel, mt, sz = sig1[0]
    assert isinstance(rel, str) and isinstance(mt, int) and isinstance(sz, int)


def test_signature_skips_underscore_dirs():
    """指纹必须与加载逻辑一致：跳过 `_` 开头目录（如 _ai_gen_quarantine）。"""
    from pathlib import Path
    fdir = Path(FL.__file__).parent / 'factors'
    sig = FL._dir_signature(fdir)
    assert not any(r.startswith('_') for r, _, _ in sig)


# ══════════════════════════════════════════════════════════════════════
# 3. 失败加载不得入缓存（否则会把缺失锁死整个进程生命周期）
# ══════════════════════════════════════════════════════════════════════

def test_failed_load_is_not_cached():
    import io as _io
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    src = _io.open(os.path.join(root, 'backend/services/factor_engine/factor_loader.py'), encoding='utf-8').read()
    i = src.find('if not self.failed_files and self.loaded_factors:')
    assert i > 0, '写缓存必须同时要求「无失败文件」且「有成功加载」'


def test_cache_bounded():
    """缓存条数有上限（防路径变体撑爆）。"""
    assert FL._DISCOVERY_CACHE_MAX > 0
    import io as _io
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    src = _io.open(os.path.join(root, 'backend/services/factor_engine/factor_loader.py'), encoding='utf-8').read()
    assert '_DISCOVERY_CACHE.clear()' in src, '超限时应清理'


# ══════════════════════════════════════════════════════════════════════
# 4. 目录不存在时不得写缓存
# ══════════════════════════════════════════════════════════════════════

def test_missing_dir_does_not_cache(monkeypatch):
    from pathlib import Path

    class _P(type(Path())):
        pass

    # 直接把 factors 目录指向不存在的位置
    real = FL.Path

    def _fake_path(_f):
        return real('/nonexistent/factors')

    monkeypatch.setattr(FL, 'Path', _fake_path)
    a = _fresh_loader()
    assert a.discover_and_load_all() == 0
    assert not FL._DISCOVERY_CACHE, '目录缺失时不得写缓存'
