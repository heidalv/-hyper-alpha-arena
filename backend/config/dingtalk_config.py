"""
钉钉推送配置
"""
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class DingTalkConfig:
    """钉钉推送配置"""
    enabled: bool = True
    default_webhook: Optional[str] = None
    default_sign_secret: Optional[str] = None

    # 队列配置
    queue_size: int = 1000
    worker_count: int = 2

    # 重试配置
    max_retry_count: int = 3
    retry_delay_seconds: int = 60

    # 超时配置
    request_timeout_seconds: int = 10

    # 限流配置
    default_max_per_hour: int = 20

    @classmethod
    def from_env(cls) -> 'DingTalkConfig':
        """从环境变量加载配置"""
        return cls(
            enabled=os.getenv('DINGTALK_ENABLED', 'true').lower() == 'true',
            default_webhook=os.getenv('DINGTALK_DEFAULT_WEBHOOK'),
            default_sign_secret=os.getenv('DINGTALK_DEFAULT_SIGN_SECRET'),
            queue_size=int(os.getenv('DINGTALK_QUEUE_SIZE', '1000')),
            worker_count=int(os.getenv('DINGTALK_WORKER_COUNT', '2')),
            max_retry_count=int(os.getenv('DINGTALK_MAX_RETRY_COUNT', '3')),
            retry_delay_seconds=int(os.getenv('DINGTALK_RETRY_DELAY', '60')),
            request_timeout_seconds=int(os.getenv('DINGTALK_REQUEST_TIMEOUT', '10')),
            default_max_per_hour=int(os.getenv('DINGTALK_DEFAULT_MAX_PER_HOUR', '20')),
        )


# 全局配置实例
config = DingTalkConfig.from_env()
