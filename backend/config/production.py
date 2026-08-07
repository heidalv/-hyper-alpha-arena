"""
生产环境配置文件

包含生产环境的推荐配置
"""
import os
from typing import Optional


class ProductionConfig:
    """生产环境配置"""
    
    # 应用配置
    APP_NAME = "Heidalv-Alpha-Arena"
    APP_VERSION = "2.0.0"
    ENVIRONMENT = "production"
    DEBUG = False
    
    # 服务器配置
    HOST = "0.0.0.0"
    PORT = 8000
    WORKERS = 4  # 根据CPU核心数调整
    
    # 数据库配置
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "postgresql://user:password@localhost:5432/hyper_alpha"
    )
    DATABASE_POOL_SIZE = 10
    DATABASE_MAX_OVERFLOW = 20
    DATABASE_POOL_RECYCLE = 3600  # 1小时
    
    # Redis配置（可选，用于缓存）
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    CACHE_TTL = 300  # 5分钟
    
    # AI服务配置
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = "gpt-4"
    OPENAI_TEMPERATURE = 0.7
    OPENAI_MAX_TOKENS = 2000
    OPENAI_TIMEOUT = 30  # 秒
    
    # 日志配置
    LOG_LEVEL = "INFO"  # 生产环境使用INFO，开发环境用DEBUG
    LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s"
    LOG_FILE = "logs/production.log"
    LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
    LOG_BACKUP_COUNT = 10
    
    # 性能配置
    API_TIMEOUT = 30  # API超时时间（秒）
    MAX_CONCURRENT_STRATEGIES = 10  # 最大并发策略执行数
    STRATEGY_EXECUTION_TIMEOUT = 60  # 策略执行超时（秒）
    
    # 监控配置
    ENABLE_MONITORING = True
    METRICS_ENDPOINT = "/api/monitoring/metrics"
    HEALTH_ENDPOINT = "/api/monitoring/health"
    ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")  # 钉钉/企业微信webhook
    
    # 安全配置
    ALLOWED_HOSTS = ["*"]  # 生产环境应该配置具体域名
    CORS_ORIGINS = ["http://localhost:5173"]  # 前端地址
    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-in-production")
    
    # 限流配置
    RATE_LIMIT_ENABLED = True
    RATE_LIMIT_PER_MINUTE = 60
    RATE_LIMIT_PER_HOUR = 1000
    
    # 备份配置
    BACKUP_ENABLED = True
    BACKUP_SCHEDULE = "0 2 * * *"  # 每天凌晨2点
    BACKUP_RETENTION_DAYS = 30
    
    @classmethod
    def validate(cls) -> bool:
        """验证配置是否完整"""
        errors = []
        
        # 检查必需配置
        if cls.SECRET_KEY == "change-this-in-production":
            errors.append("SECRET_KEY 必须修改")
        
        if not cls.DATABASE_URL:
            errors.append("DATABASE_URL 未配置")
        
        if not cls.OPENAI_API_KEY:
            errors.append("OPENAI_API_KEY 未配置（警告）")
        
        if errors:
            print("⚠️  配置验证失败:")
            for error in errors:
                print(f"   - {error}")
            return False
        
        return True
    
    @classmethod
    def print_config(cls):
        """打印当前配置（隐藏敏感信息）"""
        print("=" * 60)
        print("生产环境配置")
        print("=" * 60)
        print(f"应用名称: {cls.APP_NAME}")
        print(f"版本: {cls.APP_VERSION}")
        print(f"环境: {cls.ENVIRONMENT}")
        print(f"调试模式: {cls.DEBUG}")
        print(f"服务器: {cls.HOST}:{cls.PORT}")
        print(f"工作进程: {cls.WORKERS}")
        print(f"数据库: {cls.DATABASE_URL.split('@')[1] if '@' in cls.DATABASE_URL else 'Not configured'}")
        print(f"日志级别: {cls.LOG_LEVEL}")
        print(f"监控: {'启用' if cls.ENABLE_MONITORING else '禁用'}")
        print(f"限流: {'启用' if cls.RATE_LIMIT_ENABLED else '禁用'}")
        print("=" * 60)


class DevelopmentConfig:
    """开发环境配置"""
    
    APP_NAME = "Heidalv-Alpha-Arena"
    APP_VERSION = "2.0.0-dev"
    ENVIRONMENT = "development"
    DEBUG = True
    
    HOST = "127.0.0.1"
    PORT = 8000
    WORKERS = 1
    
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/hyper_alpha_dev"
    )
    
    LOG_LEVEL = "DEBUG"
    LOG_FILE = "logs/development.log"
    
    ENABLE_MONITORING = True
    RATE_LIMIT_ENABLED = False
    
    SECRET_KEY = "dev-secret-key"


class TestConfig:
    """测试环境配置"""
    
    APP_NAME = "Heidalv-Alpha-Arena"
    APP_VERSION = "2.0.0-test"
    ENVIRONMENT = "test"
    DEBUG = True
    
    DATABASE_URL = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/hyper_alpha_test"
    )
    
    LOG_LEVEL = "DEBUG"
    LOG_FILE = "logs/test.log"
    
    ENABLE_MONITORING = False
    RATE_LIMIT_ENABLED = False


def get_config():
    """根据环境变量获取配置"""
    env = os.getenv("ENVIRONMENT", "development")
    
    if env == "production":
        return ProductionConfig
    elif env == "test":
        return TestConfig
    else:
        return DevelopmentConfig


# 导出当前配置
config = get_config()


if __name__ == "__main__":
    # 验证并打印配置
    config.print_config()
    
    if config.ENVIRONMENT == "production":
        if config.validate():
            print("\n✅ 配置验证通过")
        else:
            print("\n❌ 配置验证失败")
