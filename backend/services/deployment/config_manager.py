"""ATAS V2 部署配置管理"""
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any
import os

class Environment(Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

@dataclass
class EnvironmentConfig:
    name: str
    database_url: str
    redis_url: str
    log_level: str
    debug: bool

class DeploymentConfigManager:
    def __init__(self):
        self.configs = {
            "development": EnvironmentConfig("dev", "localhost:5432", "localhost:6379", "DEBUG", True),
            "production": EnvironmentConfig("prod", "prod-db:5432", "prod-redis:6379", "INFO", False)
        }
    
    def get(self, env: str) -> EnvironmentConfig:
        return self.configs.get(env, self.configs["development"])

def get_deployment_config(env: str = "development") -> EnvironmentConfig:
    manager = DeploymentConfigManager()
    return manager.get(env)
