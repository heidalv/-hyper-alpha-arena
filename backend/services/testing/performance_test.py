"""ATAS V2 性能测试"""
import time
from typing import Callable, Dict

class PerformanceTestRunner:
    def benchmark(self, func: Callable, iterations: int = 1000) -> Dict:
        start = time.time()
        for _ in range(iterations):
            func()
        duration = time.time() - start
        
        return {
            "iterations": iterations,
            "total_time": duration,
            "avg_time": duration / iterations,
            "ops_per_second": iterations / duration
        }

def run_performance_tests(func: Callable, iterations: int = 1000) -> Dict:
    runner = PerformanceTestRunner()
    return runner.benchmark(func, iterations)
