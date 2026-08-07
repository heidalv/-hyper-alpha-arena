"""
Application Startup Configuration
Modular startup configuration for Hyper Alpha Arena
"""
import os
import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)


class StartupConfig:
    """Manages application startup configuration"""
    
    def __init__(self):
        self._startup_tasks: list[tuple[str, Callable]] = []
    
    def register_task(self, name: str, task: Callable):
        """Register a startup task"""
        self._startup_tasks.append((name, task))
    
    def run_tasks(self) -> dict[str, Any]:
        """Run all registered startup tasks"""
        results = {}
        for name, task in self._startup_tasks:
            try:
                result = task()
                results[name] = {"status": "success", "result": result}
                logger.info(f"Startup task '{name}' completed successfully")
            except Exception as e:
                results[name] = {"status": "error", "error": str(e)}
                logger.error(f"Startup task '{name}' failed: {e}")
        return results


# Global startup config instance
_startup_config = StartupConfig()


def get_startup_config() -> StartupConfig:
    """Get the global startup configuration"""
    return _startup_config


def register_startup_task(name: str, task: Callable):
    """Register a startup task globally"""
    _startup_config.register_task(name, task)


def run_startup_tasks() -> dict[str, Any]:
    """Run all registered startup tasks"""
    return _startup_config.run_tasks()
