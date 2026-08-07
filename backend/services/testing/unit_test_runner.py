"""ATAS V2 单元测试运行器"""
from dataclasses import dataclass
from typing import List
import unittest

@dataclass
class TestResult:
    total: int
    passed: int
    failed: int
    errors: int
    skipped: int
    duration: float

class UnitTestRunner:
    def __init__(self):
        self.loader = unittest.TestLoader()
    
    def run(self, test_dir: str = "tests") -> TestResult:
        suite = self.loader.discover(test_dir)
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        
        return TestResult(
            total=result.testsRun,
            passed=result.testsRun - len(result.failures) - len(result.errors),
            failed=len(result.failures),
            errors=len(result.errors),
            skipped=len(result.skipped),
            duration=0.0
        )

def run_unit_tests(test_dir: str = "tests") -> TestResult:
    runner = UnitTestRunner()
    return runner.run(test_dir)
